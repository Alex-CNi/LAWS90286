"""End-to-end tests of one run, through the real OpenAI library with a fake
HTTP service (tests/fake_openai.py): call counts, citation cleaning, the
revision loop, key handling, model routing and the report."""

import json
import re

import httpx
import pytest

from core import config
from core.facts import CaseInput, ConfirmedFacts, validate
from core.framework import build_framework
from core.llm import LLM, LLMError
from core.pipeline import Run, run_remaining, step_confirm, step_extract
from core.report import build_blocks, render_docx, render_markdown
from core.schemas import StageAnalysis
from tests.fake_openai import FakeOpenAI

KEY = "sk-test-key-0123456789"


def load_case(name: str) -> CaseInput:
    data = json.loads((config.TEST_CASES_DIR / name).read_text(encoding="utf-8"))
    case = CaseInput.from_dict(dict(CaseInput().to_dict(), **data["form"]))
    case.narrative = (config.TEST_CASES_DIR / data["narrative_file"]).read_text(encoding="utf-8")
    return case


def run_case(kb, retriever, name, bad_first=True, kind="auto", first="auto"):
    fake = FakeOpenAI(bad_first_proposal=bad_first, proposal_kind=kind, first_kind=first)
    llm = LLM(api_key=KEY, http_client=fake.client())
    run = Run(case=load_case(name), models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    return run, fake


def test_full_run_with_revision(kb, retriever):
    run, fake = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=True)
    # 1 extraction + 4 stages + synthesis + audit + revision + re-audit
    assert run.ai_calls == 9
    assert run.finished and len(run.attempts) == 2
    first_errors = [c for c in run.attempts[0].checks if c.outcome == "error"]
    assert first_errors, "the fake's first proposal should break the one-year combination limit"
    assert not [c for c in run.current.checks if c.outcome == "error"]
    # invented ids are removed and reported
    assert any("invented.item_id" in x for st in run.stages.values() for x in st.invalid)
    assert all("invented.item_id" not in pt.item_ids for st in run.stages.values() for pt in st.analysis.points)
    assert any("made.up" in x for x in run.current.proposal.invalid)
    # a quote that is not in the narrative is flagged
    assert any(r["quote_verified"] is False for r in run.fact_rows)
    # the mandatory alcohol exclusion order is required by the hard checks
    assert any(c.check_id == "audit.mandatory_ancillary_orders" and c.outcome == "pass" for c in run.current.checks)


def test_seven_calls_without_errors(kb, retriever):
    run, _ = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    assert run.ai_calls == 7 and len(run.attempts) == 1 and run.finished
    assert run.ai_calls >= config.MIN_AI_CALLS_PER_RUN


@pytest.mark.parametrize("name", ["tc2_paramedic_icsi.json", "tc3_unclear_status.json"])
def test_other_test_cases_complete(kb, retriever, name):
    run, _ = run_case(kb, retriever, name)
    assert run.ai_calls >= 7 and run.finished
    md = render_markdown(build_blocks(run, kb))
    assert "## 9. Sources" in md


def test_key_only_in_authorization_header(kb, retriever):
    run, fake = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    assert all(r["auth"] == f"Bearer {KEY}" for r in fake.requests)
    assert all(KEY not in json.dumps(r["body"]) for r in fake.requests)
    md = render_markdown(build_blocks(run, kb))
    assert KEY not in md and all(KEY not in (c.note or "") for c in run.calls)


def test_models_follow_roles(kb, retriever):
    _, fake = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    by_schema = {}
    for r in fake.requests:
        if "text" in r["body"]:
            by_schema.setdefault(r["body"]["text"]["format"]["name"], set()).add(r["body"]["model"])
            assert r["body"]["store"] is False
            assert r["body"]["text"]["format"]["strict"] is True
    assert by_schema["FactsExtraction"] == {config.DEFAULT_MODELS["extraction"]}
    assert by_schema["StageAnalysis"] == {config.DEFAULT_MODELS["analysis"]}
    assert by_schema["Proposal"] == {config.DEFAULT_MODELS["synthesis"]}
    assert by_schema["AuditResult"] == {config.DEFAULT_MODELS["audit"]}
    embeds = [r for r in fake.requests if r["path"].endswith("/embeddings")]
    assert len(embeds) == 1 and embeds[0]["body"]["dimensions"] == config.EMBEDDING_DIMENSIONS


def test_report_renders(kb, retriever):
    run, _ = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    blocks = build_blocks(run, kb)
    md = render_markdown(blocks)
    assert "Indicative band" in md and "AI calls in this run: 7" in md and "no warranty" in md.lower()
    assert render_docx(blocks)[:2] == b"PK"


def test_no_key_is_refused():
    with pytest.raises(LLMError):
        LLM(api_key="").structured("x", "analysis", "i", "t", StageAnalysis)


def test_retry_without_reasoning_parameter():
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        calls.append(body)
        if "reasoning" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'reasoning.effort'",
                                                       "type": "invalid_request_error", "param": "reasoning.effort"}})
        answer = StageAnalysis(points=[], conclusion="ok", gaps=[], flags_engaged=[])
        return httpx.Response(200, json=FakeOpenAI.response(body["model"], answer.model_dump_json()))

    llm = LLM(api_key=KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    parsed, record = llm.structured("x", "analysis", "instructions", "input", StageAnalysis)
    assert parsed.conclusion == "ok" and "reasoning" in record.note and len(calls) == 2


def test_wrong_key_message_does_not_echo_key():
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY[:6]}***",
                                                   "type": "invalid_request_error", "code": "invalid_api_key"}})
    llm = LLM(api_key=KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError) as exc:
        llm.structured("x", "analysis", "i", "t", StageAnalysis)
    assert "rejected the API key" in str(exc.value) and KEY[:6] not in str(exc.value)


def test_audit_cannot_exceed_a_caution_standard(kb, retriever):
    class Strict(FakeOpenAI):
        def answer_AuditResult(self, text, body):
            res = super().answer_AuditResult(text, body)
            for f in res.findings:
                if f.check_id == "audit.estimate_presentation":
                    f.outcome = "error"
            return res
    fake = Strict(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    run = Run(case=load_case("tc1_glassing_rcsi.json"), models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    f = next(f for f in run.current.audit.result.findings if f.check_id == "audit.estimate_presentation")
    assert f.outcome == "caution" and run.ai_calls == 7


def test_failed_revision_keeps_the_first_proposal(kb, retriever):
    class FailRevision(FakeOpenAI):
        def handle(self, request):
            body = json.loads(request.content or b"{}")
            if "## Your previous proposal" in str(body.get("input", "")):
                return httpx.Response(400, json={"error": {"message": "something unexpected", "type": "invalid_request_error"}})
            return super().handle(request)
    fake = FailRevision(bad_first_proposal=True)
    llm = LLM(api_key=KEY, http_client=fake.client())
    run = Run(case=load_case("tc1_glassing_rcsi.json"), models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    assert run.finished and run.revision_error and len(run.attempts) == 1 and run.ai_calls == 7
    assert "The revision failed" in render_markdown(build_blocks(run, kb))


def test_word_report_survives_control_characters(kb, retriever):
    from core.report import Block
    data = render_docx([Block("h1", "Title\x00"), Block("p", "Text with \x0b and \x1b"),
                        Block("table", headers=["a\x0c", "b"], items=[["x\x00", "y"]])])
    assert data[:2] == b"PK"


def test_model_fallback_when_not_available():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "unavailable-model":
            return httpx.Response(404, json={"error": {"message": "The model `unavailable-model` does not exist",
                                                       "type": "invalid_request_error", "code": "model_not_found"}})
        answer = StageAnalysis(points=[], conclusion="ok", gaps=[], flags_engaged=[])
        return httpx.Response(200, json=FakeOpenAI.response(body["model"], answer.model_dump_json()))

    llm = LLM(api_key=KEY, models=dict(config.DEFAULT_MODELS, analysis="unavailable-model"),
              http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    parsed, record = llm.structured("x", "analysis", "i", "t", StageAnalysis)
    assert record.model == config.FALLBACK_MODELS[0] and "not available" in record.note
    assert calls[0] == "unavailable-model"


def test_key_not_in_llm_repr():
    assert KEY not in repr(LLM(api_key=KEY))


# ─────────────────────────────────────────────────────────────────────────────
# Second review (R2): readings in a whole run, suggestions, the call counter,
# refusals that are not about the model, and the report
# ─────────────────────────────────────────────────────────────────────────────
def fresh_run(kb, fake, case):
    llm = LLM(api_key=KEY, http_client=fake.client())
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    return run, llm


def test_an_unconfirmed_scheme_still_revises_an_error_that_does_not_depend_on_it(kb, retriever):
    class NoConviction(FakeOpenAI):
        def answer_Proposal(self, text, body):
            p = super().answer_Proposal(text, body)
            if "## Your previous proposal" not in text:
                p.conviction_recorded = None                   # imprisonment without a recorded conviction
            return p
    case = load_case("tc2_paramedic_icsi.json")
    case.protected_scheme_confirmed = "unknown"
    run, llm = fresh_run(kb, NoConviction(bad_first_proposal=False), case)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    assert [a.kind for a in run.framework.alternatives] == ["open"]
    run_remaining(run, llm, kb, retriever)
    first = run.attempts[0].checks
    assert any(c.check_id == "audit.conviction_requirements" and c.outcome == "error" for c in first)
    assert len(run.attempts) == 2 and run.ai_calls == 9
    assert run.current.proposal.proposal.conviction_recorded is True


def test_unticking_a_suggestion_withdraws_it(kb):
    class Suggests(FakeOpenAI):
        def answer_FactsExtraction(self, text, body):
            ex = super().answer_FactsExtraction(text, body)
            ex.suggested.victim_protected_worker = "yes"
            ex.suggested.offender_knew_or_reckless_as_to_status = "yes"
            return ex
    run, _ = fresh_run(kb, Suggests(), load_case("tc3_unclear_status.json"))
    assert run.suggestion_choices["victim_protected_worker"] is False     # adverse: starts unticked
    adverse = {k: run.suggestions[k] for k in ("victim_protected_worker", "offender_knew_or_reckless")}
    step_confirm(run, kb, run.fact_rows, adverse)
    assert run.case.protected_official == "yes" and run.framework.category == 1
    step_confirm(run, kb, run.fact_rows, {})                               # unticked, confirmed again
    assert run.case.protected_official == "unknown" and run.framework.category is None
    assert run.facts.applied_suggestions == {}


def test_confirming_again_does_not_count_the_discarded_calls(kb, retriever):
    run, _ = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    assert run.ai_calls == 7
    step_confirm(run, kb, run.facts.facts, {})
    assert run.ai_calls == 1 and sum(1 for c in run.discarded_calls if c.kind == "ai") == 6
    run_remaining(run, LLM(api_key=KEY, http_client=FakeOpenAI(bad_first_proposal=False).client()), kb, retriever)
    assert run.ai_calls == 7
    assert "Not counted: " in render_markdown(build_blocks(run, kb))
    assert "replaced when the facts were confirmed again" in render_markdown(build_blocks(run, kb))


def test_a_refusal_that_is_not_about_the_model_is_shown_and_not_retried():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(403, json={"error": {"message": "You have insufficient permissions for this operation. "
                                                              "Missing scopes: api.responses.write.",
                                                   "type": "invalid_request_error", "code": None}})
    llm = LLM(api_key=KEY, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(LLMError) as exc:
        llm.structured("x", "analysis", "i", "t", StageAnalysis)
    assert len(calls) == 1 and "api.responses.write" in str(exc.value) and KEY not in str(exc.value)


def test_the_report_labels_each_reading_and_filters_the_flags(kb, retriever):
    case = load_case("tc2_paramedic_icsi.json")
    case.protected_scheme_confirmed = "unknown"
    fake = FakeOpenAI(bad_first_proposal=False)
    run, llm = fresh_run(kb, fake, case)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    md = render_markdown(build_blocks(run, kb))
    assert "Other reading (unresolved in the Manual)" in md and "If protected status were proved" not in md
    run, _ = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    md = render_markdown(build_blocks(run, kb))
    assert "### Manual flags engaged by this case" in md
    for flag in ("flag.body_corporate_fine_multiplier", "flag.penalty_unit_values_end_2025", "flag.cyfa_disposition_power"):
        assert flag not in md, flag


def test_a_key_that_cannot_use_embeddings_still_completes_the_run(kb, retriever):
    assert retriever.uses_vectors                          # the test retriever has a (fake) vector index

    class NoEmbeddings(FakeOpenAI):
        def handle(self, request):
            if request.url.path.endswith("/embeddings"):
                return httpx.Response(403, json={"error": {"message": "You have insufficient permissions for this "
                                                                      "operation. Missing scopes: api.model.request.",
                                                           "type": "invalid_request_error", "code": None}})
            return super().handle(request)
    run, llm = fresh_run(kb, NoEmbeddings(bad_first_proposal=False), load_case("tc1_glassing_rcsi.json"))
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    assert run.finished and run.ai_calls == 7
    assert run.retrieval_mode.startswith("keyword ranking") and run.messages
    assert "embeddings endpoint" in run.retrieval_mode and KEY not in run.retrieval_mode
    assert "keyword ranking" in render_markdown(build_blocks(run, kb))


def test_the_report_lists_related_flags_by_title_only(kb, retriever):
    from core.framework import RELATED_FLAGS_GATE
    from core.report import engaged_flags
    run, _ = run_case(kb, retriever, "tc2_paramedic_icsi.json", bad_first=False)
    gate = next(g for g in run.framework.gates if g.title == RELATED_FLAGS_GATE)
    md = render_markdown(build_blocks(run, kb))
    engaged_part = md.split("### Manual flags engaged by this case")[1].split("### Other Manual flags")[0]
    for f in set(gate.flags) - set(engaged_flags(run)):
        assert f"({f})" not in engaged_part and f in md.split("### Other Manual flags")[1]


# ─────────────────────────────────────────────────────────────────────────────
# Version 2.0: one test case per family of statutory schemes (docs/ALL_OFFENCES_GUIDE.md step 21), end to end. Each
# test checks that the scheme its case was written for shows in the framework; where the fake service's first answer
# breaks that scheme's rule, that the hard check catches it and the revision corrects it; and, for test cases 10 and
# 12, the rule's limits at the check level (hand-made proposals).
# ─────────────────────────────────────────────────────────────────────────────
V2_CASES = sorted((p.name for p in config.TEST_CASES_DIR.glob("tc*.json")
                   if int(re.match(r"tc(\d+)", p.name).group(1)) >= 4), key=lambda n: int(re.match(r"tc(\d+)", n).group(1)))


def titled(results, title):
    return [c.outcome for c in results if c.title == title]


def proposal_of(run):
    return run.current.proposal.proposal


def reframed(kb, name, **changes):
    """The framework of a test case with some answers changed (no AI calls)."""
    case = load_case(name)
    for k, v in changes.items():
        setattr(case, k, v)
    return build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))


def test_there_are_ten_new_test_cases_and_the_three_of_version_1():
    assert len(V2_CASES) == 10
    assert {"tc1_glassing_rcsi.json", "tc2_paramedic_icsi.json", "tc3_unclear_status.json"} <= {
        p.name for p in config.TEST_CASES_DIR.glob("*.json")}


@pytest.mark.parametrize("name", V2_CASES)
def test_every_new_test_case_is_valid_and_runs_to_the_end(kb, retriever, name):
    data = json.loads((config.TEST_CASES_DIR / name).read_text(encoding="utf-8"))
    assert data["title"].startswith(f"Test case {data['id'][2:]} — ") and len(data["shows"]) > 100
    assert not set(data["form"]) - set(CaseInput().to_dict())            # every form answer is a real question
    assert not validate(load_case(name), kb)
    run, _ = run_case(kb, retriever, name, kind="life" if "murder" in name else "auto")
    assert run.finished and run.ai_calls >= 7
    assert not [c for c in run.current.checks if c.outcome == "error"]
    md = render_markdown(build_blocks(run, kb))
    assert "## 9. Sources" in md and data["form"]["case_name"] in md


def test_tc4_single_punch_manslaughter_has_the_ten_year_minimum(kb, retriever):
    name = "tc4_single_punch_manslaughter.json"
    run, _ = run_case(kb, retriever, name)
    fw = run.framework
    assert fw.mnpp_months == 120 and fw.category == 2
    assert "error" in titled(run.attempts[0].checks, "Below the minimum non-parole period")
    p = proposal_of(run)
    assert p.npp_months >= 120 and any(o.type == "alcohol_exclusion_order" for o in p.ancillary_orders)
    assert "A single punch or strike (proved beyond reasonable doubt)" in render_markdown(build_blocks(run, kb))
    assert reframed(kb, name, prosecution_notice="no").mnpp_months is None          # no notice, no minimum


def test_tc5_rape_is_sentenced_under_the_standard_sentence_scheme(kb, retriever):
    run, _ = run_case(kb, retriever, "tc5_rape_trial.json")
    fw = run.framework
    assert fw.sso.applies == "yes" and fw.sso.years == 10 and fw.category == 1
    assert "error" in titled(run.attempts[0].checks, "Standard sentence — relationship not stated")
    assert titled(run.current.checks, "Standard sentence — non-parole floor") == ["pass"]
    p = proposal_of(run)
    assert p.standard_sentence_statement and not p.s6aaa.stated and not p.plea_reduced_sentence     # a trial
    assert "**Standard sentence.**" in render_markdown(build_blocks(run, kb))
    assert any("s 39(2)" in g.title for g in fw.gates)                           # the Manual's own problem, shown


def test_tc6_murder_of_a_paramedic_is_life_with_the_30_year_standard_sentence(kb, retriever):
    name = "tc6_murder_paramedic.json"
    run, _ = run_case(kb, retriever, name, kind="life")
    fw = run.framework
    assert fw.maximum.life and fw.sso.years == 30 and "proved" in fw.sso.variant
    assert "error" in titled(run.attempts[0].checks, "Standard sentence — below the s 11A(4) floor")
    p = proposal_of(run)
    assert p.disposition_type == "life_imprisonment" and p.npp_months == 360
    assert any(g.title == "Offender-status schemes not checked" for g in fw.gates)
    assert "Life imprisonment, with a non-parole period of 30 years" in render_markdown(build_blocks(run, kb))
    assert reframed(kb, name, sso_variant_met="unknown").sso.years == 25


def test_tc7_culpable_driving_needs_the_licence_order(kb, retriever):
    name = "tc7_culpable_driving.json"
    run, _ = run_case(kb, retriever, name)
    fw = run.framework
    assert fw.licence.applies == "yes" and fw.licence.minimum == 24 and fw.sso.years == 8
    assert "error" in titled(run.attempts[0].checks, "Licence order missing")
    assert titled(run.current.checks, "Licence order") == ["pass"]
    assert titled(run.current.checks, "Alcohol exclusion order — relevant offence?") == ["caution"]
    p = proposal_of(run)
    assert any(o.type == "licence_cancellation_and_disqualification" and o.months >= 24 for o in p.ancillary_orders)
    assert "Licence cancellation and disqualification (mandatory), 3 years" in render_markdown(build_blocks(run, kb))


def test_tc8_armed_robbery_is_category_2_only_on_its_condition(kb, retriever):
    name = "tc8_armed_robbery.json"
    run, _ = run_case(kb, retriever, name, first="combination")      # the first answer: imprisonment and a CCO
    assert run.framework.category == 2
    first = [c for c in run.attempts[0].checks if c.check_id == "audit.category_1_2" and c.outcome == "error"]
    assert first and "Category 2" in first[0].title                 # the Category 2 rule catches it
    p = proposal_of(run)
    assert p.disposition_type == "imprisonment" and p.cco is None
    assert not [c for c in run.current.checks if c.check_id == "audit.category_1_2" and c.outcome == "error"]
    open_ = reframed(kb, name, category_condition_met="unknown")
    assert open_.category is None and any(g.title == "Category condition not established" for g in open_.gates)


def test_tc9_aggravated_home_invasion_has_the_three_year_minimum(kb, retriever):
    run, _ = run_case(kb, retriever, "tc9_aggravated_home_invasion.json")
    fw = run.framework
    assert fw.category == 1 and fw.mnpp_months == 36
    titles = [g.title for g in fw.gates]
    assert any("s 10AC" in t and "s 10AD" in t for t in titles) and any("“might”" in t for t in titles)
    assert "error" in titled(run.attempts[0].checks, "Below the minimum non-parole period")
    assert proposal_of(run).npp_months >= 36


def test_tc10_trafficking_near_a_school_has_the_conditional_maximum(kb, retriever):
    name = "tc10_trafficking_near_school.json"
    run, _ = run_case(kb, retriever, name)
    m = run.framework.maximum
    assert m.conditional_applied and m.months == 240 and "flag.school_500m_condition" in run.framework.flags
    unconfirmed = reframed(kb, name, school_condition_confirmed="unknown").maximum
    assert unconfirmed.months == 180 and unconfirmed.conditional_ceiling_months == 240
    assert reframed(kb, name, conditional_condition_met="no").maximum.months == 180
    # the band at the check level: 200 months passes when confirmed, is unresolved when not, and 250 is an error
    from core.checks import run_hard_checks
    from tests.test_checks import proposal
    for confirmed, months, want in (("yes", 200, ["pass"]), ("unknown", 200, ["unresolved"]),
                                    ("no", 200, ["unresolved"]), ("yes", 250, ["error"])):
        case = load_case(name)
        case.school_condition_confirmed = confirmed
        facts = ConfirmedFacts(case=case, summary="", facts=[])
        res = run_hard_checks(kb, facts, build_framework(kb, facts), proposal(term_months=months, npp_months=130))
        assert [r.outcome for r in res if r.check_id == "audit.maximum_applicable"] == want, (confirmed, months)


def test_tc11_a_company_is_fined_within_the_body_corporate_maximum(kb, retriever):
    run, _ = run_case(kb, retriever, "tc11_ohs_company.json")
    fw = run.framework
    assert fw.body_corporate and fw.maximum.company_fine_units == 9000 and fw.maximum.fine_penalty_units == 1800
    assert "error" in titled(run.attempts[0].checks, "Fine above the body corporate maximum")
    p = proposal_of(run)
    assert p.disposition_type == "fine" and p.fine_penalty_units <= 9000 and p.cco is None
    md = render_markdown(build_blocks(run, kb))
    assert "the offender is a body corporate" in md and "| Body corporate fine maximum | 9000 penalty units" in md


def test_tc12_an_attempt_is_sixty_percent_of_the_offence_attempted(kb, retriever):
    run, _ = run_case(kb, retriever, "tc12_attempted_theft_of_firearm.json")
    fw = run.framework
    assert fw.maximum.months == 108 and fw.maximum.fine_penalty_units == 1080
    assert "Theft of firearm" in fw.maximum.relative_note and fw.licence.applies == "no"
    assert not any("flag.attempt_table_not_reproduced" in g.flags for g in fw.gates)    # theft of a firearm: no level
    assert titled(run.current.checks, "Within the maximum") == ["pass"]
    from core.checks import run_hard_checks
    from tests.test_checks import proposal
    facts = ConfirmedFacts(case=load_case("tc12_attempted_theft_of_firearm.json"), summary="", facts=[])
    for months, want in ((108, "pass"), (109, "error")):                  # 60% of 15 years, never rounded up
        res = run_hard_checks(kb, facts, fw, proposal(term_months=months, npp_months=70))
        assert want in [r.outcome for r in res if r.check_id == "audit.maximum_applicable"], months


def test_tc13_contempt_shows_the_limits_as_cautions(kb, retriever):
    run, _ = run_case(kb, retriever, "tc13_juror_contempt.json")
    fw = run.framework
    assert fw.maximum.at_large
    assert {"flag.contempt_sentencing_act_and_orders", "flag.contempt_gravity_culpability_29_2_2_2"} <= set(fw.flags)
    assert any(g.title == "Contempt — limits of the estimate" for g in fw.gates)
    first = run.attempts[0].checks
    assert not [c for c in first if c.outcome == "error"] and len(run.attempts) == 1
    assert titled(first, "Combination sentence over one year") == ["caution"]       # an error for any other charge


def test_flags_that_cannot_bear_on_the_case_are_not_shown_to_the_ai(kb, retriever):
    """A body corporate's flag never reaches a person's case (framework.flag_applies), whatever the ranking."""
    from core.stages import select_items
    person = load_case("tc1_glassing_rcsi.json")
    printed = load_case("tc11_ohs_company.json")                 # the row prints the company maximum
    theft = load_case("tc11_ohs_company.json")
    theft.offence_id = "crimes-act-s-74-theft"                   # no printed company maximum: five times a person's
    query = "fine body corporate company penalty units multiplier maximum"
    for case, shown in ((person, False), (printed, False), (theft, True)):
        ids, _ = select_items(kb, retriever, ["framework", "disposition"], case.offence_id, query, 400, case=case)
        corporate = [i for i in ids if i.startswith("flag.") and "body_corporate" in i]
        assert bool(corporate) == shown, (case.offence_id, corporate)
    run, _ = run_case(kb, retriever, "tc1_glassing_rcsi.json", bad_first=False)
    shown = {i for st in run.stages.values() for i in st.item_ids} | set(run.current.proposal.item_ids)
    assert not [i for i in shown if i.startswith("flag.") and "body_corporate" in i]
