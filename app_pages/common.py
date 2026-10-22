"""
app_pages/common.py — HELPERS SHARED BY THE PAGES.

  get_kb(), get_retriever()   load the knowledge base and search index once for
                              all users (public data, no secrets)
  get_case_db()               the case database (comparable cases): loaded from the app's files
                              (cases/case_database.sqlite3 + the ChromaDB collection cases/chroma,
                              core/case_store.py) when a session starts — or, the first time, from
                              a database.json committed to the repository — and held in
                              st.session_state while the session runs
  case_db_changed()           call after any change: saves the database to those files
  model_settings_sidebar()    the "Model settings" box in the sidebar
  make_llm()                  an LLM object built from THIS user's key and settings
  show_*()                    small display helpers (check results, citations,
                              the run log)
"""

from __future__ import annotations

import uuid

import streamlit as st

from core import config
from core.case_store import CaseStore, StoreError
from core.cases import POLICY, CaseDB, CaseRecord, Comparables, load_committed
from core.kb import KnowledgeBase
from core.llm import LLM
from core.results import sort_results
from core.retrieval import Retriever, VectorIndex

ROLE_LABELS = {"extraction": "Facts extraction (AI call 1) and case chunking", "analysis": "Stage analyses (AI calls 2–5)",
               "synthesis": "Proposed sentence (AI call 6, revision)", "audit": "Audit (AI call 7, re-audit)"}
BADGE = {"pass": "🟢 pass", "caution": "🟠 caution", "unresolved": "🟠 unresolved", "error": "🔴 error",
         "info": "⚪ info", "not_applicable": "⚪ n/a"}


# ─────────────────────────────────────────────────────────────────────────────
# Loading (cached for every user: the data is public)
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="Loading the knowledge base…")
def get_kb() -> KnowledgeBase:
    return KnowledgeBase()


@st.cache_resource(show_spinner="Loading the search index…")
def get_retriever() -> Retriever:
    return Retriever(get_kb(), VectorIndex.load())


@st.cache_resource(show_spinner=False)
def get_keyword_retriever() -> Retriever:
    """Keyword (BM25) ranking, for searches made without a key."""
    return Retriever(get_kb(), None)


@st.cache_resource(show_spinner="Loading the case database…")
def get_committed_case_db() -> tuple[CaseDB | None, str]:
    """The case database committed to the repository (public data, no secrets), and any error loading it.
    Never changed: each user works on their own copy (get_case_db)."""
    try:
        return load_committed(), ""
    except Exception as exc:  # noqa: BLE001 — a damaged file must not break the app for every user
        return None, f"The repository's cases/database.json could not be loaded ({type(exc).__name__}: {exc})."


def get_case_db() -> CaseDB:
    """The case database. A session starts from what is saved in the app's files (core/case_store.py); if nothing
    has been saved yet, from a copy of the database committed as cases/database.json (which is then saved to the
    files, so later sessions start from them); otherwise empty."""
    if "case_db" not in st.session_state:
        store, db, damaged = CaseStore(), None, False
        try:
            db = store.load()
        except StoreError as exc:
            damaged = True
            st.session_state.cdb_notice = ("error", f"{exc} The session starts without the saved cases; the file is "
                                                    "left as it is until you change the database here (copy it first "
                                                    "if you want to recover it).")
        if db is None:
            base, error = get_committed_case_db()
            if error:
                st.session_state.cdb_notice = ("error", error)
            db = base.copy() if base else CaseDB()
            if base:
                db.origin = f"the repository's {base.origin}"
                if not damaged:
                    save_case_db(db)                      # seeds the files the first time
        st.session_state.case_db = db
        st.session_state.case_db_version = 0
    return st.session_state.case_db


def save_case_db(db: CaseDB | None = None) -> bool:
    """Write the database to the app's files (SQLite + the ChromaDB collection). A failure is shown on the Case
    database page, never raised: the cases stay in the session."""
    db = db if db is not None else st.session_state.get("case_db")
    if db is None:
        return False
    try:
        CaseStore().save(db)
        return True
    except StoreError as exc:
        st.session_state.cdb_notice = ("error", f"{exc} The cases are kept for this session: download them.")
        return False


def case_db_changed() -> None:
    """Call after adding, loading, editing or clearing cases: saves the database to the app's files and resets
    the case table's editing state."""
    st.session_state.case_db_version = st.session_state.get("case_db_version", 0) + 1
    save_case_db()


# ─────────────────────────────────────────────────────────────────────────────
# Model settings in the sidebar (per user session)
# ─────────────────────────────────────────────────────────────────────────────
def model_settings_sidebar() -> None:
    if "models" not in st.session_state:
        st.session_state.models = dict(config.DEFAULT_MODELS)
        st.session_state.efforts = dict(config.DEFAULT_EFFORT)
        st.session_state.parallel = config.PARALLEL_STAGES
    with st.sidebar.expander("Model settings", expanded=False):
        st.caption("Each step's OpenAI model. Change one if your key cannot use it (the app also falls back to "
                   "another model automatically and says so in the run log). Changes apply to the next case.")
        everything = st.selectbox("Use one model for every step (optional)", ["—"] + config.MODEL_OPTIONS,
                                  key="model_all")
        for role, label in ROLE_LABELS.items():
            if everything != "—":
                st.session_state.models[role] = everything
            current = st.session_state.models[role]
            options = config.MODEL_OPTIONS + ([current] if current not in config.MODEL_OPTIONS else []) + ["Other…"]
            if st.session_state.get(f"model_{role}") not in options or everything != "—":
                st.session_state[f"model_{role}"] = current
            choice = st.selectbox(label, options, key=f"model_{role}", disabled=everything != "—")
            if choice == "Other…":
                typed = st.text_input("Model name", value="", key=f"model_other_{role}").strip()
                if typed:
                    st.session_state.models[role] = typed
            else:
                st.session_state.models[role] = choice
            effort = st.session_state.efforts[role]
            st.session_state.efforts[role] = st.select_slider(
                "Reasoning effort", config.EFFORT_OPTIONS, value=effort if effort in config.EFFORT_OPTIONS else "medium",
                key=f"effort_{role}")
        st.session_state.parallel = st.checkbox(
            "Run the four analyses at the same time", value=st.session_state.parallel, key="parallel_box",
            help="Faster. Untick if your OpenAI account's rate limit is low and calls fail with “rate limit”.")


def has_key() -> bool:
    return bool(st.session_state.get("openai_api_key"))


def make_llm(models: dict | None = None, efforts: dict | None = None) -> LLM:
    """The AI client for this user: their key, and the given (or current) model settings."""
    # An anonymous random id for this browser session (OpenAI asks end-user apps
    # to send one with each request; it identifies nothing about the user).
    session = st.session_state.setdefault("_session_tag", uuid.uuid4().hex[:16])
    return LLM(api_key=st.session_state.get("openai_api_key") or "",
               models=dict(models or st.session_state.get("models", config.DEFAULT_MODELS)),
               efforts=dict(efforts or st.session_state.get("efforts", config.DEFAULT_EFFORT)),
               safety_id=f"vse-{session}")


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────
def show_citations(kb: KnowledgeBase, item_ids: list[str], passage_ids: list[str] | None = None,
                   label: str = "Sources") -> None:
    """An expander listing each cited item with its verbatim quotes."""
    item_ids = [i for i in dict.fromkeys(item_ids) if kb.exists(i)]
    passage_ids = list(dict.fromkeys(passage_ids or []))
    if not item_ids and not passage_ids:
        return
    with st.expander(f"{label} ({len(item_ids) + len(passage_ids)})"):
        for i in item_ids:
            st.markdown(f"**{kb.title(i)}** · `{i}`")
            cits = kb.citations_for(i, 2)
            if not cits:
                st.caption(kb.statement(i))
            for c in cits:
                st.markdown(f"> {c.quote}\n>\n> — *{c.label}*")
                for fn in c.footnotes[:3]:
                    st.caption(f"fn {fn.n}: {fn.text[:220]}")
        for cid in passage_ids:
            c = kb.chunk_citation(cid)
            if c:
                st.markdown(f"**Passage** · `{cid}` — *{c.label}*")
                st.caption(c.quote[:700] + ("…" if len(c.quote) > 700 else ""))


def show_results(kb: KnowledgeBase, results, hide_pass: bool = False) -> None:
    """Errors, unresolved points and cautions first (each with the Manual's
    text); the checks that passed are listed in a collapsed box."""
    problems = [r for r in sort_results(results) if r.outcome in ("error", "unresolved", "caution")]
    passed = [r for r in sort_results(results) if r.outcome == "pass"]
    for r in problems:
        text = f"**{BADGE.get(r.outcome, r.outcome)} — {r.title}.** {r.message}"
        if r.app_policy:
            text += " *(app policy)*"
        if r.flags:
            text += "  \nManual flag: " + ", ".join(f"`{f}`" for f in r.flags)
        st.markdown(text)
        if r.cite or r.flags:
            show_citations(kb, r.cite + r.flags, label="Manual text")
    if not problems:
        st.markdown("No errors or cautions.")
    if passed and not hide_pass:
        with st.expander(f"Passed ({len(passed)})"):
            for r in passed:
                st.markdown(f"**{BADGE['pass']} — {r.title}.** {r.message}")
            show_citations(kb, [c for r in passed for c in r.cite], label="Manual text for these checks")


def show_case(c: CaseRecord, similarity: str = "") -> None:
    """One case from the case database: its figures, then its text word for word."""
    st.markdown(f"**{c.id} — {c.citation}**" + (f" · {similarity}" if similarity else ""))
    st.caption(f"{c.court}{', ' + str(c.year) if c.year else ''} · principal offence: {c.principal_offence or '—'} · "
               f"head sentence: {c.head_sentence_text or '—'} · plea: {c.plea.replace('_', ' ')} · "
               f"from {c.source}, lines {c.lines[0]}–{c.lines[1]}")
    st.text(c.text)


def show_case_citations(case_ids: list[str], comparables: Comparables | None) -> None:
    """An expander listing the comparable cases a point or reason relied on."""
    cases = [comparables.case(i) for i in dict.fromkeys(case_ids)] if comparables else []
    cases = [c for c in cases if c]
    if not cases:
        return
    with st.expander(f"Comparable cases relied on ({len(cases)}) — yardsticks, not law"):
        for c in cases:
            show_case(c)


def show_comparables(comparables: Comparables | None) -> None:
    """The cases a run was given from the case database, and those left out."""
    if not comparables or not comparables.available:
        return
    st.markdown(f"**Comparable cases from your case database** — chosen by {comparables.method}, the same offence "
                "first. Yardsticks of current practice only; the Manual is the only source of law.")
    st.caption(POLICY[0].upper() + POLICY[1:] + ".")
    if not comparables.selected:
        st.caption(f"None given: {comparables.note}")
    else:
        st.dataframe([{"Id": e["id"], "Case": e["case"]["citation"], "Match": e["match"],
                       comparables.score_label.capitalize(): e["similarity"],
                       "Principal offence": e["case"]["principal_offence"],
                       "Its sentence for this offence (months)": e.get("offence_sentence"),
                       "Head sentence / orders": e["case"]["head_sentence_text"],
                       "Also listed as": ", ".join(e.get("same_as") or []),
                       "Check": e["case"].get("figures_note", "")} for e in comparables.selected],
                     hide_index=True, width="stretch")
    for e in comparables.left_out:
        st.caption(f"Left out: {e['id']} {e['case'].get('citation', '')} — {e['reason']}.")


def show_call_log(run) -> None:
    def rows(calls):
        return [{"#": k + 1, "Step": c.step, "Kind": "AI call" if c.kind == "ai" else "embeddings", "Model": c.model,
                 "Effort": c.effort or "", "Tokens in": c.input_tokens, "Tokens out": c.output_tokens,
                 "Seconds": c.seconds, "Status": ("ok" if c.ok else "failed") + (f" — {c.note}" if c.note else "")}
                for k, c in enumerate(calls)]
    st.dataframe(rows(run.calls), hide_index=True, width="stretch")
    reused = getattr(run, "reused_calls", [])
    if reused:
        st.caption(f"Made for an earlier run of this case and reused here (counted there, not here): "
                   f"{sum(1 for c in reused if c.kind == 'ai' and c.ok)} AI call(s).")
        st.dataframe(rows(reused), hide_index=True, width="stretch")
    discarded = getattr(run, "discarded_calls", [])
    if discarded:
        st.caption(f"Not counted in this run — their results were replaced or not used (see each call's status): "
                   f"{sum(1 for c in discarded if c.kind == 'ai' and c.ok)} AI call(s).")
        st.dataframe(rows(discarded), hide_index=True, width="stretch")
