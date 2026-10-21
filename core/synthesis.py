"""
core/synthesis.py — STEP 5: THE PROPOSED SENTENCE (AI call 6) AND ITS REVISION.

  propose()  weighs the four stage analyses with the framework and the Manual's
             rules on dispositions, and returns one formulated sentence plus an
             indicative band (schemas.Proposal).
  revise()   if the hard checks or the audit found errors, sends the proposal
             back with the problems listed, once (config.MAX_REVISIONS).

The items in SYNTHESIS_ALWAYS are always shown to this call because the hard
checks enforce them; the rest are chosen by similarity to the facts. The most
similar cases from the user's case database (if any) are shown too, as
yardsticks only (core/cases.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from core import config, prompts
from core.cases import Comparables
from core.facts import ConfirmedFacts, case_details_text
from core.framework import Framework
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord, LLMError
from core.results import CheckResult
from core.retrieval import Retriever
from core.schemas import AuditResult, Proposal
from core.stages import StageResult, clean_citations, select_items, stages_text

# Rules the hard checks enforce (core/checks.py) — always shown to the synthesis.
SYNTHESIS_ALWAYS = [
    "method.instinctive_synthesis", "method.two_tier", "method.quantified_discounts", "formulation.order_of_steps",
    "reasons.statutory_statements", "reasons.content", "sentence.dispositions", "sentence.recording_conviction",
    "principle.proportionality", "principle.parsimony", "principle.sentence_type_ordering", "imp.last_resort",
    "imp.conviction_required", "npp.must_fix_two_years", "npp.none_under_12_months", "npp.six_month_gap",
    "npp.none_with_cco_12m_2y", "npp.usual_range", "psd.declare", "psd.combination_limit",
    "cat1.custodial_required", "cat2.custodial_unless", "cat2.substantial_compelling_test", "mm.special_reason",
    "mmnpp.gross_violence_4y", "mmnpp.protected_officials", "order.cco", "order.mtmo", "order.fine",
    "order.adjourned_undertaking", "order.discharge", "order.dismissal", "order.cco.boulton_principles_detail",
    "order.cco.combination_parsimony", "order.cco.combination_time_served", "s6aaa.statement_required",
    "s6aaa.method", "anc.alcohol_exclusion_order",
]
SYNTHESIS_STAGES = ["disposition", "synthesis", "ancillary", "framework"]


@dataclass
class ProposalResult:
    proposal: Proposal | None
    item_ids: list[str]
    invalid: list[str] = field(default_factory=list)
    error: str = ""


def _input_text(kb, facts, fw, stages, item_ids, quotes_for, comparables: Comparables | None = None) -> str:
    cases = comparables.block() if comparables else ""
    return (
        f"## Case details\n{case_details_text(facts.case, kb)}\n\n"
        f"## Summary\n{facts.summary}\n\n"
        f"## Confirmed facts (cite by id in fact_ids)\n{facts.facts_text()}\n\n"
        f"## Legal framework (worked out in code — binding on the proposal)\n{fw.text()}\n\n"
        f"## The four stage analyses\n{stages_text(kb, stages)}\n\n"
        + (f"{cases}\n\n" if cases else "")
        + f"## Knowledge items from the Manual on dispositions and formulation (cite by id)\n"
        f"{kb.prompt_block(item_ids, quotes_for)}"
    )


def _allowed(kb, fw, stages, item_ids, comparables: Comparables | None = None):
    """Ids the synthesis may cite: the items it was shown, the ids the stage
    analyses (also in its input) cited, and the comparable cases it was shown."""
    items, passages = set(item_ids), set()
    for st in stages.values():
        for pt in (st.analysis.points if st.analysis else []):
            items |= set(pt.item_ids)
            passages |= set(pt.passage_ids)
    cases = comparables.ids if comparables and comparables.block() else set()
    return items, passages, cases


def _clean(kb, proposal: Proposal, allowed_items, allowed_passages, fact_ids, allowed_cases) -> list[str]:
    invalid = clean_citations(proposal.reasons, allowed_items, allowed_passages, fact_ids, allowed_cases)
    for order in proposal.ancillary_orders:
        bad = [i for i in order.item_ids if i not in allowed_items]
        invalid += [f"item {b}" for b in bad]
        order.item_ids = [i for i in order.item_ids if i in allowed_items]
    proposal.flags_shown = [f for f in proposal.flags_shown if kb.exists(f)]
    return invalid


def propose(llm: LLM, kb: KnowledgeBase, retriever: Retriever, facts: ConfirmedFacts, fw: Framework,
            stages: dict[str, StageResult], query, comparables: Comparables | None = None
            ) -> tuple[ProposalResult, CallRecord | None]:
    """AI call 6."""
    item_ids, quotes_for = select_items(kb, retriever, SYNTHESIS_STAGES, facts.case.offence_id, query,
                                        config.ITEMS_FOR_SYNTHESIS, always=SYNTHESIS_ALWAYS)
    result = ProposalResult(proposal=None, item_ids=item_ids)
    instructions = prompts.COMMON_RULES + "\n\n" + prompts.SYNTHESIS
    try:
        proposal, record = llm.structured("Synthesis — proposed sentence", "synthesis", instructions,
                                          _input_text(kb, facts, fw, stages, item_ids, quotes_for, comparables),
                                          Proposal)
    except LLMError as exc:
        result.error = str(exc)
        return result, exc.record
    items, passages, cases = _allowed(kb, fw, stages, item_ids, comparables)
    result.invalid = _clean(kb, proposal, items, passages, facts.fact_ids(), cases)
    result.proposal = proposal
    return result, record


def problems_text(checks: list[CheckResult], audit: AuditResult | None) -> str:
    """The errors and cautions found, for the revision call."""
    lines = []
    for c in checks:
        if c.outcome in ("error", "caution", "unresolved"):
            lines.append(f"- [hard check {c.check_id} — {c.outcome}] {c.title}: {c.message}")
    if audit:
        for f in audit.findings:
            if f.outcome in ("error", "caution"):
                lines.append(f"- [audit {f.check_id} — {f.outcome}] {f.explanation} Fix: {f.fix}")
    return "\n".join(lines) or "- (none)"


def revise(llm: LLM, kb: KnowledgeBase, facts: ConfirmedFacts, fw: Framework, stages: dict[str, StageResult],
           previous: ProposalResult, checks: list[CheckResult], audit: AuditResult | None,
           comparables: Comparables | None = None) -> tuple[ProposalResult, CallRecord | None]:
    """The revision call (AI call 8, only when errors were found)."""
    item_ids = previous.item_ids
    quotes_for = set(item_ids[:config.QUOTES_FOR_TOP_ITEMS])
    result = ProposalResult(proposal=None, item_ids=item_ids)
    previous_json = previous.proposal.model_dump_json(indent=1) if previous.proposal else "{}"
    text = (_input_text(kb, facts, fw, stages, item_ids, quotes_for, comparables)
            + f"\n\n## Your previous proposal\n{previous_json}"
            + f"\n\n## Problems found by the app's checks and the audit\n{problems_text(checks, audit)}")
    instructions = prompts.COMMON_RULES + "\n\n" + prompts.SYNTHESIS + "\n\n" + prompts.REVISION
    try:
        proposal, record = llm.structured("Revision — proposed sentence", "synthesis", instructions, text, Proposal)
    except LLMError as exc:
        result.error = str(exc)
        return result, exc.record
    items, passages, cases = _allowed(kb, fw, stages, item_ids, comparables)
    result.invalid = _clean(kb, proposal, items, passages, facts.fact_ids(), cases)
    result.proposal = proposal
    return result, record


def proposal_summary_json(p: Proposal) -> str:
    """A compact copy of the proposal (without the long reasons) for the audit."""
    data = json.loads(p.model_dump_json())
    data.pop("reasons", None)
    return json.dumps(data, indent=1)
