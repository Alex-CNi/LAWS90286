"""
core/pipeline.py — THE ORDER OF THE STEPS, AND WHAT ONE RUN REMEMBERS.

One "run" = one case from the form to the report. Its results are kept in a Run
object (stored in st.session_state by the page), so Streamlit's reruns never
repeat an AI call; starting a new run starts a fresh Run, so every run makes its
own calls (the assignment's "at least 5 AI calls in each run").

    Step 1  Case input                 code      (core/facts.py: validate); with the simple input, an AI
                                                 call first sorts a plain description into the form (sort_description)
    Step 2  Facts extraction           AI call 1 (core/facts.py)
            User confirms the facts
    Step 3  Legal framework + gates    code      (core/framework.py)
    Step 4  Four stage analyses        AI calls 2–5, in parallel (core/stages.py); the most similar cases
                                                 from the case database go to the comparables stage (core/cases.py)
    Step 5  Proposed sentence          AI call 6 (core/synthesis.py)
            Hard checks                code      (core/checks.py)
    Step 6  Audit of the reasoning     AI call 7 (core/audit.py)
            If errors: revision + re-check + re-audit (AI calls 8–9), once
    Step 7  Report                     code      (core/report.py)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace

import numpy as np

from core import config
from core.audit import AuditOutcome, run_audit
from core.cases import CaseDB, Comparables, case_query_text, related_offences, select_comparables
from core.checks import run_hard_checks
from core.facts import (CHARGE_SPECIFIC_FIELDS, SORT_STEP, CaseInput, ConfirmedFacts, check_quote, extract_facts,
                        is_adverse, suggestions_for_unknowns, verify_quotes)
from core.framework import Framework, build_framework
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord, LLMError
from core.results import CheckResult, errors
from core.retrieval import Retriever
from core.schemas import FactsExtraction
from core.stages import STAGE_ORDER, StageResult, all_query_texts, run_all_stages
from core.synthesis import ProposalResult, propose, revise


@dataclass
class Attempt:
    """One proposal with its checks and audit (the first, then any revision)."""
    proposal: ProposalResult
    checks: list[CheckResult] = field(default_factory=list)
    audit: AuditOutcome | None = None


@dataclass
class Run:
    case: CaseInput
    models: dict
    efforts: dict
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    started: str = field(default_factory=lambda: time.strftime("%Y-%m-%d %H:%M"))
    extraction: FactsExtraction | None = None
    fact_rows: list[dict] = field(default_factory=list)
    suggestions: dict = field(default_factory=dict)
    facts: ConfirmedFacts | None = None
    framework: Framework | None = None
    queries: dict = field(default_factory=dict)          # stage -> vector (or text); "cases" -> the case database query
    retrieval_mode: str = ""
    comparables: Comparables | None = None               # the cases chosen from the case database (None: no database)
    stages: dict[str, StageResult] = field(default_factory=dict)
    attempts: list[Attempt] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)    # notices for the user (e.g. a search fallback), shown on the page
    suggestion_choices: dict = field(default_factory=dict)  # field -> True/False (the user's ticks, kept across pages)
    facts_version: int = 0                               # bumps on each confirmation (resets the facts editor)
    revision_error: str = ""                             # a failed revision: the first proposal stands
    report_cache: dict = field(default_factory=dict)     # rendered report for the current attempt
    entered: CaseInput | None = None                     # the case exactly as entered in the form (before any suggestion)
    discarded_calls: list[CallRecord] = field(default_factory=list)  # calls whose results were replaced (not counted)
    reused_calls: list[CallRecord] = field(default_factory=list)     # calls made for an earlier run of this case whose
                                                                     # results this run reuses (counted there, not here)
    simple: bool = False                                 # the case came from the simple input's sorting

    def __post_init__(self):
        if self.entered is None:
            self.entered = CaseInput.from_dict(self.case.to_dict())

    # ── progress ────────────────────────────────────────────────────────────
    @property
    def ai_calls(self) -> int:
        """Successful AI calls whose results this run uses (embeddings are not counted)."""
        return sum(1 for c in self.calls if c.kind == "ai" and c.ok)

    @property
    def current(self) -> Attempt | None:
        return self.attempts[-1] if self.attempts else None

    @property
    def stages_done(self) -> bool:
        return all(k in self.stages and self.stages[k].analysis for k in STAGE_ORDER)

    @property
    def finished(self) -> bool:
        a = self.current
        return bool(a and a.proposal.proposal and a.audit and a.audit.result) and not self.needs_revision()

    def needs_revision(self) -> bool:
        a = self.current
        if self.revision_error or not a or not a.proposal.proposal or not a.audit or not a.audit.result:
            return False
        has_errors = bool(errors(a.checks)) or bool(a.audit.errors())
        return has_errors and (len(self.attempts) - 1) < config.MAX_REVISIONS

    def log(self, record: CallRecord | None):
        if record is not None:
            self.calls.append(record)


# Calls made before the facts are confirmed: a second confirmation keeps them (simple input: the sorting call).
BEFORE_CONFIRMATION = {"Facts extraction", SORT_STEP}


# ─────────────────────────────────────────────────────────────────────────────
# The steps. Each does nothing if its result already exists.
# ─────────────────────────────────────────────────────────────────────────────
def step_extract(run: Run, llm: LLM, kb: KnowledgeBase) -> None:
    """Step 2 — AI call 1."""
    if run.extraction is not None:
        return
    try:
        extraction, record = extract_facts(llm, run.case, kb)
    except LLMError as exc:
        run.log(exc.record)
        raise
    run.log(record)
    run.extraction = extraction
    run.fact_rows = verify_quotes(extraction, run.case.narrative)
    run.suggestions = suggestions_for_unknowns(run.case, extraction, kb)
    # Suggested values that count against the offender start unticked, and so does any answer to a charge's own
    # question ("no" or "neither" too: it settles the fact a minimum, a category or a higher maximum turns on, and
    # removes the reading "if proved"). With the simple input every suggestion starts unticked: the open questions
    # were left open because the description gives no words for them.
    run.suggestion_choices = {f: not run.simple and not is_adverse(f, v) and f not in CHARGE_SPECIFIC_FIELDS
                              for f, v in run.suggestions.items()}


def step_confirm(run: Run, kb: KnowledgeBase, fact_rows: list[dict], accepted: dict) -> None:
    """The user's confirmation, then Step 3 (framework, in code).
    accepted: the suggested values ticked now ({field: value}). The case is rebuilt
    each time from the case as entered plus those values, so unticking a
    suggestion after an earlier confirmation withdraws it."""
    ex = run.extraction
    case = CaseInput.from_dict(run.entered.to_dict())
    for name, value in accepted.items():
        setattr(case, name, value)
    run.case = case
    if run.stages or run.attempts or run.queries:
        # Confirming again discards the later steps: their calls are kept in the log but no longer counted.
        run.discarded_calls += [replace(c, note="replaced when the facts were confirmed again"
                                        + (f" ({c.note})" if c.note else ""))
                                for c in run.calls if c.step not in BEFORE_CONFIRMATION]
        run.calls = [c for c in run.calls if c.step in BEFORE_CONFIRMATION]
    for row in fact_rows:                     # re-check quotes after the user's edits
        row["quote_verified"] = check_quote(row.get("source_quote", ""), case.narrative)
    run.facts = ConfirmedFacts(
        case=case, summary=ex.summary, facts=fact_rows,
        missing=[m.model_dump() for m in ex.missing], scope_warnings=list(ex.scope_warnings),
        conflicts=list(ex.conflicts_with_form), charge_fit=ex.charge_fit, applied_suggestions=dict(accepted))
    run.framework = build_framework(kb, run.facts)
    # Anything confirmed afresh invalidates later steps.
    run.stages, run.attempts, run.queries, run.report_cache, run.messages = {}, [], {}, {}, []
    run.comparables = None
    run.revision_error = ""
    run.facts_version += 1


def step_queries(run: Run, llm: LLM, kb: KnowledgeBase, retriever: Retriever, case_db: CaseDB | None = None,
                 exclude_case_ids=()) -> None:
    """Search queries for the stages and the synthesis, and the most similar cases
    from the case database. One embeddings request (with the user's key) covers
    the queries that can use vectors: the knowledge-base queries when its index
    exists, and the case query when every case in use has an embedding (otherwise
    the cases are ranked by keywords on the query text)."""
    if run.queries:
        return
    texts = all_query_texts(kb, run.facts)
    if case_db is not None and case_db.usable():
        texts["cases"] = case_query_text(kb, run.facts)
    run.retrieval_mode = retriever.description
    run.queries = dict(texts)
    embed = [k for k in texts if (k != "cases" and retriever.uses_vectors) or (k == "cases" and case_db.has_vectors())]
    if embed:
        try:
            vectors, record = llm.embed("Search queries (embeddings)", [texts[k] for k in embed])
        except LLMError as exc:
            # The key cannot use the embeddings endpoint (or the request failed): rank by keywords
            # instead of stopping the run, and say so on screen, in the run log and in the report.
            run.log(exc.record)
            if retriever.uses_vectors:
                run.retrieval_mode = f"keyword ranking, because the embeddings request failed ({exc})"
            what = " and ".join(x for x in ("the Manual passages" if retriever.uses_vectors else "",
                                            "the comparable cases" if "cases" in embed else "") if x)
            run.messages.append(f"Keyword ranking was used for {what}: the embeddings request failed ({exc}).")
        else:
            run.log(record)
            run.queries.update({k: vectors[i] for i, k in enumerate(embed)})
    if case_db is not None:
        vector = run.queries.get("cases")
        run.comparables = select_comparables(
            case_db, run.facts.case.offence_id, query_vector=vector if isinstance(vector, np.ndarray) else None,
            query_text=texts.get("cases", ""), case_name=run.case.case_name, narrative=run.case.narrative,
            exclude_ids=exclude_case_ids, related=related_offences(kb, run.facts.case.offence_id))
        if case_db.usable() and not case_db.has_vectors():
            missing = sum(1 for c in case_db.usable() if c.id not in case_db.vectors)
            run.messages.append(f"Comparable cases were ranked by keywords: {missing} case(s) in use have no "
                                "embeddings (embed them on the Case database page).")


def step_stages(run: Run, llm: LLM, kb: KnowledgeBase, retriever: Retriever,
                parallel: bool = config.PARALLEL_STAGES, case_db: CaseDB | None = None,
                exclude_case_ids=()) -> None:
    """Step 4 — AI calls 2–5 (only the stages not yet done)."""
    try:
        step_queries(run, llm, kb, retriever, case_db, exclude_case_ids)
    except LLMError as exc:
        run.log(exc.record)
        raise
    todo = [k for k in STAGE_ORDER if not (k in run.stages and run.stages[k].analysis)]
    if not todo:
        return
    results, records = run_all_stages(llm, kb, retriever, run.facts, run.framework, run.queries, only=todo,
                                      parallel=parallel, comparables=run.comparables)
    for r in records:
        run.log(r)
    run.stages.update(results)
    failed = [r for r in results.values() if r.error]
    if failed:
        raise LLMError("; ".join(f"{r.title}: {r.error}" for r in failed))


def step_propose(run: Run, llm: LLM, kb: KnowledgeBase, retriever: Retriever) -> None:
    """Step 5 — AI call 6, then the hard checks in code."""
    if run.current and run.current.proposal.proposal:
        return
    result, record = propose(llm, kb, retriever, run.facts, run.framework, run.stages, run.queries["synthesis"],
                             run.comparables)
    run.log(record)
    if result.error:
        raise LLMError(result.error)
    attempt = Attempt(proposal=result)
    attempt.checks = run_hard_checks(kb, run.facts, run.framework, result.proposal)
    run.attempts = [attempt]


def step_audit(run: Run, llm: LLM, kb: KnowledgeBase) -> None:
    """Step 6 — AI call 7 (or the re-audit after a revision)."""
    a = run.current
    if a is None or (a.audit and a.audit.result):
        return
    label = "Audit of the reasoning" if len(run.attempts) == 1 else "Re-audit after revision"
    outcome, record = run_audit(llm, kb, run.facts, run.framework, run.stages, a.proposal, a.checks, step=label,
                                comparables=run.comparables)
    run.log(record)
    a.audit = outcome
    if outcome.error:
        raise LLMError(outcome.error)


def step_revise(run: Run, llm: LLM, kb: KnowledgeBase) -> None:
    """The revision loop — AI call 8 (+ re-checks in code, and the re-audit
    in step_audit). Runs only when errors were found."""
    if not run.needs_revision():
        return
    prev = run.current
    result, record = revise(llm, kb, run.facts, run.framework, run.stages, prev.proposal, prev.checks,
                            prev.audit.result if prev.audit else None, run.comparables)
    run.log(record)
    if result.error:
        # The first proposal (with its checks and audit) stands; the report says so.
        run.revision_error = result.error
        return
    attempt = Attempt(proposal=result)
    attempt.checks = run_hard_checks(kb, run.facts, run.framework, result.proposal)
    run.attempts.append(attempt)


def run_remaining(run: Run, llm: LLM, kb: KnowledgeBase, retriever: Retriever, progress=None,
                  parallel: bool = config.PARALLEL_STAGES, case_db: CaseDB | None = None,
                  exclude_case_ids=()) -> None:
    """Steps 4–6 (and the revision loop) one after the other. case_db: the user's case database (its most
    similar cases go to the comparables stage, the synthesis and the audit); exclude_case_ids: cases the user
    left out of the comparisons (e.g. the case being estimated)."""
    def say(msg):
        if progress:
            progress(msg)
    say("Step 4 — analysing gravity, the offender, purposes and comparables (AI calls 2–5)…")
    step_stages(run, llm, kb, retriever, parallel=parallel, case_db=case_db, exclude_case_ids=exclude_case_ids)
    say("Step 5 — proposing the sentence (AI call 6) and running the hard checks…")
    step_propose(run, llm, kb, retriever)
    say("Step 6 — auditing the reasoning (AI call 7)…")
    step_audit(run, llm, kb)
    while run.needs_revision():
        say("Errors found — revising the proposal (AI call 8) and checking it again…")
        step_revise(run, llm, kb)
        if run.revision_error:
            say(f"The revision failed ({run.revision_error}); the first proposal stands.")
            break
        say("Re-auditing the revised proposal (AI call 9)…")
        step_audit(run, llm, kb)
