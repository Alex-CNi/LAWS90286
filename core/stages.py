"""
core/stages.py — STEP 4: THE FOUR STAGE ANALYSES (AI calls 2–5, run in parallel).

For each stage (gravity, offender, purposes, comparables — defined in
core/prompts.py STAGES):
  1. choose the knowledge items: every item linked to the offence, plus the
     items most similar to the facts (cosine similarity, core/retrieval.py),
     up to ITEMS_PER_STAGE;
  2. for "comparables", also find the most similar Manual passages, and add the
     most similar cases from the user's case database (core/cases.py);
  3. ask the analysis model for points that cite item ids and fact ids;
  4. check every cited id exists in what the model was shown (invented ids
     are removed and reported).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from core import config, prompts
from core.cases import Comparables
from core.facts import ConfirmedFacts, case_details_text
from core.framework import Framework
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord, LLMError
from core.retrieval import Retriever
from core.schemas import StageAnalysis

STAGE_ORDER = ["gravity", "offender", "purposes", "comparables"]


@dataclass
class StageResult:
    key: str
    title: str
    analysis: StageAnalysis | None
    item_ids: list[str]                       # items the model was shown
    passage_ids: list[str] = field(default_factory=list)
    passage_scores: dict = field(default_factory=dict)
    standards: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)   # cited ids that were not supplied (removed)
    proof_warnings: list[str] = field(default_factory=list)  # adverse facts treated as established without proof
    error: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 1. Search queries (one per stage, plus one for the synthesis)
# ─────────────────────────────────────────────────────────────────────────────
def query_text(stage_key: str, kb: KnowledgeBase, facts: ConfirmedFacts) -> str:
    """The text whose similarity to each item decides what the stage is shown."""
    if stage_key == "synthesis":
        hint = ("sentencing disposition, imprisonment, community correction order, non-parole period, "
                "instinctive synthesis, statutory statements, ancillary orders")
    else:
        hint = prompts.STAGES[stage_key]["query"]
    facts_lines = " ".join(f["text"] for f in facts.facts)[:3000]
    return f"{hint}\n{kb.offence_label(facts.case.offence_id)}\n{facts.summary}\n{facts_lines}"


def all_query_texts(kb: KnowledgeBase, facts: ConfirmedFacts) -> dict[str, str]:
    return {k: query_text(k, kb, facts) for k in STAGE_ORDER + ["synthesis"]}


# ─────────────────────────────────────────────────────────────────────────────
# 2. Choosing what each stage is shown
# ─────────────────────────────────────────────────────────────────────────────
def select_items(kb: KnowledgeBase, retriever: Retriever, stages: list[str], offence_id: str, query,
                 limit: int, always: list[str] | None = None) -> tuple[list[str], set[str]]:
    """Items to show: the always-included ones, then the most similar. Returns
    (ids in order, ids that also get their quotes)."""
    candidates = kb.candidates(stages, offence_id)
    linked = [i for i in candidates if offence_id in (kb.meta[i].get("offence_ids") or [])]
    pinned = [i for i in (always or []) if kb.exists(i)] + [i for i in linked if i not in (always or [])]
    rest = [i for i in candidates if i not in pinned]
    ranked = [i for i, _ in retriever.rank("item", query, rest, max(0, limit - len(pinned)))]
    ids = pinned + ranked
    quotes_for = set(pinned) | set(ranked[:config.QUOTES_FOR_TOP_ITEMS])
    return ids, quotes_for


def standards_for(kb: KnowledgeBase, stages: list[str]) -> list[str]:
    """The critic audit standards that will later be applied to this stage."""
    return [i for i in kb.audit_items("critic") if set(kb.meta[i].get("stage") or []) & set(stages)]


def standards_block(kb: KnowledgeBase, ids: list[str]) -> str:
    return "\n".join(f"[{i}] {kb.title(i)}: {kb.statement(i)}" for i in ids)


# ─────────────────────────────────────────────────────────────────────────────
# 3. One stage
# ─────────────────────────────────────────────────────────────────────────────
def run_stage(key: str, llm: LLM, kb: KnowledgeBase, retriever: Retriever, facts: ConfirmedFacts,
              fw: Framework, query, comparables: Comparables | None = None) -> tuple[StageResult, CallRecord | None]:
    spec = prompts.STAGES[key]
    offence_id = facts.case.offence_id
    item_ids, quotes_for = select_items(kb, retriever, spec["kb_stages"], offence_id, query, config.ITEMS_PER_STAGE)
    standards = standards_for(kb, spec["kb_stages"])
    result = StageResult(key=key, title=spec["title"], analysis=None, item_ids=item_ids, standards=standards)

    passages_text, cases_text, allowed_cases = "", "", set()
    if key == "comparables":
        ranked = retriever.rank("chunk", query, kb.comparable_passages(), config.PASSAGES_FOR_COMPARABLES)
        result.passage_ids = [c for c, _ in ranked]
        result.passage_scores = {c: round(s, 3) for c, s in ranked}
        passages_text = ("\n\n## Most similar Manual passages (cite by id in passage_ids)\n"
                         + kb.passage_block(result.passage_ids))
        if comparables and comparables.block():               # the user's case database (only this stage sees it)
            cases_text = "\n\n" + comparables.block()
            allowed_cases = comparables.ids

    input_text = (
        f"## Stage\n{spec['title']}\n\n"
        f"## Case details\n{case_details_text(facts.case, kb)}\n\n"
        f"## Summary\n{facts.summary}\n\n"
        f"## Confirmed facts (cite by id in fact_ids)\n{facts.facts_text()}\n\n"
        f"## Legal framework (worked out in code from the Manual's offence register)\n{fw.text()}\n\n"
        f"## Audit standards this stage will be checked against\n{standards_block(kb, standards)}\n\n"
        f"## Knowledge items from the Manual (cite by id in item_ids)\n"
        f"{kb.prompt_block(item_ids, quotes_for)}"
        f"{passages_text}"
        f"{cases_text}"
    )
    instructions = prompts.COMMON_RULES + "\n\n" + prompts.STAGE_COMMON + "\n\n" + spec["instructions"]
    try:
        analysis, record = llm.structured(f"Stage analysis — {spec['title']}", "analysis",
                                          instructions, input_text, StageAnalysis)
    except LLMError as exc:
        result.error = str(exc)
        return result, exc.record
    allowed_items = set(item_ids) | set(standards)            # only what the model was shown
    result.invalid = clean_citations(analysis.points, allowed_items, set(result.passage_ids), facts.fact_ids(),
                                     allowed_cases)
    result.proof_warnings = proof_warnings(analysis.points, facts)
    bad_flags = [f for f in analysis.flags_engaged if not kb.exists(f)]
    analysis.flags_engaged = [f for f in analysis.flags_engaged if kb.exists(f)]
    result.invalid += [f"flag {f}" for f in bad_flags]
    result.analysis = analysis
    return result, record


def proof_warnings(points, facts: ConfirmedFacts) -> list[str]:
    """audit.facts_and_proof: a point that increases the sentence and treats as
    established a fact whose basis is alleged, disputed or assumed."""
    basis = {f["id"]: f for f in facts.facts}
    out = []
    for pt in points:
        if pt.effect != "increases" or pt.proof != "established":
            continue
        for fid in pt.fact_ids:
            f = basis.get(fid)
            if f and f["basis"] in ("alleged", "disputed", "assumption"):
                out.append(f"“{pt.heading}” treats {fid} (“{f['text']}”) as established, but its basis is "
                           f"{f['basis']}: it needs proof beyond reasonable doubt.")
    return out


def clean_citations(paragraphs, allowed_items: set[str], allowed_passages: set[str], fact_ids: set[str],
                    allowed_cases: set[str] = frozenset()) -> list[str]:
    """Remove ids the model was not given (invented or mistyped); return them."""
    invalid = []
    for para in paragraphs:
        for attr, allowed in (("item_ids", allowed_items), ("passage_ids", allowed_passages), ("fact_ids", fact_ids),
                              ("case_ids", allowed_cases)):
            ids = [str(i).strip().strip("[]").strip() for i in getattr(para, attr)]     # "[C3]" -> "C3"
            setattr(para, attr, ids)
            bad = [i for i in ids if i not in allowed]
            if bad:
                invalid += [f"{attr[:-4]} {b}" for b in bad]
                setattr(para, attr, [i for i in ids if i in allowed])
    return invalid


# ─────────────────────────────────────────────────────────────────────────────
# 4. All four stages, in parallel (each is independent of the others)
# ─────────────────────────────────────────────────────────────────────────────
def run_all_stages(llm: LLM, kb: KnowledgeBase, retriever: Retriever, facts: ConfirmedFacts, fw: Framework,
                   queries: dict, only: list[str] | None = None, parallel: bool = config.PARALLEL_STAGES,
                   comparables: Comparables | None = None) -> tuple[dict[str, StageResult], list[CallRecord]]:
    keys = only or STAGE_ORDER
    results, records = {}, []
    with ThreadPoolExecutor(max_workers=len(keys) if parallel else 1) as pool:
        futures = {k: pool.submit(run_stage, k, llm, kb, retriever, facts, fw, queries[k], comparables) for k in keys}
        for k in keys:
            res, rec = futures[k].result()
            results[k] = res
            if rec is not None:
                records.append(rec)
    return results, records


def stages_text(kb: KnowledgeBase, stages: dict[str, StageResult]) -> str:
    """The stage analyses written out for the synthesis and the audit."""
    parts = []
    for key in STAGE_ORDER:
        st = stages.get(key)
        if not st or not st.analysis:
            continue
        lines = [f"### {st.title}"]
        for p in st.analysis.points:
            cites = ", ".join(p.item_ids + p.passage_ids)
            lines.append(f"- {p.heading} [{p.effect}; {p.weight}; proof: {p.proof}; facts {', '.join(p.fact_ids) or '—'}; "
                         f"items {cites or '—'}" + (f"; cases {', '.join(p.case_ids)}" if p.case_ids else "")
                         + f"]: {p.analysis}")
        lines.append(f"Conclusion: {st.analysis.conclusion}")
        if st.analysis.gaps:
            lines.append("Gaps: " + "; ".join(st.analysis.gaps))
        if st.analysis.flags_engaged:
            lines.append("Flags: " + ", ".join(st.analysis.flags_engaged))
        for w in st.proof_warnings:     # the app's proof check on this analysis (audit.facts_and_proof)
            lines.append(f"PROOF WARNING (app check): {w} Do not rely on it unless it is proved.")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
