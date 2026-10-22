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

import math
from dataclasses import dataclass

from core import config
from core.coverage import AEO_PLAINLY_RELEVANT, DRIVING_PROTECTED
from core.facts import ConfirmedFacts
from core.framework import MINIMUM_ITEMS, Framework
from core.kb import KnowledgeBase
from core.results import CheckResult
from core.schemas import Proposal

IMPRISONMENT_TYPES = {"imprisonment", "imprisonment_and_cco", "life_imprisonment"}
LIFE = "life_imprisonment"
CCO_TYPES = {"cco", "imprisonment_and_cco", "mtmo"}
CCO_CONDITIONS_ALLOWED = {
    "unpaid_community_work", "treatment_and_rehabilitation", "supervision", "non_association",
    "residence_restriction_or_exclusion", "place_or_area_exclusion", "curfew", "alcohol_exclusion",
    "judicial_monitoring", "electronic_monitoring", "bond", "justice_plan",
}
# principle.sentence_type_ordering (app policy): imprisonment (incl. combination)
# > CCO > fine > {adjourned undertaking, discharge, dismissal}. Any comparison
# involving another order (an MTMO, with or without a term) is unsettled. Life
# imprisonment is ranked above any term (app policy: it is the most severe form
# of imprisonment, the Level 1 maximum).
SEVERITY = {"life_imprisonment": 4, "imprisonment": 3, "imprisonment_and_cco": 3, "cco": 2, "fine": 1,
            "adjourned_undertaking": 0, "discharge": 0, "dismissal": 0}
DAYS_PER_MONTH = 30.4375
LICENCE_FORMS = {"victorian": "licence_cancellation_and_disqualification",          # licence.form_by_licence_status
                 "other_jurisdiction": "licence_disqualification_from_driving",
                 "none": "licence_disqualification_from_obtaining"}


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
        """ss 16, 17, 18 against a protected official (not gross violence), or ss 317AC/317AD with the official
        injured (VSM 9.1.2.4: the special-reason dispositions are those of 9.1.2.3), on this reading."""
        return (self.fw.mm_group == "protected_official" and self.case.offence_id not in config.GROSS_VIOLENCE_OFFENCES) \
            or self.fw.mm_group == "driving_protected"

    @property
    def is_gross_violence(self) -> bool:
        return self.case.offence_id in config.GROSS_VIOLENCE_OFFENCES

    @property
    def is_category1_with_minimum(self) -> bool:
        """A Category 1 offence that carries a mandatory minimum non-parole period other than the protected-official
        one (register minimum_non_parole: ss 15A/15B, ss 77B, 79A): a special reason with another disposition is
        unresolved (audit.category_1_2 route (d), flag.category1_special_reason), not an error. Not ss 317AC/317AD:
        audit.category_1_2 excludes them from route (d), and with the official injured their special-reason
        dispositions are those of 9.1.2.3 (is_protected_injury)."""
        if self.case.offence_id in config.CAUSING_INJURY_OFFENCES:
            return self.is_gross_violence
        if self.case.offence_id in DRIVING_PROTECTED:
            return False
        return bool(self.kb.offence(self.case.offence_id).get("minimum_non_parole"))

    @property
    def is_contempt(self) -> bool:
        """The 29.1.3 rows: the Sentencing Act does not apply in its entirety (VSM 29.5.2)."""
        return str(self.kb.offence(self.case.offence_id).get("section") or "").startswith("29.1.3")


def R(check_id, title, outcome, message, **kw) -> CheckResult:
    return CheckResult(check_id=check_id, title=title, outcome=outcome, message=message, **kw)


def months_text(m) -> str:
    if m is None:
        return "—"
    y, mo = divmod(int(m), 12)
    parts = ([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{mo} month{'s' if mo != 1 else ''}"] if mo else [])
    return " ".join(parts) or "0 months"


def _school_answer(ctx: "Ctx") -> str:
    """How the user answered the school's statutory condition (ss 71AC, 71B; flag.school_500m_condition)."""
    return ("you answered is not met — the Manual's flag keeps the higher maximum unresolved"
            if ctx.case.school_condition_confirmed == "no" else "you have not confirmed (Step 1)")


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
    if p.disposition_type == LIFE:
        if p.term_months:
            out.append(R("app.proposal_complete", "Life imprisonment with a term", "caution",
                         "A life sentence has no term in months; the term given is ignored.", app_policy=True))
        if p.cco is not None:
            out.append(R("audit.cco_limits", "CCO with life imprisonment", "error",
                         "A CCO cannot accompany a life sentence: a CCO may be combined only with imprisonment of up "
                         "to one year (after pre-sentence detention).", limb="(3)", cite=["cco.combination_one_year"]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 1. audit.maximum_applicable — within the maximum at the offence date
# ─────────────────────────────────────────────────────────────────────────────
def check_maximum(ctx: Ctx) -> list[CheckResult]:
    m, p, out = ctx.fw.maximum, ctx.p, []
    cite = ["audit.maximum_applicable", "maximum.misidentification"]
    if m.unresolved:
        return [R("audit.maximum_applicable", "Maximum", "unresolved",
                  f"The maximum cannot be determined from the Manual: {m.unresolved}", cite=cite)]
    if m.fine_only:
        if p.term_months or ctx.has_custody:
            out.append(R("audit.maximum_applicable", "Imprisonment on a fine-only offence", "error",
                         f"The offence is punishable by a fine only ({m.text}): no term of imprisonment may be imposed.",
                         limb="(7)", cite=cite))
        if p.disposition_type in CCO_TYPES and (m.fine_penalty_units or 0) <= 5:
            out.append(R("audit.cco_limits", "CCO not available", "error",
                         "A CCO is available only for an offence punishable by more than five penalty units.",
                         limb="(1)", cite=["order.cco"]))
        return out or [R("audit.maximum_applicable", "Within the maximum", "pass",
                         f"Maximum {m.text}; no imprisonment is proposed.", cite=cite)]
    if p.disposition_type == LIFE and not m.life:
        if m.at_large:                          # scale.at_large: no legal limit on the maximum penalty
            return [R("audit.maximum_applicable", "Life imprisonment on an offence at large", "caution",
                      f"The maximum is at large ({m.text}): there is no legal limit, so life imprisonment is not above "
                      "it, but the sentence must not be inordinate (scale.at_large).", limb="(4a)",
                      cite=cite + ["scale.at_large"])]
        return [R("audit.maximum_applicable", "Life imprisonment above the maximum", "error",
                  f"Life imprisonment is available only where the maximum is life; the maximum is {m.describe()}.",
                  limb="(8)", cite=cite)]
    if m.life and m.caution_above_months and (p.disposition_type == LIFE or (p.term_months or 0) > m.caution_above_months):
        return [R("audit.maximum_applicable", "Above 15 years on an incitement to a life offence", "caution",
                  f"{'Life imprisonment' if p.disposition_type == LIFE else months_text(p.term_months)} on an incitement "
                  "to commit an offence punishable by life: fn 4807 gives an operative maximum of 15 years but speaks "
                  "of conspiracy, so this is a caution, not an error.", limb="(4)",
                  flags=[m.caution_flag] if m.caution_flag else [], cite=cite + ["guide.inchoate.maxima_notes"])]
    if m.life or m.at_large:
        what = ("life imprisonment, or such other term as is fixed by the court" if m.life
                else "at large: the sentence must not be inordinate (scale.at_large)")
        return [R("audit.maximum_applicable", "Within the maximum", "pass" if m.life else "caution",
                  f"Maximum {m.text} ({what}).", cite=cite)]
    if m.months is None:
        return [R("audit.maximum_applicable", "Maximum", "unresolved",
                  "The maximum cannot be determined from the Manual.", cite=cite)]
    ceiling, floor = m.ceiling_months, min(m.months, m.scale_months or m.months)
    level_ceiling = max(m.months, m.scale_months or 0)
    if p.term_months and p.term_months > ceiling:
        out.append(R("audit.maximum_applicable", "Above the maximum", "error",
                     f"The term ({months_text(p.term_months)}) exceeds the maximum of {months_text(ceiling)} "
                     f"({m.text}" + (f"; {m.conditional_status}" if m.conditional_ceiling_months else "") + ").",
                     limb="(8)", cite=cite))
    elif p.term_months and m.conditional_ceiling_months and p.term_months > level_ceiling:
        # ss 71AC, 71B within 500 metres of a school, the statutory condition not confirmed (flag.school_500m_condition)
        out.append(R("audit.maximum_applicable", "Above the base maximum — conditional maximum unresolved", "unresolved",
                     f"The term ({months_text(p.term_months)}) is above the base maximum of {months_text(level_ceiling)} "
                     f"but within {months_text(m.conditional_ceiling_months)}: that higher maximum applies only if the "
                     "statutory condition is met, which " + _school_answer(ctx) + ".", limb="(3)",
                     flags=[m.conditional_flag] if m.conditional_flag else [], cite=cite))
    elif p.term_months and p.term_months > floor:
        # The printed level and term disagree: error above the higher figure, unresolved between the two
        # (audit.maximum_applicable (8); audit.level_term_mismatch).
        out.append(R("audit.maximum_applicable", "Between the printed term and the level's term", "unresolved",
                     f"The term ({months_text(p.term_months)}) is above {months_text(floor)} but within "
                     f"{months_text(ceiling)}: "
                     + (f"the Manual prints Level {m.level} with {months_text(m.months)}, and the s 109 scale gives the "
                        "level a different term." if m.level else
                        "the maximum is a share of the offence identified's maximum, whose printed level and printed "
                        "term disagree (the share of each is shown).")
                     + " The level prevails unless a contrary intention appears; check the legislation.", limb="(8)",
                     cite=cite + ["audit.level_term_mismatch"]))
    if p.band.term_high_months and p.band.term_high_months > ceiling:
        out.append(R("audit.maximum_applicable", "Band above the maximum", "caution",
                     f"The band's upper end ({months_text(p.band.term_high_months)}) exceeds the maximum; the band "
                     "should stop at the maximum.", limb="(8)", cite=cite, app_policy=True))
    if not [r for r in out if r.outcome in ("error", "unresolved")]:
        out.append(R("audit.maximum_applicable", "Within the maximum", "pass",
                     f"Maximum {m.text}" + (" (the conditional maximum: its condition is proved, as you answered)"
                                            if m.conditional_applied else "")
                     + f" for an offence on {ctx.fw.offence_date}; the sentence is within it.", cite=cite))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 2. audit.category_1_2 — Category 1 and 2 offences (depends on the reading)
# ─────────────────────────────────────────────────────────────────────────────
def check_category(ctx: Ctx) -> list[CheckResult]:
    fw, p = ctx.fw, ctx.p
    if not fw.category:
        return [R("audit.category_1_2", "Category 1/2", "info", "Not a Category 1 or 2 offence on these facts.")]
    straight = p.disposition_type in ("imprisonment", LIFE)          # imprisonment without a CCO
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
        elif ctx.is_category1_with_minimum and sr:
            flag = ("flag.gross_violence_protected_special_reason" if fw.applied_protected == "yes"
                    else "flag.category1_special_reason")
            out.append(R("audit.category_1_2", "Category 1 — special reason, other disposition", "caution",
                         "A special reason on a Category 1 charge that carries a mandatory minimum: whether any "
                         "disposition other than imprisonment without a CCO is open is unresolved in the Manual.",
                         flags=[flag], limb="(c)" if fw.applied_protected == "yes" else "(d)", cite=cite))
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
    band_types = set(p.band.disposition_types) - {"imprisonment", LIFE}
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
                  "No mandatory minimum non-parole period applies on the facts as entered"
                  + (" (one applies only if further facts are proved: see “If proved” below)."
                     if any(a.mnpp_months for a in fw.alternatives) else "."))]
    cite = MINIMUM_ITEMS.get(fw.mm_group, ["mmnpp.overview"]) + ["mm.special_reason"]
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
    if p.disposition_type != "mtmo" or ctx.case.body_corporate:
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
    if p.disposition_type == LIFE:
        return _life_non_parole(ctx)
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


def _life_non_parole(ctx: Ctx) -> list[CheckResult]:
    """Life imprisonment: a non-parole period must be fixed unless the court states why one is inappropriate
    (npp.must_fix_two_years); life without parole is exceptional (guide.murder.life_without_parole)."""
    p = ctx.p
    cite = ["npp.must_fix_two_years", "guide.murder.life_without_parole"]
    if p.npp_months:
        return [R("audit.non_parole_rules", "Non-parole period", "pass",
                  f"A non-parole period of {months_text(p.npp_months)} is fixed on the life sentence.", cite=cite)]
    if not (p.npp_reason or "").strip():
        return [R("audit.non_parole_rules", "Non-parole period required", "error",
                  "A life sentence needs a non-parole period unless the court states why one is inappropriate.",
                  cite=cite)]
    return [R("audit.non_parole_rules", "Life without parole", "caution",
              "No non-parole period is fixed, with reasons stated. Life without parole is an exceptional step, imposed "
              "in a handful of cases; declining a non-parole period removes a major incentive to rehabilitation.",
              cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 4b. The standard sentence scheme (VSM 9.2): npp.sso_minimum and sso.reasons (presence only)
# ─────────────────────────────────────────────────────────────────────────────
def _sso_outcome(ctx: Ctx, outcome: str) -> str:
    """An error is unresolved while the scheme's commencement is (flag.sso_effective_date)."""
    return "unresolved" if outcome == "error" and ctx.fw.sso.applies == "unresolved" else outcome


def check_sso_npp(ctx: Ctx) -> list[CheckResult]:
    """npp.sso_minimum (s 11A(4)), only where the scheme applies (sso.scope): unless it is not in the interests of
    justice, a non-parole period of at least 30 years for life, 70% of a term over 20 years, 60% of a term under 20
    years; exactly 20 years: the 60% floor, with a caution below 70% (flag.sso_npp_20_year_overlap); a term under 15
    months: unresolved, citing the three restrictions (flag.sso_npp_short_terms); below the floor without an
    interests-of-justice reason: error."""
    p, sso = ctx.p, ctx.fw.sso
    if not sso or sso.applies == "no" or not ctx.has_custody:
        return []
    cite = ["npp.sso_minimum", "sso.reasons"]
    reason = (p.npp_interests_of_justice_reason or "").strip()
    flags = list(sso.flags)
    if p.disposition_type == LIFE:                  # none fixed is below the floor too (npp.sso_minimum)
        floor, label = 360, "30 years (life)"
    else:
        tes = p.term_months or 0
        if tes < 15:
            return [R("audit.non_parole_rules", "Standard sentence — short term", "unresolved",
                      f"For a term of {months_text(tes)} the s 11A(4) floor (60% of the term) cannot be met together with "
                      "the rules that no non-parole period is fixed for a term under 12 months and that it must be at "
                      "least six months less than the term; the Manual does not reconcile them.",
                      flags=flags + ["flag.sso_npp_short_terms"],
                      cite=cite + ["npp.none_under_12_months", "npp.six_month_gap"])]
        if ctx.is_combination and 12 < tes < 24:
            return [R("audit.non_parole_rules", "Standard sentence — combination sentence", "unresolved",
                      "A term of more than 12 and less than 24 months combined with a CCO cannot have a non-parole "
                      "period, but s 11A(4) asks for one of at least 60% of the term; the Manual does not reconcile "
                      "them.", flags=flags, cite=cite + ["npp.none_with_cco_12m_2y"], app_policy=True)]
        share = 0.7 if tes > 240 else 0.6
        floor, label = share * tes, f"{share:.0%} of the term ({months_text(math.ceil(share * tes))})"
    npp = p.npp_months or 0
    out = []
    if npp + 1e-9 < floor:
        if reason:
            out.append(R("audit.non_parole_rules", "Standard sentence — below the s 11A(4) floor, with reasons",
                         _sso_outcome(ctx, "pass"),
                         f"The non-parole period ({months_text(p.npp_months) if p.npp_months else 'none'}) is below "
                         f"{label}; the interests-of-justice reasons are stated (the audit checks them).", flags=flags,
                         cite=cite))
        else:
            out.append(R("audit.non_parole_rules", "Standard sentence — below the s 11A(4) floor",
                         _sso_outcome(ctx, "error"),
                         f"The non-parole period ({months_text(p.npp_months) if p.npp_months else 'none'}) is below "
                         f"{label}, and no reason is stated why the floor is not in the interests of justice."
                         + (f" (Whether the scheme applies is unresolved: {sso.reason}.)" if sso.applies == "unresolved"
                            else ""), flags=flags, cite=cite))
    elif p.disposition_type != LIFE and p.term_months == 240 and npp + 1e-9 < 168 and not reason:
        out.append(R("audit.non_parole_rules", "Standard sentence — a term of exactly 20 years", "caution",
                     "The non-parole period meets the 60% floor (12 years) but is below 70% (14 years): as printed, a "
                     "term of exactly 20 years falls under both limbs of s 11A(4); check the provision.",
                     flags=flags + ["flag.sso_npp_20_year_overlap"], cite=cite))
    else:
        out.append(R("audit.non_parole_rules", "Standard sentence — non-parole floor", _sso_outcome(ctx, "pass"),
                     f"The non-parole period ({months_text(p.npp_months)}) is at least {label} (s 11A(4)).",
                     flags=flags, cite=cite))
    return out


def check_sso_reasons(ctx: Ctx) -> list[CheckResult]:
    """sso.reasons (hard, presence only): within the scheme the reasons must state how the sentence relates to the
    standard sentence (audit.standard_sentence_use (3)); whether the statement is adequate is the audit's."""
    p, sso = ctx.p, ctx.fw.sso
    if not sso or sso.applies == "no":
        if (p.standard_sentence_statement or "").strip() or (p.npp_interests_of_justice_reason or "").strip():
            return [R("audit.standard_sentence_use", "Standard sentence — no scheme for this charge", "caution",
                      "The proposal relates the sentence to a standard sentence (or gives interests-of-justice reasons "
                      "for a non-parole period below a standard sentence floor), but the standard sentence scheme does "
                      "not apply to this charge" + (f" ({sso.reason})" if sso and sso.reason else "") + ": the app "
                      "does not show or use that statement.", cite=["sso.scope"], app_policy=True)]
        return []
    cite = ["sso.reasons", "audit.standard_sentence_use"]
    if not (p.standard_sentence_statement or "").strip():
        return [R("audit.standard_sentence_use", "Standard sentence — relationship not stated",
                  "error" if sso.applies == "yes" else "caution",
                  "The reasons must state how the sentence relates to the standard sentence "
                  f"({sso.years} years), identifying the facts, matters and circumstances relied on.",
                  flags=list(sso.flags), cite=cite)]
    return [R("audit.standard_sentence_use", "Standard sentence — relationship stated", "pass",
              "The proposal states how the sentence relates to the standard sentence (the audit checks how).",
              cite=cite)]


# ─────────────────────────────────────────────────────────────────────────────
# 5. audit.cco_limits
# ─────────────────────────────────────────────────────────────────────────────
def check_cco(ctx: Ctx) -> list[CheckResult]:
    p, case = ctx.p, ctx.case
    if p.disposition_type not in CCO_TYPES or p.cco is None or case.body_corporate:
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
        name = (ctx.kb.offence(case.offence_id).get("offence") or "").lower()
        if excess > 0 and ("arson" in name or "bushfire" in name):
            # The one-year limit does not apply to arson offences in the higher courts (cco.combination_one_year),
            # but the Manual does not define "arson offences" (flag.cco_arson_combination): a caution, never an error.
            out.append(R("audit.cco_limits", "Combination sentence over one year — arson?", "caution",
                         "The one-year limit on the imprisonment in a combination sentence does not apply to arson "
                         "offences in the Supreme or County Court, but the Manual does not define “arson offences”: "
                         "confirm this charge is one.", limb="(3)", flags=["flag.cco_arson_combination"],
                         cite=["cco.combination_one_year"]))
        elif excess > 0.25:
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
            (fw.category == 1 and ctx.is_category1_with_minimum and p.special_reason.found)   # routes (c), (d)
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
    if LIFE in (t, n.type):
        return [_s6aaa_life(p, n, cite, order)]
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


def _s6aaa_life(p, n, cite, order) -> CheckResult:
    """Section 6AAA with life imprisonment: life is more severe than any term (app policy); between two life
    sentences the non-parole period decides (none fixed is more severe than any)."""
    if n.type == LIFE and p.disposition_type != LIFE:
        return R("audit.s6aaa_statement", "Section 6AAA", "pass", "The notional sentence (life imprisonment) is more "
                 "severe than the sentence imposed.", cite=order, app_policy=True)
    if n.type != LIFE:
        return R("audit.s6aaa_statement", "Section 6AAA type", "error", "The sentence imposed is life imprisonment: "
                 "the notional sentence must be more severe, so it can only be life imprisonment with a longer "
                 "non-parole period (or none) (life ranks above any term: app policy).", cite=order, app_policy=True)
    if p.npp_months and (n.npp_months is None or n.npp_months > p.npp_months):
        return R("audit.s6aaa_statement", "Section 6AAA", "pass",
                 "The notional life sentence has " + ("no non-parole period" if n.npp_months is None else
                                                      f"a non-parole period of {months_text(n.npp_months)}")
                 + f", more severe than the {months_text(p.npp_months)} fixed.", cite=cite, app_policy=True)
    return R("audit.s6aaa_statement", "Section 6AAA figures", "error",
             "The notional life sentence must be more severe: a longer non-parole period than the one fixed (or none).",
             cite=cite, app_policy=True)


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
        actual = SEVERITY.get(LIFE if p.disposition_type == LIFE else
                              "imprisonment" if ctx.has_custody else p.disposition_type)
        indicated = SEVERITY.get(case.indication_type)
        if actual is None or indicated is None:
            out.append(R("audit.sentence_indication", "Sentence indication", "caution",
                         "The Manual does not settle the comparison between these sentence types.",
                         cite=cite + ["principle.sentence_type_ordering"], app_policy=True))
        elif actual > indicated:
            out.append(R("audit.sentence_indication", "More severe than indicated", "error",
                         "The proposed sentence type is more severe than the one indicated.", cite=cite))
    if case.indication_max_months and (p.disposition_type == LIFE or (p.term_months or 0) > case.indication_max_months):
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
    if level == 1 and fw.maximum.fine_source == "printed":
        # DPCSA ss 71, 72 (and s 71AA for a criminal organisation) print "life and fine not exceeding 5000 p.u.",
        # against 12.1's rule of no fine for Level 1 (flag.level1_fine_dpcsa): a caution, within the printed fine.
        out.append(R("audit.fine_rules", "Fine on a Level 1 offence", "caution", kb_statement(ctx, "flag.level1_fine_dpcsa"),
                     flags=["flag.level1_fine_dpcsa"], cite=cite))
    elif level == 1 and fw.maximum.caution_above_months and p.fine_penalty_units > fw.maximum.caution_above_months * 10:
        out.append(R("audit.fine_rules", "Fine above the maximum", "error",
                     f"The fine ({p.fine_penalty_units} penalty units) is unlawful on either reading: no fine on the "
                     "life maximum, and on fn 4807's 15 years at most "
                     f"{fw.maximum.caution_above_months * 10} penalty units (10 a month).",
                     flags=[fw.maximum.caution_flag] if fw.maximum.caution_flag else [], cite=cite))
    elif level == 1 and fw.maximum.caution_above_months:
        # incitement to an offence punishable by life (s 321I(1)(c)): Level 1 is copied from the offence identified,
        # but fn 4807 gives an operative maximum of 15 years (flag.inchoate_life_maximum_footnotes)
        out.append(R("audit.fine_rules", "Fine on an incitement to a life offence", "caution",
                     "The offence incited is punishable by life (Level 1: no fine), but fn 4807 gives an incitement to it "
                     "an operative maximum of 15 years, for which a fine is available: the Manual does not settle "
                     "which applies.", flags=[fw.maximum.caution_flag] if fw.maximum.caution_flag else [],
                     cite=cite + ["guide.inchoate.maxima_notes"]))
    elif level == 1:
        out.append(R("audit.fine_rules", "Fine on a Level 1 offence", "error", "No fine for a Level 1 offence.", cite=cite))
    corporate = ctx.case.body_corporate             # a body corporate's fine: check_body_corporate
    if level == 2 and not ctx.has_custody and not corporate:
        out.append(R("audit.fine_rules", "Level 2 fine", "error",
                     "For a Level 2 offence a natural person may be fined only in addition to imprisonment.",
                     cite=["scale.level2_fine_additional_only"]))
    if level is None and not fw.maximum.confirmed_by_user and not ctx.has_custody and not corporate and \
            (fw.maximum.life or fw.maximum.months == 300):
        out.append(R("audit.fine_rules", "Fine on a life or 25-year maximum printed without a level", "caution",
                     kb_statement(ctx, "flag.fine_unlabelled_life_25_maximum"),
                     flags=["flag.fine_unlabelled_life_25_maximum"], cite=cite))
    if fw.maximum.life and not fw.maximum.fine_penalty_units and level != 1 and not corporate:
        out.append(R("audit.fine_rules", "No fine on a life maximum", "error",
                     "The maximum is life imprisonment and the register prints no fine: the general fine (10 penalty "
                     "units per month of the maximum term) is not available for life imprisonment.", cite=cite))
    conditional_fine = (fw.maximum.conditional_ceiling_months or 0) * 10      # ss 71AC, 71B: the school's band
    if fw.maximum.fine_penalty_units and p.fine_penalty_units > fw.maximum.fine_penalty_units and not corporate \
            and fw.maximum.fine_penalty_units < p.fine_penalty_units <= conditional_fine:
        out.append(R("audit.fine_rules", "Fine above the base maximum — conditional maximum unresolved", "unresolved",
                     f"The fine ({p.fine_penalty_units} penalty units) is above the base maximum of "
                     f"{fw.maximum.fine_penalty_units} but within {conditional_fine} (10 penalty units per month of the "
                     "higher maximum): that maximum applies only if the statutory condition is met, which "
                     + _school_answer(ctx) + ".", limb="(3)",
                     flags=[fw.maximum.conditional_flag] if fw.maximum.conditional_flag else [], cite=cite))
    elif fw.maximum.fine_penalty_units and p.fine_penalty_units > fw.maximum.fine_penalty_units and not corporate:
        out.append(R("audit.fine_rules", "Fine above the maximum", "error",
                     f"The fine ({p.fine_penalty_units} penalty units) exceeds "
                     + (f"{conditional_fine}, the most on any reading of the school condition (the base maximum is "
                        f"{fw.maximum.fine_penalty_units}: " if conditional_fine else
                        f"the maximum of {fw.maximum.fine_penalty_units} (")
                     + ("as printed" if fw.maximum.fine_source == "printed" else
                        "as you entered it from the Manual's table" if fw.maximum.fine_source == "entered" else
                        "10 penalty units per month of the maximum term") + ").", cite=cite))
    if fw.offence_date >= "2026-07-01":
        out.append(R("audit.fine_rules", "Penalty unit value", "caution",
                     kb_statement(ctx, "flag.penalty_unit_values_end_2025"),
                     flags=["flag.penalty_unit_values_end_2025"]))
    if corporate:
        return out                              # a body corporate's maximum is checked by check_body_corporate
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
    """The alcohol exclusion order (natural persons) and the mandatory licence orders."""
    out = [] if ctx.case.body_corporate else _alcohol_exclusion_order(ctx)
    return out + _licence_order(ctx)


def _alcohol_exclusion_order(ctx: Ctx) -> list[CheckResult]:
    """anc.alcohol_exclusion_order: conviction ∧ relevant offence ∧ intoxicated by liquor ∧ the intoxication
    significantly contributed ∧ no previous AEO for the offence → an AEO (two years). "Relevant offences" are listed by
    category (VSM 20.1.1), not by register id: the charges it names (murder or manslaughter, causing injury) are
    relevant; for any other charge the user's answer decides (CaseInput.aeo_relevant_offence: "yes" relevant, "no"
    not, "unknown" a caution — app policy)."""
    p, case, out = ctx.p, ctx.case, []
    aeo = [o for o in p.ancillary_orders if o.type == "alcohol_exclusion_order"]
    cite = ["anc.alcohol_exclusion_order"]
    facts_met = case.intoxicated_by_liquor == "yes" and case.intoxication_contributed == "yes" and case.previous_aeo == "no"
    any_unknown = "unknown" in (case.intoxicated_by_liquor, case.intoxication_contributed, case.previous_aeo)
    named = case.offence_id in AEO_PLAINLY_RELEVANT
    relevant = named or case.aeo_relevant_offence == "yes"
    if not relevant:
        if case.aeo_relevant_offence != "no" and (facts_met or any_unknown) and p.conviction_recorded is not False \
                and case.previous_aeo != "yes" and "no" not in (case.intoxicated_by_liquor, case.intoxication_contributed):
            out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order — relevant offence?", "caution",
                         "An alcohol exclusion order (two years) is mandatory on a conviction for a “relevant offence” "
                         "if intoxication by liquor significantly contributed and there is no previous order for the "
                         "offence. The Manual lists relevant offences by category (murder or manslaughter; causing "
                         "injury; administering certain substances; threat to kill, conduct endangering, kidnap and "
                         "assault; certain resisting arrest and assisting offender offences; use of firearms; certain "
                         "sexual offences; child abuse material offences): "
                         + ("confirm whether this charge is one (Step 1)" if case.intoxicated_by_liquor == "yes" else
                            "answer the intoxication questions in Step 1; if intoxication by liquor is answered yes, "
                            "the app then asks whether this charge is one")
                         + (" before relying on the order listed" if aeo else "") + " (app policy).",
                         cite=cite, app_policy=True))
        facts_met = any_unknown = False                     # the checks below are for a relevant offence
    which = ("a relevant offence (VSM 20.1.1: murder or manslaughter, or a causing injury offence)" if named
             else "an offence you confirmed is a relevant offence")
    if facts_met and p.conviction_recorded is True:
        if not aeo:
            out.append(R("audit.mandatory_ancillary_orders", "Alcohol exclusion order missing", "error",
                         f"A conviction is recorded for {which} and intoxication by liquor significantly contributed: "
                         "an alcohol exclusion order (two years) must be made.", cite=cite, app_policy=not named))
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
    return out


def _licence_order(ctx: Ctx) -> list[CheckResult]:
    """audit.mandatory_ancillary_orders (licence): the framework's licence rule (core/framework.py, licence_rule, from
    the Manual's map) → the order must be listed, at or above its minimum, in the form the licence held requires
    (licence.form_by_licence_status); a serious motor vehicle offence with no conviction recorded → still listed, with
    a caution (flag.licence_guilty_or_convicted); s 319AA and theft of a motor vehicle → only on conviction; RSA
    s 61(3) → 4 years (2 without a conviction), a subsequent offence 8 (or 4)."""
    rule, p, case = ctx.fw.licence, ctx.p, ctx.case
    if not rule or rule.applies == "no":
        return []
    cite = rule.cite
    if rule.conviction_only and p.conviction_recorded is False:
        return []                                   # these orders follow a conviction (licence.other_mandatory)
    if rule.applies == "unknown":
        what = ("whether the property was a motor vehicle" if rule.group == "motor_vehicle_theft" else
                "the offence attempted and, if it is theft, whether the property was a motor vehicle"
                if rule.group == "attempted_motor_vehicle_theft" else "the driving questions")
        return [R("audit.mandatory_ancillary_orders", "Licence order may be mandatory", "caution",
                  f"If {rule.condition}, a licence order is mandatory: {rule.line()} Answer {what} in Step 1.",
                  cite=cite)]
    orders = [o for o in p.ancillary_orders if o.type.startswith("licence_")]
    minimum, if_convicted = rule.minimum, None
    if rule.group == "rsa_s61_3" and p.conviction_recorded is False:
        minimum = rule.minimum_no_conviction
    elif rule.group == "rsa_s61_3" and p.conviction_recorded is None:
        minimum, if_convicted = rule.minimum_no_conviction, rule.minimum    # 2 (or 4) years; 4 (or 8) on conviction
    # s 319AA and theft: only on a conviction — while the proposal does not say whether one is recorded, a breach is
    # a breach only if it is (licence.other_mandatory): a caution, not an error
    soft = rule.conviction_only and p.conviction_recorded is None
    outcome, pre = ("caution", "If a conviction is recorded: ") if soft else ("error", "")
    want = LICENCE_FORMS.get(case.licence_status)
    out = []
    if not orders:
        out.append(R("audit.mandatory_ancillary_orders", "Licence order missing", outcome,
                     f"{pre}A licence order is mandatory for this charge: {rule.line()}", cite=cite))
    else:
        longest = max(o.months or 0 for o in orders)
        if minimum and longest < minimum:
            out.append(R("audit.mandatory_ancillary_orders", "Licence order too short", outcome,
                         f"{pre}The licence order must be for at least {months_text(minimum)}.", cite=cite))
        elif if_convicted and longest < if_convicted:
            out.append(R("audit.mandatory_ancillary_orders", "Licence order too short if convicted", "caution",
                         f"If a conviction is recorded, the licence order must be for at least "
                         f"{months_text(if_convicted)} (without one, {months_text(minimum)}): the proposal does not say "
                         "whether one is recorded.", cite=cite))
        if want and want not in {o.type for o in orders}:        # the form, whatever the period
            out.append(R("audit.mandatory_ancillary_orders", "Licence order form", outcome,
                         f"{pre}The form of the order must follow the licence held "
                         f"({case.licence_status.replace('_', ' ')}): {want.replace('_', ' ')}.", cite=cite))
        elif not want:
            out.append(R("audit.mandatory_ancillary_orders", "Licence status unknown", "caution",
                         "State the licence order in the alternative: the licence held is unknown.", cite=cite))
    if rule.group == "rsa_s61_3" and case.licence_status in ("other_jurisdiction", "none"):
        out.append(R("audit.mandatory_ancillary_orders", "RSA s 61(3) — no Victorian licence", "caution",
                     "The 23.1.1 table speaks of licence cancellation and disqualification, which assumes a Victorian "
                     "licence; the offender holds none: the order follows the licence held.", cite=cite))
    if rule.group == "rsa_s61_3" and case.previous_s61_3_offence == "unknown":
        out.append(R("audit.mandatory_ancillary_orders", "RSA s 61(3) — previous offence unknown", "caution",
                     "Whether the offender has a previous s 61(3) offence is unknown: the first-offence minimum is "
                     "applied (a subsequent offence: at least 8 years, or 4 without a conviction).", cite=cite))
    if not rule.conviction_only and rule.group != "rsa_s61_3" and p.conviction_recorded is not True:
        out.append(R("audit.mandatory_ancillary_orders", "Licence order and conviction", "caution",
                     "The order follows a finding of guilt or a conviction; the Manual states both.",
                     flags=["flag.licence_guilty_or_convicted"], cite=cite))
    if orders and not [r for r in out if r.outcome == "error" or r.title in ("Licence order form", "Licence order missing")
                       or r.title.startswith("Licence order too short")]:
        out.append(R("audit.mandatory_ancillary_orders", "Licence order", "pass",
                     "The mandatory licence order is listed" + (f", for at least {months_text(minimum)}" if minimum
                                                                else "") + ".", cite=cite))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 13b. A body corporate (intake.offender_type; fine.maximum; flag.body_corporate_fine_multiplier)
# ─────────────────────────────────────────────────────────────────────────────
def check_body_corporate(ctx: Ctx) -> list[CheckResult]:
    """A body corporate: no order on a person (app policy); a fine within the printed company maximum, else five times
    the natural person's maximum (error above, any offence), and for an offence outside the Crimes Act a fine above
    the natural person's maximum is a caution (flag.body_corporate_fine_multiplier)."""
    p, m = ctx.p, ctx.fw.maximum
    if not ctx.case.body_corporate:
        return []
    out = []
    cite = ["fine.maximum", "intake.offender_type"]
    if ctx.has_custody or p.disposition_type in CCO_TYPES:
        out.append(R("audit.fine_rules", "An order on a person for a body corporate", "error",
                     "A body corporate cannot be imprisoned or made subject to a CCO: propose a fine, an adjourned "
                     "undertaking, a discharge or a dismissal.", cite=cite, app_policy=True))
    if p.fine_penalty_units:
        cap, source = m.company_fine_units, m.company_fine_source
        crimes_act = (ctx.kb.offence(ctx.case.offence_id).get("act") or "").startswith("Crimes Act")
        higher = ctx.case.company_maximum_units          # a provision the user identifies (fine.maximum: app policy)
        how = {"printed": "as printed", "entered": "as you entered it from the Manual's table",
               "5 × natural person": "five times the natural person's maximum"}.get(source, source)
        if m.at_large:
            out.append(R("audit.fine_rules", "Body corporate fine — at large", "caution",
                         "The maximum is at large: no statutory maximum fine, but the fine must not be inordinate "
                         "(scale.at_large).", cite=cite + ["scale.at_large"]))
        elif cap is None:
            out.append(R("audit.fine_rules", "Body corporate fine — no maximum computed", "unresolved",
                         ("The maximum fine for a body corporate cannot be worked out: "
                          + (m.company_fine_note or "the register prints no fine the app can multiply.")
                          + " Confirm the maximum."), cite=cite))
        elif p.fine_penalty_units > cap and m.conditional_ceiling_months and source == "5 × natural person" and \
                p.fine_penalty_units <= m.conditional_ceiling_months * 10 * 5:
            out.append(R("audit.fine_rules", "Body corporate fine — conditional maximum unresolved", "unresolved",
                         f"The fine ({p.fine_penalty_units} penalty units) is above the body corporate maximum on the base "
                         f"maximum ({cap}) but within {m.conditional_ceiling_months * 50} (five times the fine on the "
                         "higher maximum): that maximum applies only if the statutory condition is met, which "
                         + _school_answer(ctx) + ".", limb="(3)",
                         flags=[m.conditional_flag] if m.conditional_flag else [], cite=cite))
        elif p.fine_penalty_units > cap and source == "5 × natural person" and higher and p.fine_penalty_units <= higher:
            out.append(R("audit.fine_rules", "Body corporate fine — the provision you identified", "caution",
                         f"The fine ({p.fine_penalty_units} penalty units) is above five times the natural person's "
                         f"maximum ({cap}) but within the higher company maximum you identified ({higher} penalty "
                         "units, Step 1): the app cannot check that provision.", flags=["flag.body_corporate_fine_multiplier"],
                         cite=cite + ["scale.body_corporate_fine"], app_policy=True))
        elif p.fine_penalty_units > cap:
            out.append(R("audit.fine_rules", "Fine above the body corporate maximum", "error",
                         f"The fine ({p.fine_penalty_units} penalty units) exceeds the body corporate maximum of "
                         f"{higher if higher and source == '5 × natural person' and higher > cap else cap} ("
                         + ("the higher company maximum you identified" if higher and source == "5 × natural person"
                            and higher > cap else how) + ").",
                         cite=cite + ["scale.body_corporate_fine"],
                         flags=["flag.body_corporate_fine_multiplier"] if source == "5 × natural person" else []))
        elif source != "printed" and not crimes_act and m.fine_penalty_units and p.fine_penalty_units > m.fine_penalty_units:
            out.append(R("audit.fine_rules", "Body corporate fine above the natural person's maximum", "caution",
                         kb_statement(ctx, "flag.body_corporate_fine_multiplier"),
                         flags=["flag.body_corporate_fine_multiplier"], cite=cite))
        else:
            out.append(R("audit.fine_rules", "Body corporate fine", "pass",
                         f"The fine is within the body corporate maximum of {cap} penalty units ({how}).", cite=cite))
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
OTHER_CHECKS = [check_complete, check_maximum, check_non_parole, check_sso_npp, check_sso_reasons, check_cco,
                check_formulation, check_conviction, check_s6aaa, check_cooperation, check_indication, check_fine,
                check_adjournment, check_ancillary, check_body_corporate, check_facts_relied_on, check_band]
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


# The hard checks that rest on the Sentencing Act 1991: for contempt they give cautions (run_hard_checks).
SENTENCING_ACT_CHECKS = {"audit.category_1_2", "audit.cco_limits", "audit.conviction_requirements",
                         "audit.cooperation_statement", "audit.fine_rules", "audit.mandatory_ancillary_orders",
                         "audit.mandatory_minimum", "audit.non_parole_rules", "audit.s6aaa_statement",
                         "audit.sentence_each_charge_and_formulation", "audit.standard_sentence_use",
                         "audit.adjournment_and_other_order_limits"}


# The app's own rules, which stay errors for contempt too (not the Sentencing Act's).
APP_RULES_KEPT = {"An order on a person for a body corporate"}


def _sentencing_act_based(r: CheckResult, fw: Framework) -> bool:
    """A result that rests on the Sentencing Act (contempt: a caution). Not an app-policy rule, and not a fine above
    the maximum the contempt's own Act prints (for a person or a company); a fine above the Sentencing Act's general
    rule (10 penalty units a month) or above five times a person's maximum is."""
    if r.check_id not in SENTENCING_ACT_CHECKS or r.title in APP_RULES_KEPT:
        return False
    if r.title == "Fine above the maximum":
        return fw.maximum.fine_source == "10 per month"
    if r.title == "Fine above the body corporate maximum":
        return "flag.body_corporate_fine_multiplier" in r.flags
    return True


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
    # Contempt (the 29.1.3 rows): the Sentencing Act does not apply in its entirety (VSM 29.5.2), so every hard
    # check based on it yields a caution, not an error (audit.scope; flag.contempt_sentencing_act_and_orders). The
    # other checks keep their errors: the maximum (the contempt's own printed maximum, a company's too), the facts
    # relied on, a sentence indication (Criminal Procedure Act 2009) and the app's own rules.
    if ctx.is_contempt:
        for r in results:
            if r.outcome == "error" and _sentencing_act_based(r, fw):
                r.outcome = "caution"
                r.message += (" (Contempt: the Sentencing Act does not apply in its entirety, so this is a caution, "
                              "not an error — VSM 29.5.2.)")
                r.flags = list(dict.fromkeys(list(r.flags) + ["flag.contempt_sentencing_act_and_orders"]))
    # keep only citations that exist in the knowledge base
    for r in results:
        r.cite = [c for c in dict.fromkeys(r.cite) if kb.exists(c)]
    return results
