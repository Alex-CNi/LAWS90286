"""
core/facts.py — STEPS 1–2: THE CASE INPUT AND THE FACTS EXTRACTION (AI call 1).

  CaseInput        the form values + the narrative the user typed or uploaded
  validate()       checks done in code BEFORE any AI call (scope, dates, ages)
  extract_facts()  AI call 1: turns the narrative into a list of facts, each
                   with its basis (admitted / evidence / alleged / disputed),
                   lists missing facts and suggests values for "unknown" fields
  verify_quotes()  code check: every quote the model gives must really be in
                   the narrative (as in the Day 3 code)
  ConfirmedFacts   what the user confirms on screen; every later step uses it
  sort_description()  SIMPLE INPUT: an AI call that sorts a plain description into
                   the form's questions (only what the description states; the
                   user checks the result and answers what it leaves open)
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone

from core import config, prompts
from core.coverage import (AEO_PLAINLY_RELEVANT, RELATIONS_USER_SUPPLIED, asked_category_clause, charge_questions,
                           contempt_kind, is_school_condition, licence_group, relative_entry)
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord
from core.schemas import FactsExtraction, SortedCase

# Labels shown in the form for each coded value.
PLEA_TIMING = {
    "earliest_opportunity": "At the earliest reasonable opportunity",
    "after_committal": "After committal / some delay",
    "late": "Late (at or near trial)",
    "not_applicable": "Not applicable (found guilty at trial)",
    "unknown": "Unknown",
}
YNU = ["unknown", "yes", "no"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. The case as entered in the form
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CaseInput:
    # The charge (one per run in version 1) and the court
    offence_id: str = config.CAUSING_INJURY_OFFENCES[3]
    offence_date: date = date(2024, 1, 1)
    offence_date_end: date | None = None          # for an offence period
    court: str = "County Court"
    case_name: str = ""
    user_role: str = "other"                      # prosecution | defence | other (affects one caution)

    # Plea
    plea: str = "guilty"                          # guilty | not_guilty (found guilty)
    plea_timing: str = "earliest_opportunity"

    # Offender
    offender_type: str = "natural_person"         # natural_person | body_corporate (intake.offender_type)
    age_at_offence: int = 25
    age_at_sentence: int = 26
    age_at_sentence_assumed: bool = False         # simple input: not stated, taken as the age at the offence
    prior_convictions: str = "unknown"            # none | some | relevant_or_significant | unknown
    serving_sentence_or_on_parole: str = "no"     # yes | no | unknown
    mental_impairment_evidence: str = "unknown"
    intoxicated_by_liquor: str = "unknown"
    intoxication_contributed: str = "unknown"
    previous_aeo: str = "no"
    cooperation: str = "none"                     # none | admissions_only | past_assistance | undertaking | unknown

    # Victim (protected officials: emergency worker, custodial officer, youth
    # justice custodial worker, on duty — VSM 9.1.2.3)
    victim_protected_worker: str = "no"
    offender_knew_or_reckless: str = "unknown"
    # The Manual gives no commencement date for the protected-official minimums
    # and Category 1 status (flag.protected_official_minimum_dates): the user
    # confirms that they applied at the offence date.
    protected_scheme_confirmed: str = "unknown"
    accessory_only: str = "no"                    # found guilty only as directing / assisting / encouraging
    minor_involvement_proved: str = "unknown"     # accessory who proves minor involvement (protected officials)

    # Sentencing hearing
    presentence_detention_days: int | None = 0    # None = unknown
    presentence_report: str = "unknown"           # available | ordered | none | unknown
    cco_consent: str = "unknown"
    indication_given: bool = False
    indication_type: str = "imprisonment"         # the indicated sentence type
    indication_max_months: int | None = None
    indication_plea_entered_on_it: str = "unknown"
    indication_same_judge: str = "unknown"

    # Historic offending under a penalty table the Manual holds only as text (VSM 24.1.6.x, 31.1.2): the maximum
    # in force at the time, in months, as the user reads it from the table (None until confirmed).
    historic_maximum_months: int | None = None
    historic_maximum_fine_units: int | None = None   # …and its fine (penalty units; a body corporate's for a company)

    # Driving (only if the offence arose from driving a motor vehicle)
    arising_from_driving: str = "no"
    alcohol_influenced_driving: str = "unknown"
    licence_status: str = "unknown"               # victorian | other_jurisdiction | none | unknown

    # Questions only some charges need: core/coverage.charge_questions() says which (the form asks only those, and
    # only those answers reach the AI calls and the report). Each counts against the offender when answered "yes" (or
    # with a circumstance), so each starts "unknown" — not proved — except the contempt question.
    manslaughter_circumstance: str = "unknown"    # gross_violence | single_punch | neither | unknown (VSM 9.1.2.1)
    prosecution_notice: str = "unknown"           # manslaughter: notice that the prosecution applies for the minimum
    official_injured: str = "unknown"             # ss 317AC, 317AD: the protected official was injured (VSM 9.1.2.4)
    category1_confirmed: str = "unknown"          # ss 317AC, 317AD: Category 1 confirmed (flag.driving_protected_category1)
    category_condition_met: str = "unknown"       # a register category clause no other answer settles (armed robbery)
    historic_provision_marked: str = "unknown"    # offending under table 24.1.6.2: the provision is marked "#"
    conditional_condition_met: str = "unknown"    # the condition of the row's conditional maximum (ss 71AA, 71AC, 71B,
                                                  # 195H, 195I)
    school_condition_confirmed: str = "unknown"   # ss 71AC, 71B: the statutory condition confirmed (flag.school_500m_condition)
    contempt_by_higher_court: str = "unknown"     # a tribunal's or other body's contempt: the County Court or Supreme
                                                  # Court is dealing with it
    sso_variant_met: str = "unknown"              # a standard sentence variant's condition (murder: the victim was a
                                                  # custodial officer or emergency worker on duty — 30 years, sso.list)

    # A maximum fixed by reference to another offence (chapter 32 attempts, conspiracy, incitement; s 247B): the
    # offence attempted, conspired, incited or intended, or the maximum the user reads from the legislation where the
    # Manual cannot resolve it (app policy; also the accumulated maxima of several offences).
    primary_offence_id: str | None = None
    relative_maximum_months: int | None = None
    relative_maximum_life: bool = False

    # Licence orders and the alcohol exclusion order (VSM 16, 20.1; audit.mandatory_ancillary_orders)
    motor_vehicle_stolen: str = "unknown"         # theft (or attempted theft): the property is a motor vehicle
    previous_s61_3_offence: str = "unknown"       # RSA s 61(3): a previous offence (the subsequent-offence minimum)
    aeo_relevant_offence: str = "unknown"         # the charge is a "relevant offence" for an AEO (VSM 20.1.1)
    # A body corporate: a provision the user identifies setting a company maximum above five times the natural
    # person's (fine.maximum: a fine up to it is a caution, app policy), in penalty units.
    company_maximum_units: int | None = None

    # The story of the case
    narrative: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["offence_date"] = self.offence_date.isoformat() if self.offence_date else None
        d["offence_date_end"] = self.offence_date_end.isoformat() if self.offence_date_end else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CaseInput":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        for key in ("offence_date", "offence_date_end"):
            if isinstance(data.get(key), str) and data[key]:
                data[key] = date.fromisoformat(data[key])
        return cls(**data)

    @property
    def body_corporate(self) -> bool:
        return self.offender_type == "body_corporate"

    @property
    def protected_official(self) -> str:
        """Both limbs must be proved beyond reasonable doubt (VSM 9.1.2.3):
        the victim was a protected official on duty AND the offender knew or
        was reckless as to that. yes / no / unknown."""
        if self.victim_protected_worker == "no" or self.offender_knew_or_reckless == "no":
            return "no"
        if self.victim_protected_worker == "yes" and self.offender_knew_or_reckless == "yes":
            return "yes"
        return "unknown"


def melbourne_today() -> date:
    """Today's date in Victoria (the server's clock is UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Australia/Melbourne")).date()
    except Exception:  # noqa: BLE001 — no time-zone data: allow for the time difference
        return (datetime.now(timezone.utc) + timedelta(hours=11)).date()


# The questions the app cannot do without. The form starts them empty: the app never supplies them by default.
REQUIRED_FIELDS = {"offence_id": "the charge", "court": "the court", "offence_date": "the date of the offence",
                   "plea": "the plea", "age_at_offence": "the offender's age at the offence",
                   "age_at_sentence": "the offender's age at sentence"}
AGE_FIELDS = ("age_at_offence", "age_at_sentence")
# Questions about a natural person: hidden for a body corporate, never sent to the AI calls or applied from a
# description for one (intake.offender_type; the app's rules for a body corporate: no order on a person).
PERSON_ONLY_FIELDS = {
    "age_at_offence", "age_at_sentence", "serving_sentence_or_on_parole", "mental_impairment_evidence",
    "intoxicated_by_liquor", "intoxication_contributed", "previous_aeo", "victim_protected_worker",
    "offender_knew_or_reckless", "protected_scheme_confirmed", "accessory_only", "minor_involvement_proved",
    "presentence_detention_days", "presentence_report", "cco_consent", "arising_from_driving",
    "alcohol_influenced_driving", "licence_status", "manslaughter_circumstance", "prosecution_notice",
    "official_injured", "category1_confirmed", "category_condition_met", "historic_provision_marked",
    "sso_variant_met", "motor_vehicle_stolen", "previous_s61_3_offence", "aeo_relevant_offence"}


def required_fields(offender_type: str | None) -> dict:
    """The required questions for this offender: a body corporate has no age (intake.offender_type)."""
    if offender_type == "body_corporate":
        return {k: v for k, v in REQUIRED_FIELDS.items() if k not in AGE_FIELDS}
    return dict(REQUIRED_FIELDS)


def offender_form(name: str) -> tuple[str, str]:
    """A register row printed for one kind of offender (“… – natural person”, “… – body corporate”: DPCSA ss 56D–56F):
    (the offence's name, "natural person" | "body corporate"), or (the name, "") for a row for every offender."""
    base, _, form = (name or "").rpartition("–")
    form = form.strip().lower()
    if base and form in ("natural person", "body corporate"):
        return base.strip().lower(), form
    return (name or "").strip().lower(), ""


def body_corporate_counterpart(kb: KnowledgeBase, offence_id: str) -> str | None:
    """The register's row for the other kind of offender (natural person ↔ body corporate), if it has one."""
    row = kb.offences.get(offence_id) or {}
    base, form = offender_form(row.get("offence") or "")
    if not form:
        return None
    want = "natural person" if form == "body corporate" else "body corporate"
    return next((o for o, r in kb.offences.items() if offender_form(r.get("offence") or "") == (base, want)
                 and r.get("provision_norm") == row.get("provision_norm")), None)


# ─────────────────────────────────────────────────────────────────────────────
# 1b. Questions only some charges need (core/coverage.charge_questions says which)
# ─────────────────────────────────────────────────────────────────────────────
MANSLAUGHTER_CIRCUMSTANCES = {
    "unknown": "Unknown / not proved",
    "gross_violence": "Gross violence (proved beyond reasonable doubt)",
    "single_punch": "A single punch or strike (proved beyond reasonable doubt)",
    "neither": "Neither",
}
CHARGE_QUESTION_CHOICES = {"manslaughter_circumstance": list(MANSLAUGHTER_CIRCUMSTANCES)}   # the others: yes/no/unknown


def charge_question(kb: KnowledgeBase, field_name: str, offence_id: str) -> tuple[str, str]:
    """(question, help) for one of the charge-specific questions, worded from the charge's register row."""
    row = kb.offence(offence_id) if offence_id in kb.offences else {}
    if field_name == "manslaughter_circumstance":
        return ("Was the manslaughter committed in circumstances of gross violence, or by a single punch or strike?",
                "From 1 November 2014 a 10-year minimum non-parole period applies if one of these is proved beyond "
                "reasonable doubt and the prosecution gave notice (VSM 9.1.2.1). Gross violence: the offender caused "
                "the death in company with two or more people (or by an agreement with them to engage in the conduct), "
                "and planned to have and use an offensive weapon or firearm and used it, or planned the conduct when a "
                "reasonable person would have foreseen it was likely to cause death, or caused two or more serious "
                "injuries in a sustained or prolonged attack. A single punch or strike: a dangerous act intended to be "
                "delivered to the head or neck, which the victim was not expecting, as the offender knew "
                "(mmnpp.gross_violence_definition).")
    if field_name == "prosecution_notice":
        return ("Did the prosecution give notice that it would apply for the 10-year minimum non-parole period?",
                "The manslaughter minimum applies only if the prosecution gives notice that it intends to apply for it "
                "(VSM 9.1.2.1; intake.prosecution_notices).")
    if field_name == "official_injured":
        return ("Was the emergency worker, custodial officer or youth justice custodial worker injured in the "
                "commission of the offence?",
                "If so, imprisonment with a non-parole period of at least two years applies unless a special reason is "
                "found (VSM 9.1.2.4). Answer “Yes” only if it is proved.")
    if field_name == "category1_confirmed":
        return (f"Do you confirm that {row.get('section_ref') or 'this offence'} is a Category 1 offence?",
                "Footnote 2005 describes it as a Category 1 offence, but the penalty table (and so the register) marks "
                "no category (flag.driving_protected_category1). “Yes”: Category 1 applies. “No”: the table applies "
                "(no category). “Unknown”: a sentence other than imprisonment is shown as unresolved, not as an error.")
    if field_name == "category_condition_met":
        cat, clause = asked_category_clause(row) or (None, "")
        return (f"Is it proved that {clause}?",
                f"The register makes this charge a Category {cat} offence only if {clause} (and the offender was 18 "
                "or older, and the offence was on or after 20 March 2017). Answer “Yes” only if it is proved beyond "
                "reasonable doubt or admitted; until then the category is not applied (audit.category_1_2).")
    if field_name == "historic_provision_marked":
        return ("Is the provision charged marked “#” in the Manual's table at VSM 24.1.6.2?",
                "Footnote 3556: a provision marked “#” is a Category 1 offence where the offender was 18 or older. For "
                "offending from 20 March to 30 June 2017 the category turns on it (cat1.historic_sexual_provisions). "
                "Check the provision against the table shown above.")
    if field_name == "conditional_condition_met":
        c = (row.get("conditional_maxima") or [{}])[0]
        return (f"Is it proved that the offence was committed {_condition_words(c)}?",
                f"If so, the maximum is “{c.get('text', '')}” (VSM {row.get('section', '')}). Answer “Yes” only if it "
                "is proved beyond reasonable doubt or admitted; until then the base maximum applies "
                "(audit.maximum_applicable (3)).")
    if field_name == "school_condition_confirmed":
        return ("Do you confirm that the statutory condition for the 20-year maximum is met?",
                "The table prints “within 500 metres of a school”; 28.3.6 describes the factor as supply or trafficking "
                "to children within 500 metres of a school, and its footnote cites provisions that do not match the "
                "table (flag.school_500m_condition). The 20-year maximum is applied only when you confirm the "
                "statutory condition (“Yes”); otherwise — unknown, or “No” (e.g. the trafficking was not to a child) — "
                "a term above the base maximum and up to 20 years is unresolved, as the flag directs.")
    if field_name == "contempt_by_higher_court":
        return ("Is the County Court or Supreme Court dealing with this contempt (not the tribunal or body itself)?",
                "The app covers sentencing in the County Court and Supreme Court only. The Manual's table (VSM 29.1.3) "
                "gives the Act's maximum; it does not say which body deals with the contempt.")
    if field_name == "motor_vehicle_stolen":
        return ("Was the property stolen (or that the offender attempted to steal) a motor vehicle?",
                "On a conviction for stealing or attempting to steal a motor vehicle a licence disqualification order is "
                "mandatory (a three-month default period applies if none is specified: licence.other_mandatory).")
    if field_name == "previous_s61_3_offence":
        return ("Has the offender a previous offence under Road Safety Act s 61(3)?",
                "First offence: licence cancellation and disqualification for at least 4 years if a conviction is "
                "recorded, 2 years otherwise; a subsequent offence: at least 8 years, or 4 years "
                "(audit.mandatory_ancillary_orders). Unknown: the first-offence minimum is applied, with a caution.")
    if field_name == "aeo_relevant_offence":
        return ("Is this charge a “relevant offence” for an alcohol exclusion order?",
                "VSM 20.1.1 lists relevant offences by category (murder or manslaughter; causing injury; administering "
                "certain substances; threat to kill, conduct endangering, kidnap and assault; certain resisting arrest "
                "and assisting offender offences; use of firearms; certain sexual offences; child abuse material "
                "offences). “Yes”: the order is checked as mandatory when intoxication by liquor significantly "
                "contributed; “No”: not checked; “Unknown”: a caution (anc.alcohol_exclusion_order).")
    if field_name == "sso_variant_met":
        v = (row.get("standard_sentence_variants") or [{}])[0]
        return (f"Is it proved that {v.get('if', '')}?",
                f"The standard sentence is {row.get('standard_sentence_years')} years, or {v.get('years')} years if "
                f"{v.get('if', '')} (sso.list). It is a guidepost, not a starting point (VSM 9.2). Answer “Yes” only if it "
                "is proved beyond reasonable doubt or admitted.")
    return field_name, ""


def _condition_words(entry: dict) -> str:
    """A conditional maximum's condition as words that follow "the offence was committed …"."""
    cond = (entry.get("condition") or "").strip()
    if is_school_condition(entry):
        return "within 500 metres of a school"
    if cond.startswith("trafficking at"):
        return cond.replace("trafficking at", "at", 1)            # "at the direction of a criminal organisation"
    if cond.startswith("wearing"):
        return "while the offender was " + cond                  # face covering (ss 195H, 195I)
    return cond


def charge_answer_text(field_name: str, value) -> str:
    if field_name == "manslaughter_circumstance":
        return MANSLAUGHTER_CIRCUMSTANCES.get(value, str(value))
    return str(value)


def asked_charge_questions(kb: KnowledgeBase, case: "CaseInput") -> list[str]:
    """The charge-specific questions that bear on this case as entered (the school confirmation only once the
    offence is said to be within 500 metres of a school; none about a person for a body corporate)."""
    out = charge_questions(kb, case.offence_id, case.offence_date, case.offence_date_end,
                           body_corporate=case.body_corporate)
    if case.conditional_condition_met != "yes":
        out = [f for f in out if f != "school_condition_confirmed"]
    if relative_entry(kb.offences.get(case.offence_id) or {}) and case.primary_offence_id != THEFT:
        out = [f for f in out if f != "motor_vehicle_stolen"]      # an attempt: only an attempted theft
    if aeo_question_applies(case):
        out.append("aeo_relevant_offence")
    return out


THEFT = "crimes-act-s-74-theft"


def aeo_question_applies(case: "CaseInput") -> bool:
    """The "relevant offence" question for an alcohol exclusion order: a natural person, a charge VSM 20.1.1 does not
    name, and intoxication by liquor that may have significantly contributed."""
    return (not case.body_corporate and case.offence_id not in AEO_PLAINLY_RELEVANT
            and case.intoxicated_by_liquor == "yes" and case.intoxication_contributed != "no")


def validate(case: CaseInput, kb: KnowledgeBase) -> list[str]:
    """Problems that stop the run before any AI call (so no run ends early
    with fewer than five AI calls)."""
    missing = [label for name, label in required_fields(case.offender_type).items()
               if getattr(case, name) in (None, "")]
    if missing:
        return ["Answer " + ", ".join(missing) + " in Step 1 first: the app does not supply them by default."]
    problems = []
    form = offender_form((kb.offences.get(case.offence_id) or {}).get("offence") or "")[1]
    if case.offence_id not in config.IN_SCOPE_OFFENCES:
        problems.append("Choose the charge from the charge box (the offences in the Manual's register).")
    elif form == "body corporate" and not case.body_corporate:
        other = body_corporate_counterpart(kb, case.offence_id)
        problems.append("This is the body corporate form of the offence: choose “Body corporate” as the offender (Step "
                        "1), or the offence's “– natural person” row"
                        + (f" (“{kb.charge_label(other)}”)" if other else "") + ".")
    elif form == "natural person" and case.body_corporate:
        other = body_corporate_counterpart(kb, case.offence_id)
        problems.append("This is the natural person form of the offence: choose its “– body corporate” row for a body "
                        "corporate" + (f" (“{kb.charge_label(other)}”)" if other else "") + ".")
    if case.offender_type not in ("natural_person", "body_corporate"):
        problems.append("Say whether the offender is a natural person or a body corporate.")
    if case.court not in config.IN_SCOPE_COURTS:
        problems.append("The app covers sentencing in the County Court and Supreme Court only.")
    contempt = contempt_kind(kb.offences.get(case.offence_id) or {})
    if contempt == "magistrates":
        problems.append("This row is the Magistrates' Court Act 1989's contempt provision (VSM 29.1.3). The app covers "
                        "sentencing in the County Court and Supreme Court only (the Magistrates' Court: guide step 18); "
                        "for a contempt of the County Court or Supreme Court choose “Contempt … (County Court / Supreme "
                        "Court)”.")
    elif contempt == "other_body" and case.contempt_by_higher_court == "no":
        problems.append("You said the tribunal or body itself is dealing with the contempt. The app covers sentencing "
                        "in the County Court and Supreme Court only.")
    if not case.body_corporate and case.age_at_offence < config.MIN_AGE_AT_OFFENCE:
        problems.append("The app covers adult offenders only (18 or older at the offence). "
                        "Children are sentenced under a largely separate regime (VSM 10).")
    if not case.body_corporate and case.age_at_sentence < case.age_at_offence:
        problems.append("Age at sentence cannot be less than age at the offence.")
    if case.offence_date_end and case.offence_date_end < case.offence_date:
        problems.append("The end of the offence period is before its start.")
    if case.offence_date > melbourne_today():
        problems.append("The offence date is in the future.")
    row = kb.offences.get(case.offence_id)
    end = case.offence_date_end.isoformat() if case.offence_date_end else None
    if row and not kb.historic_tables(case.offence_id, case.offence_date.isoformat(), end):
        maxima = row.get("maxima") or []
        starts = [m.get("from") for m in maxima if m.get("from")]
        # An entry with no start date (e.g. "prior to 1 September 1997 …") covers the earlier dates itself.
        if starts and all(m.get("from") for m in maxima) and case.offence_date.isoformat() < min(starts):
            problems.append(f"The Manual's register gives no maximum for this offence before {min(starts)}, so "
                            f"the Manual is silent for this date and the maximum cannot be determined. Check the "
                            f"charge and the date.")
    if row and relative_entry(row) and case.primary_offence_id:
        if case.primary_offence_id not in config.IN_SCOPE_OFFENCES:
            problems.append("Choose the offence attempted, conspired, incited or intended from the box (the offences "
                            "in the Manual's register).")
        elif case.primary_offence_id == case.offence_id:
            problems.append("The offence attempted, conspired, incited or intended cannot be the charge itself.")
    if row and relative_entry(row) and case.relative_maximum_months is not None and case.relative_maximum_months <= 0:
        problems.append("The maximum you enter must be a number of months above nil.")
    if row and (relative_entry(row) or {}).get("relation") in RELATIONS_USER_SUPPLIED and \
            not (case.relative_maximum_months or case.relative_maximum_life):
        problems.append("This charge's maximum is fixed by reference to another offence in a way the Manual cannot "
                        "give (" + RELATIONS_USER_SUPPLIED[relative_entry(row)["relation"]] + "): enter the maximum you "
                        "read from the legislation in Step 1, under the offence attempted, conspired or incited.")
    if len(case.narrative.strip()) < 80:
        problems.append("Describe the case in the narrative box (at least a few sentences), or upload a file.")
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# 2. AI call 1 — extract the facts
# ─────────────────────────────────────────────────────────────────────────────
def case_details_text(case: CaseInput, kb: KnowledgeBase) -> str:
    """The form values, written out for the AI calls."""
    period = case.offence_date.isoformat() + (f" to {case.offence_date_end.isoformat()}" if case.offence_date_end else "")
    head = [
        f"Charge: {kb.offence_label(case.offence_id)}",
        f"Offence date: {period}",
        f"Court: {case.court}",
        f"Plea: {'guilty' if case.plea == 'guilty' else 'not guilty — found guilty at trial'}"
        + (f" ({PLEA_TIMING.get(case.plea_timing, case.plea_timing)})" if case.plea == "guilty" else ""),
    ]
    if case.body_corporate:
        lines = head + [
            "Offender: a body corporate (company) — the questions about a natural person (age, intoxication, a mental "
            "impairment, custody, a pre-sentence report, consent to a CCO, protected officials, driving) do not apply",
            f"Prior convictions: {case.prior_convictions}",
            f"Cooperation with authorities: {case.cooperation}"]
        from core.framework import company_maximum_input_applies      # (framework imports this module)
        if case.company_maximum_units and company_maximum_input_applies(kb, case.offence_id, case.offence_date,
                                                                        case.offence_date_end):
            lines.append(f"A provision the user identifies sets a higher maximum fine for a body corporate: "
                         f"{case.company_maximum_units} penalty units (app policy: the app cannot check it)")
        if case.indication_given:
            lines.append(f"Sentence indication: given — type {case.indication_type}, maximum total "
                         f"{case.indication_max_months or 'not stated'} months; plea entered on it: "
                         f"{case.indication_plea_entered_on_it}; same judge: {case.indication_same_judge}")
        return "\n".join(lines + _scheme_lines(case, kb))
    lines = head + [
        f"Offender's age at the offence: {case.age_at_offence}; at sentence: {case.age_at_sentence}"
        + (" (assumed: not stated, taken as the age at the offence)" if case.age_at_sentence_assumed else ""),
        f"Prior convictions: {case.prior_convictions}",
        f"Serving a sentence or on parole: {case.serving_sentence_or_on_parole}",
        (f"Victim an emergency worker, custodial officer or youth justice custodial worker on duty: "
         f"{case.victim_protected_worker}; offender knew or was reckless as to that: {case.offender_knew_or_reckless}"
         + (f"; user confirms the protected-official scheme applied at the offence date: {case.protected_scheme_confirmed}"
            if case.victim_protected_worker != "no" else ""))
        if case.offence_id in config.CAUSING_INJURY_OFFENCES else
        "Protected-official scheme (ss 15A–18 only): not applicable to this charge",
        f"Found guilty only as an accessory (directing, assisting or encouraging): {case.accessory_only}"
        + (f"; minor involvement proved: {case.minor_involvement_proved}" if case.accessory_only == "yes" else ""),
        f"Intoxicated by liquor at the offence: {case.intoxicated_by_liquor}; intoxication significantly "
        f"contributed: {case.intoxication_contributed}; previous alcohol exclusion order for this offence: {case.previous_aeo}",
        f"Psychiatrist's or psychologist's report on a mental impairment: {case.mental_impairment_evidence}",
        f"Cooperation with authorities: {case.cooperation}",
        f"Pre-sentence detention (days): {case.presentence_detention_days if case.presentence_detention_days is not None else 'unknown'}",
        f"Pre-sentence report: {case.presentence_report}; offender consents to a CCO: {case.cco_consent}",
    ]
    if case.indication_given:
        lines.append(f"Sentence indication: given — type {case.indication_type}, maximum total "
                     f"{case.indication_max_months or 'not stated'} months; plea entered on it: "
                     f"{case.indication_plea_entered_on_it}; same judge: {case.indication_same_judge}")
    if case.arising_from_driving != "no":
        lines.append(f"Arising from driving: {case.arising_from_driving}; under the influence of alcohol "
                     f"(or alcohol and a drug): {case.alcohol_influenced_driving}; licence: {case.licence_status}")
    elif licence_group(kb, case.offence_id):                    # a licence order can follow (VSM 16): its form
        lines.append(f"Licence held (the form of any licence order follows it): {case.licence_status}")
    return "\n".join(lines + _scheme_lines(case, kb))


def _scheme_lines(case: CaseInput, kb: KnowledgeBase) -> list[str]:
    """The case details a charge's statutory scheme turns on: its questions, the offence a relative maximum refers
    to, and a maximum entered from a historic table."""
    lines: list[str] = []
    for name in asked_charge_questions(kb, case):               # the questions this charge's scheme turns on
        lines.append(f"{charge_question(kb, name, case.offence_id)[0]} "
                     f"{charge_answer_text(name, getattr(case, name))}")
    relative = relative_entry(kb.offence(case.offence_id)) if case.offence_id in kb.offences else None
    if relative:
        lines.append(f"Maximum fixed by reference to another offence (“{relative.get('text', '')}”): the offence "
                     "attempted, conspired, incited or intended: "
                     + (kb.offence_label(case.primary_offence_id) if case.primary_offence_id in kb.offences
                        else "not identified")
                     + ("; maximum entered by the user: life imprisonment" if case.relative_maximum_life else
                        f"; maximum entered by the user: {case.relative_maximum_months} months"
                        if case.relative_maximum_months else ""))
    end = case.offence_date_end.isoformat() if case.offence_date_end else None
    hist = kb.historic_tables(case.offence_id, case.offence_date.isoformat(), end)
    if hist and hist[0] == "table":
        entered = [f"{case.historic_maximum_months} months" if case.historic_maximum_months else "",
                   f"a fine of {case.historic_maximum_fine_units} penalty units" if case.historic_maximum_fine_units
                   else ""]
        lines.append(f"Historic offending (the Manual's table at {', '.join(hist[1])}): maximum confirmed by the user: "
                     + (" and ".join(x for x in entered if x) or "not yet"))
    return lines


def extract_facts(llm: LLM, case: CaseInput, kb: KnowledgeBase) -> tuple[FactsExtraction, CallRecord]:
    """AI call 1 (fast model)."""
    text = (f"## Case details (from the form)\n{case_details_text(case, kb)}\n\n"
            f"## Case narrative (from the user)\n{case.narrative.strip()}")
    return llm.structured("Facts extraction", "extraction", prompts.EXTRACTION, text, FactsExtraction)


def _norm_quote(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'\"-]", " ", text)).strip()


MIN_QUOTE_WORDS = 4   # shorter quotes are too short to show where a fact comes from


def check_quote(quote: str, narrative: str) -> bool | None:
    """True if the quote appears in the narrative, False if not, None if there
    is no quote (the fact came from the form) or it is too short to check."""
    q = _norm_quote(quote)
    if not q or len(q.split()) < MIN_QUOTE_WORDS:
        return None
    return q in _norm_quote(narrative)


def verify_quotes(extraction: FactsExtraction, narrative: str) -> list[dict]:
    """Facts as editable rows, each marked quote_verified True/False/None."""
    return [{"id": f.id, "text": f.text, "topic": f.topic, "bearing": f.bearing, "basis": f.basis,
             "source_quote": f.source_quote, "quote_verified": check_quote(f.source_quote, narrative)}
            for f in extraction.facts]


# Map from the model's suggestions to the form fields they may fill.
SUGGESTION_FIELDS = {
    "victim_protected_worker": "victim_protected_worker",
    "offender_knew_or_reckless_as_to_status": "offender_knew_or_reckless",
    "intoxicated_by_liquor": "intoxicated_by_liquor",
    "intoxication_contributed": "intoxication_contributed",
    "mental_impairment_evidence": "mental_impairment_evidence",
    "cco_consent": "cco_consent",
    "presentence_report": "presentence_report",
    "cooperation": "cooperation",
    "prior_convictions": "prior_convictions",
    "arising_from_driving": "arising_from_driving",
    # charge-specific: used only for the charges whose scheme turns on them (core/coverage.charge_questions)
    "manslaughter_circumstance": "manslaughter_circumstance",
    "prosecution_notice": "prosecution_notice",
    "official_injured": "official_injured",
    "category_condition_met": "category_condition_met",
    "conditional_condition_met": "conditional_condition_met",
}
CHARGE_SPECIFIC_FIELDS = {"manslaughter_circumstance", "prosecution_notice", "official_injured", "category1_confirmed",
                          "category_condition_met", "historic_provision_marked", "conditional_condition_met",
                          "school_condition_confirmed", "contempt_by_higher_court", "sso_variant_met",
                          "motor_vehicle_stolen", "previous_s61_3_offence", "aeo_relevant_offence"}


# Suggested values that would count against the offender. They are shown
# unticked: the user must choose to apply them (adverse facts need proof).
ADVERSE_VALUES = {
    "victim_protected_worker": {"yes"}, "offender_knew_or_reckless": {"yes"},
    "intoxicated_by_liquor": {"yes"}, "intoxication_contributed": {"yes"},
    "prior_convictions": {"some", "relevant_or_significant"}, "arising_from_driving": {"yes"},
    "serving_sentence_or_on_parole": {"yes"},
    # answers that close off a mitigating consideration or a disposition
    "cco_consent": {"no"}, "presentence_report": {"none"}, "mental_impairment_evidence": {"no"},
    "cooperation": {"none"},
    # answers that bring in a minimum, a category or a higher maximum (charge-specific)
    "manslaughter_circumstance": {"gross_violence", "single_punch"}, "prosecution_notice": {"yes"},
    "official_injured": {"yes"}, "category1_confirmed": {"yes"}, "category_condition_met": {"yes"},
    "historic_provision_marked": {"yes"}, "conditional_condition_met": {"yes"}, "school_condition_confirmed": {"yes"},
    "sso_variant_met": {"yes"}, "motor_vehicle_stolen": {"yes"}, "previous_s61_3_offence": {"yes"},
    "aeo_relevant_offence": {"yes"},
}


def is_adverse(field_name: str, value: str) -> bool:
    return value in ADVERSE_VALUES.get(field_name, set())


def suggestions_for_unknowns(case: CaseInput, extraction: FactsExtraction,
                             kb: KnowledgeBase | None = None) -> dict[str, str]:
    """Form fields the user left 'unknown' for which the narrative supplies a
    value. The confirm screen shows them; nothing is applied silently. A charge-specific question is suggested only
    for a charge whose scheme turns on it."""
    asked = set(asked_charge_questions(kb, case)) if kb is not None else set()
    out = {}
    for s_name, field_name in SUGGESTION_FIELDS.items():
        if field_name in CHARGE_SPECIFIC_FIELDS and field_name not in asked:
            continue
        if case.body_corporate and field_name in PERSON_ONLY_FIELDS:
            continue
        current = getattr(case, field_name)
        value = getattr(extraction.suggested, s_name)
        if current == "unknown" and value not in ("unknown", None):
            out[field_name] = value
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 3. The confirmed facts used by every later step
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ConfirmedFacts:
    case: CaseInput
    summary: str
    facts: list[dict]                        # rows from verify_quotes(), possibly edited by the user
    missing: list[dict] = field(default_factory=list)
    scope_warnings: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    charge_fit: str = ""
    applied_suggestions: dict = field(default_factory=dict)

    def fact_ids(self) -> set[str]:
        return {f["id"] for f in self.facts}

    def facts_text(self) -> str:
        """The facts, written out for the AI calls (F-ids are cited)."""
        lines = []
        for f in self.facts:
            q = f.get("source_quote") or ""
            tag = f"[{f['topic']} | {f['bearing']} | basis: {f['basis']}]"
            if f.get("quote_verified") is False:
                tag += " (quote not found in the narrative — treat as unverified)"
            lines.append(f"{f['id']} {tag} {f['text']}" + (f'  (“{q}”)' if q else ""))
        if self.missing:
            lines.append("\nNot supplied (the estimate assumes):")
            for m in self.missing:
                lines.append(f"- {m['question']} → assumed: {m['assumption']}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Simple input: the AI sorts a plain description into the form's questions
#    (before AI call 1). Nothing is guessed:
#    * an answer counts only if the words the model gives for it are in the
#      description — whole words, at least two (three for an answer that counts
#      against the offender), and, for such an answer, not negated, disputed or
#      only alleged in its sentence (check_words);
#    * an answer that counts against the offender is never applied by itself:
#      the user ticks it, and only if it is proved;
#    * every other question stays "unknown" (never "no"), the required questions
#      stay empty until the description or the user answers them, and the age
#      at sentence, if not stated, is the age at the offence, marked "assumed".
# ─────────────────────────────────────────────────────────────────────────────
SORT_STEP = "Sorting the description"
REQUIRED_FOR_SIMPLE = {k: v for k, v in REQUIRED_FIELDS.items() if k != "age_at_sentence"}


def required_for_simple(offender_type: str | None) -> dict:
    return {k: v for k, v in required_fields(offender_type).items() if k != "age_at_sentence"}
SORTED_CHOICE_FIELDS = ["prior_convictions", "serving_sentence_or_on_parole", "mental_impairment_evidence",
                        "intoxicated_by_liquor", "intoxication_contributed", "previous_aeo", "cooperation",
                        "victim_protected_worker", "offender_knew_or_reckless", "accessory_only", "presentence_report",
                        "cco_consent", "arising_from_driving", "manslaughter_circumstance", "prosecution_notice",
                        "official_injured", "category_condition_met", "conditional_condition_met"]
# What a simple-input case says for every question the description leaves open.
SIMPLE_DEFAULTS = {
    "offence_id": None, "court": None, "offence_date": None, "plea": None, "age_at_offence": None,
    "age_at_sentence": None, "age_at_sentence_assumed": False,
    "user_role": "other", "offence_date_end": None, "plea_timing": "unknown", "prior_convictions": "unknown",
    "serving_sentence_or_on_parole": "unknown", "mental_impairment_evidence": "unknown",
    "intoxicated_by_liquor": "unknown", "intoxication_contributed": "unknown", "previous_aeo": "unknown",
    "cooperation": "unknown", "victim_protected_worker": "unknown", "offender_knew_or_reckless": "unknown",
    "protected_scheme_confirmed": "unknown", "accessory_only": "unknown", "minor_involvement_proved": "unknown",
    "presentence_detention_days": None, "presentence_report": "unknown", "cco_consent": "unknown",
    "indication_given": False, "indication_type": "imprisonment", "indication_max_months": None,
    "indication_plea_entered_on_it": "unknown", "indication_same_judge": "unknown", "arising_from_driving": "unknown",
    "alcohol_influenced_driving": "unknown", "licence_status": "unknown",
    "manslaughter_circumstance": "unknown", "prosecution_notice": "unknown", "official_injured": "unknown",
    "category1_confirmed": "unknown", "category_condition_met": "unknown", "historic_provision_marked": "unknown",
    "conditional_condition_met": "unknown", "school_condition_confirmed": "unknown", "contempt_by_higher_court": "unknown",
    "sso_variant_met": "unknown", "primary_offence_id": None, "relative_maximum_months": None,
    "relative_maximum_life": False, "offender_type": "natural_person", "motor_vehicle_stolen": "unknown",
    "previous_s61_3_offence": "unknown", "aeo_relevant_offence": "unknown",
}


@dataclass
class SortedInput:
    """What the AI found in a plain description, checked by code."""
    values: dict                       # field -> an answer whose words are in the description (written into the form)
    adverse: dict                      # field -> an answer that counts against the offender, with its words in the
                                       #          description: used only if the user ticks it
    quotes: dict                       # field -> the words that show the answer
    context: dict                      # field -> the sentence those words are in (shown with an answer to tick)
    unverified: dict                   # field -> (answer, why it is not used)
    problems: list[str]                # answers the AI gave that are not valid (an impossible date, an age out of range)
    missing: list[str]                 # required questions (REQUIRED_FOR_SIMPLE) the description does not answer
    outside: list[str]                 # anything outside version 1 (the model's notes, then code's)
    not_stated: list[str]              # open questions that matter
    offence_as_described: str
    offence_year: int | None
    description: str                   # the text sorted (to notice later edits)


def _iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm_quote(text))


# Words that make a statement doubtful for an answer that counts against the offender: it is negated, disputed,
# only alleged or uncertain. Checked in the quote's sentence, up to the end of the quote.
NEGATION_WORDS = {"no", "not", "never", "nor", "neither", "without"}
DOUBT_WORDS = NEGATION_WORDS | {
    "deny", "denies", "denied", "dispute", "disputes", "disputed", "contest", "contests", "contested", "allege",
    "alleges", "alleged", "allegedly", "unproven", "unproved", "unclear", "uncertain", "unknown", "may", "might",
    "possibly", "perhaps", "whether", "if", "suspected", "claims", "claimed"}
# A plea or a verdict must also not be pending, future or hypothetical ("will stand trial", "awaiting trial").
PLEA_DOUBT_WORDS = DOUBT_WORDS | {"will", "would", "should", "could", "awaiting", "await", "awaits", "pending",
                                  "listed", "yet", "expected", "expects", "intends", "intend", "intended", "plans",
                                  "proposed", "propose", "initially"}
# Answers against the offender that are themselves negative: their own "no" or "not" is not a doubt.
NEGATIVE_ANSWERS = {("cco_consent", "no"), ("presentence_report", "none"), ("mental_impairment_evidence", "no"),
                    ("cooperation", "none"), ("manslaughter_circumstance", "neither"), ("prosecution_notice", "no"),
                    ("official_injured", "no"), ("category_condition_met", "no"), ("conditional_condition_met", "no")}
# The words a plea of guilty, or a verdict of guilty, must contain (as whole-word sequences).
_PLEA_PHRASES = [("pleaded", "guilty"), ("pled", "guilty"), ("pleads", "guilty"), ("plea", "of", "guilty"),
                 ("guilty", "plea"), ("pleading", "guilty")]
_VERDICT_PHRASES = [("found", "guilty"), ("convicted",), ("verdict", "of", "guilty"), ("guilty", "verdict"),
                    ("jury", "found"), ("jury", "convicted")]
_SENTENCES = re.compile(r"(?<=[.!?])\s+")


def _contains_phrase(words: list[str], phrases) -> bool:
    return any(words[i:i + len(ph)] == list(ph) for ph in phrases for i in range(len(words) - len(ph) + 1))


def find_words(quote: str, text: str, minimum: int = 2, doubt: frozenset | set = frozenset()) -> tuple[str, str]:
    """(why the quote does not show an answer, or ""; the sentence it was found in). The quote's words must appear,
    whole, in one sentence of the text; there must be at least `minimum` of them, one a real word or a number; and
    none of the `doubt` words may come in that sentence before the end of the quote."""
    q = _words(quote)
    shown = f"“{quote.strip()}”"
    if not q:
        return "no words from the description were given for it", ""
    if len(q) < minimum or not any(len(w) >= 3 or w.isdigit() for w in q):
        return f"the words given ({shown}) are too few to show it", ""
    found = False
    for sentence in _SENTENCES.split(text or ""):
        words = _words(sentence)
        for i in range(len(words) - len(q) + 1):
            if words[i:i + len(q)] == q:
                found = True
                if doubt & set(words[:i + len(q)]):
                    continue
                return "", sentence.strip()
    if not found:
        return f"the words given ({shown}) are not in the description", ""
    return f"the description negates, disputes, only alleges or does not settle it ({shown})", ""


def check_words(quote: str, text: str, adverse: bool = False, negative: bool = False) -> str:
    """"" if the quote shows an answer (see find_words): at least two words, three for an answer that counts against
    the offender, and for such an answer no doubt word before the end of the quote in its sentence (a negative
    answer — "does not consent" — may contain its own negation). Otherwise, why the answer is not used."""
    doubt = (DOUBT_WORDS - NEGATION_WORDS if negative else DOUBT_WORDS) if adverse else frozenset()
    return find_words(quote, text, 3 if adverse else 2, doubt)[0]


def quote_in(quote: str, text: str) -> bool:
    """True if the words show an answer that does not count against the offender (see check_words)."""
    return check_words(quote, text) == ""


def sorted_input(r: SortedCase, description: str) -> SortedInput:
    """The model's answer turned into form values. Only valid answers are kept, and only those whose words show
    them (check_words); an answer that counts against the offender is kept apart (`adverse`) for the user to tick."""
    v: dict = {}
    problems: list[str] = []
    if r.offence_id in config.IN_SCOPE_OFFENCES:
        v["offence_id"] = r.offence_id
    if r.court in config.IN_SCOPE_COURTS:
        v["court"] = r.court
    if r.offence_date.strip():
        start = _iso_date(r.offence_date)
        if start and date.fromisoformat(config.EARLIEST_OFFENCE_DATE) <= start <= melbourne_today():
            v["offence_date"] = start
            end = _iso_date(r.offence_date_end)
            if end and start < end <= melbourne_today():
                v["offence_date_end"] = end
        else:
            problems.append(f"The AI read the date of the offence as “{r.offence_date.strip()}”, which is not a "
                            f"possible date: give the date yourself.")
    if r.plea == "guilty":
        v["plea"] = "guilty"
        if r.plea_timing not in ("unknown", "not_applicable"):
            v["plea_timing"] = r.plea_timing
    elif r.plea == "found_guilty_at_trial":
        v["plea"] = "not_guilty"                          # the form's value for "found guilty at trial"
    if r.age_at_offence is not None:
        if 10 <= r.age_at_offence <= 100:
            v["age_at_offence"] = int(r.age_at_offence)
            if r.age_at_sentence is not None and r.age_at_offence <= r.age_at_sentence <= 110:
                v["age_at_sentence"] = int(r.age_at_sentence)
        else:
            problems.append(f"The AI read the offender's age as {r.age_at_offence}, which is not possible: give the "
                            f"age yourself.")
    for name in SORTED_CHOICE_FIELDS:
        value = getattr(r, name)
        if value != "unknown":
            v[name] = value
    if r.offender_type == "body_corporate":                  # a natural person is the form's default
        v["offender_type"] = "body_corporate"
    if r.presentence_detention_days is not None and 0 <= r.presentence_detention_days <= 20000:
        v["presentence_detention_days"] = int(r.presentence_detention_days)
    if r.case_name.strip():
        v["case_name"] = r.case_name.strip()

    quotes = {}
    for q in r.quotes:
        name, words = q.field.strip(), q.quote.strip()
        if name in v and words and name not in quotes:
            quotes[name] = words
    values, adverse, unverified, context = {}, {}, {}, {}
    for name, value in v.items():
        # An answer to a charge's own question — "yes" or "no" — settles a fact the scheme turns on (a minimum, a
        # category, a higher maximum, or the "if proved" reading that shows it): used only if the user ticks it.
        bad = is_adverse(name, value) or name in CHARGE_SPECIFIC_FIELDS
        words = quotes.get(name, "")
        if name == "plea":                                 # a plea or verdict: settled, and in so many words
            why, sentence = find_words(words, description, 2, PLEA_DOUBT_WORDS)
            phrases = _PLEA_PHRASES if value == "guilty" else _VERDICT_PHRASES
            if not why and not _contains_phrase(_words(words), phrases):
                why = ("the words given do not show a plea of guilty" if value == "guilty" else
                       "the words given do not show a verdict of guilty after a trial")
        elif bad:
            negative = (name, value) in NEGATIVE_ANSWERS
            why, sentence = find_words(words, description, 3,
                                       DOUBT_WORDS - NEGATION_WORDS if negative else DOUBT_WORDS)
        else:
            why, sentence = find_words(words, description, 2)
        if why:
            unverified[name] = (value, why)
        else:
            (adverse if bad else values)[name] = value
            context[name] = sentence
    if values.get("offender_type") == "body_corporate":      # a body corporate (its words checked): no person's
        for d in (values, adverse, unverified, context):          # answers
            for k in [k for k in d if k in PERSON_ONLY_FIELDS]:
                del d[k]
    if values.get("plea") == "not_guilty":
        values["plea_timing"] = "not_applicable"          # follows from the verdict; needs no words of its own
    elif "plea" not in values:
        values.pop("plea_timing", None)                    # a plea's timing without the plea is not used

    # Code's own scope notes (the model's notes come first).
    outside = [x.strip() for x in r.outside_version_1 if x.strip()]
    if r.court in ("Magistrates' Court", "Children's Court"):
        outside.append(f"The description names the {r.court}; the app covers the County Court and Supreme Court only.")
    if r.offence_id == "other":
        outside.append("The offence described (" + (r.offence_as_described.strip() or "not named") + ") is not one of "
                       "the offences in the Manual's register.")
    if values.get("age_at_offence") is not None and values["age_at_offence"] < config.MIN_AGE_AT_OFFENCE:
        outside.append(f"The offender was {values['age_at_offence']} at the offence; the app covers adult "
                       f"offenders only.")
    return SortedInput(
        values=values, adverse=adverse, quotes=quotes, context=context, unverified=unverified, problems=problems,
        missing=[label for f, label in required_for_simple(values.get("offender_type")).items() if f not in values],
        outside=outside, not_stated=[x.strip() for x in r.not_stated if x.strip()][:6],
        offence_as_described=r.offence_as_described.strip(), offence_year=r.offence_year, description=description)


def sort_description(llm: LLM, description: str, kb: KnowledgeBase) -> tuple[SortedInput, CallRecord]:
    """The simple input's AI call (fast model): the description sorted into the form's questions."""
    listing, codes = kb.offence_code_list()
    text = "## Offence codes you may use\n" + listing + f"\n\n## The description\n{description.strip()}"
    result, record = llm.structured(SORT_STEP, "extraction", prompts.SORTING, text, SortedCase)
    answer = result.offence_id.strip()
    # A code is mapped to its offence id; an answer that is neither a listed code nor "other" is treated as unclear.
    result.offence_id = codes.get(answer, answer if answer in config.IN_SCOPE_OFFENCES or answer == "other"
                                  else "unclear")
    return sorted_input(result, description), record


def simple_case_values(sorted_values: dict, chosen: dict) -> dict:
    """The form values of a simple-input case: every open question "unknown" and every required one empty
    (SIMPLE_DEFAULTS), then what the AI found (its answers that do not count against the offender), then the user's
    own answers (`chosen`: the fields the user answered, which always come first). An age at sentence that neither
    gives is the age at the offence, marked as assumed. Nothing else is filled in."""
    own = {k: v for k, v in chosen.items() if k in SIMPLE_DEFAULTS or k == "case_name"}   # None included
    values = dict(SIMPLE_DEFAULTS)
    values.update(sorted_values)
    values.update(own)
    if values.get("plea") == "not_guilty":
        values["plea_timing"] = "not_applicable"
    elif values.get("plea") is None:
        values["plea_timing"] = "unknown"
    if "age_at_sentence" in sorted_values or "age_at_sentence" in own:
        values["age_at_sentence_assumed"] = False
    else:
        values["age_at_sentence"] = values.get("age_at_offence")
        values["age_at_sentence_assumed"] = values.get("age_at_offence") is not None
    return values
