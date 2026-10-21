"""The simple input (core/facts.py section 4): the AI call that sorts a plain
description into the form's questions, and the code that checks its answers.
Nothing is guessed: an answer counts only if its words are in the description
(whole words, enough of them, and — for an answer that counts against the
offender — not negated, disputed or only alleged); such an answer is never
applied by itself; the open questions are "unknown" and the required ones
empty; and the user's own answers come first."""

from datetime import date

from core import config
from core.facts import (REQUIRED_FIELDS, REQUIRED_FOR_SIMPLE, SIMPLE_DEFAULTS, SORT_STEP, CaseInput, check_words,
                        is_adverse, simple_case_values, sort_description, sorted_input, validate)
from core.llm import LLM
from core.pipeline import Run, run_remaining, step_confirm, step_extract
from core.report import build_blocks, render_markdown
from core.schemas import FieldQuote, SortedCase
from tests.fake_openai import FakeOpenAI

KEY = "sk-test-key-0123456789"
S17 = "crimes-act-s-17-recklessly-causing-serious-injury"
TEXT = ("DPP v Lane. On 3 May 2023 the offender, aged 27, punched the victim in a car park. He pleaded guilty to "
        "recklessly causing serious injury in the County Court at the earliest opportunity. He has two prior "
        "convictions for unlawful assault. He had been drinking heavily that night.")


def answer(**changes) -> SortedCase:
    """A sorting answer that gives nothing, with the given changes."""
    base = dict(case_name="", offence_id="unclear", offence_as_described="", court="unclear", offence_date="",
                offence_date_end="", offence_year=None, plea="unclear", plea_timing="unknown", age_at_offence=None,
                age_at_sentence=None, prior_convictions="unknown", serving_sentence_or_on_parole="unknown",
                mental_impairment_evidence="unknown", intoxicated_by_liquor="unknown",
                intoxication_contributed="unknown", previous_aeo="unknown", cooperation="unknown",
                victim_protected_worker="unknown", offender_knew_or_reckless="unknown", accessory_only="unknown",
                presentence_detention_days=None, presentence_report="unknown", cco_consent="unknown",
                arising_from_driving="unknown", quotes=[], outside_version_1=[], not_stated=[])
    base.update(changes)
    return SortedCase(**base)


def q(field, words):
    return FieldQuote(field=field, quote=words)


# ── the words that show an answer ──
def test_short_or_partial_words_do_not_show_an_answer():
    text = "He has no prior convictions and had not been drinking. The victim was a patron of the hotel."
    for words in ["on", "t", "he", "drinking"]:                   # R5 M6: one letter, part of a word, one word
        assert check_words(words, text) != "", words
    assert "too few" in check_words("on", text)
    assert "not in the description" in check_words("the victim was a paramedic", text)
    assert check_words("a patron", text) == ""                     # two whole words are enough for a neutral answer
    assert check_words("He has no prior convictions", text) == ""
    assert check_words("14 March 2024", "On 14 March, 2024 he …") == ""


def test_an_answer_against_the_offender_needs_more_words_and_must_not_be_negated_or_disputed():
    text = ("He has no prior convictions and had not been drinking. The defence disputes that the offender knew the "
            "victim was a paramedic on duty. He may have been on parole. He has two prior convictions for assault.")
    assert "negates, disputes" in check_words("had not been drinking", text, adverse=True)
    assert "negates, disputes" in check_words("the offender knew the victim was a paramedic on duty",
                                                             text, adverse=True)
    assert "negates, disputes" in check_words("been on parole", text, adverse=True)
    assert "too few" in check_words("prior convictions", text, adverse=True)
    assert check_words("two prior convictions for assault", text, adverse=True) == ""
    # a word after the quote does not undo it ("… and no history of violence")
    assert check_words("two prior convictions", "He has two prior convictions and no history of violence.",
                       adverse=True) == ""


def test_answers_against_the_offender_are_kept_apart_for_the_user_to_tick():
    r = answer(offence_id=S17, court="County Court", offence_date="2023-05-03", plea="guilty",
               plea_timing="earliest_opportunity", age_at_offence=27, prior_convictions="some",
               intoxicated_by_liquor="yes", victim_protected_worker="yes",
               quotes=[q("offence_id", "recklessly causing serious injury"), q("court", "County Court"),
                       q("offence_date", "3 May 2023"), q("plea", "pleaded guilty"),
                       q("plea_timing", "at the earliest opportunity"), q("age_at_offence", "aged 27"),
                       q("prior_convictions", "two prior convictions for unlawful assault"),
                       q("intoxicated_by_liquor", "had been drinking heavily"),
                       q("victim_protected_worker", "the victim was a police officer on duty")])  # not in the text
    s = sorted_input(r, TEXT)
    assert s.values["offence_id"] == S17 and s.values["offence_date"] == date(2023, 5, 3)
    assert s.values["plea_timing"] == "earliest_opportunity" and s.values["age_at_offence"] == 27
    assert s.adverse == {"prior_convictions": "some", "intoxicated_by_liquor": "yes"}
    assert "prior_convictions" not in s.values and "intoxicated_by_liquor" not in s.values
    assert s.unverified["victim_protected_worker"][0] == "yes"
    assert s.missing == []
    # simple_case_values never applies them: they stay "unknown" until ticked
    v = simple_case_values(s.values, {})
    assert v["prior_convictions"] == "unknown" and v["intoxicated_by_liquor"] == "unknown"


def test_adverse_values_cover_answers_that_close_off_mitigation_or_a_disposition():
    for field, value in [("cco_consent", "no"), ("presentence_report", "none"), ("mental_impairment_evidence", "no"),
                         ("cooperation", "none"), ("serving_sentence_or_on_parole", "yes"),
                         ("prior_convictions", "relevant_or_significant")]:
        assert is_adverse(field, value), (field, value)
    for field, value in [("cco_consent", "yes"), ("prior_convictions", "none"), ("cooperation", "admissions_only")]:
        assert not is_adverse(field, value), (field, value)


def test_a_verdict_needs_words_that_show_one():
    text = "The offender pleaded not guilty. He was found guilty after a trial in the County Court."
    r = answer(plea="found_guilty_at_trial", plea_timing="late", quotes=[q("plea", "found guilty after a trial")])
    assert sorted_input(r, text).values == {"plea": "not_guilty", "plea_timing": "not_applicable"}
    wrong = answer(plea="found_guilty_at_trial", quotes=[q("plea", "pleaded not guilty")])
    s = sorted_input(wrong, text)
    assert "plea" not in s.values and "plea" in s.unverified
    vague = answer(plea="found_guilty_at_trial", quotes=[q("plea", "a trial in the County Court")])
    assert "verdict" in sorted_input(vague, text).unverified["plea"][1]


def test_invalid_values_are_reported_not_used():
    r = answer(offence_date="2023-02-30", age_at_offence=7, quotes=[q("offence_date", "30 February 2023")])
    s = sorted_input(r, "On 30 February 2023 the offender, aged 7 …")
    assert "offence_date" not in s.values and "age_at_offence" not in s.values
    assert any("not a possible date" in p for p in s.problems) and any("age" in p for p in s.problems)
    future = answer(offence_date="2999-01-01", quotes=[q("offence_date", "1 January 2999")])
    assert "offence_date" not in sorted_input(future, "On 1 January 2999 …").values


def test_code_adds_its_own_scope_notes():
    r = answer(court="Magistrates' Court", offence_id="other", offence_as_described="affray", age_at_offence=16,
               quotes=[q("age_at_offence", "aged 16")])
    s = sorted_input(r, "The offender, aged 16, is charged with affray in the Magistrates' Court.")
    assert any("Magistrates' Court" in x and "County Court and Supreme Court only" in x for x in s.outside)
    assert any("affray" in x for x in s.outside)
    assert any("16 at the offence" in x for x in s.outside)
    assert "court" not in s.values and "offence_id" not in s.values


# ── the form values of a simple case ──
def test_open_questions_are_unknown_and_required_ones_empty():
    v = simple_case_values({"offence_id": S17}, {})
    for name in ("court", "offence_date", "plea", "age_at_offence", "age_at_sentence"):
        assert v[name] is None, name                                 # R5 M3: never a default nobody gave
    for name in ("victim_protected_worker", "accessory_only", "previous_aeo", "cooperation", "arising_from_driving"):
        assert v[name] == "unknown", name                            # never "no"
    assert v["plea_timing"] == "unknown" and v["presentence_detention_days"] is None
    assert set(SIMPLE_DEFAULTS) <= set(v)


def test_your_answers_come_first_and_survive_a_new_sorting():
    found = {"offence_id": S17, "court": "County Court", "plea": "guilty", "plea_timing": "late", "age_at_offence": 40,
             "presentence_report": "ordered"}
    yours = {"court": "Supreme Court", "presentence_report": "available", "prior_convictions": "relevant_or_significant",
             "user_role": "defence", "case_name": "R v Mine"}
    v = simple_case_values(found, yours)                             # R5 M4
    assert v["court"] == "Supreme Court" and v["presentence_report"] == "available"
    assert v["prior_convictions"] == "relevant_or_significant" and v["user_role"] == "defence"
    assert v["case_name"] == "R v Mine" and v["offence_id"] == S17


def test_the_age_at_sentence_is_assumed_and_marked_only_when_not_given():
    v = simple_case_values({"age_at_offence": 19}, {})
    assert v["age_at_sentence"] == 19 and v["age_at_sentence_assumed"]          # R5 M5
    v = simple_case_values({"age_at_offence": 19, "age_at_sentence": 22}, {})
    assert v["age_at_sentence"] == 22 and not v["age_at_sentence_assumed"]
    v = simple_case_values({"age_at_offence": 19}, {"age_at_sentence": 25})
    assert v["age_at_sentence"] == 25 and not v["age_at_sentence_assumed"]
    v = simple_case_values({}, {})
    assert v["age_at_sentence"] is None and not v["age_at_sentence_assumed"]
    v = simple_case_values({"age_at_offence": 40, "age_at_sentence": 41}, {"age_at_offence": 45})
    assert v["age_at_sentence"] == 41                               # never raised silently: validation reports it
    case = CaseInput(**{k: val for k, val in simple_case_values({"age_at_offence": 19}, {}).items()
                        if k in CaseInput().to_dict()})
    assert case.age_at_sentence_assumed


def test_validation_asks_for_the_required_answers_first(kb):
    case = CaseInput(offence_id=None, court=None, offence_date=None, plea=None, age_at_offence=None,
                     age_at_sentence=None, narrative="x" * 100)
    problems = validate(case, kb)
    assert len(problems) == 1 and all(label in problems[0] for label in REQUIRED_FIELDS.values())
    assert case.to_dict()["offence_date"] is None
    assert set(REQUIRED_FOR_SIMPLE) == set(REQUIRED_FIELDS) - {"age_at_sentence"}


# ── the sorting call in a run ──
def test_the_sorting_call_and_a_run_that_starts_with_it(kb, retriever):
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    description = (config.TEST_CASES_DIR / "simple_example.txt").read_text(encoding="utf-8")
    s, record = sort_description(llm, description, kb)
    assert record.step == SORT_STEP and record.ok and record.kind == "ai"
    sent = fake.requests[-1]["body"]
    assert S17 in str(sent["input"]) and sent["text"]["format"]["name"] == "SortedCase"
    assert s.values["offence_id"] == S17 and "cco_consent" in s.unverified
    assert s.adverse == {"prior_convictions": "some", "intoxicated_by_liquor": "yes"}
    values = simple_case_values(s.values, {})
    case = CaseInput(**{k: v for k, v in values.items() if k in CaseInput().to_dict()})
    case.narrative = description
    assert validate(case, kb) == [] and case.age_at_sentence_assumed
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT), simple=True)
    run.calls.append(record)
    step_extract(run, llm, kb)
    assert run.suggestions and not any(run.suggestion_choices.values())          # R5 M1: none starts ticked
    step_confirm(run, kb, run.fact_rows, {})
    run_remaining(run, llm, kb, retriever)
    assert run.finished and run.ai_calls == 8                          # the sorting + the usual 7
    md = render_markdown(build_blocks(run, kb))
    assert "(assumed: not stated, taken as the age at the offence)" in md
    step_confirm(run, kb, run.fact_rows, {})                           # confirming again keeps the sorting call
    assert [c.step for c in run.calls] == [SORT_STEP, "Facts extraction"]
    assert run.ai_calls == 2


def test_in_the_detailed_input_favourable_suggestions_still_start_ticked(kb):
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    case = CaseInput(prior_convictions="unknown", narrative="The offender punched the victim outside a hotel. " * 3)
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    assert run.suggestions["prior_convictions"] == "none" and run.suggestion_choices["prior_convictions"]


def test_the_report_names_the_calls_not_counted(kb, retriever):
    from dataclasses import replace
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    description = (config.TEST_CASES_DIR / "simple_example.txt").read_text(encoding="utf-8")
    s, record = sort_description(llm, description, kb)
    case = CaseInput(**{k: v for k, v in simple_case_values(s.values, {}).items() if k in CaseInput().to_dict()})
    case.narrative = description
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT), simple=True)
    run.discarded_calls.append(replace(record, note="replaced by a later sorting of the description"))
    run.reused_calls.append(replace(record, note="made for an earlier run of this case"))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, {})
    run_remaining(run, llm, kb, retriever)
    md = render_markdown(build_blocks(run, kb))
    assert "Not counted: Sorting the description — replaced by a later sorting of the description" in md
    assert "reused here (counted there): Sorting the description" in md
    assert "confirmed again" not in md                                 # R5 m1


def test_the_accessory_caution_says_when_no_minimum_binds(kb):
    from core.facts import ConfirmedFacts
    from core.framework import build_framework
    case = CaseInput(offence_id=S17, accessory_only="unknown", victim_protected_worker="unknown",
                     offender_knew_or_reckless="unknown", protected_scheme_confirmed="yes", narrative="x")
    fw = build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))
    gate = next(g for g in fw.gates if g.title == "Accessory basis unknown")
    assert "the minimum is applied" not in gate.message                   # R5 m3: no minimum on the main reading
    assert "only if further facts are proved" in gate.message


# ── regressions from the verification of the fixes (R5b) ──
import pytest  # noqa: E402


@pytest.mark.parametrize("text, plea, words", [
    ("He has not pleaded guilty and the trial is listed for March next year.", "guilty", "pleaded guilty"),
    ("He pleaded not guilty and will stand trial in the County Court.", "found_guilty_at_trial", "will stand trial"),
    ("He was not found guilty at trial; the jury could not agree.", "found_guilty_at_trial", "found guilty at trial"),
    ("He is awaiting trial in the County Court.", "found_guilty_at_trial", "awaiting trial in the County Court"),
    ("He will plead guilty at the next mention.", "guilty", "plead guilty at the next mention"),
])
def test_a_plea_or_verdict_must_be_settled_and_in_so_many_words(text, plea, words):
    s = sorted_input(answer(plea=plea, quotes=[q("plea", words)]), text)        # R5b MAJOR-2
    assert "plea" not in s.values and "plea" in s.unverified
    assert REQUIRED_FOR_SIMPLE["plea"] in s.missing


@pytest.mark.parametrize("text, plea, words, value", [
    ("He pleaded guilty at the first opportunity.", "guilty", "pleaded guilty", "guilty"),
    ("He entered a plea of guilty to the charge.", "guilty", "a plea of guilty", "guilty"),
    ("After a trial he was found guilty by a jury.", "found_guilty_at_trial", "found guilty by a jury", "not_guilty"),
    ("He was convicted after a five-day trial.", "found_guilty_at_trial", "was convicted after", "not_guilty"),
])
def test_a_settled_plea_or_verdict_is_used(text, plea, words, value):
    assert sorted_input(answer(plea=plea, quotes=[q("plea", words)]), text).values["plea"] == value


def test_doubt_in_the_same_sentence_after_a_semicolon_and_the_sentence_shown():
    text = "He denies it; the victim was a paramedic on duty. Later he had been drinking heavily at home."
    r = answer(victim_protected_worker="yes", intoxicated_by_liquor="yes",
               quotes=[q("victim_protected_worker", "the victim was a paramedic on duty"),
                       q("intoxicated_by_liquor", "had been drinking heavily")])
    s = sorted_input(r, text)                                                       # R5b MINOR-5
    assert "victim_protected_worker" in s.unverified
    assert s.context["intoxicated_by_liquor"] == "Later he had been drinking heavily at home."


def test_negative_answers_against_the_offender_can_be_offered():
    text = ("He does not consent to a community correction order. No pre-sentence report was prepared. There is no "
            "psychiatric report. He made no admissions to police. The defence says it is disputed that he did not "
            "cooperate in any other way.")
    r = answer(cco_consent="no", presentence_report="none", mental_impairment_evidence="no", cooperation="none",
               quotes=[q("cco_consent", "does not consent to a community correction order"),
                       q("presentence_report", "No pre-sentence report was prepared"),
                       q("mental_impairment_evidence", "There is no psychiatric report"),
                       q("cooperation", "he did not cooperate")])
    s = sorted_input(r, text)                                                       # R5b MINOR-6
    assert s.adverse == {"cco_consent": "no", "presentence_report": "none", "mental_impairment_evidence": "no"}
    assert "disputes" in s.unverified["cooperation"][1] or "negates" in s.unverified["cooperation"][1]


def test_the_users_empty_answers_survive_a_new_sorting():
    v = simple_case_values({"presentence_detention_days": 40, "offence_date_end": date(2024, 5, 3)},
                           {"presentence_detention_days": None, "offence_date_end": None})    # R5b MAJOR-1
    assert v["presentence_detention_days"] is None and v["offence_date_end"] is None


def test_the_accessory_caution_names_the_minimum_that_applies_only_if_proved(kb):
    from core.facts import ConfirmedFacts
    from core.framework import build_framework
    s15a = "crimes-act-s-15a-1-intentionally-causing-serious-injury-in-circumstances-of-gross-violence"
    case = CaseInput(offence_id=s15a, accessory_only="unknown", victim_protected_worker="unknown",
                     offender_knew_or_reckless="unknown", protected_scheme_confirmed="yes", narrative="x",
                     offence_date=date(2024, 1, 1))
    fw = build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))
    gate = next(g for g in fw.gates if g.title == "Accessory basis unknown")
    assert "the minimum is applied" in gate.message                                 # the gross-violence minimum binds
    assert "The protected-official minimum applies here only if further facts are proved" in gate.message
    assert "A minimum applies here" not in gate.message                             # R5b MINOR-4


def test_the_report_gives_the_reason_for_each_call_not_counted(kb, retriever):
    from dataclasses import replace
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    description = (config.TEST_CASES_DIR / "simple_example.txt").read_text(encoding="utf-8")
    s, record = sort_description(llm, description, kb)
    case = CaseInput(**{k: v for k, v in simple_case_values(s.values, {}).items() if k in CaseInput().to_dict()})
    case.narrative = description
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    run.discarded_calls.append(replace(record, note="not used: a test case was loaded"))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, {})
    run_remaining(run, llm, kb, retriever)
    step_confirm(run, kb, run.fact_rows, {})                                         # 6 calls replaced
    md = render_markdown(build_blocks(run, kb))
    assert "Sorting the description — not used: a test case was loaded" in md      # R5b MINOR-2
    assert "replaced when the facts were confirmed again" in md
