"""
app_pages/estimate.py — THE MAIN PAGE: ONE CASE, STEP BY STEP.

What the page shows, top to bottom:
  A. Header — scope, progress through the steps, the AI-call counter, "New case"
  B. Step 1 — the case, in one of two inputs (the switch at the top of Step 1):
       Simple   — describe the case in your own words, with a few optional answers;
                  an AI call sorts the description into the form's questions
                  (core/facts.py sort_description); you check what it found and
                  answer the required questions it leaves open; the rest stay "unknown"
       Detailed — the full form (load a test case, or answer every question and
                  type / upload the facts); the most accurate estimate
     Both fill the same form: the detailed form shows what the sorting found.
  C. Step 2 — confirm the facts the AI extracted (AI call 1)
  D. Step 3 — the legal framework, worked out in code, and the gates
  E. Steps 4–6 — one button runs the four analyses (AI calls 2–5), the proposed
     sentence (AI call 6), the hard checks (code) and the audit (AI call 7), and
     the revision loop if errors are found (AI calls 8–9)
  F. Results — analysis, proposed sentence, checks, report downloads, run log
     (the comparables tab also lists the cases taken from the user's case
     database — see the Case database page and core/cases.py)

State that must survive moving to another page and back:
  * the current run: st.session_state.run (core/pipeline.py Run) — every result
    is stored there, so moving around never repeats an AI call;
  * the form: Streamlit deletes the values of widgets that are not drawn (e.g.
    while another page is open). Two things keep them: streamlit_app.py
    re-assigns every "f_" key at the top of each run (Streamlit's documented
    workaround), and the values are also copied into st.session_state.form_shadow,
    a plain dictionary, which init_form() puts back into any widget key that is
    missing. The case for a run is always built from form_shadow.
  * the simple input: its own answers are widget keys "s_<name>" (kept across pages
    the same way); the sorting result is st.session_state.sorted, and the sorting
    and other AI calls not yet in a run are st.session_state.pending_calls (the
    next run's log takes them: the sorting whose answers it uses counts; the others
    are listed as not counted, with the reason).
"""

from __future__ import annotations

import io
import json
import math
import typing
from dataclasses import replace
from datetime import date

import pandas as pd
import streamlit as st

from app_pages.common import (BADGE, get_case_db, get_kb, get_retriever, has_key, make_llm, show_call_log,
                              show_case_citations, show_citations, show_comparables, show_results)
from core import config
from core.checks import months_text
from core.facts import (PLEA_TIMING, REQUIRED_FIELDS, SORT_STEP, CaseInput, melbourne_today, simple_case_values,
                        sort_description, validate)
from core.llm import LLMError
from core.pipeline import Run, run_remaining, step_confirm, step_extract
from core.report import (LABELS, build_blocks, describe_band, describe_sentence, minimum_text, protected_text,
                         reading_heading, render_docx, render_markdown)
from core.schemas import FactBasis, FactTopic
from core.stages import STAGE_ORDER

kb = get_kb()
retriever = get_retriever()
OFFENCE_LABELS = {oid: kb.offence_label(oid) for oid in config.IN_SCOPE_OFFENCES}
YNU = ["unknown", "yes", "no"]
FIELD_LABELS = {
    "victim_protected_worker": "victim was a protected official on duty",
    "offender_knew_or_reckless": "offender knew or was reckless as to that",
    "intoxicated_by_liquor": "intoxicated by liquor", "intoxication_contributed": "intoxication contributed",
    "mental_impairment_evidence": "psychiatrist's or psychologist's report on a mental impairment",
    "cco_consent": "consents to a CCO", "presentence_report": "pre-sentence report", "cooperation": "cooperation",
    "prior_convictions": "prior convictions", "arising_from_driving": "arose from driving",
}


# ─────────────────────────────────────────────────────────────────────────────
# Form state: widget key "f_<field>"; the lasting copy is form_shadow[<field>]
# ─────────────────────────────────────────────────────────────────────────────
# The questions the app never answers by default start empty (core/facts.py REQUIRED_FIELDS); the plea's timing
# starts "unknown".
EMPTY_AT_START = {"offence_id": None, "court": None, "offence_date": None, "plea": None, "plea_timing": "unknown",
                  "age_at_offence": None, "age_at_sentence": None, "age_at_sentence_assumed": False}


def form_defaults() -> dict:
    d = CaseInput().to_dict()
    d.update(EMPTY_AT_START)
    d.update(offence_date_end=None, has_period=False, psd_unknown=False, indication_max_months=0, input_mode="Simple",
             cooperation="unknown")
    return d


def write_form(values: dict) -> None:
    """Put case values into the form: the lasting copy and the widgets. Call it from a callback or before
    the widgets are drawn (Streamlit does not let a drawn widget's value be set)."""
    form = dict(values)
    if "offence_date_end" in form:
        form["has_period"] = form["offence_date_end"] is not None
    if "presentence_detention_days" in form:
        form["psd_unknown"] = form["presentence_detention_days"] is None
        if form["psd_unknown"]:
            form["presentence_detention_days"] = 0
    if "indication_max_months" in form:
        form["indication_max_months"] = form["indication_max_months"] or 0
    st.session_state.form_shadow.update(form)
    for key, value in form.items():
        st.session_state[f"f_{key}"] = value


def init_form():
    """Put the lasting form values back into any widget whose value Streamlit
    has dropped (runs on every page load)."""
    shadow = st.session_state.setdefault("form_shadow", form_defaults())
    for name, value in form_defaults().items():          # keys added in a later version of the app
        shadow.setdefault(name, value)
    pending = st.session_state.pop("pending_form", None)     # the sorting's answers, written before any widget
    if pending:
        write_form(pending)
    for name, value in shadow.items():
        if f"f_{name}" not in st.session_state:
            st.session_state[f"f_{name}"] = value
    for name, value in SIMPLE_START.items():
        st.session_state.setdefault(name, value)
    st.session_state.setdefault("user_set", set())


def save_form():
    """Copy the widgets' current values into the lasting copy."""
    shadow = st.session_state["form_shadow"]
    for name in list(shadow):
        if f"f_{name}" in st.session_state:
            shadow[name] = st.session_state[f"f_{name}"]


def reset_simple(note: str) -> None:
    """A new case for the simple input: its answers back to "Detect", no sorting, no answers of the user's. A
    sorting call not yet in a run's log stays pending, with `note` saying why it was not used."""
    ss = st.session_state
    for name, value in SIMPLE_START.items():
        ss[name] = value
    ss.user_set = set()
    for rec in pending_calls():
        if rec.ok and rec.step == SORT_STEP and rec is not None:
            rec.note = note
    ss.sorted, ss.sorted_record, ss.sort_counted_by = None, None, None
    ss.pop("pending_form", None)


def load_test_case():
    """Callback for the test-case picker: copies the case into the form (and opens the detailed input). A test
    case is a new case: the simple input starts again."""
    name = st.session_state.get("test_case_choice")
    if not name or name == "—":
        return
    data = json.loads((config.TEST_CASES_DIR / name).read_text(encoding="utf-8"))
    form = dict(form_defaults(), **data["form"])
    for key in ("offence_date", "offence_date_end"):
        if isinstance(form.get(key), str):
            form[key] = date.fromisoformat(form[key])
    if data.get("narrative_file"):
        form["narrative"] = (config.TEST_CASES_DIR / data["narrative_file"]).read_text(encoding="utf-8")
    form["input_mode"] = "Detailed"                # a test case answers every question
    write_form(form)
    reset_simple("not used: a test case was loaded")
    st.session_state.loaded_case = data


def read_upload():
    """Callback: put the uploaded file's text into the narrative box."""
    up = st.session_state.get("f_upload")
    if up is None:
        return
    raw, name = up.getvalue(), up.name.lower()
    try:
        if name.endswith(".docx"):
            from docx import Document
            text = "\n".join(p.text for p in Document(io.BytesIO(raw)).paragraphs)
        elif name.endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
        else:
            text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        st.session_state.upload_error = f"Could not read {up.name}: {type(exc).__name__}"
        return
    st.session_state.f_narrative = text.strip()
    st.session_state.form_shadow["narrative"] = text.strip()
    st.session_state.upload_error = ""


def case_from_form() -> CaseInput:
    f = st.session_state.form_shadow
    data = {name: f.get(name) for name in CaseInput().to_dict()}
    data["offence_date_end"] = f["offence_date_end"] if f.get("has_period") else None
    data["presentence_detention_days"] = None if f.get("psd_unknown") else int(f.get("presentence_detention_days") or 0)
    data["indication_max_months"] = int(f["indication_max_months"]) if f.get("indication_max_months") else None
    return CaseInput(**data)


def ynu(label: str, field: str, help_text: str | None = None):
    st.selectbox(label, YNU, key=f"f_{field}", format_func=str.capitalize, help=help_text, on_change=answered,
                 args=(field,))


def clean_cell(value):
    """Empty cells in the facts table arrive as None or NaN."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return value


def new_case():
    """Callback: start a new case. The run, the form, the description, the simple input's answers and sorting,
    and the cases left out of the comparisons are cleared (the choice of input is kept)."""
    ss = st.session_state
    old = ss.pop("run", None)
    if old is not None and old.extraction is None:         # a failed attempt: its calls go to the next run's log
        pending_calls().extend(replace(c, note=c.note or "an attempt that failed")
                               for c in old.calls + old.discarded_calls if c.step != SORT_STEP)
    ss.pop("last_error", None)
    mode = ss.form_shadow.get("input_mode", "Simple")
    ss.form_shadow = form_defaults()
    ss.form_shadow["input_mode"] = mode
    write_form(ss.form_shadow)
    reset_simple("not used: made for a case left with “New case”")
    ss.loaded_case, ss.test_case_choice, ss.upload_error = None, "—", ""
    ss.cdb_exclude = []


def llm_for(run: Run):
    """The AI client for this run: the models chosen when the run started."""
    return make_llm(run.models, run.efforts)


# ─────────────────────────────────────────────────────────────────────────────
# Simple input: a plain description, a few optional answers, and an AI call that
# sorts the description into the form's questions (core/facts.py section 4).
# Whose answer is it? user_set holds the questions the user answered (in either
# input, or by ticking an answer that counts against the offender): those are
# never replaced when the description is sorted again.
# ─────────────────────────────────────────────────────────────────────────────
DETECT = "detect"
DETECT_LABEL = "Detect from the description"
SIMPLE_PLEAS = {                     # one choice gives the plea and, for a guilty plea, when it was entered
    DETECT: DETECT_LABEL,
    "earliest_opportunity": "Pleaded guilty at the earliest opportunity",
    "after_committal": "Pleaded guilty after some delay",
    "late": "Pleaded guilty late (at or near trial)",
    "unknown": "Pleaded guilty (when is not known)",
    "not_guilty": "Found guilty at trial",
}
SIMPLE_PROTECTED = {DETECT: DETECT_LABEL, "no": "No", "yes": "Yes"}
SIMPLE_KEYS = {"offence_id": "s_offence", "court": "s_court", "plea": "s_plea", "age_at_offence": "s_age",
               "offence_date": "s_date", "victim_protected_worker": "s_protected"}   # form field -> simple widget
SIMPLE_START = {"s_offence": DETECT, "s_court": DETECT, "s_plea": DETECT, "s_age": None, "s_date": None,
                "s_protected": DETECT}
OPEN = {"offence_id": None, "court": None, "offence_date": None, "age_at_offence": None,
        "victim_protected_worker": "unknown"}           # an answer left on "Detect" that the description does not give
EXAMPLE_FILE = config.TEST_CASES_DIR / "simple_example.txt"   # an invented example description
QUESTION = {                         # the questions the sorting can answer, in the order they are shown
    "case_name": "Case name", "offence_id": "Charge", "court": "Court", "offence_date": "Date of the offence",
    "offence_date_end": "End of the offence period", "plea": "Plea", "plea_timing": "When the plea was entered",
    "age_at_offence": "Age at the offence", "age_at_sentence": "Age at sentence",
    "prior_convictions": "Prior convictions", "serving_sentence_or_on_parole": "Serving a sentence or on parole",
    "victim_protected_worker": "Victim an emergency worker, custodial officer or youth justice custodial worker on duty",
    "offender_knew_or_reckless": "Offender knew, or was reckless, that the victim was such a worker",
    "accessory_only": "Found guilty only as an accessory",
    "intoxicated_by_liquor": "Intoxicated by liquor", "intoxication_contributed": "Intoxication significantly contributed",
    "previous_aeo": "A previous alcohol exclusion order",
    "mental_impairment_evidence": "Psychiatrist's or psychologist's report on a mental impairment",
    "cooperation": "Cooperation with the authorities", "presentence_detention_days": "Pre-sentence detention",
    "presentence_report": "Pre-sentence report", "cco_consent": "Consents to a CCO",
    "arising_from_driving": "Arose from driving",
}


def show_value(field: str, value) -> str:
    """A form value in words."""
    if value is None:
        return "unknown"
    if field == "offence_id":
        return OFFENCE_LABELS.get(value, value)
    if field == "plea":
        return "Pleaded guilty" if value == "guilty" else "Found guilty at trial"
    if field == "plea_timing":
        return PLEA_TIMING.get(value, value)
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if field == "presentence_detention_days":
        return f"{value} days"
    if field in ("case_name", "court") or not isinstance(value, str):
        return str(value)
    return value.replace("_", " ").capitalize()                  # a coded answer, e.g. "admissions_only"


def user_set() -> set:
    return st.session_state.setdefault("user_set", set())


def pending_calls() -> list:
    """AI calls made since the last run and not in any run's log yet (sortings; a failed attempt left with
    "New case"): the next run's log lists them."""
    return st.session_state.setdefault("pending_calls", [])


def simple_choices() -> dict:
    """The user's own answers (whichever input they were given in): kept when the description is sorted again. Two
    answers live in two widgets each: pre-sentence detention ("Unknown" ticked = none known) and the offence period
    ("Committed over a period" unticked = a single day)."""
    ss, form = st.session_state, st.session_state.form_shadow
    out = {}
    for f in user_set():
        value = ss.get(f"f_{f}", form.get(f))
        if f == "presentence_detention_days" and ss.get("f_psd_unknown", form.get("psd_unknown")):
            value = None
        if f == "offence_date_end" and not ss.get("f_has_period", form.get("has_period")):
            value = None
        out[f] = value
    return out


def required_missing() -> list[str]:
    """The required questions the form has no answer to yet (the description did not give one, nor the user)."""
    form = st.session_state.form_shadow
    return [label for f, label in REQUIRED_FIELDS.items() if form.get(f) in (None, "")]


def sync_simple(field: str):
    """The simple input shows the user's answer to one of its questions (given in the detailed form, or by ticking)."""
    ss = st.session_state
    if field in ("plea", "plea_timing"):
        plea, timing = ss.get("f_plea"), ss.get("f_plea_timing")
        ss.s_plea = (DETECT if plea is None else "not_guilty" if plea == "not_guilty"
                     else timing if timing in SIMPLE_PLEAS and timing != DETECT else "unknown")
    elif field in SIMPLE_KEYS:
        value = ss.get(f"f_{field}")
        if field == "victim_protected_worker":
            value = value if value in ("yes", "no") else DETECT
        elif field in ("offence_id", "court") and value is None:
            value = DETECT
        ss[SIMPLE_KEYS[field]] = value


def answered(field: str):
    """Callback of the detailed form: the user answered this question. Their answer is kept if the description is
    sorted again, and the simple input shows it."""
    ss = st.session_state
    user_set().add(field)
    if field in ("plea", "plea_timing"):
        user_set().update({"plea", "plea_timing"})
    if field == "plea":                                    # the timing must fit the plea
        if ss.get("f_plea") == "not_guilty":
            write_form({"plea_timing": "not_applicable"})
        elif ss.get("f_plea_timing") == "not_applicable":
            write_form({"plea_timing": "unknown"})
    if field == "age_at_sentence":
        write_form({"age_at_sentence_assumed": False})
    elif field == "age_at_offence" and "age_at_sentence" not in user_set() and (
            ss.form_shadow.get("age_at_sentence_assumed")
            or (ss.get("sorted") is not None and ss.form_shadow.get("age_at_sentence") is None)):
        age = ss.get("f_age_at_offence")                   # a sorted case: the age at sentence follows, assumed
        write_form({"age_at_sentence": age, "age_at_sentence_assumed": age is not None})
    sync_simple(field)


def apply_simple(field: str):
    """Callback of one of the simple input's answers: put it in the form. "Detect" puts back what the sorting
    found, or leaves the question open."""
    ss = st.session_state
    srt = ss.get("sorted")
    found = srt.values if srt else {}
    raw = ss.get(SIMPLE_KEYS[field])
    if field == "plea":
        if raw == DETECT:
            user_set().difference_update({"plea", "plea_timing"})
            plea = found.get("plea")
            values = {"plea": plea, "plea_timing": found.get("plea_timing", "unknown")}
        else:
            user_set().update({"plea", "plea_timing"})
            values = ({"plea": "not_guilty", "plea_timing": "not_applicable"} if raw == "not_guilty"
                      else {"plea": "guilty", "plea_timing": raw})
    elif raw is None or raw == DETECT:
        user_set().discard(field)
        values = {field: found.get(field, OPEN[field])}
    else:
        user_set().add(field)
        values = {field: raw}
    if field == "age_at_offence" and "age_at_sentence" not in user_set() and "age_at_sentence" not in found:
        values["age_at_sentence"] = values["age_at_offence"]               # assumed, and marked so
        values["age_at_sentence_assumed"] = values["age_at_offence"] is not None
    if field == "offence_date":
        start, end = values["offence_date"], ss.form_shadow.get("offence_date_end")
        if start and end and end <= start:
            values["offence_date_end"] = None                              # a period cannot end before it starts
    write_form(values)


def apply_adverse(field: str):
    """Callback of a tick box for an answer that counts against the offender: ticked, the answer is used (the
    user's own); unticked, the question is open again."""
    ss = st.session_state
    srt = ss.get("sorted")
    if srt is None or field not in srt.adverse:
        return
    if ss.get(f"sadv_{field}"):
        user_set().add(field)
        write_form({field: srt.adverse[field]})
    else:
        user_set().discard(field)
        write_form({field: "unknown"})
    sync_simple(field)


def use_example():
    """Callback: put the invented example description in the box."""
    text = EXAMPLE_FILE.read_text(encoding="utf-8").strip()
    st.session_state.f_narrative = text
    st.session_state.form_shadow["narrative"] = text


def sort_now(description: str) -> None:
    """The simple input's AI call. Its answers are written into the form on the next run (before any widget):
    every question the user has not answered is set from the description, or left open."""
    ss = st.session_state
    with st.spinner("Sorting the description (AI)…"):
        try:
            result, record = sort_description(make_llm(), description, kb)
        except LLMError as exc:
            ss.last_error = f"The description could not be sorted: {exc}"
            if exc.record is not None:
                pending_calls().append(exc.record)             # listed (as failed) in the next run's log
            return
    pending_calls().append(record)
    ss.sorted, ss.sorted_record, ss.sort_counted_by = result, record, None
    ss.pending_form = simple_case_values(result.values, simple_choices())
    ss.last_error = ""


def attach_sort_calls(run: Run) -> None:
    """The sorting calls in the run's log. The sorting whose answers the form holds counts in the first run that
    uses them and is listed as reused in a later run of the same case; earlier or failed sortings are listed as
    discarded (not counted)."""
    ss = st.session_state
    current, counted_by = ss.get("sorted_record"), ss.get("sort_counted_by")
    run.simple = ss.get("sorted") is not None
    for rec in pending_calls():
        if rec is current and counted_by is None:
            run.calls.append(rec)
        elif rec.step == SORT_STEP and rec.ok and not rec.note:
            run.discarded_calls.append(replace(rec, note="replaced by a later sorting of the description"))
        else:
            run.discarded_calls.append(rec)                 # with its reason (or its error)
    if current is not None and counted_by is not None:
        run.reused_calls.append(replace(current, note=f"made for an earlier run of this case (run {counted_by})"))


def show_sorted(srt, stale: bool) -> None:
    """What the sorting found, what the form now holds and from whom, and what is still open."""
    if stale:
        st.warning("The description has changed since it was sorted: sort it again before extracting the facts "
                   "(the answers below are from the earlier text; your own answers are kept).")
    case, mine = case_from_form(), user_set()
    rows = []
    for f, question in QUESTION.items():
        value = getattr(case, f, None)
        ai = srt.values.get(f, srt.adverse.get(f))
        if f in mine:
            source = "you"
        elif f in srt.values and value == srt.values[f]:
            source = "the description"
        elif f == "age_at_sentence" and case.age_at_sentence_assumed:
            source = "assumed: the age at the offence (not stated)"
        else:
            continue
        rows.append({"Question": question, "Answer used": show_value(f, value), "From": source,
                     "Words in the description": srt.quotes.get(f, "") if ai is not None and value == ai else ""})
    st.markdown("**What the AI found, and the answers used**")
    if rows:
        st.table(pd.DataFrame(rows).set_index("Question"))
    else:
        st.caption("Nothing yet — the description answers none of the questions.")
    if srt.adverse:
        st.markdown("**Answers that count against the offender.** None is used unless you tick it — tick one only "
                    "if it is proved (admitted, or found by the court); otherwise it stays unknown.")
        form = st.session_state.form_shadow
        for f, value in srt.adverse.items():
            key = f"sadv_{f}"
            st.session_state[key] = form.get(f) == value          # the box shows what the form holds
            st.checkbox(f"{QUESTION.get(f, f)}: **{show_value(f, value)}** — “{srt.context.get(f) or srt.quotes.get(f, '')}”",
                        key=key, on_change=apply_adverse, args=(f,),
                        help="The whole sentence the AI relied on: check that it states the fact as established.")
    if srt.unverified:
        st.caption("Not used: " + "; ".join(f"{QUESTION.get(f, f)} — {show_value(f, v)} ({why})"
                                             for f, (v, why) in srt.unverified.items())
                   + ". Answer them yourself in the detailed input if you know them.")
    for note in srt.problems:
        st.warning(note)
    for note in srt.outside:
        st.error(f"Possibly outside version 1: {note}")
    missing = required_missing()
    if missing:
        st.warning("Still needed: " + ", ".join(missing) + ". The description does not give "
                   + ("it" if len(missing) == 1 else "them") + " — answer under “A few answers” above"
                   + (" (the age at sentence in the detailed input)" if "the offender's age at sentence" in missing
                      else "") + ".")
        if case.offence_id is None and srt.offence_as_described:
            st.info(f"The description describes the offence as “{srt.offence_as_described}”. "
                    "The app does not choose the charge for you.")
        if case.offence_date is None and srt.offence_year:
            st.info(f"The description gives the year of the offence ({srt.offence_year}) but not the day.")
    if srt.not_stated:
        st.markdown("**Not stated in the description** (treated as unknown; answer them in the detailed input "
                    "for a more accurate estimate):\n" + "\n".join(f"- {q}" for q in srt.not_stated))
    st.caption("Every other question stays unknown unless you answer it in the detailed input, where these answers "
               "already are. Step 2 may suggest answers for unknown questions; none is used unless you tick it.")


# ─────────────────────────────────────────────────────────────────────────────
# A. Header
# ─────────────────────────────────────────────────────────────────────────────
init_form()
run: Run | None = st.session_state.get("run")

st.title("Estimate a sentence")
st.caption(f"Version 1 covers one charge of a causing-injury offence (Crimes Act 1958 ss 15A, 15B, 16, 17, 18) "
           f"for an adult offender sentenced in the County Court or Supreme Court. Every legal proposition comes "
           f"from the {config.MANUAL_NAME} (version of {config.MANUAL_VERSION_DATE}). The result is an indicative "
           f"estimate, not a prediction and not legal advice.")

steps = ["Case", "Facts", "Framework", "Analysis", "Proposal", "Audit", "Report"]
done = 0
if run:
    done = 1 + bool(run.extraction) + bool(run.framework) + bool(run.stages_done) + \
           bool(run.current and run.current.proposal.proposal) + bool(run.current and run.current.audit and run.current.audit.result)
    done += bool(run.finished)
cols = st.columns([5, 1.3, 1])
cols[0].markdown("Progress: " + " → ".join(f"✅ **{s}**" if k < done else f"⬜ {s}" for k, s in enumerate(steps)))
cols[1].metric("AI calls this run", run.ai_calls if run else 0,
               help=f"The assignment requires at least {config.MIN_AI_CALLS_PER_RUN} per run. Each run makes 7 "
                    "(9 if the proposal is revised); the simple input adds one before them, which sorts the "
                    "description. Embeddings are not counted.")
cols[2].button("New case", on_click=new_case, help="Clear the case and this run, and start a new case.")

if not has_key():
    st.warning("Enter your OpenAI API key in the sidebar (then press “Update API Key”) to run the AI steps.")
if retriever.stale_index:
    st.warning("The search index does not match the knowledge base; keyword ranking is used until it is rebuilt "
               "(Knowledge base page).")
elif not retriever.uses_vectors:
    st.info("No search index has been built, so knowledge is ranked by keywords rather than cosine similarity. "
            "Build the index on the Knowledge base page (or run scripts/build_index.py).")
if st.session_state.get("last_error"):
    st.error(st.session_state.last_error)
for notice in (run.messages if run else []):          # e.g. search fell back to keyword ranking
    st.warning(notice)

# ─────────────────────────────────────────────────────────────────────────────
# B. Step 1 — the case: the simple input or the detailed form (the same form underneath)
# ─────────────────────────────────────────────────────────────────────────────
def simple_input() -> None:
    """Simple: a description in the user's own words, a few optional answers, and the sorting call."""
    st.caption("Describe the case in your own words — what happened, the charge, the court, the plea, the "
               "offender's age and anything else you know. One AI call sorts the description into the app's "
               "questions, using only what the description states; you check what it found and answer what it "
               "leaves open. Questions it does not answer stay unknown, so the estimate is less precise than with "
               "the detailed input.")
    st.text_input("Case name (optional, for the report)", key="f_case_name", on_change=answered, args=("case_name",))
    st.text_area("Describe the case", key="f_narrative", height=220,
                 placeholder="For example: the offender, 31, pleaded guilty in the County Court to recklessly "
                             "causing serious injury. On 14 March 2024 he punched a stranger outside a hotel…")
    c1, c2, c3 = st.columns([3, 1, 1])
    c1.file_uploader("…or upload a case summary (.txt, .md, .docx, .pdf)", type=["txt", "md", "docx", "pdf"],
                     key="f_upload")
    c2.button("Use the file's text", on_click=read_upload, disabled=st.session_state.get("f_upload") is None)
    c3.button("Use an example", on_click=use_example, help="Puts an invented example description in the box.")
    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)

    st.markdown("##### A few answers (optional)")
    st.caption("Leave a question on “Detect” and the AI looks for the answer in the description. An answer you "
               "give here is used instead, and kept if you sort the description again.")
    c1, c2, c3 = st.columns([3, 2, 3])
    c1.selectbox("Charge", [DETECT] + config.IN_SCOPE_OFFENCES, key="s_offence", on_change=apply_simple,
                 args=("offence_id",), format_func=lambda x: DETECT_LABEL if x == DETECT else OFFENCE_LABELS[x])
    c2.selectbox("Court", [DETECT] + config.IN_SCOPE_COURTS, key="s_court", on_change=apply_simple, args=("court",),
                 format_func=lambda x: DETECT_LABEL if x == DETECT else x)
    c3.selectbox("Plea", list(SIMPLE_PLEAS), key="s_plea", on_change=apply_simple, args=("plea",),
                 format_func=SIMPLE_PLEAS.get)
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.number_input("Age at the offence", min_value=10, max_value=100, step=1, value=None, key="s_age",
                    placeholder="Detect", on_change=apply_simple, args=("age_at_offence",))
    c2.date_input("Date of the offence", value=None, key="s_date", min_value=date(1990, 1, 1),
                  max_value=melbourne_today(), format="DD/MM/YYYY", on_change=apply_simple, args=("offence_date",))
    c3.selectbox("Was the victim an emergency worker, custodial officer or youth justice custodial worker on duty?",
                 list(SIMPLE_PROTECTED), key="s_protected", on_change=apply_simple, args=("victim_protected_worker",),
                 format_func=SIMPLE_PROTECTED.get,
                 help="Protected officials (VSM 9.1.2.3): if so, a category and a minimum non-parole period may "
                      "apply. Answer “No” if you know the victim was not one; “Yes” only if it is proved.")

    description = (st.session_state.get("f_narrative") or "").strip()
    srt = st.session_state.get("sorted")
    stale = srt is not None and srt.description.strip() != description
    fresh = srt is None or stale
    too_short = len(description) < 80
    if st.button("Sort my description (AI) →" if fresh else "Sort it again (AI)",
                 type="primary" if fresh else "secondary", disabled=not has_key() or too_short,
                 help="Sorting sets every question you have not answered yourself from the description (or leaves "
                      "it open); your own answers are kept."):
        sort_now(description)
        st.rerun()
    if too_short:
        st.caption("Write a few sentences (at least 80 characters) to sort.")
    elif fresh and st.session_state.get("loaded_case"):
        st.caption("Sorting replaces the loaded test case's answers with what the description states: questions it "
                   "leaves open become unknown or empty, and answers that count against the offender wait for your "
                   "tick.")
    if srt is not None:
        show_sorted(srt, stale)


def detailed_input() -> None:
    """Detailed: every question of the form, and the facts. The required questions start empty."""
    st.caption("Answer every question and add the facts — the most accurate estimate.")
    form = st.session_state.form_shadow
    if st.session_state.get("sorted") is not None:
        st.info("Answers marked in the simple input as found in the description are filled in here; questions it "
                "left open are “Unknown” (or empty). Change any of them — your answers are kept if you sort the "
                "description again."
                + (" The description has changed since it was sorted: check the answers."
                   if st.session_state.sorted.description.strip() != (form.get("narrative") or "").strip() else ""))
    st.markdown("##### The charge")
    c1, c2 = st.columns([3, 2])
    c1.selectbox("Charge (one per run in version 1)", config.IN_SCOPE_OFFENCES, key="f_offence_id", index=None,
                 placeholder="Choose the charge", format_func=OFFENCE_LABELS.get, on_change=answered,
                 args=("offence_id",))
    c2.radio("Court", config.IN_SCOPE_COURTS, key="f_court", index=None, horizontal=True, on_change=answered,
             args=("court",))
    c1, c2, c3 = st.columns(3)
    c1.date_input("Date of the offence", value=None, key="f_offence_date", min_value=date(1990, 1, 1),
                  max_value=melbourne_today(), format="DD/MM/YYYY", on_change=answered, args=("offence_date",))
    c2.checkbox("Committed over a period", key="f_has_period", on_change=answered, args=("offence_date_end",))
    if st.session_state.get("f_has_period"):
        if st.session_state.get("f_offence_date_end") is None:
            st.session_state.f_offence_date_end = st.session_state.get("f_offence_date")
        c3.date_input("End of the period", value=None, key="f_offence_date_end", min_value=date(1990, 1, 1),
                      max_value=melbourne_today(), format="DD/MM/YYYY", on_change=answered, args=("offence_date_end",))
    c1, c2, c3 = st.columns(3)
    c1.radio("Plea", ["guilty", "not_guilty"], key="f_plea", index=None, horizontal=True, on_change=answered,
             args=("plea",), format_func=lambda x: "Guilty" if x == "guilty" else "Found guilty at trial")
    if st.session_state.get("f_plea") == "guilty":
        c2.selectbox("When was the plea entered?", list(PLEA_TIMING), key="f_plea_timing", format_func=PLEA_TIMING.get,
                     on_change=answered, args=("plea_timing",))
    c3.radio("You are acting for", ["defence", "prosecution", "other"], key="f_user_role", horizontal=True,
             format_func=str.capitalize, on_change=answered, args=("user_role",))

    st.markdown("##### The offender and the victim")
    c1, c2, c3, c4 = st.columns(4)
    c1.number_input("Age at the offence", min_value=10, max_value=100, step=1, value=None, key="f_age_at_offence",
                    placeholder="Required", on_change=answered, args=("age_at_offence",))
    c2.number_input("Age at sentence", min_value=10, max_value=110, step=1, value=None, key="f_age_at_sentence",
                    placeholder="Required", on_change=answered, args=("age_at_sentence",))
    if form.get("age_at_sentence_assumed") and form.get("age_at_sentence") is not None:
        c2.caption("Assumed: not stated in the description, so taken as the age at the offence. Change it if you "
                   "know it.")
    c3.selectbox("Prior convictions", ["unknown", "none", "some", "relevant_or_significant"], key="f_prior_convictions",
                 format_func=lambda x: x.replace("_", " ").capitalize(), on_change=answered,
                 args=("prior_convictions",))
    with c4:
        ynu("Serving a sentence or on parole?", "serving_sentence_or_on_parole")
    c1, c2, c3 = st.columns(3)
    with c1:
        ynu("Victim an emergency worker, custodial officer or youth justice custodial worker on duty?",
            "victim_protected_worker", "Protected officials (VSM 9.1.2.3). Must be proved beyond reasonable doubt.")
    with c2:
        ynu("Did the offender know, or was the offender reckless, that the victim was such a worker on duty?",
            "offender_knew_or_reckless")
    with c3:
        ynu("Found guilty only as an accessory (directing, assisting or encouraging)?", "accessory_only")
    if st.session_state.get("f_victim_protected_worker") != "no" or st.session_state.get("f_accessory_only") == "yes":
        c1, c2, c3 = st.columns(3)
        if st.session_state.get("f_victim_protected_worker") != "no":
            with c1:
                ynu("Do you confirm the protected-official scheme (Category 1 and the minimum non-parole period) "
                    "applied at the offence date?", "protected_scheme_confirmed",
                    "The Manual gives no commencement date for these provisions (flag.protected_official_minimum_dates), "
                    "so the app asks. Until confirmed, breaches that depend on them are shown as unresolved.")
        if st.session_state.get("f_accessory_only") == "yes":
            with c3:
                ynu("Has the offender proved (on the balance of probabilities) that their involvement was minor?",
                    "minor_involvement_proved")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ynu("Intoxicated by liquor at the offence?", "intoxicated_by_liquor")
    with c2:
        ynu("Did the intoxication significantly contribute?", "intoxication_contributed")
    with c3:
        ynu("A previous alcohol exclusion order for this offence?", "previous_aeo")
    with c4:
        ynu("A psychiatrist's or psychologist's report on a mental impairment?", "mental_impairment_evidence")
    st.selectbox("Cooperation with the authorities", ["none", "admissions_only", "past_assistance", "undertaking", "unknown"],
                 key="f_cooperation", format_func=lambda x: x.replace("_", " ").capitalize(), on_change=answered,
                 args=("cooperation",))

    st.markdown("##### The sentencing hearing")
    c1, c2, c3, c4 = st.columns(4)
    c1.number_input("Pre-sentence detention (days)", min_value=0, max_value=20000, step=1,
                    key="f_presentence_detention_days", disabled=bool(st.session_state.get("f_psd_unknown")),
                    on_change=answered, args=("presentence_detention_days",))
    c1.checkbox("Unknown", key="f_psd_unknown", on_change=answered, args=("presentence_detention_days",))
    c2.selectbox("Pre-sentence report", ["unknown", "available", "ordered", "none"], key="f_presentence_report",
                 format_func=str.capitalize, on_change=answered, args=("presentence_report",))
    with c3:
        ynu("Offender consents to a CCO?", "cco_consent")
    c4.checkbox("A sentence indication was given", key="f_indication_given", on_change=answered,
                args=("indication_given",))
    if st.session_state.get("f_indication_given"):
        c1, c2, c3, c4 = st.columns(4)
        c1.selectbox("Indicated type", ["imprisonment", "imprisonment_and_cco", "cco", "fine", "adjourned_undertaking"],
                     key="f_indication_type", format_func=lambda x: LABELS.get(x, x), on_change=answered,
                     args=("indication_type",))
        c2.number_input("Indicated maximum (months)", min_value=0, max_value=600, step=1, key="f_indication_max_months",
                        on_change=answered, args=("indication_max_months",))
        with c3:
            ynu("Plea entered on it?", "indication_plea_entered_on_it")
        with c4:
            ynu("Same judge sentencing?", "indication_same_judge")
    with st.expander("Driving (only if the offence arose from driving a motor vehicle)"):
        c1, c2, c3 = st.columns(3)
        with c1:
            ynu("Arose from driving?", "arising_from_driving")
        with c2:
            ynu("Under the influence of alcohol (or alcohol and a drug)?", "alcohol_influenced_driving")
        c3.selectbox("Licence held", ["unknown", "victorian", "other_jurisdiction", "none"], key="f_licence_status",
                     format_func=lambda x: x.replace("_", " ").capitalize(), on_change=answered,
                     args=("licence_status",))

    st.markdown("##### What happened")
    st.text_input("Case name (optional, for the report)", key="f_case_name", on_change=answered, args=("case_name",))
    st.text_area("The facts of the offence and what is known about the offender (the AI extracts the facts from this "
                 "text; say which facts are agreed, admitted or disputed)", key="f_narrative", height=260)
    c1, c2 = st.columns([3, 1])
    c1.file_uploader("…or upload a case summary (.txt, .md, .docx, .pdf)", type=["txt", "md", "docx", "pdf"],
                     key="f_upload")
    c2.button("Use the file's text", on_click=read_upload, disabled=st.session_state.get("f_upload") is None)
    if st.session_state.get("upload_error"):
        st.error(st.session_state.upload_error)


with st.expander("**Step 1 — The case**", expanded=run is None):
    st.segmented_control("How do you want to enter the case?", ["Simple", "Detailed"], key="f_input_mode",
                         required=True,
                         format_func={"Simple": "Simple — describe it", "Detailed": "Detailed — answer every question"}.get,
                         help="Simple: describe the case in your own words; an AI call sorts the description into the "
                              "questions and you answer the few it leaves open. Detailed: answer every question "
                              "yourself (the most accurate estimate). Both fill the same form, so you can start "
                              "simple and refine the answers in the detailed form.")
    simple = st.session_state.get("f_input_mode") != "Detailed"
    cases = sorted(p.name for p in config.TEST_CASES_DIR.glob("*.json"))
    st.selectbox("Load a test case (optional" + ("; it opens the detailed input)" if simple else ")"), ["—"] + cases,
                 key="test_case_choice", on_change=load_test_case,
                 format_func=lambda n: n if n == "—" else json.loads((config.TEST_CASES_DIR / n).read_text())["title"])
    if st.session_state.get("loaded_case"):
        st.caption(f"Loaded: {st.session_state.loaded_case['title']}. What it shows: "
                   f"{st.session_state.loaded_case.get('shows', '')}")
    if simple:
        simple_input()
    else:
        detailed_input()
    save_form()

    # Extract: never with a required question unanswered (either input); with the simple input, only once the
    # description as it now reads has been sorted.
    blocked = ""
    srt = st.session_state.get("sorted")
    if simple and srt is None:
        blocked = "Sort the description first."
    elif simple and srt.description.strip() != (st.session_state.form_shadow.get("narrative") or "").strip():
        blocked = "Sort the description again first: it has changed."
    elif required_missing():
        blocked = "Still needed before AI call 1: " + ", ".join(required_missing()) + "."
    if st.button("Extract the facts (AI call 1) →", type="primary", disabled=not has_key() or bool(blocked)):
        case = case_from_form()
        problems = validate(case, kb)
        if problems:
            for p in problems:
                st.error(p)
        else:
            st.session_state.last_error = ""
            previous = st.session_state.get("run")
            run = Run(case=case, models=dict(st.session_state.models), efforts=dict(st.session_state.efforts))
            attach_sort_calls(run)                     # simple input: the sorting call is this run's first call
            if previous is not None and previous.extraction is None:     # an earlier attempt that failed
                run.discarded_calls += [replace(c, note=c.note or "an earlier attempt that failed")
                                        for c in previous.calls + previous.discarded_calls if c.step != SORT_STEP]
            st.session_state.run = run
            with st.spinner("AI call 1 — structuring the facts…"):
                try:
                    step_extract(run, llm_for(run), kb)
                    if st.session_state.get("sorted_record") is not None and \
                            st.session_state.get("sort_counted_by") is None:
                        st.session_state.sort_counted_by = run.run_id
                    st.session_state.pending_calls = []   # now in this run's log
                except LLMError as exc:
                    st.session_state.last_error = str(exc)
            st.rerun()
    if blocked:
        st.caption(blocked)
    if pending_calls():
        st.caption(f"{len(pending_calls())} AI call(s) made since the last run (" + ", ".join(
                   dict.fromkeys(c.step.lower() for c in pending_calls())) + "): they will be listed in the next "
                   "run's log.")

# ─────────────────────────────────────────────────────────────────────────────
# C. Step 2 — confirm the facts
# ─────────────────────────────────────────────────────────────────────────────
if run and run.extraction:
    # The run uses the case as it was when the facts were extracted (Run.entered). Say so if Step 1 changed since.
    entered = run.entered.to_dict()
    changed = [k for k, v in case_from_form().to_dict().items() if v != entered.get(k)]
    if changed:
        st.warning("Step 1 was changed after the facts were extracted (" + ", ".join(c.replace("_", " ") for c in changed)
                   + "). This run keeps using the case as it was then, even if you confirm the facts again: press "
                     "“Extract the facts” in Step 1 to start a new run with the changes.")

if run and run.extraction:
    ex = run.extraction
    with st.expander("**Step 2 — Confirm the facts** (from AI call 1)", expanded=run.framework is None):
        st.markdown(ex.summary)
        for w in ex.scope_warnings:
            st.error(f"Possible scope problem: {w}")
        st.info(f"Fit with the charge: {ex.charge_fit}")
        for c in ex.conflicts_with_form:
            st.warning(f"The narrative and the form differ: {c}")
        accepted = {}
        if run.suggestions:
            if run.simple:
                st.markdown("**Answers the facts extraction suggests for questions left “unknown”.** None is ticked: "
                            "tick one only if the material supports it — an answer that counts against the offender "
                            "only if it is proved.")
            else:
                st.markdown("**Answers the narrative gives for fields left as “unknown”.** Tick to use one. Answers "
                            "that count against the offender start unticked: use them only if they are proved.")
            for field, value in run.suggestions.items():
                key = f"sugg_{run.run_id}_{field}"
                if key not in st.session_state:
                    st.session_state[key] = run.suggestion_choices.get(field, False)
                st.checkbox(f"{FIELD_LABELS.get(field, field)}: **{value}**", key=key)
                run.suggestion_choices[field] = st.session_state[key]
                if st.session_state[key]:
                    accepted[field] = value
        st.markdown("**The facts** — edit the text, the bearing or the basis; delete rows that are wrong. "
                    "Adverse facts need proof beyond reasonable doubt; mitigating facts need evidence. "
                    "Confirm before leaving this page: unconfirmed edits are not kept.")
        base_rows = run.facts.facts if run.facts else run.fact_rows
        table = pd.DataFrame([dict(r, quote_found={True: "found", False: "NOT FOUND", None: "—"}[r.get("quote_verified")])
                              for r in base_rows]).drop(columns=["quote_verified"], errors="ignore")
        editor = st.data_editor(
            table, key=f"facts_{run.run_id}_{run.facts_version}", num_rows="dynamic", hide_index=True,
            column_order=["id", "text", "bearing", "basis", "quote_found", "topic", "source_quote"],
            width="stretch",
            column_config={
                "id": st.column_config.TextColumn("Id", width="small"),
                "text": st.column_config.TextColumn("Fact", width="large", required=True),
                "topic": st.column_config.SelectboxColumn("Topic", options=list(typing.get_args(FactTopic))),
                "bearing": st.column_config.SelectboxColumn("Bearing", options=["aggravating", "mitigating", "neutral", "unclear"]),
                "basis": st.column_config.SelectboxColumn("Basis", options=list(typing.get_args(FactBasis))),
                "source_quote": st.column_config.TextColumn("Words in the narrative"),
                "quote_found": st.column_config.TextColumn("Quote check", disabled=True, width="small"),
            })
        if ex.missing:
            st.markdown("**Not in the material (the estimate assumes):**")
            for m in ex.missing:
                st.markdown(f"- {m.question} — *assumed:* {m.assumption}")
        discard_ok = True
        if run.stages:
            st.warning("Confirming again discards the analysis, the proposal and the report of this run; running them "
                       "again costs 6–8 more AI calls.")
            discard_ok = st.checkbox("Discard them and work from the edited facts", key=f"discard_{run.run_id}")
        if st.button("Confirm the facts → work out the legal framework", type="primary", disabled=not discard_ok):
            rows, used = [], set()
            for k, raw in enumerate(editor.to_dict("records")):
                r = {key: clean_cell(v) for key, v in raw.items()}
                if not str(r.get("text") or "").strip():
                    continue
                fid = str(r.get("id") or "").strip()
                if not fid or fid in used:
                    fid = f"F{len(base_rows) + k + 1}"
                used.add(fid)
                rows.append({"id": fid, "text": str(r["text"]).strip(), "topic": r.get("topic") or "other",
                             "bearing": r.get("bearing") or "unclear", "basis": r.get("basis") or "assumption",
                             "source_quote": r.get("source_quote") or "", "quote_verified": None})
            step_confirm(run, kb, rows, accepted)          # rebuilt from the case as entered + the ticks now
            st.session_state.last_error = ""
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# D. Step 3 — the legal framework (code)
# ─────────────────────────────────────────────────────────────────────────────
if run and run.framework:
    fw = run.framework
    with st.expander("**Step 3 — The legal framework** (worked out in code from the Manual's offence register)",
                     expanded=not run.stages_done):
        rows = [("Charge", fw.offence_label), ("Offence date", fw.offence_date),
                ("Maximum", f"{fw.maximum.text} — {months_text(fw.maximum.months)} ({fw.maximum.period})"
                            + (f" — UNRESOLVED: {fw.maximum.unresolved}" if fw.maximum.unresolved else "")),
                ("Maximum fine", f"{fw.maximum.fine_penalty_units} penalty units" if fw.maximum.fine_penalty_units else "—"),
                ("Category", f"Category {fw.category}" if fw.category else "None on these facts"),
                ("Protected official", protected_text(fw)),
                ("Mandatory minimum NPP", minimum_text(fw)),
                ("Dispositions", fw.dispositions_note)]
        if fw.pu_value:
            rows.append(("Penalty unit value", fw.pu_value))
        rows += [(reading_heading(a), f"{a.title}: {a.summary()}.") for a in fw.alternatives]   # one row per reading
        st.table(pd.DataFrame(rows, columns=["", "Result"]).set_index(""))
        st.markdown("**Gates and cautions**")
        show_results(kb, fw.gates)
        show_citations(kb, fw.rule_items, label="Manual text for the framework")

    if not run.finished:
        case_db = get_case_db()
        if not run.stages:
            st.caption((f"Case database: {case_db.describe()}. The most similar cases go to the current-practice "
                        "analysis, the proposal and the audit as comparable cases (yardsticks, not law)."
                        if case_db.usable() else "Case database: empty — the analysis uses the Manual only. ")
                       + " Manage it on the Case database page.")
            if case_db.usable():
                # The app leaves out a case that looks like the one being estimated; the user can name others.
                # The choice is kept across pages (streamlit_app.py); ids no longer in the database are dropped.
                ids = [c.id for c in case_db.usable()]
                if "cdb_exclude" in st.session_state:
                    st.session_state.cdb_exclude = [i for i in st.session_state.cdb_exclude if i in ids]
                st.multiselect("Leave these cases out of the comparisons (for example the case you are estimating, "
                               "if it is in the database). The same decision listed again is left out with it.",
                               ids, key="cdb_exclude",
                               format_func=lambda i: f"{i} — {case_db.get(i).citation}" if case_db.get(i) else i)
        label = ("Run the analysis, propose a sentence, check and audit it (AI calls 2–7) →" if not run.stages
                 else "Continue (resume the remaining steps) →")
        if st.button(label, type="primary", disabled=not has_key()):
            with st.status("Working — this usually takes a few minutes…", expanded=True) as status:
                try:
                    run_remaining(run, llm_for(run), kb, retriever, progress=status.write,
                                  parallel=st.session_state.get("parallel", config.PARALLEL_STAGES),
                                  case_db=case_db, exclude_case_ids=st.session_state.get("cdb_exclude", []))
                    status.update(label="Finished", state="complete")
                    st.session_state.last_error = ""
                except LLMError as exc:
                    status.update(label="Stopped", state="error")
                    st.session_state.last_error = f"{exc} — the steps already done are kept; press Continue to retry."
            st.rerun()

# ─────────────────────────────────────────────────────────────────────────────
# F. Results
# ─────────────────────────────────────────────────────────────────────────────
if run and run.stages:
    with st.expander("**Step 4 — The analysis** (AI calls 2–5)", expanded=False):
        tabs = st.tabs([run.stages[k].title if k in run.stages else k for k in STAGE_ORDER])
        for tab, key in zip(tabs, STAGE_ORDER):
            st_res = run.stages.get(key)
            with tab:
                if not st_res or not st_res.analysis:
                    st.warning(st_res.error if st_res else "Not run yet.")
                    continue
                for pt in st_res.analysis.points:
                    st.markdown(f"**{pt.heading}** — *{pt.effect}, {pt.weight}*"
                                + ("" if pt.proof in ("established", "not_applicable") else f" — ⚠️ {pt.proof.replace('_', ' ')}")
                                + (f" · facts {', '.join(pt.fact_ids)}" if pt.fact_ids else ""))
                    st.markdown(pt.analysis)
                    show_citations(kb, pt.item_ids, pt.passage_ids)
                    show_case_citations(pt.case_ids, run.comparables)
                st.markdown(f"**Conclusion.** {st_res.analysis.conclusion}")
                if st_res.analysis.gaps:
                    st.markdown("**Gaps:** " + "; ".join(st_res.analysis.gaps))
                if st_res.passage_ids:
                    st.caption("Most similar Manual passages (" + retriever.description + "): "
                               + ", ".join(f"{c} ({s})" for c, s in st_res.passage_scores.items()))
                if key == "comparables":
                    show_comparables(run.comparables)
                for w in st_res.proof_warnings:
                    st.warning(w)
                if st_res.invalid:
                    st.caption("Citations removed because the model was not shown them: " + ", ".join(st_res.invalid))

a = run.current if run else None
if a and a.proposal.proposal:
    p = a.proposal.proposal
    with st.container(border=True):
        st.subheader("Step 5 — The proposed sentence (AI call 6" + (", revised" if len(run.attempts) > 1 else "") + ")")
        if run.revision_error:
            st.warning(f"The revision failed ({run.revision_error}); the first proposal and its checks are shown.")
        st.markdown(f"#### Indicative band\n{describe_band(p)}")
        for u in p.band.main_uncertainties:
            st.markdown(f"- {u}")
        st.markdown(f"#### Formulated sentence (used for the checks)\n{describe_sentence(p)}")
        if p.s6aaa.stated:
            n = p.s6aaa
            st.markdown(f"**Section 6AAA** — but for the plea: {LABELS.get(n.type or '', n.type)}"
                        + (f", {months_text(n.term_months)}" if n.term_months else "")
                        + (f", non-parole period {months_text(n.npp_months)}" if n.npp_months else "")
                        + (f", CCO {months_text(n.cco_months)}" if n.cco_months else "")
                        + (f", {n.fine_penalty_units} penalty units" if n.fine_penalty_units else ""))
        if p.special_reason.found:
            st.markdown(f"**Special reason** ({(p.special_reason.limb or '').replace('_', ' ')}): {p.special_reason.evidential_basis}")
        if p.category2_exception.relied_on:
            st.markdown(f"**Category 2 exception** ({(p.category2_exception.exception or '').replace('_', ' ')}): "
                        f"{p.category2_exception.evidential_basis}")
        for o in p.ancillary_orders:
            st.markdown(f"- **{o.type.replace('_', ' ').capitalize()}** ({o.status}){', ' + months_text(o.months) if o.months else ''}: {o.details}")
        st.markdown("#### Reasons")
        for r in p.reasons:
            st.markdown(f"**{r.heading}.** {r.text}" + (f" *(facts {', '.join(r.fact_ids)})*" if r.fact_ids else ""))
            show_citations(kb, r.item_ids, r.passage_ids)
            show_case_citations(r.case_ids, run.comparables)
        if p.assumptions or p.unresolved:
            st.markdown("#### Assumptions and unresolved points")
            for x in p.assumptions:
                st.markdown(f"- Assumption: {x}")
            for x in p.unresolved:
                st.markdown(f"- Unresolved: {x}")
        if p.revision_notes:
            st.markdown("#### Changes made on revision")
            for x in p.revision_notes:
                st.markdown(f"- {x}")
        if a.proposal.invalid:
            st.caption("Citations removed because the model was not shown them: " + ", ".join(a.proposal.invalid))

    with st.container(border=True):
        st.subheader("Step 6 — Checks")
        st.markdown("**Hard checks in code** (the rules the Manual makes mandatory)")
        show_results(kb, a.checks)
        if a.audit and a.audit.result:
            st.markdown(f"**Audit of the reasoning** (AI call {'9' if len(run.attempts) > 1 else '7'}; standards from the "
                        "Manual the code cannot test)")
            st.markdown(a.audit.result.overall)
            findings = a.audit.result.findings
            for f in sorted([f for f in findings if f.outcome in ("error", "caution")],
                            key=lambda f: f.outcome != "error"):
                st.markdown(f"**{BADGE.get(f.outcome, f.outcome)} — {kb.title(f.check_id)}** (`{f.check_id}`). "
                            f"{f.explanation}" + (f" *Fix:* {f.fix}" if f.fix else ""))
                show_citations(kb, [f.check_id], label="The standard in the Manual")
            met = [f for f in findings if f.outcome == "pass"]
            if met:
                with st.expander(f"Standards met ({len(met)})"):
                    for f in met:
                        st.markdown(f"**{BADGE['pass']} — {kb.title(f.check_id)}.** {f.explanation}")
            na = [f.check_id for f in findings if f.outcome == "not_applicable"]
            if na:
                st.caption("Not applicable on these facts: " + ", ".join(kb.title(i) for i in na))
            if a.audit.not_assessed:
                st.caption("The audit did not report on: " + ", ".join(a.audit.not_assessed))
        if len(run.attempts) > 1:
            with st.expander("The first proposal and what the checks found"):
                first = run.attempts[0]
                st.markdown(describe_sentence(first.proposal.proposal))
                show_results(kb, first.checks, hide_pass=True)
                if first.audit and first.audit.result:
                    for f in first.audit.result.findings:
                        if f.outcome in ("error", "caution"):
                            st.markdown(f"- {BADGE[f.outcome]} {kb.title(f.check_id)}: {f.explanation}")

if run and run.finished:
    with st.container(border=True):
        st.subheader("Step 7 — Report")
        if run.report_cache.get("attempt") != len(run.attempts):      # build once per proposal, not on every rerun
            blocks = build_blocks(run, kb)
            cache = {"attempt": len(run.attempts), "md": render_markdown(blocks), "docx": None, "error": ""}
            try:
                cache["docx"] = render_docx(blocks)
            except Exception as exc:  # noqa: BLE001
                cache["error"] = f"The Word file could not be made ({type(exc).__name__}); the Markdown file is complete."
            run.report_cache = cache
        cache = run.report_cache
        name = (run.case.case_name or "sentencing-estimate").replace(" ", "_")[:40]
        c1, c2 = st.columns(2)
        if cache["docx"]:
            c1.download_button("Download the report (Word)", cache["docx"], file_name=f"{name}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        else:
            c1.warning(cache["error"])
        c2.download_button("Download the report (Markdown)", cache["md"], file_name=f"{name}.md", mime="text/markdown")
        with st.expander("Read the full report here"):
            st.markdown(cache["md"])

if run and run.calls:
    with st.expander(f"Run log — {run.ai_calls} AI calls in this run (minimum {config.MIN_AI_CALLS_PER_RUN})"):
        show_call_log(run)
        st.caption(f"Retrieval: {run.retrieval_mode or retriever.description}. Models chosen for this run: "
                   + ", ".join(f"{k} = {v}" for k, v in run.models.items())
                   + ". Changes in the sidebar apply to the next case.")
