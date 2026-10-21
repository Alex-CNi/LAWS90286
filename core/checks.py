"""
core/checks.py — STEP 6: THE HARD CHECKS, RUN IN CODE ON THE PROPOSED SENTENCE.

Each function below implements one "check: hard" audit item from
knowledge/audit.json (the item id is the CheckResult's check_id), limited to
what version 1 can propose: one charge, an adult, the County or Supreme Court.
The limb numbers in the comments follow the item's "test" text.

Outcomes follow the knowledge base's rules:
  * "error" only where the Manual makes the result unlawful or erroneous on
    every reading. The checks that depend on Category 1/2 or a minimum are run
    under every reading the framework records (core/framework.py, Reading), and
    merge_readings() keeps an error only if it is an error under all of them;
  * anything that depends on a Manual flag is a "caution" carrying the flag id;
  * the app's own rules (e.g. about the band) are cautions marked app policy.

To add a check: write a function taking (ctx) and returning a list of
CheckResult, and add it to ALL_CHECKS (or READING_CHECKS if it depends on the
category or the minimum) at the bottom.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import config
from core.facts import ConfirmedFacts
from core.framework import Framework
from core.kb import KnowledgeBase
from core.results import CheckResult
from core.schemas import Proposal

IMPRISONMENT_TYPES = {"imprisonment", "imprisonment_and_cco"}
CCO_TYPES = {"cco", "imprisonment_and_cco", "mtmo"}
CCO_CONDITIONS_ALLOWED = {
    "unpaid_community_work", "treatment_and_rehabilitation", "supervision", "non_association",
    "residence_restriction_or_exclusion", "place_or_area_exclusion", "curfew", "alcohol_exclusion",
    "judicial_monitoring", "electronic_monitoring", "bond", "justice_plan",
}
# principle.sentence_type_ordering (app policy): imprisonment (incl. combination)
# > CCO > fine > {adjourned undertaking, discharge, dismissal}. Any comparison
# involving another order (an MTMO, with or without a term) is unsettled.
SEVERITY = {"imprisonment": 3, "imprisonment_and_cco": 3, "cco": 2, "fine": 1,
            "adjourned_undertaking": 0, "discharge": 0, "dismissal": 0}
DAYS_PER_MONTH = 30.4375


@dataclass
class Ctx:
    kb: KnowledgeBase
    facts: ConfirmedFacts
    fw: Framework
    p: Proposal

    @property
    def case(self):
        return self.facts.case

    @property
    def has_custody(self) -> bool:
        """Imprisonment is part of the sentence (including an MTMO combined with a term)."""
        return self.p.disposition_type in IMPRISONMENT_TYPES or (self.p.disposition_type == "mtmo" and bool(self.p.term_months))

    @property
    def is_combination(self) -> bool:
        return self.p.disposition_type == "imprisonment_and_cco" or (self.p.disposition_type == "mtmo" and bool(self.p.term_months))

    @property
    def is_protected_injury(self) -> bool:
        """ss 16, 17, 18 against a protected official (not gross violence), on this reading."""
        return self.fw.mm_group == "protected_official" and self.case.offence_id not in config.GROSS_VIOLENCE_OFFENCES

    @property
    def is_gross_violence(self) -> bool:
        return self.case.offence_id in config.GROSS_VIOLENCE_OFFENCES


def R(check_id, title, outcome, message, **kw) -> CheckResult:
    return CheckResult(check_id=check_id, title=title, outcome=outcome, message=message, **kw)


def months_text(m) -> str:
    if m is None:
        return "—"
    y, mo = divmod(int(m), 12)
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{mo} month{'s' if mo != 1 else ''}"] if mo else [])
    return " ".join(parts) or "0 months"


def kb_statement(ctx: Ctx, item_id: str) -> str:
    return ctx.kb.statement(item_id) or item_id


# ─────────────────────────────────────────────────────────────────────────────
# 0. The proposal is complete enough to check (app policy)
# ─────────────────────────────────────────────────────────────────────────────
def check_complete(ctx: Ctx) -> list[CheckResult]:
    p, out = ctx.p, []
    need = {"imprisonment": ["term_months"], "imprisonment_and_cco": ["term_months", "cco"], "cco": ["cco"],
            "mtmo": ["cco"], "fine": ["fine_penalty_units"], "adjourned_undertaking": ["undertaking_months"]}
    for name in need.get(p.disposition_type, []):
        value = getattr(p, name)
        if value is None or (isinstance(value, int) and value <= 0):
            out.append(R("app.proposal_complete", "Proposal incomplete", "error",
                         f"A {p.disposition_type.replace('_', ' ')} needs {name.replace('_', ' ')}, which is missing.",
                         app_policy=True))
    if p.npp_months and not ctx.has_custody:
        out.append(R("audit.non_parole_rules", "Non-parole period without imprisonment", "error",
                     "A non-parole period can only accompany a term of imprisonment.", cite=["npp.definition"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1. audit.maximum_applicable — within the maximum at the offence date
# ─────────────────────────────────────────────────────────────────────────────
def check_maximum(ctx: Ctx) -> list[CheckResult]:
    m, p, out = ctx.fw.maximum, ctx.p, []
    cite = ["audit.maximum_applicable", "maximum.misidentification"]
    if m.months is None:
        return [R("audit.maximum_applicable", "Maximum", "unresolved",
                  f"The maximum cannot be determined from the Manual: {m.unresolved}", cite=cite)]
    if p.term_months and p.term_months > m.months:
        out.append(R("audit.maximum_applicable", "Above the maximum", "error",
                     f"The term ({months_text(p.term_months)}) exceeds the maximum of {months_text(m.months)} "
                     f"({m.text}).", limb="(8)", cite=cite))
    if p.band.term_high_months and p.band.term_high_months > m.months:
        out.append(R("audit.maximum_applicable", "Band above the maximum", "caution",
                     f"The band's upper end ({months_text(p.band.term_high_months)}) exceeds the maximum; the band "
                     "should stop at the maximum.", limb="(8)", cite=cite, app_policy=True))
    if not [r for r in out if r.outcome == "error"]:
        out.append(R("audit.maximum_applicable", "Within the maximum", "pass",
                     f"Maximum {m.text} for an offence on {ctx.fw.offence_date}; the sentence is within it.", cite=cite))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. audit.category_1_2 — Category 1 and 2 offences (depends on the reading)
# ─────────────────────────────────────────────────────────────────────────────
def check_category(ctx: Ctx) -> list[CheckResult]:
    fw, p = ctx.fw, ctx.p
    if not fw.category:
        return [R("audit.category_1_2", "Category 1/2", "info", "Not a Category 1 or 2 offence on these facts.")]
    straight = p.disposition_type == "imprisonment"
    sr = p.special_reason.found
    out = []
    if fw.category == 1:
        cite = ["cat1.custodial_required"]
        if straight:
            out.append(R("audit.category_1_2", "Category 1", "pass", "Category 1: imprisonment without a CCO.", cite=cite))
        elif ctx.is_protected_injury and sr and p.disposition_type == "mtmo":
            if p.term_months:
                out.append(R("audit.category_1_2", "Category 1 — MTMO with imprisonment", "caution",
                             "A mandatory treatment and monitoring order combined with imprisonment on a protected-official "
                             "charge: the Manual does not say whether this is open.", flags=["flag.mtmo_combination_sentence"],
                             limb="(a)", cite=cite))
            else:
                out.append(R("audit.category_1_2", "Category 1 — special-reason route", "pass",
                             "Special reason on a protected-official injury charge: a mandatory treatment and monitoring "
                             "order is one of the permitted dispositions (route (a)), subject to its conditions (see "
                             "“Therapeutic order conditions”).", limb="(a)", cite=cite + ["mmnpp.protected_officials"]))
        elif ctx.is_gross_violence and sr:
            flag = ("flag.gross_violence_protected_special_reason" if fw.applied_protected == "yes"
                    else "flag.category1_special_reason")
            out.append(R("audit.category_1_2", "Category 1 — special reason, other disposition", "caution",
                         "A special reason on a gross-violence charge: whether any disposition other than imprisonment "
                         "without a CCO is open is unresolved in the Manual.", flags=[flag],
                         limb="(c)" if fw.applied_protected == "yes" else "(d)", cite=cite))
        else:
            out.append(R("audit.category_1_2", "Category 1 — disposition not permitted", "error",
                         f"{fw.offence_label} is a Category 1 offence on this reading: imprisonment without a CCO is "
                         "required" + (" (a special reason opens only the routes the framework lists)" if sr else "") + ".",
                         cite=cite))
    else:
        cite = ["cat2.custodial_unless", "cat2.substantial_compelling_test"]
        exc = p.category2_exception
        if straight:
            out.append(R("audit.category_1_2", "Category 2", "pass", "Category 2: imprisonment without a CCO.", cite=cite))
        elif exc.relied_on and exc.exception and exc.evidential_basis.strip():
            out += _category2_exception(ctx, exc, cite)
        else:
            out.append(R("audit.category_1_2", "Category 2 — no exception established", "error",
                         "Category 2: imprisonment without a CCO is required unless an exception is identified with "
                         "its evidential basis.", cite=cite))
    # The band should keep to the same rule (app policy: a caution, not an error).
    band_types = set(p.band.disposition_types) - {"imprisonment"}
    exception = (fw.category == 2 and p.category2_exception.relied_on) or (fw.category == 1 and sr)
    if band_types and not exception:
        out.append(R("audit.category_1_2", "Band includes dispositions the category excludes", "caution",
                     f"The band includes {', '.join(sorted(band_types))}, which Category {fw.category} excludes "
                     "without an exception.", app_policy=True))
    return out


def _category2_exception(ctx: Ctx, exc, cite) -> list[CheckResult]:
    """The exception must fit the facts (cat2.custodial_unless)."""
    case = ctx.case
    name = exc.exception
    if name == "therapeutic_order":
        return [R("audit.category_1_2", "Category 2 — therapeutic-order exception", "error",
                  "This exception exists only where the court proposes a court secure treatment order or a "
                  "residential treatment order, which is not the sentence proposed.", cite=cite)]
    if name == "assistance":
        if case.cooperation in ("none", "admissions_only"):
            return [R("audit.category_1_2", "Category 2 — assistance exception", "error",
                      "The assistance exception needs information supplied to bring others to justice or to prevent "
                      "crime; an admission is not enough, and the facts record no assistance.", cite=cite)]
        if case.cooperation == "unknown":
            return [R("audit.category_1_2", "Category 2 — assistance exception", "caution",
                      "The assistance exception is relied on, but the facts do not record the assistance.", cite=cite)]
    if name in ("impaired_culpability", "impaired_burden") and case.mental_impairment_evidence != "yes":
        return [R("audit.category_1_2", "Category 2 — impaired mental functioning", "caution",
                  "The offender must prove impaired mental functioning on the balance of probabilities; no expert "
                  "report is recorded" + (" (unknown)." if case.mental_impairment_evidence == "unknown" else "."),
                  cite=cite)]
    return [R("audit.category_1_2", "Category 2 — exception relied on", "pass",
              f"Category 2 exception relied on ({name.replace('_', ' ')}), with its evidential basis stated. The audit "
              "checks whether it is made out.", cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 3. audit.mandatory_minimum — minimum non-parole periods (depends on the reading)
# ─────────────────────────────────────────────────────────────────────────────
def check_mandatory_minimum(ctx: Ctx) -> list[CheckResult]:
    fw, p = ctx.fw, ctx.p
    if not fw.mnpp_months:
        return [R("audit.mandatory_minimum", "Mandatory minimum", "info",
                  "No mandatory minimum non-parole period applies on these facts.")]
    cite = (["mmnpp.gross_violence_4y"] if fw.mm_group == "gross_violence" else ["mmnpp.protected_officials"]) \
        + ["mm.special_reason"]
    sr = p.special_reason
    if fw.mnpp_excluded:
        out = [R("audit.mandatory_minimum", "Minimum excluded", "info", fw.mnpp_excluded, cite=cite)]
        relies = p.disposition_type == "mtmo" or (fw.category == 1 and p.disposition_type != "imprisonment")
        if sr.found and relies:
            # The proposal still relies on a special reason to reach a disposition other than imprisonment
            # (on a Category 1 charge, or an MTMO): the special reason must be properly identified (mm.special_reason).
            out += _special_reason_form(ctx, sr, cite)
            out.append(R("audit.mandatory_minimum", "Special reason with the minimum excluded", "caution",
                         "The minimum does not apply to this offender. Whether a special reason can still open the "
                         "dispositions that replace imprisonment (on a Category 1 charge, or a mandatory treatment and "
                         "monitoring order) is not settled by the Manual.", cite=cite, app_policy=True))
        return out
    out = []
    if sr.found:
        out += _special_reason_form(ctx, sr, cite)
        if ctx.is_protected_injury:
            if p.disposition_type == "mtmo" and p.term_months:
                out.append(R("audit.mandatory_minimum", "MTMO combined with imprisonment", "caution",
                             "Whether imprisonment combined with an MTMO is open on a protected-official charge is "
                             "unresolved in the Manual.", flags=["flag.mtmo_combination_sentence"], cite=cite))
            elif p.disposition_type not in ("imprisonment", "mtmo"):
                out.append(R("audit.mandatory_minimum", "Special reason — disposition not permitted", "error",
                             "On a protected-official injury charge a special reason permits only imprisonment "
                             "(without a CCO) or a mandatory treatment and monitoring order, residential treatment "
                             "order or court secure treatment order.", cite=cite))
        elif ctx.is_gross_violence and fw.applied_protected == "yes" and p.disposition_type != "imprisonment":
            out.append(R("audit.mandatory_minimum", "Gross violence against a protected official", "caution",
                         "The disposition that follows a special reason on this charge is unresolved in the Manual.",
                         flags=["flag.gross_violence_protected_special_reason"], cite=cite))
        if not [r for r in out if r.outcome in ("error", "caution")]:
            out.append(R("audit.mandatory_minimum", "Special reason identified", "pass",
                         f"Special reason ({sr.limb.replace('_', ' ')}) identified with its basis and recorded in "
                         "writing; the minimum need not be imposed. The audit checks whether it is made out.",
                         cite=cite))
        return out
    # Without a special reason the test is the non-parole period (logic: npp_months >= MIN). A term combined
    # with a CCO is tested the same way; its own non-parole rules are checked in check_non_parole.
    if p.disposition_type not in IMPRISONMENT_TYPES:
        out.append(R("audit.mandatory_minimum", "Minimum requires imprisonment", "error",
                     f"A mandatory minimum non-parole period of {months_text(fw.mnpp_months)} applies: the sentence "
                     "must be imprisonment with at least that non-parole period, unless a special reason is found.",
                     cite=cite))
    elif (p.npp_months or 0) < fw.mnpp_months:
        fixed = (f"The non-parole period ({months_text(p.npp_months)}) is below the minimum of "
                 f"{months_text(fw.mnpp_months)}" if p.npp_months else
                 "No non-parole period is fixed" + (" (none can be for a term under 12 months)"
                                                      if (p.term_months or 0) < 12 else "")
                 + f", but the minimum non-parole period is {months_text(fw.mnpp_months)}")
        out.append(R("audit.mandatory_minimum", "Below the minimum non-parole period", "error",
                     f"{fixed}, and no special reason is found.", cite=cite))
    else:
        out.append(R("audit.mandatory_minimum", "Minimum met", "pass",
                     f"Non-parole period {months_text(p.npp_months)} ≥ the minimum {months_text(fw.mnpp_months)}."
                     + (" The term is combined with a CCO: the non-parole rules for that combination still apply."
                        if p.disposition_type == "imprisonment_and_cco" else ""), cite=cite))
    if p.band.npp_low_months is not None and p.band.npp_low_months < fw.mnpp_months:
        out.append(R("audit.mandatory_minimum", "Band below the minimum", "caution",
                     "The band's lowest non-parole period is below the mandatory minimum, and no special reason is "
                     "found.", app_policy=True))
    return out


def _special_reason_form(ctx: Ctx, sr, cite) -> list[CheckResult]:
    """mm.special_reason: identify which of the four circumstances is relied on and its
    evidential basis, and state it in writing; the limb must fit the facts."""
    out = []
    if not (sr.limb and sr.evidential_basis.strip() and sr.stated_in_writing):
        out.append(R("audit.mandatory_minimum", "Special reason not properly identified", "error",
                     "A special reason must identify which of the four circumstances is relied on, its evidential "
                     "basis, and that it is stated in writing and recorded.", cite=cite))
    return out + _special_reason_limb(ctx, sr, cite)


def _special_reason_limb(ctx: Ctx, sr, cite) -> list[CheckResult]:
    """The limb relied on must fit the facts and the sentence (mm.special_reason)."""
    case = ctx.case
    if sr.limb == "csto_or_rto":
        return [R("audit.mandatory_minimum", "Special reason — CSTO or RTO", "error",
                  "This circumstance exists only if the court intends to impose a court secure treatment order or a "
                  "residential treatment order, which is not the sentence proposed.", cite=cite)]
    if sr.limb == "assistance" and case.cooperation in ("none", "admissions_only"):
        return [R("audit.mandatory_minimum", "Special reason — assistance", "error",
                  "The facts record no assistance or undertaking to assist the authorities.", cite=cite)]
    if sr.limb == "assistance" and case.cooperation == "unknown":
        return [R("audit.mandatory_minimum", "Special reason — assistance", "caution",
                  "Assistance is relied on but not recorded in the facts.", cite=cite)]
    if sr.limb == "mental_impairment" and case.mental_impairment_evidence != "yes":
        return [R("audit.mandatory_minimum", "Special reason — mental impairment", "caution",
                  "The offender must prove the impairment on the balance of probabilities; no expert report is "
                  "recorded.", cite=cite)]
    return []


def check_therapeutic_order(ctx: Ctx) -> list[CheckResult]:
    """VSM 9.1.2.3: a therapeutic order (here an MTMO) on a protected-official
    injury charge "only if" the offender proves impaired mental functioning,
    causally linked, not caused substantially by self-induced intoxication, and
    the court has received a psychiatrist's or registered psychologist's report."""
    p, case = ctx.p, ctx.case
    if not (ctx.is_protected_injury and p.special_reason.found and p.disposition_type == "mtmo"):
        return []
    cite = ["mmnpp.protected_officials", "order.mtmo"]
    if case.mental_impairment_evidence == "no":
        return [R("audit.mandatory_minimum", "Therapeutic order conditions", "error",
                  "An MTMO in place of imprisonment needs a psychiatrist's or registered psychologist's report and proof "
                  "of impaired mental functioning causally linked to the offence; no report is recorded.", cite=cite)]
    out = []
    if case.mental_impairment_evidence == "unknown":
        out.append(R("audit.mandatory_minimum", "Therapeutic order conditions", "caution",
                     "An MTMO needs a psychiatrist's or registered psychologist's report; whether there is one is "
                     "unknown.", cite=cite))
    if case.intoxication_contributed == "yes":
        out.append(R("audit.mandatory_minimum", "Therapeutic order — intoxication", "caution",
                     "The impairment must not have been caused substantially by self-induced intoxication; the facts "
                     "record intoxication contributing to the offence.", cite=cite))
    if not out:
        out.append(R("audit.mandatory_minimum", "Therapeutic order conditions", "pass",
                     "A report on the offender's mental impairment is recorded. The offender must still prove impaired "
                     "functioning (causally linked, substantially and materially reducing culpability) and the court "
                     "must be satisfied the order is appropriate.", cite=cite))
    return out


def check_mtmo_available(ctx: Ctx) -> list[CheckResult]:
    """order.mtmo: a mandatory treatment and monitoring order is made when, sentencing
    for offending against protected workers, the court finds special reasons
    (logic: "special reasons found (mm.special_reason)"). On ss 15A/15B against a
    protected official the disposition after a special reason is unresolved
    (flag.gross_violence_protected_special_reason: a caution in check_mandatory_minimum)."""
    p, fw = ctx.p, ctx.fw
    if p.disposition_type != "mtmo":
        return []
    found = p.special_reason.found
    if (ctx.is_protected_injury or (ctx.is_gross_violence and fw.applied_protected == "yes")) and found:
        return []
    why = ("no special reason is found" if (ctx.is_protected_injury or fw.applied_protected == "yes")
           else "the protected-official provisions do not apply on this reading"
           + ("" if found else ", and no special reason is found"))
    return [R("audit.cco_limits", "MTMO not available", "error",
              "A mandatory treatment and monitoring order is made only when, sentencing for offending against a "
              f"protected official, the court finds special reasons; here {why}. If a CCO with judicial monitoring "
              "and treatment conditions is intended, propose it as a CCO.", limb="(7)",
              cite=["order.mtmo", "mmnpp.protected_officials", "mm.special_reason"])]


# ─────────────────────────────────────────────────────────────────────────────
# 4. audit.non_parole_rules
# ─────────────────────────────────────────────────────────────────────────────
def check_non_parole(ctx: Ctx) -> list[CheckResult]:
    p = ctx.p
    if not ctx.has_custody:
        return []
    tes, npp, out = p.term_months or 0, p.npp_months, []
    cite = ["npp.must_fix_two_years", "npp.none_under_12_months", "npp.six_month_gap"]
    if tes >= 24 and not npp and not (p.npp_reason or "").strip():
        out.append(R("audit.non_parole_rules", "Non-parole period required", "error",
                     "A term of two years or more needs a non-parole period, unless the court states why one is "
                     "inappropriate.", cite=["npp.must_fix_two_years"]))
    if tes < 12 and npp:
        out.append(R("audit.non_parole_rules", "No non-parole period under 12 months", "error",
                     "A non-parole period cannot be fixed if the term is less than 12 months.",
                     cite=["npp.none_under_12_months"]))
    if npp and npp > tes - 6:
        out.append(R("audit.non_parole_rules", "Six-month gap", "error",
                     "The non-parole period must be at least six months less than the term.",
                     cite=["npp.six_month_gap"]))
    if ctx.is_combination and npp:
        if 12 < tes < 24:
            out.append(R("audit.non_parole_rules", "No non-parole period with a CCO", "error",
                         "A term of more than 12 and less than 24 months combined with a CCO cannot have a "
                         "non-parole period.", cite=["npp.none_with_cco_12m_2y"]))
        elif tes == 12:
            out.append(R("audit.non_parole_rules", "12 months with a CCO", "caution",
                         kb_statement(ctx, "flag.npp_cco_twelve_months"), flags=["flag.npp_cco_twelve_months"]))
    if npp and tes and tes < 120 and not (0.6 * tes <= npp <= 0.75 * tes) and not (p.npp_reason or "").strip():
        out.append(R("audit.non_parole_rules", "Unusual non-parole period", "caution",
                     f"The non-parole period is {npp / tes:.0%} of the term, outside the usual 60–75%; reasons should "
                     "be given.", cite=["npp.usual_range"]))
    if not out:
        out.append(R("audit.non_parole_rules", "Non-parole period", "pass",
                     "The non-parole period complies with the rules described in VSM 8.3–8.3.3.3.", cite=cite))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 5. audit.cco_limits
# ─────────────────────────────────────────────────────────────────────────────
def check_cco(ctx: Ctx) -> list[CheckResult]:
    p, case = ctx.p, ctx.case
    if p.disposition_type not in CCO_TYPES or p.cco is None:
        return []
    cco, out = p.cco, []
    cite = ["order.cco"]
    kinds = [c.kind for c in cco.conditions]
    work = [c for c in cco.conditions if c.kind == "unpaid_community_work"]
    work_hours = sum(c.hours or 0 for c in work)

    # (1) availability: consent and pre-sentence report
    if case.cco_consent == "no":
        out.append(R("audit.cco_limits", "No consent", "error", "A CCO needs the offender's consent.",
                     limb="(1)", cite=["cco.consent"]))
    elif case.cco_consent == "unknown":
        out.append(R("audit.cco_limits", "Consent not recorded", "caution",
                     "A CCO needs the offender's informed consent; it is assumed here and must be confirmed.",
                     limb="(1)", cite=["cco.consent"]))
    work_only = set(kinds) == {"unpaid_community_work"}
    if case.presentence_report != "available":
        if work_only and work_hours < 300:
            pass
        elif work_only and work_hours == 300:
            out.append(R("audit.cco_limits", "300 hours without a report", "caution",
                         kb_statement(ctx, "flag.cco_work_only_300_hours"), flags=["flag.cco_work_only_300_hours"],
                         limb="(1)"))
        elif case.presentence_report == "none":
            out.append(R("audit.cco_limits", "No pre-sentence report", "error",
                         "A pre-sentence report is required before this CCO (it is not a work-only CCO of under "
                         "300 hours).", limb="(1)", cite=["order.cco.presentence_report"]))
        else:
            out.append(R("audit.cco_limits", "Pre-sentence report", "caution",
                         f"A pre-sentence report is required before this CCO; its status is {case.presentence_report}.",
                         limb="(1)", cite=["order.cco.presentence_report"]))
    # (2) duration — higher courts: 5 years
    if cco.months > 60:
        out.append(R("audit.cco_limits", "CCO too long", "error",
                     f"A CCO imposed by the Supreme or County Court may not exceed five years ({cco.months} months proposed).",
                     limb="(2)", cite=["cco.maximum_term"]))
    # (3) combination — imprisonment after pre-sentence detention ≤ 12 months; excluded offences
    if ctx.is_combination:
        psd_days = case.presentence_detention_days
        psd_months = (psd_days or 0) / DAYS_PER_MONTH
        excess = (p.term_months or 0) - psd_months - 12           # months over the one-year ceiling
        if excess > 0.25:
            out.append(R("audit.cco_limits", "Combination sentence over one year", "error",
                         f"In a combination sentence the imprisonment, after deducting pre-sentence detention "
                         f"({psd_days or 0} days), may not exceed 12 months (proposed {months_text(p.term_months)}).",
                         limb="(3)", cite=["cco.combination_one_year", "order.cco.combination_time_served"]))
        elif excess > 0:
            out.append(R("audit.cco_limits", "Combination sentence at the one-year limit", "caution",
                         f"After deducting {psd_days or 0} days of pre-sentence detention the imprisonment is within a "
                         "week of the one-year limit; check it by dates (months are converted at 30.44 days).",
                         limb="(3)", cite=["cco.combination_one_year"], app_policy=True))
        if psd_days is None:
            out.append(R("audit.cco_limits", "Pre-sentence detention unknown", "caution",
                         "Pre-sentence detention is unknown; the one-year limit was checked as if it were nil.",
                         limb="(3)"))
        out.append(R("audit.cco_limits", "Excluded offence?", "caution",
                     "A combination sentence cannot be imposed for an “excluded offence”, which the Manual does not "
                     "define. Confirm this offence is not one.", limb="(3)",
                     flags=["flag.cco_excluded_offence_undefined"], cite=["cco.combination_excluded_offences"]))
        if cco.commences == "on_making":
            out.append(R("audit.cco_limits", "Combination CCO commencement", "error",
                         "A CCO combined with imprisonment commences on the offender's release, not before.",
                         limb="(9)", cite=["sentence.commencement_rules"]))
    elif cco.commences == "later_date" and (cco.months_after_order or 0) > 3:
        out.append(R("audit.cco_limits", "CCO commencement", "error",
                     "A CCO must commence no later than three months after it is made.", limb="(9)",
                     cite=["sentence.commencement_rules"]))
    # (4) conditions
    if not set(kinds) & CCO_CONDITIONS_ALLOWED:
        out.append(R("audit.cco_limits", "No Division 4 condition", "error",
                     "A CCO needs at least one Division 4 condition or a justice plan condition.", limb="(4)",
                     cite=["cco.conditions_required"]))
    if "restitution_or_compensation" in kinds:
        out.append(R("audit.cco_limits", "Prohibited condition", "error",
                     "Restitution, compensation, costs or damages cannot be a CCO condition.", limb="(4)",
                     cite=["cco.conditions_required"]))
    for c in work:
        if (c.hours or 0) > 600:
            out.append(R("audit.cco_limits", "Unpaid work over 600 hours", "error",
                         "Unpaid community work may not exceed 600 hours.", limb="(4)", cite=["cco.unpaid_work_limits"]))
        if not c.hours or not c.completion_months:
            out.append(R("audit.cco_limits", "Unpaid work terms", "error",
                         "The number of hours and the period within which they are to be worked must both be stated.",
                         limb="(4)", cite=["cco.unpaid_work_limits"]))
    if work_hours > 600 and len(work) > 1:
        out.append(R("audit.cco_limits", "Unpaid work total", "error", "Total unpaid work exceeds 600 hours.",
                     limb="(4)", cite=["cco.unpaid_work_limits"]))
    for c in cco.conditions:
        if c.kind == "curfew":
            if c.hours_per_day is None or c.duration_months is None:
                out.append(R("audit.cco_limits", "Curfew terms", "caution",
                             "State the curfew's hours per day and its duration so its limits can be checked.",
                             limb="(4)", cite=["cco.curfew_limits"], app_policy=True))
            elif not (2 <= c.hours_per_day <= 12) or c.duration_months > 6:
                out.append(R("audit.cco_limits", "Curfew limits", "error",
                             "A curfew must be between 2 and 12 hours a day, for no more than six months.",
                             limb="(4)", cite=["cco.curfew_limits"]))
        if c.kind == "justice_plan":
            if c.duration_months is None:
                out.append(R("audit.cco_limits", "Justice plan terms", "caution",
                             "State the justice plan condition's period (less than two years, and no longer than the "
                             "CCO) so its limits can be checked.", limb="(4)",
                             cite=["order.cco.condition_justice_plan"], app_policy=True))
            elif c.duration_months >= 24 or c.duration_months > cco.months:
                out.append(R("audit.cco_limits", "Justice plan condition", "error",
                             "A justice plan condition must be stated for less than 24 months and no longer than the "
                             "CCO.", limb="(4)", cite=["order.cco.condition_justice_plan"]))
        if c.kind == "electronic_monitoring":
            target = c.monitors
            has_target = (target == "curfew" and "curfew" in kinds) or \
                         (target == "place_or_area_exclusion" and "place_or_area_exclusion" in kinds)
            if not has_target:
                out.append(R("audit.cco_limits", "Electronic monitoring", "error",
                             "Electronic monitoring may only monitor a curfew or a place or area exclusion condition "
                             "attached to the CCO.", limb="(4)", cite=["cco.electronic_monitoring"]))
            else:
                monitored = next((x for x in cco.conditions if x.kind == target), None)
                if monitored and c.duration_months and monitored.duration_months and c.duration_months > monitored.duration_months:
                    out.append(R("audit.cco_limits", "Electronic monitoring period", "error",
                                 "Monitoring cannot last longer than the condition it monitors.", limb="(4)",
                                 cite=["cco.electronic_monitoring"]))
    # (7) MTMO conditions
    if p.disposition_type == "mtmo":
        if not ("judicial_monitoring" in kinds and ({"treatment_and_rehabilitation", "justice_plan"} & set(kinds))):
            out.append(R("audit.cco_limits", "MTMO conditions", "error",
                         "A mandatory treatment and monitoring order needs a judicial monitoring condition and a "
                         "treatment and rehabilitation condition or a justice plan condition.", limb="(7)",
                         cite=["order.mtmo"]))
    if not [r for r in out if r.outcome == "error"]:
        out.append(R("audit.cco_limits", "CCO limits", "pass",
                     "The CCO is within its availability, length, combination and condition limits.", cite=cite))
    return out


def check_cco_category(ctx: Ctx) -> list[CheckResult]:
    """audit.cco_limits (3)/(6): a CCO on a Category 1/2 charge through an
    exception route is a caution ("excluded offence" undefined); without a
    route it is an error, which check_category reports."""
    p, fw = ctx.p, ctx.fw
    if p.disposition_type not in CCO_TYPES or not fw.category or ctx.is_combination:
        return []
    route = (fw.category == 2 and p.category2_exception.relied_on) or \
            (fw.category == 1 and p.special_reason.found and p.disposition_type == "mtmo") or \
            (fw.category == 1 and ctx.is_gross_violence and p.special_reason.found)     # routes (c) and (d)
    if route:
        return [R("audit.cco_limits", "CCO on a Category 1/2 charge", "caution",
                  "A CCO on a Category 1 or 2 charge through an exception route: whether the charge is an “excluded "
                  "offence” is not settled by the Manual.", limb="(6)", flags=["flag.cco_excluded_offence_undefined"],
                  cite=["cco.combination_excluded_offences"])]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# 6. audit.sentence_each_charge_and_formulation (single charge in version 1)
# ─────────────────────────────────────────────────────────────────────────────
def check_formulation(ctx: Ctx) -> list[CheckResult]:
    if not ctx.has_custody:
        return []
    days = ctx.case.presentence_detention_days
    if days is None:
        return [R("audit.sentence_each_charge_and_formulation", "Time served", "caution",
                  "Pre-sentence detention must be declared in days; it is unknown here.", cite=["psd.declare"])]
    return [R("audit.sentence_each_charge_and_formulation", "Time served", "pass",
              f"Declare {days} days of pre-sentence detention as time already served.", cite=["psd.declare"])]


# ─────────────────────────────────────────────────────────────────────────────
# 7. audit.conviction_requirements
# ─────────────────────────────────────────────────────────────────────────────
def check_conviction(ctx: Ctx) -> list[CheckResult]:
    p = ctx.p
    t, conv = p.disposition_type, p.conviction_recorded
    cite = ["imp.conviction_required", "sentence.recording_conviction"]
    if ctx.has_custody and conv is not True:
        return [R("audit.conviction_requirements", "Conviction", "error",
                  "Imprisonment can only be imposed if a conviction is recorded; the proposal must record one.",
                  cite=cite)]
    if t == "discharge" and conv is not True:
        return [R("audit.conviction_requirements", "Conviction", "error",
                  "A discharge requires a recorded conviction.", cite=["order.discharge"])]
    if t == "dismissal" and conv is True:
        return [R("audit.conviction_requirements", "Conviction", "error",
                  "A dismissal is only available without conviction.", cite=["order.dismissal"])]
    if t in ("cco", "mtmo", "fine", "adjourned_undertaking") and conv is None:
        return [R("audit.conviction_requirements", "Conviction", "caution",
                  "State whether a conviction is recorded.", cite=cite)]
    return [R("audit.conviction_requirements", "Conviction", "pass",
              "The order and the recording of a conviction match.", cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 8. audit.s6aaa_statement
# ─────────────────────────────────────────────────────────────────────────────
def check_s6aaa(ctx: Ctx) -> list[CheckResult]:
    p, case = ctx.p, ctx.case
    cite = ["s6aaa.statement_required", "s6aaa.method"]
    order = cite + ["principle.sentence_type_ordering"]
    if case.plea != "guilty":
        if p.plea_reduced_sentence:
            return [R("audit.s6aaa_statement", "Plea", "caution",
                      "The proposal says a guilty plea reduced the sentence, but the offender was found guilty at "
                      "trial.", app_policy=True)]
        return []
    if not p.plea_reduced_sentence:
        return [R("audit.s6aaa_statement", "Section 6AAA", "info",
                  "The proposal does not reduce the sentence for the plea, so no s 6AAA statement is required.",
                  cite=cite)]
    t = p.disposition_type
    cco_months = p.cco.months if p.cco else 0
    triggered = ctx.has_custody or (t in CCO_TYPES and cco_months >= 24) or ((p.fine_penalty_units or 0) > 10)
    n = p.s6aaa
    if not triggered:
        if not n.stated:
            return [R("audit.s6aaa_statement", "Section 6AAA (recommended)", "caution",
                      "The statement is not required for this form of sentence on 7.1.4, but 2.4.9 says 'must'; "
                      "stating the notional sentence is recommended.", flags=["flag.s6aaa_scope_2_4_9_vs_7_1_4"],
                      cite=cite)]
        return [R("audit.s6aaa_statement", "Section 6AAA", "pass", "Notional sentence stated.", cite=cite)]
    if not n.stated or not n.type:
        return [R("audit.s6aaa_statement", "Section 6AAA statement missing", "error",
                  "The plea produced a less severe sentence of a listed form: state the sentence (and non-parole "
                  "period) that would have been imposed but for the plea. (A failure is an irregularity, not of "
                  "itself invalidating the sentence.)", cite=cite)]
    if "mtmo" in (t, n.type):
        return [R("audit.s6aaa_statement", "Section 6AAA comparison", "caution",
                  "A comparison involving a mandatory treatment and monitoring order is not settled by the Manual; "
                  "check that the notional sentence is more severe.", app_policy=True, cite=order)]
    if n.type in IMPRISONMENT_TYPES and t in IMPRISONMENT_TYPES:
        if n.term_months is None or (p.npp_months and n.npp_months is None):
            return [R("audit.s6aaa_statement", "Section 6AAA figures", "error",
                      "State the notional term" + (" and the notional non-parole period" if p.npp_months else "") + ".",
                      cite=cite)]
        term_ok = n.term_months >= (p.term_months or 0)
        npp_ok = (not p.npp_months) or (n.npp_months or 0) >= p.npp_months
        strictly = n.term_months > (p.term_months or 0) or (p.npp_months and (n.npp_months or 0) > p.npp_months)
        if term_ok and npp_ok and strictly:
            return [R("audit.s6aaa_statement", "Section 6AAA", "pass",
                      f"Notional sentence {months_text(n.term_months)}"
                      + (f" with a non-parole period of {months_text(n.npp_months)}" if n.npp_months else "")
                      + " is more severe than the sentence imposed.", cite=cite)]
        return [R("audit.s6aaa_statement", "Section 6AAA figures", "error",
                  "The notional sentence must be more severe than the actual sentence (term and non-parole period).",
                  cite=cite)]
    if n.type == t == "cco":
        dm = (n.cco_months or 0) - cco_months
        actual_hours = sum(c.hours or 0 for c in (p.cco.conditions if p.cco else []) if c.kind == "unpaid_community_work")
        dh = (n.unpaid_hours or 0) - actual_hours
        if dm >= 0 and dh >= 0 and (dm > 0 or dh > 0):
            return [R("audit.s6aaa_statement", "Section 6AAA", "pass", "The notional CCO is more severe.", cite=cite)]
        if dm == 0 and dh == 0:
            outcome = "pass" if (n.conditions_reduced or "").strip() else "caution"
            return [R("audit.s6aaa_statement", "Section 6AAA", outcome,
                      "The notional CCO has the same length and hours; it is more severe only if other conditions "
                      "were reduced" + (" (stated)." if outcome == "pass" else " — say which."), app_policy=True,
                      cite=cite)]
        if dm <= 0 and dh <= 0:
            return [R("audit.s6aaa_statement", "Section 6AAA figures", "error",
                      "The notional CCO is less severe than the actual one.", cite=cite)]
        return [R("audit.s6aaa_statement", "Section 6AAA", "caution",
                  "One measure of the notional CCO is higher and the other lower; severity is not settled.",
                  app_policy=True, cite=cite)]
    if n.type == t == "fine":
        ok = (n.fine_penalty_units or 0) > (p.fine_penalty_units or 0)
        return [R("audit.s6aaa_statement", "Section 6AAA", "pass" if ok else "error",
                  "The notional fine is higher." if ok else "The notional fine must be higher than the fine imposed.",
                  cite=cite)]
    actual_rank, notional_rank = SEVERITY.get(t), SEVERITY.get(n.type)
    if actual_rank is None or notional_rank is None or notional_rank == actual_rank:
        return [R("audit.s6aaa_statement", "Section 6AAA", "caution",
                  "The Manual does not settle whether the notional sentence type is more severe.",
                  app_policy=True, cite=order)]
    if notional_rank > actual_rank:
        return [R("audit.s6aaa_statement", "Section 6AAA", "pass",
                  f"The notional sentence ({n.type.replace('_', ' ')}) is a more severe type.", cite=order)]
    return [R("audit.s6aaa_statement", "Section 6AAA type", "error",
              "The notional sentence must be more severe than the sentence imposed.", cite=order)]


# ─────────────────────────────────────────────────────────────────────────────
# 9. audit.cooperation_statement
# ─────────────────────────────────────────────────────────────────────────────
def check_cooperation(ctx: Ctx) -> list[CheckResult]:
    c = ctx.p.cooperation
    if not c.less_severe_sentence:
        return []
    cite = ["coop.victorian_statement_requirements"]
    if c.basis == "undertaking" and not (c.announced and c.undertaking_recorded):
        return [R("audit.cooperation_statement", "Cooperation statement", "error",
                  "A less severe sentence for an undertaking to assist must be announced and the undertaking's "
                  "details recorded.", cite=cite)]
    if c.basis == "past_assistance" and not c.announced:
        return [R("audit.cooperation_statement", "Cooperation statement", "caution",
                  "Announcing a reduction for past assistance is recommended; the Manual leaves it unsettled.",
                  flags=["flag.s5_2AB_past_vs_future_cooperation"], cite=cite)]
    return [R("audit.cooperation_statement", "Cooperation statement", "pass", "Announced as required.", cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 10. audit.sentence_indication
# ─────────────────────────────────────────────────────────────────────────────
def check_indication(ctx: Ctx) -> list[CheckResult]:
    case, p = ctx.case, ctx.p
    if not case.indication_given or case.indication_plea_entered_on_it != "yes":
        return []
    cite = ["indication.schemes"]
    if case.indication_same_judge != "yes":
        return [R("audit.sentence_indication", "Sentence indication", "caution",
                  "An indication binds only the judge who gave it; check who will sentence.", cite=cite)]
    out = []
    if "mtmo" in (p.disposition_type, case.indication_type):
        out.append(R("audit.sentence_indication", "Sentence indication", "caution",
                     "A comparison involving a mandatory treatment and monitoring order is not settled by the Manual.",
                     cite=cite + ["principle.sentence_type_ordering"], app_policy=True))
    else:
        actual = SEVERITY.get("imprisonment" if ctx.has_custody else p.disposition_type)
        indicated = SEVERITY.get(case.indication_type)
        if actual is None or indicated is None:
            out.append(R("audit.sentence_indication", "Sentence indication", "caution",
                         "The Manual does not settle the comparison between these sentence types.",
                         cite=cite + ["principle.sentence_type_ordering"], app_policy=True))
        elif actual > indicated:
            out.append(R("audit.sentence_indication", "More severe than indicated", "error",
                         "The proposed sentence type is more severe than the one indicated.", cite=cite))
    if case.indication_max_months and (p.term_months or 0) > case.indication_max_months:
        out.append(R("audit.sentence_indication", "Longer than indicated", "error",
                     "The total effective sentence exceeds the indicated maximum.", cite=cite))
    return out or [R("audit.sentence_indication", "Sentence indication", "pass", "Within the indication.", cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 11. audit.fine_rules
# ─────────────────────────────────────────────────────────────────────────────
def check_fine(ctx: Ctx) -> list[CheckResult]:
    p, fw = ctx.p, ctx.fw
    if not p.fine_penalty_units:
        return []
    cite = ["fine.maximum", "scale.fine_alternative"]
    out = []
    level = fw.maximum.level
    if level == 1:
        out.append(R("audit.fine_rules", "Fine on a Level 1 offence", "error", "No fine for a Level 1 offence.", cite=cite))
    if level == 2 and not ctx.has_custody:
        out.append(R("audit.fine_rules", "Level 2 fine", "error",
                     "For a Level 2 offence a natural person may be fined only in addition to imprisonment.",
                     cite=["scale.level2_fine_additional_only"]))
    if fw.maximum.fine_penalty_units and p.fine_penalty_units > fw.maximum.fine_penalty_units:
        out.append(R("audit.fine_rules", "Fine above the maximum", "error",
                     f"The fine ({p.fine_penalty_units} penalty units) exceeds the maximum of "
                     f"{fw.maximum.fine_penalty_units} (10 penalty units per month of the maximum term).", cite=cite))
    if fw.offence_date >= "2026-07-01":
        out.append(R("audit.fine_rules", "Penalty unit value", "caution",
                     kb_statement(ctx, "flag.penalty_unit_values_end_2025"),
                     flags=["flag.penalty_unit_values_end_2025"]))
    return out or [R("audit.fine_rules", "Fine", "pass", "The fine is available and within the maximum.", cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 12. audit.adjournment_and_other_order_limits (adjourned undertakings)
# ─────────────────────────────────────────────────────────────────────────────
def check_adjournment(ctx: Ctx) -> list[CheckResult]:
    p = ctx.p
    if p.disposition_type != "adjourned_undertaking":
        return []
    cite = ["order.adjourned_undertaking"]
    if (p.undertaking_months or 0) > 60:
        return [R("audit.adjournment_and_other_order_limits", "Adjourned undertaking too long", "error",
                  "An adjourned undertaking may not exceed five years.", cite=cite)]
    return [R("audit.adjournment_and_other_order_limits", "Adjourned undertaking", "caution",
              "Within five years. The offender must agree to give the undertaking; confirm that agreement.",
              cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 13. audit.mandatory_ancillary_orders (alcohol exclusion order; licence orders)
# ─────────────────────────────────────────────────────────────────────────────
def check_ancillary(ctx: Ctx) -> list[CheckResult]:
    p, case, out = ctx.p, ctx.case, []
    aeo = [o for o in p.ancillary_orders if o.type == "alcohol_exclusion_order"]
    cite = ["anc.alcohol_exclusion_order"]
    # Alcohol exclusion order: conviction ∧ relevant offence (causing injury offences are a listed
    # category, VSM 20.1.1) ∧ intoxicated by liquor ∧ intoxication significantly contributed ∧ no previous AEO.
    facts_met = case.intoxicated_by_liquor == "yes" and case.intoxication_contributed == "yes" and case.previous_aeo == "no"
    any_unknown = "unknown" in (case.intoxicated_by_liquor, case.intoxication_contributed, case.previous_aeo)
    if facts_met and p.conviction_recorded is True:
        if not aeo:
            out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order missing", "error",
                         "A conviction is recorded for a causing injury offence and intoxication by liquor "
                         "significantly contributed: an alcohol exclusion order (two years) must be made.", cite=cite))
        else:
            o = aeo[0]
            if o.months not in (None, 24) or o.status != "mandatory":
                out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order terms", "caution",
                             "The order is mandatory on these facts and lasts two years.", cite=cite))
            else:
                out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order", "pass",
                             "The mandatory alcohol exclusion order (two years) is listed.", cite=cite))
    elif facts_met and p.conviction_recorded is None:
        out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order may be mandatory", "caution",
                     "If a conviction is recorded, an alcohol exclusion order (two years) is mandatory on these facts.",
                     cite=cite))
    elif any_unknown and case.intoxicated_by_liquor != "no" and case.intoxication_contributed != "no" \
            and case.previous_aeo != "yes" and p.conviction_recorded is not False:
        out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order may be mandatory", "caution",
                     "An alcohol exclusion order is mandatory if intoxication by liquor significantly contributed and "
                     "there is no previous order for the offence; some of those facts are unknown.", cite=cite))
    if aeo and p.cco and any(c.kind == "alcohol_exclusion" for c in p.cco.conditions):
        out.append(R("audit.mandatory_ancillary_orders", "AEO and a CCO alcohol exclusion condition", "error",
                     "With an alcohol exclusion order the court may not also attach an alcohol exclusion condition to "
                     "a CCO.", cite=cite))
    # Licence orders: ss 15A–18 are serious motor vehicle offences only if they arose out of driving
    # under the influence of alcohol (or alcohol and a drug): minimum 12 months (VSM 16.1).
    if case.arising_from_driving == "yes":
        cite = ["licence.serious_mv_offences", "licence.form_by_licence_status"]
        licence_orders = [o for o in p.ancillary_orders if o.type.startswith("licence_")]
        if case.alcohol_influenced_driving == "yes":
            want = {"victorian": "licence_cancellation_and_disqualification",
                    "other_jurisdiction": "licence_disqualification_from_driving",
                    "none": "licence_disqualification_from_obtaining"}.get(case.licence_status)
            if not licence_orders or max(o.months or 0 for o in licence_orders) < 12:
                out.append(R("audit.mandatory_ancillary_orders", "Licence order missing or too short", "error",
                             "A substance-influenced serious motor vehicle offence requires licence cancellation or "
                             "disqualification for at least 12 months.", cite=cite))
            elif want and want not in {o.type for o in licence_orders}:
                out.append(R("audit.mandatory_ancillary_orders", "Licence order form", "error",
                             f"The form of the order must follow the licence held ({case.licence_status}).", cite=cite))
            elif not want:
                out.append(R("audit.mandatory_ancillary_orders", "Licence status unknown", "caution",
                             "State the licence order in the alternative: the licence held is unknown.", cite=cite))
            if p.conviction_recorded is not True:
                out.append(R("audit.mandatory_ancillary_orders", "Licence order and conviction", "caution",
                             "The order follows a finding of guilt or a conviction; the Manual states both.",
                             flags=["flag.licence_guilty_or_convicted"], cite=cite))
        elif case.alcohol_influenced_driving == "unknown":
            out.append(R("audit.mandatory_ancillary_orders", "Licence order may be mandatory", "caution",
                         "If the offence arose out of driving under the influence of alcohol, licence cancellation or "
                         "disqualification of at least 12 months is mandatory.", cite=cite))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 14. audit.facts_and_proof — facts the proposal relies on
# ─────────────────────────────────────────────────────────────────────────────
def check_facts_relied_on(ctx: Ctx) -> list[CheckResult]:
    """Facts the reasons rely on (audit.facts_and_proof test): "→ error if a
    mitigating fact is assumed without any stated basis, if an aggravating fact
    is relied on with no basis in the user's material …". An adverse fact that is
    alleged or disputed needs proof beyond reasonable doubt; a mitigating one,
    evidence on the balance of probabilities."""
    facts = {f["id"]: f for f in ctx.facts.facts}
    cite = ["findings.burden", "findings.formosa"]
    out, seen = [], set()
    for para in ctx.p.reasons:
        for fid in para.fact_ids:
            f = facts.get(fid)
            if not f or fid in seen:
                continue
            seen.add(fid)
            bearing, basis, text = f["bearing"], f["basis"], f["text"]
            if bearing == "aggravating" and basis == "assumption":
                out.append(R("audit.facts_and_proof", f"Reasons rely on an assumed adverse fact ({fid})", "error",
                             f"The reasons rely on “{text}”, which is adverse to the offender and has no basis in "
                             "the material.", cite=cite))
            elif bearing == "aggravating" and basis in ("alleged", "disputed"):
                out.append(R("audit.facts_and_proof", f"Reasons rely on an unproved adverse fact ({fid})", "caution",
                             f"The reasons rely on “{text}” ({basis}); it may be used only if proved beyond "
                             "reasonable doubt.", cite=cite))
            elif bearing == "mitigating" and basis == "assumption":
                out.append(R("audit.facts_and_proof", f"Reasons rely on an assumed mitigating fact ({fid})", "error",
                             f"The reasons rely in mitigation on “{text}”, which has no stated basis in the material; "
                             "mitigation must be proved by evidence and cannot be inferred from an absence of "
                             "evidence.", cite=cite))
            elif bearing == "mitigating" and basis in ("alleged", "disputed"):
                out.append(R("audit.facts_and_proof", f"Mitigating fact needs evidence ({fid})", "caution",
                             f"The reasons rely in mitigation on “{text}” ({basis}); it needs evidence on the balance "
                             "of probabilities.", cite=cite))
            elif bearing == "unclear" and basis in ("alleged", "disputed", "assumption"):
                out.append(R("audit.facts_and_proof", f"Fact of unclear bearing ({fid})", "caution",
                             f"The reasons rely on “{text}”, whose bearing is unclear and whose basis is {basis}: "
                             "against the offender it needs proof beyond reasonable doubt; in mitigation, evidence on "
                             "the balance of probabilities.", cite=cite, app_policy=True))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 15. The band is consistent with the formulated sentence (app policy)
# ─────────────────────────────────────────────────────────────────────────────
def check_band(ctx: Ctx) -> list[CheckResult]:
    p, b = ctx.p, ctx.p.band
    out = []
    if p.disposition_type not in b.disposition_types:
        out.append(R("app.band", "Band and sentence", "caution",
                     "The formulated sentence's type is not within its own band.", app_policy=True))
    if p.term_months and b.term_low_months is not None and b.term_high_months is not None and \
            not (b.term_low_months <= p.term_months <= b.term_high_months):
        out.append(R("app.band", "Band and sentence", "caution",
                     "The formulated term lies outside the band's term range.", app_policy=True))
    if b.term_low_months is not None and b.term_high_months is not None and b.term_low_months > b.term_high_months:
        out.append(R("app.band", "Band", "caution", "The band's lower end is above its upper end.", app_policy=True))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Running the checks, under every reading of the framework
# ─────────────────────────────────────────────────────────────────────────────
# The checks whose result depends on the reading (the category, the minimum and the
# protected-official status). Only these are run under the other readings and merged;
# every other check gives the same result on every reading, so its errors always stand.
READING_CHECKS = [check_category, check_mandatory_minimum, check_therapeutic_order, check_mtmo_available,
                  check_cco_category]
OTHER_CHECKS = [check_complete, check_maximum, check_non_parole, check_cco, check_formulation, check_conviction,
                check_s6aaa, check_cooperation, check_indication, check_fine, check_adjournment, check_ancillary,
                check_facts_relied_on, check_band]
ALL_CHECKS = OTHER_CHECKS[:2] + READING_CHECKS + OTHER_CHECKS[2:]


def _key(r: CheckResult):
    return (r.check_id, r.title) if r.check_id == "audit.cco_limits" else r.check_id


def _from_reading(a: CheckResult, reading, outcome: str, title: str, message: str) -> CheckResult:
    """A result found under another reading, restated for the main list."""
    return CheckResult(a.check_id, title, outcome, message, limb=a.limb,
                       flags=list(dict.fromkeys(a.flags + reading.flags)), cite=a.cite + reading.cite,
                       app_policy=a.app_policy or reading.app_policy or reading.kind == "unproved")


def merge_readings(main: list[CheckResult], alternatives: list[tuple]) -> list[CheckResult]:
    """Merge the reading-dependent results (READING_CHECKS only).
    main: the results under the main reading; alternatives: [(Reading, results under it)].

    Open readings (the Manual leaves the point open). The sentence breaches the
    category or minimum rules on every reading only if the main reading AND every
    open reading give at least one error (whichever check gives it):
      * if so, the main errors stay errors, and errors that arise only on an open
        reading are added as "unresolved" (so a revision can satisfy every reading);
      * if not, the main errors become "unresolved", naming the reading(s) on which
        the sentence is not a breach, and errors that arise only on an open reading
        are added as "unresolved".
    Unproved readings (adverse facts not proved): every error under them that is
    not already reported becomes an "If proved" caution (app policy)."""
    out = list(main)
    main_errors = [r for r in out if r.outcome == "error"]
    open_alts = [(rd, res) for rd, res in alternatives if rd.kind == "open"]
    unproved = [(rd, res) for rd, res in alternatives if rd.kind == "unproved"]
    reported = {_key(r) for r in main_errors}
    lawful_on = [rd for rd, res in open_alts if not any(r.outcome == "error" for r in res)]

    if main_errors and lawful_on:
        names = "; ".join(rd.label for rd in lawful_on)
        flags = [f for rd in lawful_on for f in rd.flags]
        for r in main_errors:
            r.outcome = "unresolved"
            r.message += f" On another reading ({names}) this would not be a breach."
            r.flags = list(dict.fromkeys(r.flags + flags))
            r.cite = r.cite + [c for rd in lawful_on for c in rd.cite]
            r.app_policy = r.app_policy or any(rd.app_policy for rd in lawful_on)
    elif main_errors and open_alts:
        for r in main_errors:
            r.message += (" On each other reading of the points the Manual leaves open, the sentence also breaches "
                          "a rule that depends on the reading.")

    # Errors that arise only on another reading: every one is kept (the same error found under two readings
    # is shown once, under the first).
    seen = set()
    for rd, res in open_alts:
        for a in res:
            if a.outcome == "error" and _key(a) not in reported and (a.check_id, a.title, a.message) not in seen:
                seen.add((a.check_id, a.title, a.message))
                out.append(_from_reading(a, rd, "unresolved", a.title, f"On another reading ({rd.label}): {a.message}"))
    reported |= {_key(a) for rd, res in open_alts for a in res if a.outcome == "error"}
    for rd, res in unproved:
        for a in res:
            if a.outcome == "error" and _key(a) not in reported and (a.check_id, a.title, a.message) not in seen:
                seen.add((a.check_id, a.title, a.message))
                out.append(_from_reading(a, rd, "caution", f"If proved: {a.title}", f"{rd.title}: {a.message}"))
    # the same caution can arise under several unproved readings: keep one
    unique, seen = [], set()
    for r in out:
        sig = (r.check_id, r.title, r.outcome, r.message)
        if sig not in seen:
            seen.add(sig)
            unique.append(r)
    return unique


def run_hard_checks(kb: KnowledgeBase, facts: ConfirmedFacts, fw: Framework, proposal: Proposal) -> list[CheckResult]:
    """All the hard checks, in the order of ALL_CHECKS. The reading-dependent
    ones are also run under every other reading and merged (merge_readings)."""
    ctx = Ctx(kb=kb, facts=facts, fw=fw, p=proposal)
    alternatives = []
    for reading in fw.alternatives:
        alt_ctx = Ctx(kb=kb, facts=facts, fw=fw.under(reading), p=proposal)
        alternatives.append((reading, [r for check in READING_CHECKS for r in check(alt_ctx)]))
    results: list[CheckResult] = []
    for check in OTHER_CHECKS[:2]:
        results.extend(check(ctx))
    results.extend(merge_readings([r for check in READING_CHECKS for r in check(ctx)], alternatives))
    for check in OTHER_CHECKS[2:]:
        results.extend(check(ctx))
    # keep only citations that exist in the knowledge base
    for r in results:
        r.cite = [c for c in dict.fromkeys(r.cite) if kb.exists(c)]
    return results
