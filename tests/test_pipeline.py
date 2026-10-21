"""End-to-end tests of one run, through the real OpenAI library with a fake
HTTP service (tests/fake_openai.py): call counts, citation cleaning, the
revision loop, key handling, model routing and the report."""

import json

import httpx
import pytest

from core import config
from core.facts import CaseInput
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


def run_case(kb, retriever, name, bad_first=True):
    fake = FakeOpenAI(bad_first_proposal=bad_first)
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
