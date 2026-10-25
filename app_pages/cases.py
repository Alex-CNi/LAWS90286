"""
app_pages/cases.py — THE CASE DATABASE PAGE: COMPARABLE CASES FOR THE ESTIMATES.

What the page does, top to bottom:
  A. Status — how many cases, how many are in use and embedded, by offence
  B. Add cases — upload documents of case summaries (e.g. the Judicial College's
     Sentencing Manual case summaries, downloaded by the user); the AI splits each
     document into individual cases and labels each by the offences charged — any
     offence of the Manual's register, or "other" (core/cases.py chunk_document: one
     AI call per part of the document, listing the offences of the chapters the user
     says the document covers), then each case is embedded (one embeddings request
     per 48 cases). Cases saved by version 1.x (chapter 25 labels only) can be
     relabelled by their names, with no AI
  C. Saved in the app's files — every change is written to cases/case_database.sqlite3
     (SQLite: the cases and their embeddings) and the ChromaDB collection in
     cases/chroma/ (core/case_store.py), and loaded again when the app starts; the
     JSON download/upload is a portable copy
  D. The cases — a table to check and correct the labels and figures, untick a case
     to leave it out, and read any case word for word

While a session runs the database is held in st.session_state.case_db (saved by
case_db_changed after each change). The estimate page gives the most similar cases
(cosine similarity, from the ChromaDB collection) to the current-practice analysis,
the proposal and the audit, as comparable cases only: the Manual is the only source
of law.
"""

from __future__ import annotations

import hashlib
import json
import math

import pandas as pd
import streamlit as st

from core.case_store import CHROMA_DIR, SQLITE_FILE, CaseStore, collection_name
from app_pages.common import (case_db_changed, get_case_db, get_committed_case_db, get_kb, has_key, make_llm,
                              show_case)
from app_pages.theme import hero
from core import config
from core.cases import (CaseDB, case_labels, check_figures, chunk_document, read_document, related_offences,
                        relabel_by_name, relabel_case, segments, set_principal_sentence, to_lines)
from core.llm import LLMError

kb = get_kb()
db = get_case_db()
LABELS = case_labels(kb)                              # (register id, charge label): every register offence
NAME_OF = dict(LABELS)
ID_OF = {name: rid for rid, name in LABELS}
OTHER = "Other offence"
CONFIRM_ABOVE = 30                                    # more AI calls than this: ask the user to confirm

hero("Your case database", small=True)
st.markdown(
    "Upload sentencing case summaries — for example the Judicial College's *Sentencing Manual case summaries*. "
    "Its **Word or PDF download** works best; a page saved from the browser may not contain the cases, because "
    "the site builds them with JavaScript. The AI splits each document into individual cases and labels each by "
    "the offences charged; the code copies each case's text from your document word for word and checks the "
    "AI's figures against it. When you estimate a sentence, the cases most similar to the facts (cosine "
    "similarity of embeddings, the same offence first) are given to the current-practice analysis, the proposal "
    "and the audit as **comparable cases**: yardsticks of current sentencing practice, never a source of law. "
    "The Manual stays the only source of law.")


# ─────────────────────────────────────────────────────────────────────────────
# A. Status
# ─────────────────────────────────────────────────────────────────────────────
c1, c2, c3 = st.columns(3)
c1.metric("Cases", len(db.cases))
c2.metric("In use", len(db.usable()))
c3.metric("With embeddings", len(db.cases) - len(db.missing_vectors()))
if db.origin:
    st.caption(f"Started from {db.origin}.")
if db.cases:
    with st.expander("Cases by principal offence"):
        st.dataframe([{"Principal offence": name, "Cases": n} for name, n in db.counts_by_offence(kb)],
                     hide_index=True, width="stretch")
    with st.expander("Which of these cases would an estimate use? Check a charge"):
        st.caption("The estimate uses, in this order: cases whose principal offence is the charge; cases in which the "
                   "charge was also one of the offences; then cases for another offence in the same section of the "
                   "Manual's register (app policy). Cases for any other offence, cases marked “Other offence”, and "
                   "cases unticked in the table are never used.")
        pick = st.selectbox("Charge", config.IN_SCOPE_OFFENCES, key="cdb_check_charge",
                            format_func=lambda o: NAME_OF.get(o, o))
        usable = db.usable()
        same = [c for c in usable if c.principal_offence_id == pick]
        also = [c for c in usable if c.principal_offence_id != pick
                and pick in {o.get("offence_id") for o in c.offences}]
        rel_ids = related_offences(kb, pick)
        related = [c for c in usable if c.principal_offence_id in rel_ids and c not in also]
        other = [c for c in usable if c.principal_offence_id not in (rel_ids | {pick}) and c not in also]
        unlabelled = [c for c in other if c.principal_offence_id not in kb.offences]
        st.dataframe([{"Tier": "Same offence", "Cases in use": len(same)},
                      {"Tier": "The charge also among the offences", "Cases in use": len(also)},
                      {"Tier": "Same section of the register (" + ", ".join(sorted(
                          NAME_OF.get(o, o).split(" — ")[0] for o in rel_ids))[:120] + ")", "Cases in use": len(related)},
                      {"Tier": "Other offences — not used", "Cases in use": len(other)},
                      {"Tier": "   of which “Other offence” (no register label)", "Cases in use": len(unlabelled)}],
                     hide_index=True, width="stretch")
        would = len(same) + len(also) + len(related)
        if not would:
            st.error(f"No case in use would reach an estimate for this charge. {len(other)} case(s) are for other "
                     "offences" + (f", {len(unlabelled)} with no register label (if their names match a register "
                                   "offence, press Relabel below; otherwise set the principal offence in the table)."
                                   if unlabelled else "."))
        else:
            missing = [c for c in same + also + related if c.id not in db.vectors]
            st.success(f"{would} case(s) would be compared, up to {config.CASES_FOR_COMPARISON} shown"
                       + (f"; {len(missing)} of them have no embeddings, so they are ranked by keywords until embedded."
                          if missing else ", ranked by cosine similarity."))


def embed_now(note_prefix: str = "") -> None:
    bar = st.progress(0.0, text="Embedding the cases…")
    try:
        db.embed_missing(make_llm(), progress=lambda f, m: bar.progress(f, text=m))
        st.session_state.cdb_notice = ("success", note_prefix + "Embeddings made.")
    except LLMError as exc:
        st.session_state.cdb_notice = ("error", note_prefix + f"Embeddings failed: {exc} The cases are kept; embed "
                                                              "them later with the button at the top of the page.")
    case_db_changed()


missing = db.missing_vectors()
if missing and st.session_state.pop("cdb_embed_after_load", False) and has_key():
    embed_now(st.session_state.pop("cdb_notice", ("", ""))[1] + " ")      # a file was just loaded
    st.rerun()
if missing:
    st.warning(f"{len(missing)} case(s) have no embeddings, so the estimate ranks the cases by keywords instead of "
               "cosine similarity until they are embedded.")
    if st.button(f"Embed the {len(missing)} case(s) now (embeddings requests with your key)", disabled=not has_key()):
        embed_now()
        st.rerun()

notice = st.session_state.pop("cdb_notice", None)
if notice and notice[1].strip():
    getattr(st, notice[0])(notice[1])


# ─────────────────────────────────────────────────────────────────────────────
# B. Add cases from documents (AI chunking and labelling)
# ─────────────────────────────────────────────────────────────────────────────
def file_text(upload) -> str:
    """The text of an uploaded file, read once per file (a large PDF is slow to read)."""
    raw = upload.getvalue()
    key = hashlib.sha1(raw).hexdigest()
    texts = st.session_state.setdefault("cdb_texts", {})
    if key not in texts:
        if len(texts) > 20:
            texts.clear()
        texts[key] = read_document(upload.name, raw)
    return texts[key]


with st.container(border=True):
    st.subheader("Add cases from a document")
    uploads = st.file_uploader("Documents of case summaries (.docx, .pdf, .txt, .md, .html)",
                               type=["docx", "pdf", "txt", "md", "html", "htm"], accept_multiple_files=True,
                               key="cdb_files")
    chapters = st.multiselect(
        "Chapters of the Manual the documents cover", config.CASE_LABEL_CHAPTERS,
        default=config.CASE_LABEL_CHAPTERS, format_func=kb.chapter_title, key="cdb_chapters",
        help="Each AI call lists the offences of these chapters for the labels (fewer chapters: shorter calls). A "
             "case for an offence of a chapter not chosen is labelled “Other offence”; you can relabel it below.")
    plan, total = [], 0
    for up in uploads or []:
        try:
            text = file_text(up)
        except Exception as exc:  # noqa: BLE001 — a damaged or unreadable file
            st.error(f"{up.name}: the file could not be read ({type(exc).__name__}).")
            continue
        lines = to_lines(text)
        parts = len(segments(lines))
        plan.append((up.name, text))
        total += parts
        st.caption(f"{up.name}: {len(lines)} lines, {len(text):,} characters → {parts} part(s), one AI call each.")
    confirmed = True
    if total > CONFIRM_ABOVE:
        confirmed = st.checkbox(f"I understand this makes about {total} AI calls with my key "
                                f"(and more if a part has to be retried or split).", key="cdb_confirm")
    st.caption(f"Chunking uses the sidebar's facts-extraction model "
               f"({st.session_state.get('models', {}).get('extraction', config.DEFAULT_MODELS['extraction'])}), "
               f"{config.CHUNK_PARALLEL} calls at a time; these calls are not part of any estimate run. Stay on this "
               "page until it finishes: clicking elsewhere stops it and cancels the parts not yet started. Chunking "
               "a document again adds only the cases not already here.")
    if plan and not chapters:
        st.warning("Choose at least one chapter: the labels come from the chapters' offences.")
    if st.button(f"Chunk and label the cases with AI ({total} AI call(s)) →", type="primary",
                 disabled=not (has_key() and plan and confirmed and chapters)):
        llm = make_llm()
        summaries = []
        for name, text in plan:
            with st.status(f"Chunking {name}…", expanded=True) as status:
                bar = st.progress(0.0)
                try:
                    report = chunk_document(llm, kb, name, text, progress=lambda f, m: bar.progress(f, text=m),
                                            chapters=chapters)
                except Exception as exc:  # noqa: BLE001 — shown, not raised
                    status.update(label=f"{name}: stopped ({type(exc).__name__}: {exc})", state="error")
                    summaries.append({"file": name, "error": f"{type(exc).__name__}: {exc}"})
                    continue
                added, skipped = db.add(report.cases)
                status.update(label=f"{name}: {len(report.cases)} case(s) found, {added} added",
                              state="error" if report.errors else "complete")
                summaries.append({"file": name, "found": len(report.cases), "added": added, "skipped": skipped,
                                  "parts": report.segments, "errors": report.errors, "warnings": report.warnings,
                                  "calls": report.calls})
        if db.missing_vectors():
            bar = st.progress(0.0, text="Embedding the new cases…")
            try:
                db.embed_missing(llm, progress=lambda f, m: bar.progress(f, text=m))
            except LLMError as exc:
                summaries.append({"file": "Embeddings", "error": f"{exc} The cases were kept; embed them later "
                                                                 "with the button at the top of the page."})
        if any(s.get("added") for s in summaries):
            db.origin = (db.origin + " + " if db.origin else "") + "uploaded documents"
        st.session_state.cdb_reports = summaries
        case_db_changed()
        st.rerun()

    for s in st.session_state.get("cdb_reports", []):
        if s.get("error") and "found" not in s:
            st.error(f"{s['file']}: {s['error']}")
            continue
        st.markdown(f"**{s['file']}** — {s['found']} case(s) found in {s['parts']} part(s); {s['added']} added"
                    + (f", {s['skipped']} already in the database" if s["skipped"] else "") + ".")
        for e in s["errors"]:
            st.error(f"A part could not be chunked ({e}). Its cases are missing: chunk the document again — the "
                     "cases already in the database are skipped.")
        for w in s["warnings"]:
            st.warning(w)
        if s["calls"]:
            with st.expander(f"Calls made for {s['file']} ({sum(1 for c in s['calls'] if c.kind == 'ai')} AI calls)"):
                st.dataframe([{"Step": c.step, "Model": c.model, "Tokens in": c.input_tokens,
                               "Tokens out": c.output_tokens, "Seconds": c.seconds,
                               "Status": ("ok" if c.ok else "failed") + (f" — {c.note}" if c.note else "")}
                              for c in s["calls"]], hide_index=True, width="stretch")


# ─────────────────────────────────────────────────────────────────────────────
# C. Save and load (the buttons that discard the current cases need the box ticked; the callbacks untick it)
# ─────────────────────────────────────────────────────────────────────────────
def replace_db(new: CaseDB, notice: tuple[str, str]) -> None:
    st.session_state.case_db = new
    st.session_state.cdb_reports = []
    st.session_state.cdb_notice = notice
    st.session_state.cdb_discard_ok = False
    st.session_state.cdb_exclude = []        # ids start again in a new database: they would name other cases
    st.session_state.cdb_embed_after_load = bool(new.missing_vectors())
    case_db_changed()


def go_back_to_committed():
    committed, _ = get_committed_case_db()
    new = committed.copy()
    new.origin = f"the repository's {committed.origin}"
    replace_db(new, ("success", f"Back to the repository's database ({len(new.cases)} cases)."))


def clear_db():
    replace_db(CaseDB(), ("success", "The database was cleared."))


def delete_unused():
    """Callback: delete the cases not in use (the discard box is unticked again)."""
    db = st.session_state.case_db
    unused = {c.id for c in db.cases if not c.use}
    db.remove(unused)
    st.session_state.cdb_discard_ok = False
    st.session_state.cdb_notice = ("success", f"Deleted {len(unused)} case(s).")
    case_db_changed()


def load_saved():
    saved = st.session_state.get("cdb_json")
    if saved is None:
        return
    try:
        loaded = CaseDB.from_json(saved.getvalue().decode("utf-8", errors="replace"), origin=saved.name)
    except Exception as exc:  # noqa: BLE001 — any damaged file gives a message, never a traceback
        reason = str(exc) if isinstance(exc, ValueError) else f"the file is damaged ({type(exc).__name__})"
        st.session_state.cdb_notice = ("error", f"{saved.name} could not be loaded: {reason}")
        return
    if st.session_state.get("cdb_mode", "").startswith("replace"):
        replace_db(loaded, ("success", f"Loaded {len(loaded.cases)} case(s) from {saved.name}."))
        return
    current = st.session_state.case_db
    added, skipped = current.merge(loaded)
    current.origin = (current.origin + " + " if current.origin else "") + saved.name
    st.session_state.cdb_notice = ("success", f"Added {added} case(s) from {saved.name}"
                                   + (f"; {skipped} were already here." if skipped else "."))
    st.session_state.cdb_embed_after_load = bool(current.missing_vectors())
    case_db_changed()


with st.container(border=True):
    st.subheader("Saved in the app's files")
    info = CaseStore().info()
    if info["cases"]:
        st.markdown(f"**`cases/{SQLITE_FILE}`** (SQLite): {info['cases']} case(s), {info['vectors']} with embeddings, "
                    f"saved {info['saved']} · **`cases/{CHROMA_DIR}/`** (ChromaDB collection `{collection_name()}`): "
                    + (f"{info['chroma']} embedding(s), searched by cosine similarity." if not info["chroma_error"]
                       else f"unavailable ({info['chroma_error']}) — the cases are ranked with NumPy instead, the "
                            "same cosine similarity."))
    else:
        st.markdown(f"Nothing saved yet. Every change on this page is written to **`cases/{SQLITE_FILE}`** (SQLite, "
                    f"the cases and their embeddings) and to the ChromaDB collection in **`cases/{CHROMA_DIR}/`** "
                    "(the embeddings, searched by cosine similarity), and loaded again when the app starts.")
    st.caption("The files belong to the server the app runs on and are shared by everyone using it. On Streamlit "
               "Community Cloud they last until the app is rebooted or redeployed: to keep the database for good "
               f"(and to give your marker the same cases), commit `cases/{SQLITE_FILE}` to your repository "
               f"(`cases/{CHROMA_DIR}/` is rebuilt from it). The JSON download below is a portable copy you can load "
               "into any copy of the app. Case summaries published by the Judicial College are its material: check "
               "its terms of use before putting them in a public repository.")
    left, right = st.columns(2)
    discard_ok = st.session_state.get("cdb_discard_ok", False)
    with left:
        st.download_button("Download the database (.json)", db.to_json(), file_name="case_database.json",
                           mime="application/json", disabled=not db.cases)
        st.checkbox("I understand the buttons below discard the current cases", key="cdb_discard_ok",
                    disabled=not db.cases)
        committed, _ = get_committed_case_db()
        if committed:
            st.button(f"Go back to the repository's database ({len(committed.cases)} cases)",
                      on_click=go_back_to_committed, disabled=bool(db.cases) and not discard_ok)
        st.button("Clear the database", on_click=clear_db, disabled=not (db.cases and discard_ok))
    with right:
        saved = st.file_uploader("Load a saved database (.json)", type=["json"], key="cdb_json")
        mode = st.radio("Loading it should", ["add its cases to this database", "replace this database"],
                        key="cdb_mode", horizontal=True)
        st.button("Load", on_click=load_saved,
                  disabled=saved is None or (mode.startswith("replace") and bool(db.cases) and not discard_ok),
                  help="Replacing a database that has cases needs the box on the left ticked.")


# ─────────────────────────────────────────────────────────────────────────────
# D. The cases: check and correct the labels and figures
# ─────────────────────────────────────────────────────────────────────────────
def num(value):
    """A number from the table (pandas uses NaN for empty cells)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) else value


def label_of(c) -> str:
    """How a case's principal offence is shown: its name, "Other offence", or (a label from an older setting)
    its id."""
    if c.principal_offence_id in NAME_OF:
        return NAME_OF[c.principal_offence_id]
    return c.principal_offence_id or OTHER


def relabel_other():
    """Callback: label the "other" cases whose offence name as written is exactly one register offence's name."""
    db = st.session_state.case_db
    changed = relabel_by_name(db, kb)
    if changed:
        case_db_changed()
    st.session_state.cdb_notice = ("success", f"Labelled offences in {changed} case(s) by their names." if changed
                                   else "No offence marked “Other offence” has a name that is exactly one register "
                                        "offence's name.")


def snapshot(cases) -> str:
    return json.dumps([[c.use, c.principal_offence_id, c.offences, c.head_sentence_months, c.npp_months]
                       for c in cases], default=str)


if db.cases:
    with st.container(border=True):
        st.subheader("The cases")
        st.caption("Check the labels and figures the AI gave: “Check” lists anything that does not match the "
                   "case's text. Change a principal offence or a figure in the table, or untick “Use” to leave a "
                   "case out of the comparisons. “Its sentence” is the individual sentence on the principal "
                   "offence; it can be set only when the principal offence is one of the listed offences.")
        unknown = sorted({c.principal_offence_id for c in db.cases
                          if c.principal_offence_id and c.principal_offence_id not in NAME_OF})
        options = [n for _, n in LABELS] + [OTHER] + unknown
        present = {label_of(c) for c in db.cases}
        f1, f2 = st.columns([1, 1])
        show = f1.selectbox("Show", ["All cases", "Cases to check", "The same decision listed again"]
                            + [o for o in options if o in present], key="cdb_show")
        search = f2.text_input("Search the case names", key="cdb_search").strip().lower()
        same = db.same_decision()
        rows, shown = [], []
        for c in db.cases:
            label = label_of(c)
            check = "; ".join(x for x in [("citation not found in the text" if not c.citation_found else ""),
                                          c.figures_note] if x)
            if (show == "Cases to check" and not check) or (show == "The same decision listed again" and
                                                             c.id not in same) or (
                    show not in ("All cases", "Cases to check", "The same decision listed again") and label != show):
                continue
            if search and search not in c.citation.lower():
                continue
            shown.append(c)
            rows.append({"Use": c.use, "Id": c.id, "Case": c.citation, "Court": c.court, "Year": c.year,
                         "Principal offence": label, "As written": c.principal_offence,
                         "Its sentence (months)": c.principal_sentence_months,
                         "Total term (months)": c.head_sentence_months, "NPP (months)": c.npp_months,
                         "Head sentence / orders": c.head_sentence_text, "Plea": c.plea.replace("_", " "),
                         "Check": ("⚠ " + check) if check else "", "Same decision as": same.get(c.id, ""),
                         "Source": c.source})
        if not rows:
            st.caption("No case matches.")
        else:
            # The editor remembers edits by ROW POSITION. Its key names the exact rows shown, so when the rows
            # change (a relabelled case leaves a filtered view, cases are added or deleted) the old edits are
            # dropped instead of being applied to whichever case now sits in that position.
            shown_ids = hashlib.sha1("|".join(r["Id"] for r in rows).encode()).hexdigest()[:12]
            edited = st.data_editor(
                pd.DataFrame(rows), hide_index=True, width="stretch",
                key=f"cdb_editor_{st.session_state.get('case_db_version', 0)}_{shown_ids}",
                disabled=["Id", "Case", "Court", "Year", "As written", "Head sentence / orders", "Plea", "Check",
                          "Same decision as", "Source"],
                column_config={
                    "Use": st.column_config.CheckboxColumn("Use"),
                    "Principal offence": st.column_config.SelectboxColumn("Principal offence", options=options,
                                                                          required=True),
                    "Its sentence (months)": st.column_config.NumberColumn(min_value=0, max_value=1200),
                    "Total term (months)": st.column_config.NumberColumn(min_value=0, max_value=1200),
                    "NPP (months)": st.column_config.NumberColumn(min_value=0, max_value=1200)})
            before = snapshot(shown)
            was_shown = {r["Id"]: r for r in rows}
            for row in edited.to_dict("records"):           # write the user's changes back to the database
                c, was = db.get(row["Id"]), was_shown.get(row["Id"])
                if c is None or was is None:
                    continue
                # Only the cells the user changed are applied (compared with the value shown): after a relabel the
                # row's other cells still show the old values, and must not overwrite the new ones.
                touched = False
                if bool(row["Use"]) != bool(was["Use"]):
                    c.use = bool(row["Use"])
                label = row["Principal offence"]
                if label != was["Principal offence"]:
                    new_id = ID_OF.get(label) if label in ID_OF else (label if label in unknown else None)
                    if new_id != c.principal_offence_id:
                        relabel_case(c, new_id)
                        touched = True
                sentence = num(row["Its sentence (months)"])
                if sentence != num(was["Its sentence (months)"]) and set_principal_sentence(c, sentence):
                    touched = True
                for col, attr in (("Total term (months)", "head_sentence_months"), ("NPP (months)", "npp_months")):
                    if num(row[col]) != num(was[col]):
                        setattr(c, attr, num(row[col]))
                        touched = True
                if touched:
                    c.edited = True
                    c.figures_note = check_figures(c)
            if snapshot(shown) != before:
                st.rerun()                                  # only after a real change: the counts above update
        unused = [c.id for c in db.cases if not c.use]
        if unused:
            st.button(f"Delete the {len(unused)} case(s) not in use", on_click=delete_unused)
        others = sum(1 for c in db.cases if not c.principal_offence_id or any(not o.get("offence_id")
                                                                                for o in c.offences))
        if others:
            st.button(f"Label the offences marked “Other offence” in {others} case(s) by their names (no AI)",
                      on_click=relabel_other,
                      help="An offence — the principal one or another offence charged — keeps “Other offence” unless "
                           "its name as written is exactly the name of one register offence (e.g. cases saved by "
                           "version 1.x, which labelled only chapter 25 offences). Check the labels afterwards.")

        st.markdown("**Read a case**")
        pick = st.selectbox("Case", [c.id for c in db.cases], key="cdb_pick",
                            format_func=lambda i: f"{i} — {db.get(i).citation}" if db.get(i) else i)
        c = db.get(pick) if pick else None
        if c:
            show_case(c)
            if c.figures_note:
                st.caption(f"Check: {c.figures_note}.")
            if c.offences:
                st.dataframe([{"Offence": o["name"], "Label": NAME_OF.get(o.get("offence_id"), OTHER),
                               "Sentence as written": o.get("sentence_text", ""), "Months": o.get("sentence_months")}
                              for o in c.offences], hide_index=True, width="stretch")
            if c.summary:
                st.caption(f"AI summary (for lists and the embedding only): {c.summary}")
