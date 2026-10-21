"""
core/audit.py — STEP 7: THE AUDIT OF THE REASONING (AI call 7).

A separate call (by default on the audit model chosen in the sidebar) applies
every "check: critic" standard in knowledge/audit.json — the standards the Manual
sets for sentencing reasoning that code cannot test (e.g. no two-tier
sentencing, Verdins needs evidence, prior convictions are not aggravating) —
to the stage analyses and the proposal. This is the "reflection" step of the
Lab 2 design: the AI's work is checked by a second pass before it is shown.

The code then makes sure every standard got a finding and that outcomes keep to
the knowledge base's rule (a limb that depends on a Manual flag is at most a
caution — the model is told this; the code cannot see which limb was used, so it
reports, rather than overrides, the outcomes).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core import prompts
from core.cases import Comparables
from core.facts import ConfirmedFacts, case_details_text
from core.framework import Framework
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord, LLMError
from core.results import CheckResult
from core.schemas import AuditFinding, AuditResult
from core.stages import StageResult, stages_text
from core.synthesis import ProposalResult, proposal_summary_json


@dataclass
class AuditOutcome:
    result: AuditResult | None
    standards: list[str]
    not_assessed: list[str] = field(default_factory=list)
    unknown_ids: list[str] = field(default_factory=list)
    error: str = ""

    def errors(self) -> list[AuditFinding]:
        return [f for f in (self.result.findings if self.result else []) if f.outcome == "error"]


def standards_text(kb: KnowledgeBase, ids: list[str]) -> str:
    blocks = []
    for i in ids:
        item = kb.get(i) or {}
        blocks.append(f"[{i}] {kb.title(i)}\nStatement: {item.get('statement', '')}\nTest: {item.get('test', '')}")
    return "\n\n".join(blocks)


def checks_text(checks: list[CheckResult]) -> str:
    return "\n".join(f"- {c.check_id} [{c.outcome}] {c.title}: {c.message}" for c in checks)


def run_audit(llm: LLM, kb: KnowledgeBase, facts: ConfirmedFacts, fw: Framework, stages: dict[str, StageResult],
              proposal: ProposalResult, checks: list[CheckResult], step: str = "Audit of the reasoning",
              comparables: Comparables | None = None) -> tuple[AuditOutcome, CallRecord | None]:
    standards = kb.audit_items("critic")
    outcome = AuditOutcome(result=None, standards=standards)
    p = proposal.proposal
    reasons = "\n".join(f"- {r.heading}: {r.text} [items {', '.join(r.item_ids) or '—'}; facts {', '.join(r.fact_ids) or '—'}"
                        + (f"; cases {', '.join(r.case_ids)}" if r.case_ids else "") + "]"
                        for r in (p.reasons if p else []))
    cases = comparables.block() if comparables else ""      # so audit.comparable_cases_use can be applied
    text = (
        f"## Case details\n{case_details_text(facts.case, kb)}\n\n"
        f"## Confirmed facts\n{facts.facts_text()}\n\n"
        f"## Legal framework (worked out in code)\n{fw.text()}\n\n"
        f"## Stage analyses\n{stages_text(kb, stages)}\n\n"
        + (f"{cases}\n\n" if cases else "")
        + f"## Proposal (structured)\n{proposal_summary_json(p) if p else '{}'}\n\n"
        f"## Reasons given\n{reasons}\n\n"
        f"## Results of the app's hard checks (code)\n{checks_text(checks)}\n\n"
        f"## Audit standards to apply (one finding each)\n{standards_text(kb, standards)}"
    )
    instructions = prompts.COMMON_RULES + "\n\n" + prompts.AUDIT
    try:
        result, record = llm.structured(step, "audit", instructions, text, AuditResult)
    except LLMError as exc:
        outcome.error = str(exc)
        return outcome, exc.record
    for f in result.findings:                       # "[audit.x]" -> "audit.x"
        f.check_id = f.check_id.strip().strip("[]").strip()
    seen = {f.check_id for f in result.findings}
    outcome.unknown_ids = sorted(seen - set(standards))
    result.findings = [f for f in result.findings if f.check_id in standards]
    # A finding cannot be more severe than its standard (some standards are
    # cautions by nature, e.g. audit.estimate_presentation).
    for f in result.findings:
        if f.outcome == "error" and (kb.get(f.check_id) or {}).get("severity") == "caution":
            f.outcome = "caution"
            f.explanation += " (Shown as a caution: this standard's outcome is at most a caution.)"
    outcome.not_assessed = [s for s in standards if s not in seen]
    outcome.result = result
    return outcome, record
