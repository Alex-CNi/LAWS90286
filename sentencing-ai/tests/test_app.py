"""Streamlit AppTest: the pages load, the credentials sidebar works and masks
the key, and a whole run can be clicked through (with the fake OpenAI service)."""

from datetime import date
from pathlib import Path

import openai
import pytest
from streamlit.testing.v1 import AppTest

import core.llm
from tests.fake_openai import FakeOpenAI

KEY = "sk-test-key-0123456789"
APP = str(Path(__file__).resolve().parent.parent / "streamlit_app.py")


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeOpenAI(bad_first_proposal=True)
    real = openai.OpenAI

    def patched(**kwargs):
        kwargs["http_client"] = fake.client()
        return real(**kwargs)
    monkeypatch.setattr(core.llm, "OpenAI", patched)
    return fake


def new_app():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, at.exception
    return at


def test_sidebar_is_the_course_module_and_masks_the_key():
    at = new_app()
    box = at.sidebar.text_input[0]
    assert box.label == "Enter your API key:"
    assert at.sidebar.button[0].label == "Update API Key"
    box.input(KEY).run()
    at.sidebar.button[0].click().run()
    assert at.session_state.openai_api_key == KEY
    shown = " ".join(c.value for c in at.sidebar.caption) + " ".join(m.value for m in at.markdown)
    assert KEY not in shown and "…" in shown


def test_other_pages_load():
    at = new_app()
    at.switch_page("app_pages/knowledge_base.py").run()
    assert not at.exception, at.exception
    at.text_input[0].input("glassing").run()
    assert not at.exception, at.exception
    at.switch_page("app_pages/about.py").run()
    assert not at.exception, at.exception


def test_click_through_a_whole_run(fake):
    at = new_app()
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc1_glassing_rcsi.json").run()
    assert "Moretti" in at.session_state.f_narrative
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    assert not at.exception, at.exception
    assert at.session_state.run.extraction is not None
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    assert at.session_state.run.framework is not None
    next(b for b in at.button if b.label.startswith("Run the analysis")).click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.finished and run.ai_calls == 9
    assert any(m.label == "AI calls this run" and str(m.value) == "9" for m in at.metric)
    assert any("Step 7 — Report" in s.value for s in at.subheader)
    card = next(h.proto.body for h in at.get("html") if 'class="vse-result"' in h.proto.body)   # the result card
    assert 'class="vr-sentence">' in card and "No case database" in card and "VSM 2.1" in card


def test_leaving_the_page_and_coming_back_keeps_the_run_and_the_form(fake):
    at = new_app()
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc2_paramedic_icsi.json").run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Run the analysis")).click().run()
    assert at.session_state.run.finished
    at.switch_page("app_pages/about.py").run()
    assert not at.exception, at.exception
    at.switch_page("app_pages/estimate.py").run()
    assert not at.exception, at.exception
    assert any("Step 7 — Report" in s.value for s in at.subheader)
    assert "Halloran" in at.session_state.f_narrative
    assert at.session_state.f_offence_id == "crimes-act-s-16-intentionally-causing-serious-injury"
    at.run()                                   # and again, a plain rerun
    assert not at.exception, at.exception


def test_fields_shown_only_conditionally_survive_a_page_switch():
    at = new_app()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_victim_protected_worker").set_value("yes").run()
    at.selectbox(key="f_protected_scheme_confirmed").set_value("yes").run()
    at.selectbox(key="f_accessory_only").set_value("yes").run()
    at.selectbox(key="f_minor_involvement_proved").set_value("yes").run()
    at.checkbox(key="f_has_period").check().run()
    at.date_input(key="f_offence_date_end").set_value(date(2024, 3, 1)).run()
    at.checkbox(key="f_indication_given").check().run()
    at.selectbox(key="f_indication_type").set_value("cco").run()
    at.number_input(key="f_indication_max_months").set_value(24).run()
    at.switch_page("app_pages/about.py").run()
    at.switch_page("app_pages/estimate.py").run()
    assert not at.exception, at.exception
    ss = at.session_state
    assert ss.f_input_mode == "Detailed"
    assert ss.f_protected_scheme_confirmed == "yes" and ss.f_minor_involvement_proved == "yes"
    assert ss.f_offence_date_end == date(2024, 3, 1)
    assert ss.f_indication_type == "cco" and ss.f_indication_max_months == 24
    assert ss.form_shadow["protected_scheme_confirmed"] == "yes"


def test_test_case_2_after_a_page_switch_keeps_the_confirmed_scheme(fake):
    at = new_app()
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc2_paramedic_icsi.json").run()
    at.switch_page("app_pages/knowledge_base.py").run()
    at.switch_page("app_pages/estimate.py").run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Run the analysis")).click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.case.protected_scheme_confirmed == "yes" and not run.framework.alternatives
    assert run.ai_calls == 9 and len(run.attempts) == 2


def test_changing_step_1_after_extraction_says_to_extract_again(fake):
    at = new_app()
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc2_paramedic_icsi.json").run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    assert not [w for w in at.warning if "Step 1 was changed" in w.value]
    at.selectbox(key="f_protected_scheme_confirmed").set_value("unknown").run()
    assert any("Step 1 was changed" in w.value and "Extract the facts" in w.value for w in at.warning)


def charge(oid):
    from core.kb import KnowledgeBase
    return KnowledgeBase().charge_label(oid)


def test_case_database_page_loads_empty_and_with_cases():
    from tests.test_cases_db import S17, small_db
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    assert not at.exception, at.exception
    assert any(m.label == "Cases" and str(m.value) == "0" for m in at.metric)
    at.session_state["case_db"] = small_db(with_vectors=False)      # AppTest cannot upload files
    at.run()
    assert not at.exception, at.exception
    assert any(m.label == "Cases" and str(m.value) == "7" for m in at.metric)
    assert any("have no embeddings" in w.value for w in at.warning)
    at.selectbox(key="cdb_show").set_value(charge(S17)).run()
    assert not at.exception, at.exception
    at.selectbox(key="cdb_pick").set_value("C2").run()
    assert not at.exception, at.exception
    assert any("bottle nightclub" in t.value for t in at.text)
    assert at.multiselect(key="cdb_chapters").value == [str(n) for n in range(21, 35)]    # every chapter by default
    # cases labelled "other" (e.g. saved by version 1.x) are relabelled by their names, with no AI
    db = at.session_state["case_db"]
    db.get("C5").principal_offence = "Burglary"
    at.run()
    next(b for b in at.button if b.label.startswith("Label the offences marked “Other offence” in 2")).click().run()
    assert not at.exception, at.exception
    assert at.session_state["case_db"].get("C5").principal_offence_id == "crimes-act-s-76-burglary"
    assert at.session_state["case_db"].get("C3").principal_offence_id is None                 # its name is no offence's
    assert any("Labelled offences in 1 case(s)" in s.value for s in at.success)


def _editor(at):
    return next(e for e in at._tree if type(e).__name__ == "Dataframe" and (e.key or "").startswith("cdb_editor_"))


def _edit(at, rows, timeout=20):
    """Send an edit of the case table, as the browser does (AppTest has no data editor control)."""
    import json
    ed = _editor(at)
    states = at._tree.get_widget_states()
    w = states.widgets.add()
    w.id = ed.proto.id
    w.string_value = json.dumps({"edited_rows": {str(k): v for k, v in rows.items()}, "added_rows": [],
                                 "deleted_rows": []})
    at._run(states, timeout=timeout)


def test_case_table_edits():
    from tests.test_cases_db import S16, S17, small_db
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    db = small_db(with_vectors=True)
    for c in db.cases:
        c.offences = [{"name": "o", "offence_id": c.principal_offence_id, "sentence_text": "3 years",
                       "sentence_months": 36.0}]
    at.session_state["case_db"] = db
    at.run()
    _edit(at, {0: {"Its sentence (months)": 0}})                  # R4 M4: entering 0 must not rerun forever
    assert not at.exception, at.exception
    assert at.session_state["case_db"].get("C1").principal_sentence_months == 0.0
    # in a filtered view, a relabelled case leaves the view and no other case is changed
    at.selectbox(key="cdb_show").set_value(charge(S17)).run()
    _edit(at, {0: {"Principal offence": charge(S16)}})
    assert not at.exception, at.exception
    after = at.session_state["case_db"]
    assert after.get("C1").principal_offence_id == S16 and after.get("C1").edited
    assert after.get("C2").principal_offence_id != S16 and not after.get("C2").edited
    at.run()
    assert after.get("C2").principal_offence_id != S16


def test_a_run_with_a_case_database_reports_the_comparable_cases(fake):
    from tests.test_cases_db import small_db
    at = new_app()
    at.session_state["case_db"] = small_db(with_vectors=True)
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc1_glassing_rcsi.json").run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert any("Case database: 7 cases" in c.value for c in at.caption)
    at.multiselect(key="cdb_exclude").set_value(["C2"]).run()
    next(b for b in at.button if b.label.startswith("Run the analysis")).click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.finished and run.comparables and run.comparables.selected
    assert sorted(e["id"] for e in run.comparables.left_out) == ["C2", "C6"]   # left out by you; "DPP v Moretti"
    md = run.report_cache["md"]
    assert "Comparable cases from your case database" in md
    assert "These cases against the estimate's range (a check, not a probability)" in md
    assert "**Similar cases.**" in md and "not the probability that a court will impose this sentence" in md
    card = next(h.proto.body for h in at.get("html") if 'class="vse-result"' in h.proto.body)
    assert "Similar cases within this range" in card and "most similar cases, one by one" in card


def test_the_offence_panel_opens_a_chapter_of_the_register():
    at = new_app()
    next(b for b in at.button if b.key == "area_27").click().run()
    assert not at.exception, at.exception
    assert at.session_state.kb_chapter == "27"
    assert any("Offence register — chapter 27" in s.value for s in at.subheader)


# ── the simple input ──
def button(at, start):
    return next(b for b in at.button if b.label.startswith(start))


def with_key():
    at = new_app()
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    return at


def sorted_example(at):
    button(at, "Use an example").click().run()
    button(at, "Sort my description").click().run()
    assert not at.exception, at.exception
    return at


def test_simple_input_sorts_the_description_and_runs(fake):
    at = with_key()
    assert at.session_state.f_input_mode == "Simple"                 # the simple input is the default
    assert button(at, "Extract the facts").disabled                    # nothing sorted yet
    sorted_example(at)
    form = at.session_state.form_shadow
    assert form["offence_id"] == "crimes-act-s-17-recklessly-causing-serious-injury"
    assert form["court"] == "County Court" and form["offence_date"] == date(2024, 3, 14)
    assert form["plea"] == "guilty" and form["plea_timing"] == "earliest_opportunity"
    assert form["age_at_offence"] == 31 and form["age_at_sentence"] == 31 and form["age_at_sentence_assumed"]
    assert form["presentence_detention_days"] == 40 and form["case_name"] == "DPP v Hartley"
    assert form["cco_consent"] == "unknown"                            # its words are not in the description
    assert form["victim_protected_worker"] == "unknown"                # not stated: unknown, not "no"
    # answers that count against the offender are offered, never applied by themselves (R5 M2)
    assert form["prior_convictions"] == "unknown" and form["intoxicated_by_liquor"] == "unknown"
    assert at.checkbox(key="sadv_prior_convictions").value is False
    at.checkbox(key="sadv_prior_convictions").check().run()
    assert at.session_state.form_shadow["prior_convictions"] == "some"
    assert any("Not used" in c.value and "Consents to a CCO" in c.value for c in at.caption)
    assert not button(at, "Extract the facts").disabled
    button(at, "Extract the facts").click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.simple and [c.step for c in run.calls] == ["Sorting the description", "Facts extraction"]
    assert run.case.prior_convictions == "some" and run.case.intoxicated_by_liquor == "unknown"
    # AI call 1's suggestions start unticked for a sorted case (R5 M1)
    sugg = [c for c in at.checkbox if (c.key or "").startswith("sugg_")]
    assert sugg and not any(c.value for c in sugg)
    button(at, "Confirm the facts").click().run()
    button(at, "Run the analysis").click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.finished and run.ai_calls == 10                         # 1 sorting + 7, + 2 for the revision
    assert any(m.label == "AI calls this run" and str(m.value) == "10" for m in at.metric)
    # the detailed input shows what the sorting found
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    assert not at.exception, at.exception
    assert at.selectbox(key="f_offence_id").value == "crimes-act-s-17-recklessly-causing-serious-injury"
    assert at.number_input(key="f_age_at_offence").value == 31
    assert any("Assumed: not stated" in c.value for c in at.caption)


SHORT = ("The offender pleaded guilty in the County Court to recklessly causing serious injury. He punched a man "
         "outside a bar and broke his nose; the man needed surgery.")


def test_simple_input_asks_for_what_the_description_leaves_open(fake):
    at = with_key()
    at.text_area(key="f_narrative").input(SHORT).run()
    button(at, "Sort my description").click().run()
    assert not at.exception, at.exception
    assert any("Still needed" in w.value and "the date of the offence" in w.value and "age at the offence" in w.value
               for w in at.warning)
    assert button(at, "Extract the facts").disabled
    # the detailed input does not get round it: its required questions are empty (R5 M3)
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    assert at.date_input(key="f_offence_date").value is None and at.number_input(key="f_age_at_offence").value is None
    assert button(at, "Extract the facts").disabled
    assert any("Still needed before AI call 1" in c.value for c in at.caption)
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    at.number_input(key="s_age").set_value(30).run()
    at.date_input(key="s_date").set_value(date(2024, 2, 2)).run()
    assert not [w for w in at.warning if "Still needed" in w.value]
    assert not button(at, "Extract the facts").disabled
    form = at.session_state.form_shadow
    assert form["age_at_offence"] == 30 and form["offence_date"] == date(2024, 2, 2)
    assert form["age_at_sentence"] == 30 and form["age_at_sentence_assumed"]       # R5 M5
    at.selectbox(key="s_court").set_value("Supreme Court").run()          # your answer is used instead
    assert at.session_state.form_shadow["court"] == "Supreme Court"
    at.selectbox(key="s_court").set_value("detect").run()                 # back to the description's
    assert at.session_state.form_shadow["court"] == "County Court"
    # answers given in the detailed input survive a new sorting (R5 M4)
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_presentence_report").set_value("available").run()
    at.selectbox(key="f_prior_convictions").set_value("relevant_or_significant").run()
    at.number_input(key="f_age_at_sentence").set_value(33).run()
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    at.text_area(key="f_narrative").input(SHORT + " He has no prior convictions.").run()
    assert any("changed since it was sorted" in w.value for w in at.warning)
    assert button(at, "Extract the facts").disabled
    button(at, "Sort my description").click().run()
    form = at.session_state.form_shadow
    assert form["presentence_report"] == "available" and form["prior_convictions"] == "relevant_or_significant"
    assert form["age_at_offence"] == 30 and form["age_at_sentence"] == 33 and not form["age_at_sentence_assumed"]
    button(at, "Extract the facts").click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert [c.step for c in run.calls] == ["Sorting the description", "Facts extraction"]
    assert [c.step for c in run.discarded_calls] == ["Sorting the description"]
    assert run.case.offence_date == date(2024, 2, 2) and run.case.age_at_sentence == 33
    # a second confirmation keeps the sorting call
    button(at, "Confirm the facts").click().run()
    assert any(c.step == "Sorting the description" for c in at.session_state.run.calls)


def test_every_sorting_call_is_in_a_runs_log(fake):
    at = sorted_example(with_key())
    button(at, "Extract the facts").click().run()
    first = at.session_state.run
    assert first.calls[0].step == "Sorting the description" and first.ai_calls == 2
    button(at, "Extract the facts").click().run()                         # a second run of the same case (R5 M7)
    second = at.session_state.run
    assert [c.step for c in second.reused_calls] == ["Sorting the description"] and second.ai_calls == 1
    # a sorting made after a run is shown as pending, then listed in the next run's log
    at.text_area(key="f_narrative").input(at.session_state.f_narrative + " He is remorseful.").run()
    button(at, "Sort my description").click().run()
    assert any("will be listed in the next run's log" in c.value for c in at.caption)
    button(at, "New case").click().run()                                  # the case is left: the call stays pending
    assert any("will be listed in the next run's log" in c.value for c in at.caption)
    sorted_example(at)
    button(at, "Extract the facts").click().run()
    third = at.session_state.run
    assert [c.step for c in third.calls] == ["Sorting the description", "Facts extraction"]
    assert [c.note for c in third.discarded_calls] == ["not used: made for a case left with “New case”"]


def test_new_case_and_test_cases_start_the_simple_input_again(fake):
    from tests.test_cases_db import small_db
    at = new_app()
    at.session_state["case_db"] = small_db(with_vectors=True)
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.text_area(key="f_narrative").input(SHORT).run()
    at.number_input(key="s_age").set_value(30).run()
    at.date_input(key="s_date").set_value(date(2024, 2, 2)).run()
    button(at, "Sort my description").click().run()
    at.session_state["cdb_exclude"] = ["C2"]
    button(at, "New case").click().run()                                  # R5 m2, m7
    ss = at.session_state
    assert ss.s_age is None and ss.s_date is None and ss.sorted is None and not ss.user_set
    assert ss.f_narrative == "" and ss.f_case_name == "" and ss.form_shadow["offence_date"] is None
    assert ss.cdb_exclude == [] and ss.f_input_mode == "Simple"
    at.number_input(key="s_age").set_value(44).run()
    at.selectbox(key="test_case_choice").set_value("tc1_glassing_rcsi.json").run()
    ss = at.session_state
    assert ss.f_input_mode == "Detailed" and ss.s_age is None and not ss.user_set
    assert ss.form_shadow["age_at_offence"] == 24                        # the test case's own answer


def test_the_detailed_answers_show_in_the_simple_input_and_survive_pages(fake):
    at = with_key()
    at.selectbox(key="test_case_choice").set_value("tc2_paramedic_icsi.json").run()
    assert at.session_state.f_input_mode == "Detailed" and at.session_state.s_offence == "detect"
    at.radio(key="f_court").set_value("Supreme Court").run()
    assert at.session_state.s_court == "Supreme Court" and "court" in at.session_state.user_set
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    assert not at.exception, at.exception
    at.selectbox(key="s_plea").set_value("late").run()
    at.switch_page("app_pages/about.py").run()
    at.switch_page("app_pages/estimate.py").run()
    assert not at.exception, at.exception
    assert at.session_state.s_plea == "late" and at.session_state.form_shadow["plea_timing"] == "late"
    assert at.selectbox(key="s_court").value == "Supreme Court"


def test_a_fresh_detailed_form_asks_for_the_required_answers():
    at = new_app()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    assert at.selectbox(key="f_offence_id").value is None and at.radio(key="f_court").value is None
    assert at.radio(key="f_plea").value is None and at.number_input(key="f_age_at_sentence").value is None
    assert any("Still needed before AI call 1" in c.value and "the charge" in c.value for c in at.caption)


def test_answers_kept_in_two_widgets_survive_a_new_sorting(fake):
    at = sorted_example(with_key())                                      # the example gives 40 days in custody
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.checkbox(key="f_psd_unknown").check().run()                       # R5b MAJOR-1: "Unknown" is an answer
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    at.text_area(key="f_narrative").input(at.session_state.f_narrative + " He is remorseful.").run()
    button(at, "Sort my description").click().run()
    form = at.session_state.form_shadow
    assert form["psd_unknown"] and at.session_state.f_psd_unknown
    button(at, "Extract the facts").click().run()
    assert at.session_state.run.case.presentence_detention_days is None
    # a period the description gives, unticked by the user, stays a single day
    at = with_key()
    at.text_area(key="f_narrative").input(
        "Between 1 May 2024 and 3 May 2024 the offender, aged 30, punched the victim at a party. He pleaded guilty "
        "in the County Court to recklessly causing serious injury.").run()
    button(at, "Sort my description").click().run()
    assert at.session_state.form_shadow["has_period"]
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.checkbox(key="f_has_period").uncheck().run()
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    at.text_area(key="f_narrative").input(at.session_state.f_narrative + " He is sorry.").run()
    button(at, "Sort my description").click().run()
    assert not at.session_state.form_shadow["has_period"]
    assert at.session_state.form_shadow["offence_date"] == date(2024, 5, 1)


def test_failed_extractions_stay_in_the_log(monkeypatch):
    failing = FakeOpenAI(bad_first_proposal=False, extract_fail_first=2)
    real = openai.OpenAI

    def patched(**kwargs):
        kwargs["http_client"] = failing.client()
        return real(**kwargs)
    monkeypatch.setattr(core.llm, "OpenAI", patched)
    at = sorted_example(with_key())
    button(at, "Extract the facts").click().run()                         # fails
    button(at, "Extract the facts").click().run()                         # fails again
    button(at, "Extract the facts").click().run()                         # works
    run = at.session_state.run
    assert run.extraction is not None and run.calls[0].step == "Sorting the description"
    failed = [c for c in run.discarded_calls if c.step == "Facts extraction"]
    assert len(failed) == 2 and not any(c.ok for c in failed)            # R5b MINOR-1: both failures listed
    # a failed attempt left with "New case" is listed in the next run's log
    failing.extract_calls, failing.extract_fail_first = 0, 1
    button(at, "New case").click().run()
    at = sorted_example(at)
    button(at, "Extract the facts").click().run()                         # fails
    button(at, "New case").click().run()
    assert any("facts extraction" in c.value for c in at.caption)
    at = sorted_example(at)
    button(at, "Extract the facts").click().run()
    run = at.session_state.run
    assert any(c.step == "Facts extraction" and not c.ok for c in run.discarded_calls)


def test_the_age_and_the_plea_given_in_the_detailed_input(fake):
    at = with_key()
    at.text_area(key="f_narrative").input(SHORT).run()
    button(at, "Sort my description").click().run()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.number_input(key="f_age_at_offence").set_value(30).run()           # R5b MINOR-3: the age at sentence follows
    form = at.session_state.form_shadow
    assert form["age_at_sentence"] == 30 and form["age_at_sentence_assumed"]
    at.number_input(key="f_age_at_sentence").set_value(32).run()
    at.number_input(key="f_age_at_offence").set_value(31).run()
    assert at.session_state.form_shadow["age_at_sentence"] == 32          # the user's own answer stays
    at.radio(key="f_plea").set_value("not_guilty").run()                  # R5b NOTE-2: the timing fits the plea
    assert at.session_state.form_shadow["plea_timing"] == "not_applicable"
    at.radio(key="f_plea").set_value("guilty").run()
    assert at.session_state.form_shadow["plea_timing"] == "unknown"
    assert at.selectbox(key="f_cooperation").value in ("unknown", "none")


def test_a_fresh_detailed_form_does_not_answer_cooperation_against_the_offender():
    at = new_app()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    assert at.selectbox(key="f_cooperation").value == "unknown"           # R5b NOTE-5


# ── regressions from the second review of the case database (R4b) ──
def test_relabelling_in_the_table_keeps_each_offences_sentence():
    from core import cases as cs
    from tests.test_cases_db import S16, S17, record
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    db = cs.CaseDB()
    db.add([record(citation="R v Two [2021] VCC 1", label=S17, offences=[
               {"name": "ICSI", "offence_id": S16, "sentence_text": "", "sentence_months": 60.0},
               {"name": "RCSI", "offence_id": S17, "sentence_text": "", "sentence_months": 36.0}]),
            record(citation="R v Other [2021] VCC 2", label=None, offences=[
               {"name": "RCSI", "offence_id": S17, "sentence_text": "", "sentence_months": 36.0}])])
    at.session_state["case_db"] = db
    at.run()
    _edit(at, {0: {"Principal offence": charge(S16)}})
    assert not at.exception, at.exception
    c1 = at.session_state["case_db"].get("C1")
    assert [o["sentence_months"] for o in c1.offences] == [60.0, 36.0] and c1.principal_sentence_months == 60.0
    _edit(at, {1: {"Principal offence": charge(S17)}})
    c2 = at.session_state["case_db"].get("C2")
    assert c2.principal_offence_id == S17 and c2.principal_sentence_months == 36.0


def test_deleting_the_unused_cases_unticks_the_discard_box():
    from tests.test_cases_db import small_db
    at = new_app()
    at.switch_page("app_pages/cases.py").run()
    db = small_db(with_vectors=False)
    for c in db.cases:
        c.use = False
    at.session_state["case_db"] = db
    at.run()
    at.checkbox(key="cdb_discard_ok").check().run()
    button(at, "Delete the 7 case(s)").click().run()
    assert not at.exception, at.exception
    assert not at.session_state["case_db"].cases and at.session_state["cdb_discard_ok"] is False


def test_the_cases_left_out_survive_a_page_switch(fake):
    from tests.test_cases_db import small_db
    at = new_app()
    at.session_state["case_db"] = small_db(with_vectors=True)
    at.sidebar.text_input[0].input(KEY).run()
    at.sidebar.button[0].click().run()
    at.selectbox(key="test_case_choice").set_value("tc1_glassing_rcsi.json").run()
    button(at, "Extract the facts").click().run()
    button(at, "Confirm the facts").click().run()
    at.multiselect(key="cdb_exclude").set_value(["C2", "C3"]).run()
    at.switch_page("app_pages/cases.py").run()
    at.session_state["case_db"].remove({"C3"})                    # a case deleted meanwhile is dropped
    at.switch_page("app_pages/estimate.py").run()
    assert not at.exception, at.exception
    assert at.multiselect(key="cdb_exclude").value == ["C2"]


def test_any_register_offence_can_be_chosen_and_run(fake):
    """A charge outside chapter 25 (guide Part A): the box offers it, the chapter 25 questions are hidden, and a
    whole run completes with the unchecked parts of its scheme shown."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    murder = "common-law-common-law-murder"
    assert murder in at.selectbox(key="f_offence_id").options or any(
        "Murder" in o for o in at.selectbox(key="f_offence_id").options)
    at.selectbox(key="f_offence_id").set_value(murder).run()
    assert not at.exception, at.exception
    labels = " ".join(r.label for r in at.radio)
    assert "emergency worker" not in labels                                # ss 15A–18 questions hidden
    at.radio(key="f_court").set_value("Supreme Court").run()
    at.date_input(key="f_offence_date").set_value(date(2024, 5, 1)).run()
    at.radio(key="f_plea").set_value("guilty").run()
    at.number_input(key="f_age_at_offence").set_value(30).run()
    at.number_input(key="f_age_at_sentence").set_value(31).run()
    at.text_area(key="f_narrative").input("The offender stabbed the victim, who died. He pleaded guilty. " * 4).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    fw = at.session_state.run.framework
    assert fw.maximum.life and not any(g.title == "Not fully checked" for g in fw.gates)   # every scheme is checked
    next(b for b in at.button if b.label.startswith("Run the analysis")).click().run()
    assert not at.exception, at.exception
    assert at.session_state.run.finished


def test_historic_offending_takes_the_maximum_from_the_table(fake):
    """A sexual offence in 2010 (VSM 24.1.6.1, held as text): Step 1 shows the table and takes the maximum; until
    then Step 3 shows an error; once entered, the framework uses it (marked as the user's)."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value("crimes-act-ss-38-2-3-rape").run()
    at.radio(key="f_court").set_value("County Court").run()
    at.date_input(key="f_offence_date").set_value(date(2010, 6, 1)).run()
    assert not at.exception, at.exception
    assert any("Historic offending" in w.value for w in at.warning)
    box = at.number_input(key="f_historic_maximum_months")
    at.radio(key="f_plea").set_value("guilty").run()
    at.number_input(key="f_age_at_offence").set_value(30).run()
    at.number_input(key="f_age_at_sentence").set_value(45).run()
    at.text_area(key="f_narrative").input("The offender raped the complainant in 2010. He pleaded guilty. " * 4).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    fw = at.session_state.run.framework
    assert fw.maximum.unresolved and any(g.title == "Historic offending — confirm the maximum" for g in fw.gates)
    box.set_value(300).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    fw = at.session_state.run.framework
    assert fw.maximum.months == 300 and fw.maximum.confirmed_by_user


def test_simple_input_asks_about_protected_officials_only_for_injury_charges(fake):
    at = with_key()
    assert any(s.key == "s_protected" for s in at.selectbox)                   # "Detect": the charge may be one
    at.selectbox(key="s_offence").set_value("common-law-common-law-murder").run()
    assert not at.exception, at.exception
    assert not any(s.key == "s_protected" for s in at.selectbox)
    at.selectbox(key="s_offence").set_value("crimes-act-s-17-recklessly-causing-serious-injury").run()
    assert any(s.key == "s_protected" for s in at.selectbox)


def test_the_questions_for_a_charge_appear_and_decide_its_scheme(fake):
    """Guide Part B: the detailed form asks only the questions the charge's scheme turns on; the answers survive a
    page switch and reach the framework (the manslaughter minimum applies on the main reading)."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    assert not any(s.key == "f_manslaughter_circumstance" for s in at.selectbox)
    at.selectbox(key="f_offence_id").set_value("common-law-manslaughter").run()
    at.date_input(key="f_offence_date").set_value(date(2019, 5, 1)).run()
    assert not at.exception, at.exception
    assert any(m.value == "##### Questions for this charge" for m in at.markdown)
    at.selectbox(key="f_manslaughter_circumstance").set_value("single_punch").run()
    at.selectbox(key="f_prosecution_notice").set_value("yes").run()
    at.switch_page("app_pages/about.py").run()
    at.switch_page("app_pages/estimate.py").run()
    assert not at.exception, at.exception
    assert at.session_state.f_prosecution_notice == "yes"
    assert at.session_state.f_manslaughter_circumstance == "single_punch"
    at.radio(key="f_court").set_value("Supreme Court").run()
    at.radio(key="f_plea").set_value("guilty").run()
    at.number_input(key="f_age_at_offence").set_value(30).run()
    at.number_input(key="f_age_at_sentence").set_value(31).run()
    at.text_area(key="f_narrative").input("The offender punched the victim once outside a hotel; he died. " * 4).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    fw = at.session_state.run.framework
    assert fw.mm_group == "manslaughter" and fw.mnpp_months == 120
    trafficking = "drugs-poisons-and-controlled-substances-act-s-71ac-trafficking"
    at.selectbox(key="f_offence_id").set_value(trafficking).run()
    assert any(s.key == "f_conditional_condition_met" for s in at.selectbox)
    assert not any(s.key == "f_school_condition_confirmed" for s in at.selectbox)   # asked once within 500 m
    at.selectbox(key="f_conditional_condition_met").set_value("yes").run()
    assert any(s.key == "f_school_condition_confirmed" for s in at.selectbox)
    assert not any(s.key == "f_manslaughter_circumstance" for s in at.selectbox)


def test_an_attempt_asks_for_the_offence_attempted(fake):
    """Guide step 13: a maximum fixed by reference to another offence — the form asks for that offence (or the
    maximum, where the Manual cannot give it), and the framework works the maximum out."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    attempt = "crimes-act-s-321p-1-b-attempts-where-penalty-is-not-specified-by-reference-to-a-penalty-le"
    at.selectbox(key="f_offence_id").set_value(attempt).run()
    assert not at.exception, at.exception
    assert any(m.value == "##### The offence attempted" for m in at.markdown)
    at.selectbox(key="f_primary_offence_id").set_value("crimes-act-s-74-theft").run()
    at.radio(key="f_court").set_value("County Court").run()
    at.date_input(key="f_offence_date").set_value(date(2024, 5, 1)).run()
    at.radio(key="f_plea").set_value("guilty").run()
    at.number_input(key="f_age_at_offence").set_value(30).run()
    at.number_input(key="f_age_at_sentence").set_value(31).run()
    at.text_area(key="f_narrative").input("The offender tried to take a laptop from a shop but was stopped. " * 4).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    assert at.session_state.run.framework.maximum.months == 72
    table = "crimes-act-s-321p-1-a-attempts-where-a-penalty-is-specified-by-reference-to-a-penalty-leve"
    at.selectbox(key="f_offence_id").set_value(table).run()
    assert not at.exception, at.exception
    # s 321P(1)(a): the Manual cannot give the maximum (entered, and required), but the offence attempted can still be
    # named (review C3): theft carries over from the other attempt row, and so its motor-vehicle question is asked
    assert at.selectbox(key="f_primary_offence_id").value == "crimes-act-s-74-theft"
    assert any(n.key == "f_relative_maximum_months" for n in at.number_input)
    assert any(s.key == "f_motor_vehicle_stolen" for s in at.selectbox)
    shown = " ".join(x.value for x in list(at.warning) + list(at.error) + list(at.info) + list(at.caption))
    assert "the maximum you read from the legislation" in shown and button(at, "Extract the facts").disabled


def test_a_body_corporate_needs_no_age(fake):
    """Guide step 14: the offender type; a body corporate is not asked its age, and a run completes."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    ohs = "occupational-health-and-safety-act-2004-vic-21-1-failure-by-employers-to-provide-and-maint"
    at.selectbox(key="f_offence_id").set_value(ohs).run()
    at.radio(key="f_offender_type").set_value("body_corporate").run()
    assert not at.exception, at.exception
    assert not any(n.key == "f_age_at_offence" for n in at.number_input)
    at.radio(key="f_court").set_value("County Court").run()
    at.date_input(key="f_offence_date").set_value(date(2024, 5, 1)).run()
    at.radio(key="f_plea").set_value("guilty").run()
    at.text_area(key="f_narrative").input("The company failed to guard a press; a worker lost two fingers. It pleaded "
                                          "guilty at the first opportunity. " * 3).run()
    next(b for b in at.button if b.label.startswith("Extract the facts")).click().run()
    next(b for b in at.button if b.label.startswith("Confirm the facts")).click().run()
    assert not at.exception, at.exception
    fw = at.session_state.run.framework
    assert fw.body_corporate and fw.maximum.company_fine_units == 9000


# ── version 2.0: the new test cases load into the form and run (guide step 21) ──
def test_the_test_cases_are_offered_in_order():
    at = new_app()
    options = at.selectbox(key="test_case_choice").options
    numbers = [int(o.split("Test case ")[1].split(" ")[0]) for o in options if o.startswith("Test case ")]
    assert numbers == list(range(1, 14))


@pytest.mark.parametrize("name", ["tc11_ohs_company.json", "tc12_attempted_theft_of_firearm.json",
                                  "tc7_culpable_driving.json", "tc4_single_punch_manslaughter.json"])
def test_a_new_test_case_loads_and_runs_in_the_app(fake, name):
    import json as _json
    from core import config
    data = _json.loads((config.TEST_CASES_DIR / name).read_text(encoding="utf-8"))
    at = with_key()
    at.selectbox(key="test_case_choice").set_value(name).run()
    assert not at.exception, at.exception
    for field, value in data["form"].items():                          # every answer reached the form
        if field in ("offence_date", "offence_date_end") or f"f_{field}" not in at.session_state:
            continue
        assert at.session_state[f"f_{field}"] == value, field
    if data["form"].get("offender_type") == "body_corporate":
        assert not [n for n in at.number_input if n.key in ("f_age_at_offence", "f_age_at_sentence")]
    if data["form"].get("primary_offence_id"):
        assert at.selectbox(key="f_primary_offence_id").value == data["form"]["primary_offence_id"]
    button(at, "Extract the facts").click().run()
    assert not at.exception, at.exception
    button(at, "Confirm the facts").click().run()
    assert not at.exception, at.exception
    button(at, "Run the analysis").click().run()
    assert not at.exception, at.exception
    run = at.session_state.run
    assert run.finished and run.case.offence_id == data["form"]["offence_id"]
    import html as _html
    card = next(h.proto.body for h in at.get("html") if 'class="vse-result"' in h.proto.body)
    from core.kb import KnowledgeBase
    assert _html.escape(KnowledgeBase().offence(data["form"]["offence_id"])["offence"]) in card   # it names the charge


# ── regressions from the version 2.0 review (C1, C8) ──
def test_a_new_charge_starts_its_own_questions_again(fake):
    """Review C1: an answer to one charge's question never carries over to a different fact on another charge."""
    from core.kb import KnowledgeBase
    kb = KnowledgeBase()
    s71aa = next(o for o in kb.offences if kb.offence(o)["offence"] == "Trafficking commercial quantity")
    affray = next(o for o in kb.offences if kb.offence(o)["offence"] == "Affray")
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value(s71aa).run()
    at.date_input(key="f_offence_date").set_value(date(2024, 5, 1)).run()
    at.selectbox(key="f_conditional_condition_met").set_value("yes").run()
    assert at.session_state.form_shadow["conditional_condition_met"] == "yes"
    at.selectbox(key="f_offence_id").set_value(affray).run()
    assert not at.exception, at.exception
    assert at.selectbox(key="f_conditional_condition_met").value == "unknown"        # a different condition
    assert at.session_state.form_shadow["conditional_condition_met"] == "unknown"
    assert "conditional_condition_met" not in at.session_state.user_set


def test_a_body_corporate_form_asks_nothing_about_a_person(fake):
    """Review C8: no parole, accessory or driving questions for a body corporate; the higher company maximum is asked
    where the register prints none (review B11)."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value("crimes-act-s-74-theft").run()
    at.date_input(key="f_offence_date").set_value(date(2024, 5, 1)).run()    # the maximum in force decides (B11)
    at.radio(key="f_offender_type").set_value("body_corporate").run()
    assert not at.exception, at.exception
    keys = {w.key for w in list(at.selectbox) + list(at.number_input) + list(at.radio)}
    assert not {"f_serving_sentence_or_on_parole", "f_accessory_only", "f_licence_status",
                "f_arising_from_driving", "f_motor_vehicle_stolen"} & keys
    assert "f_company_maximum_units" in keys


def test_a_new_charge_drops_the_sortings_answer_to_the_old_question(fake):
    """Review C, second pass: the simple input does not offer the old charge's sorted answer under the new charge's
    question."""
    from core.facts import SortedInput
    from core.kb import KnowledgeBase
    kb = KnowledgeBase()
    s71ac = "drugs-poisons-and-controlled-substances-act-s-71ac-trafficking"
    affray = next(o for o in kb.offences if kb.offence(o)["offence"] == "Affray")
    at = with_key()
    at.selectbox(key="s_offence").set_value(s71ac).run()
    words = "sold drugs within 500 metres of a school"
    at.text_area(key="f_narrative").input(f"The offender {words} on 26 April 2023. " * 3).run()
    at.session_state["sorted"] = SortedInput(
        values={}, adverse={"conditional_condition_met": "yes"}, quotes={"conditional_condition_met": words},
        context={"conditional_condition_met": f"The offender {words} on 26 April 2023."}, unverified={}, problems=[],
        missing=[], outside=[], not_stated=[], offence_as_described="", offence_year=None,
        description=at.session_state["f_narrative"])
    at.run()
    assert any(c.key == "sadv_conditional_condition_met" for c in at.checkbox)
    at.selectbox(key="s_offence").set_value(affray).run()
    assert not at.exception, at.exception
    assert not any(c.key == "sadv_conditional_condition_met" for c in at.checkbox)


def test_a_new_historic_table_starts_its_maximum_again(fake):
    """Review C, second pass: a change of date into another historic table resets the maximum read from the old one."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value("crimes-act-ss-38-2-3-rape").run()
    at.date_input(key="f_offence_date").set_value(date(2010, 6, 1)).run()
    at.number_input(key="f_historic_maximum_months").set_value(300).run()
    at.number_input(key="f_historic_maximum_fine_units").set_value(3000).run()
    assert at.session_state.form_shadow["historic_maximum_months"] == 300
    at.date_input(key="f_offence_date").set_value(date(1975, 6, 1)).run()
    assert not at.exception, at.exception
    assert at.session_state.form_shadow["historic_maximum_months"] is None
    assert at.session_state.form_shadow["historic_maximum_fine_units"] is None


def test_a_new_sorting_that_moves_the_date_to_another_table_resets_its_maximum(fake):
    """Review C, final check: a new sorting of the description that moves the offence date into another historic
    table resets the maximum read from the old table (it was kept, and used as the new table's); a move within the
    same table keeps it."""
    at = with_key()
    text = ("The offender, aged 30, pleaded guilty in the County Court to rape. On 1 June 2010 he raped the "
            "complainant at his home, as he admitted.")
    at.text_area(key="f_narrative").input(text).run()
    at.selectbox(key="s_offence").set_value("crimes-act-ss-38-2-3-rape").run()
    button(at, "Sort my description").click().run()
    assert at.session_state.form_shadow["offence_date"] == date(2010, 6, 1)          # VSM 24.1.6.1
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.number_input(key="f_historic_maximum_months").set_value(300).run()
    at.number_input(key="f_historic_maximum_fine_units").set_value(3000).run()
    at.segmented_control(key="f_input_mode").set_value("Simple").run()
    at.text_area(key="f_narrative").input(text.replace("1 June 2010", "1 June 1992")).run()   # the same table
    button(at, "Sort my description").click().run()
    assert not at.exception, at.exception
    assert at.session_state.form_shadow["offence_date"] == date(1992, 6, 1)
    assert at.session_state.form_shadow["historic_maximum_months"] == 300
    at.text_area(key="f_narrative").input(text.replace("1 June 2010", "1 June 1975")).run()   # VSM 24.1.6.7
    button(at, "Sort my description").click().run()
    assert not at.exception, at.exception
    form = at.session_state.form_shadow
    assert form["offence_date"] == date(1975, 6, 1)
    assert form["historic_maximum_months"] is None and form["historic_maximum_fine_units"] is None


def test_an_end_left_in_the_form_without_a_period_is_not_part_of_the_date(fake):
    """Review C, final check: with "Committed over a period" unticked, an end left in the form does not count, so a
    change of date within the same historic table keeps the maximum entered; ticking the box again brings the period
    into a second table, and the maximum starts again."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value("crimes-act-ss-38-2-3-rape").run()
    at.date_input(key="f_offence_date").set_value(date(1975, 6, 1)).run()
    at.checkbox(key="f_has_period").check().run()
    at.date_input(key="f_offence_date_end").set_value(date(2010, 6, 1)).run()
    at.checkbox(key="f_has_period").uncheck().run()
    at.number_input(key="f_historic_maximum_months").set_value(300).run()
    at.date_input(key="f_offence_date").set_value(date(1976, 6, 1)).run()        # still VSM 24.1.6.7
    assert not at.exception, at.exception
    assert at.session_state.form_shadow["historic_maximum_months"] == 300
    at.checkbox(key="f_has_period").check().run()                                 # 1976–2010: 24.1.6.1 as well
    assert not at.exception, at.exception
    assert at.session_state.form_shadow["historic_maximum_months"] is None


def test_no_minor_involvement_question_for_a_body_corporate(fake):
    """Review C, second pass."""
    at = with_key()
    at.segmented_control(key="f_input_mode").set_value("Detailed").run()
    at.selectbox(key="f_offence_id").set_value("crimes-act-s-74-theft").run()
    at.selectbox(key="f_accessory_only").set_value("yes").run()
    assert any(s.key == "f_minor_involvement_proved" for s in at.selectbox)
    at.radio(key="f_offender_type").set_value("body_corporate").run()
    assert not at.exception, at.exception
    assert not any(s.key == "f_minor_involvement_proved" for s in at.selectbox)
