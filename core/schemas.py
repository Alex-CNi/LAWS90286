"""
core/schemas.py — THE SHAPE OF EVERY AI ANSWER.

Each AI call must answer in one of these shapes (OpenAI "Structured Outputs":
the model is forced to return JSON that fits the Pydantic model, as in the
Day 3 code). Code then checks the content.

Rules for editing these models (OpenAI's strict mode):
  * every field must be listed without a default value;
  * use Optional[...] for "may be empty" (the model returns null);
  * use Literal[...] for fixed choices, list[...] for repeats; no dicts.
If you add a field, also tell the model about it in core/prompts.py.

Contents
  1. Facts extraction          (AI call 1)
  2. Stage analysis            (AI calls 2–5)
  3. Proposed sentence         (AI call 6, and any revision)
  4. Audit                     (AI call 7, and any re-audit)
  5. Chunking a case document  (Case database page: one call per part of the document)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from core import config

YesNoUnknown = Literal["yes", "no", "unknown"]

# ─────────────────────────────────────────────────────────────────────────────
# 1. Facts extraction (AI call 1)
# ─────────────────────────────────────────────────────────────────────────────
FactTopic = Literal[
    "offence_circumstances", "harm_and_impact", "victim", "offender_background",
    "criminal_history", "status_at_offence", "mental_health", "substances",
    "character_and_remorse", "cooperation", "delay", "custody_and_deportation",
    "co_offenders", "other_offending", "reports", "plea", "procedure", "other",
]
FactBasis = Literal["agreed_or_admitted", "evidence", "alleged", "disputed", "assumption"]


class ExtractedFact(BaseModel):
    id: str = Field(description="F1, F2, F3 … in order")
    text: str = Field(description="One fact in plain words, as the material states it (no inference)")
    topic: FactTopic
    bearing: Literal["aggravating", "mitigating", "neutral", "unclear"]
    basis: FactBasis = Field(description="How the material establishes the fact")
    source_quote: str = Field(description="The exact words of the case narrative the fact comes from, "
                                          "copied verbatim (at most 40 words); empty string if it comes from the form")


class MissingFact(BaseModel):
    question: str = Field(description="What the user should supply")
    why_it_matters: str
    assumption: str = Field(description="What the estimate assumes until the user answers")


class SuggestedValues(BaseModel):
    """Values the narrative supports for form fields (used only where the user
    left the field as 'unknown')."""
    victim_protected_worker: YesNoUnknown = Field(description="Victim was an emergency worker, custodial officer "
                                                              "or youth justice custodial worker on duty")
    offender_knew_or_reckless_as_to_status: YesNoUnknown
    intoxicated_by_liquor: YesNoUnknown
    intoxication_contributed: YesNoUnknown = Field(description="Intoxication by liquor significantly contributed to the offence")
    mental_impairment_evidence: YesNoUnknown = Field(description="Expert evidence of a mental impairment at the offence or at sentence")
    cco_consent: YesNoUnknown
    presentence_report: Literal["available", "ordered", "none", "unknown"]
    cooperation: Literal["none", "admissions_only", "past_assistance", "undertaking", "unknown"]
    prior_convictions: Literal["none", "some", "relevant_or_significant", "unknown"]
    arising_from_driving: YesNoUnknown


class FactsExtraction(BaseModel):
    summary: str = Field(description="A neutral summary of the case in 3–6 sentences")
    facts: list[ExtractedFact]
    missing: list[MissingFact]
    conflicts_with_form: list[str]
    scope_warnings: list[str] = Field(description="Equivocal plea, element denied, possible defence, child, "
                                                  "Commonwealth offence, wrong court — empty if none")
    charge_fit: str = Field(description="Whether the facts fit the charge selected; the app sentences only for that charge")
    suggested: SuggestedValues


# ─────────────────────────────────────────────────────────────────────────────
# 2. Stage analysis (AI calls 2–5: gravity, offender, purposes, comparables)
# ─────────────────────────────────────────────────────────────────────────────
class AnalysisPoint(BaseModel):
    heading: str
    analysis: str
    effect: Literal["increases", "decreases", "neutral", "context", "uncertain"] = Field(
        description="Effect on the sentence (or on gravity/culpability)")
    weight: Literal["significant", "moderate", "slight", "none", "uncertain"]
    item_ids: list[str] = Field(description="Ids of the knowledge items relied on (only ids supplied)")
    passage_ids: list[str] = Field(description="Ids of the Manual passages relied on (only ids supplied)")
    fact_ids: list[str] = Field(description="Ids of the confirmed facts relied on (F1, F2 …)")
    case_ids: list[str] = Field(description="Ids of the comparable cases from the case database relied on (C1, C2 …); "
                                            "empty if none were supplied")
    proof: Literal["established", "needs_proof_beyond_reasonable_doubt", "needs_evidence_on_balance",
                   "not_applicable"]


class StageAnalysis(BaseModel):
    points: list[AnalysisPoint]
    conclusion: str = Field(description="The overall assessment for this stage, in words (no sentence figures)")
    gaps: list[str] = Field(description="Facts or points the Manual leaves open that would change this assessment")
    flags_engaged: list[str] = Field(description="Ids of flag.* items that bear on this stage")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Proposed sentence (AI call 6, and the revision call)
# ─────────────────────────────────────────────────────────────────────────────
DispositionType = Literal["imprisonment", "imprisonment_and_cco", "cco", "mtmo", "fine",
                          "adjourned_undertaking", "discharge", "dismissal"]

CCOConditionKind = Literal[
    "unpaid_community_work", "treatment_and_rehabilitation", "supervision", "non_association",
    "residence_restriction_or_exclusion", "place_or_area_exclusion", "curfew", "alcohol_exclusion",
    "judicial_monitoring", "electronic_monitoring", "bond", "justice_plan",
    "restitution_or_compensation", "other",
]


class CCOCondition(BaseModel):
    kind: CCOConditionKind
    details: str
    hours: Optional[int] = Field(description="Unpaid community work: number of hours")
    completion_months: Optional[int] = Field(description="Unpaid community work: months within which the hours are to be worked")
    hours_per_day: Optional[float] = Field(description="Curfew: hours per day")
    duration_months: Optional[int] = Field(description="Months the condition runs (curfew, monitoring, justice plan)")
    monitors: Optional[Literal["curfew", "place_or_area_exclusion", "other"]] = Field(
        description="Electronic monitoring: the condition it monitors")


class CCOTerms(BaseModel):
    months: int
    conditions: list[CCOCondition]
    commences: Literal["on_making", "later_date", "on_release_from_prison", "not_stated"]
    months_after_order: Optional[int] = Field(description="If commences is later_date: months after the order")


class SpecialReason(BaseModel):
    found: bool
    limb: Optional[Literal["assistance", "mental_impairment", "csto_or_rto", "substantial_and_compelling"]]
    evidential_basis: str
    stated_in_writing: bool


class Category2Exception(BaseModel):
    relied_on: bool
    exception: Optional[Literal["assistance", "impaired_culpability", "impaired_burden", "therapeutic_order",
                                "substantial_and_compelling"]]
    evidential_basis: str


class NotionalSentence(BaseModel):
    """Section 6AAA: the sentence that would have been imposed but for the plea."""
    stated: bool
    type: Optional[DispositionType]
    term_months: Optional[int]
    npp_months: Optional[int]
    cco_months: Optional[int]
    unpaid_hours: Optional[int]
    fine_penalty_units: Optional[int]
    conditions_reduced: Optional[str] = Field(description="CCO conditions (other than length and hours) the plea reduced")


class Cooperation(BaseModel):
    less_severe_sentence: bool
    basis: Literal["none", "past_assistance", "undertaking"]
    announced: bool
    undertaking_recorded: bool


class AncillaryOrder(BaseModel):
    type: Literal["alcohol_exclusion_order", "licence_cancellation_and_disqualification",
                  "licence_disqualification_from_driving", "licence_disqualification_from_obtaining",
                  "compensation", "restitution", "forfeiture", "other"]
    status: Literal["mandatory", "available", "conditional"]
    months: Optional[int]
    details: str
    item_ids: list[str]


class Band(BaseModel):
    disposition_types: list[DispositionType]
    term_low_months: Optional[int]
    term_high_months: Optional[int]
    npp_low_months: Optional[int]
    npp_high_months: Optional[int]
    cco_low_months: Optional[int]
    cco_high_months: Optional[int]
    main_uncertainties: list[str]


class ReasonParagraph(BaseModel):
    heading: str
    text: str
    item_ids: list[str]
    passage_ids: list[str]
    fact_ids: list[str]
    case_ids: list[str] = Field(description="Ids of the comparable cases relied on (C1, C2 …); empty if none")


class Proposal(BaseModel):
    disposition_type: DispositionType
    term_months: Optional[int] = Field(description="Imprisonment term in months (combination: the imprisonment part)")
    npp_months: Optional[int] = Field(description="Non-parole period in months, or null")
    npp_reason: Optional[str] = Field(description="Why no non-parole period is fixed for a term of 24 months or more, "
                                                  "or why the non-parole period is unusual")
    cco: Optional[CCOTerms]
    fine_penalty_units: Optional[int]
    undertaking_months: Optional[int]
    conviction_recorded: Optional[bool]
    special_reason: SpecialReason
    category2_exception: Category2Exception
    plea_reduced_sentence: bool = Field(description="True if the guilty plea produced a less severe sentence")
    s6aaa: NotionalSentence
    cooperation: Cooperation
    ancillary_orders: list[AncillaryOrder]
    band: Band
    reasons: list[ReasonParagraph]
    assumptions: list[str]
    unresolved: list[str]
    flags_shown: list[str]
    revision_notes: list[str] = Field(description="Only when revising: what changed and why; otherwise empty")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Audit (AI call 7, and the re-audit after a revision)
# ─────────────────────────────────────────────────────────────────────────────
class AuditFinding(BaseModel):
    check_id: str = Field(description="The audit standard's id (audit.…)")
    outcome: Literal["pass", "caution", "error", "not_applicable"]
    explanation: str
    fix: str = Field(description="What should change; empty if pass or not applicable")


class AuditResult(BaseModel):
    findings: list[AuditFinding]
    overall: str


# ─────────────────────────────────────────────────────────────────────────────
# 5. Chunking a case document (Case database page)
#    The model returns line numbers, not text: code then takes each case's
#    words exactly as they are in the document (core/cases.py).
# ─────────────────────────────────────────────────────────────────────────────
Plea = Literal["guilty_early", "guilty", "guilty_late", "not_guilty", "mixed", "unknown"]


class ChunkOffence(BaseModel):
    name: str = Field(description="The offence as written, without the sentence")
    offence_id: str = Field(description="One of the offence ids listed in the input, or \"other\"")
    sentence_text: str = Field(description="The individual sentence for this offence as written; empty if none given")
    sentence_months: Optional[float] = Field(description="That sentence in months (years × 12); null if none, "
                                                         "not imprisonment, or life")


class ChunkedCase(BaseModel):
    start_line: int = Field(description="The L number of the case's first line (its name or citation)")
    end_line: int = Field(description="The L number of the case's last line")
    continues_past_shown_lines: bool = Field(description="True if the case goes on after the last line shown")
    citation: str = Field(description="The case name and citation exactly as written")
    court: Literal["Court of Appeal", "County Court", "Supreme Court", "unknown"]
    year: Optional[int]
    principal_offence_id: str = Field(description="The offence the case is listed under (one of the ids, or \"other\")")
    principal_offence: str = Field(description="The principal offence's name as written")
    offences: list[ChunkOffence] = Field(description="Every offence charged, in the order given")
    head_sentence_text: str = Field(description="The head sentence / orders as written")
    head_sentence_months: Optional[float] = Field(description="The total effective term of imprisonment in months; "
                                                              "null if none or life")
    npp_months: Optional[float] = Field(description="The non-parole period in months; null if none")
    plea: Plea
    offender: str = Field(description="Age and gender of the offender as written (e.g. \"34y male\"); empty if not given")
    summary: str = Field(description="One or two sentences on the offending, from the text only")


class ChunkResult(BaseModel):
    cases: list[ChunkedCase]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Sorting a plain description into the form's questions (simple input)
# ─────────────────────────────────────────────────────────────────────────────
OffenceChoice = Literal[tuple(config.IN_SCOPE_OFFENCES) + ("other", "unclear")]


class FieldQuote(BaseModel):
    field: str = Field(description="The name of the field the words support")
    quote: str = Field(description="The words in the description that show the answer, copied exactly")


class SortedCase(BaseModel):
    case_name: str = Field(description="The case name if the description gives one (e.g. \"DPP v Smith\"), else empty")
    offence_id: OffenceChoice = Field(description="The charge, as one of the listed offence ids; \"other\" for any "
                                                  "other offence; \"unclear\" if the description does not show it")
    offence_as_described: str = Field(description="The offence as the description puts it, in a few words")
    court: Literal["County Court", "Supreme Court", "Magistrates' Court", "Children's Court", "unclear"]
    offence_date: str = Field(description="The date of the offence as YYYY-MM-DD; empty unless the exact day is given")
    offence_date_end: str = Field(description="The last date of an offence period as YYYY-MM-DD; empty if a single day")
    offence_year: Optional[int] = Field(description="The year of the offence if no exact date is given")
    plea: Literal["guilty", "found_guilty_at_trial", "unclear"] = Field(
        description="guilty = pleaded guilty; found_guilty_at_trial = found guilty after a trial (a verdict)")
    plea_timing: Literal["earliest_opportunity", "after_committal", "late", "not_applicable", "unknown"]
    age_at_offence: Optional[int]
    age_at_sentence: Optional[int]
    prior_convictions: Literal["none", "some", "relevant_or_significant", "unknown"]
    serving_sentence_or_on_parole: YesNoUnknown
    mental_impairment_evidence: YesNoUnknown = Field(
        description="yes only if a psychiatrist's or psychologist's report on a mental impairment is mentioned")
    intoxicated_by_liquor: YesNoUnknown
    intoxication_contributed: YesNoUnknown
    previous_aeo: YesNoUnknown = Field(description="A previous alcohol exclusion order for this kind of offence")
    cooperation: Literal["none", "admissions_only", "past_assistance", "undertaking", "unknown"]
    victim_protected_worker: YesNoUnknown = Field(
        description="The victim was an emergency worker, custodial officer or youth justice custodial worker on duty")
    offender_knew_or_reckless: YesNoUnknown = Field(
        description="The offender knew, or was reckless, that the victim was such a worker on duty")
    accessory_only: YesNoUnknown = Field(
        description="yes only if the offender was found guilty as someone who directed, assisted or encouraged "
                    "another; no if the offender committed the offence personally")
    presentence_detention_days: Optional[int] = Field(description="Days already held in custody for this offence")
    presentence_report: Literal["available", "ordered", "none", "unknown"]
    cco_consent: YesNoUnknown = Field(description="The offender consents to a community correction order")
    arising_from_driving: YesNoUnknown = Field(description="The offence arose from driving a motor vehicle")
    quotes: list[FieldQuote] = Field(description="For each answer that is not unknown/unclear/empty, the words of the "
                                                 "description that show it, copied exactly")
    outside_version_1: list[str] = Field(description="Anything that takes the case outside what the app covers "
                                                     "(another offence, several charges, a child, another court)")
    not_stated: list[str] = Field(description="The questions the description leaves open that matter most")
