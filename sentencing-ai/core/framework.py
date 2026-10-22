"""
core/framework.py — STEP 3: THE LEGAL FRAMEWORK, WORKED OUT IN CODE (no AI).

From the offence register (knowledge/offences.json) and the confirmed facts:
  * the maximum penalty at the date of offending (and the maximum fine);
  * Category 1 / Category 2 status (offences on or after 20 March 2017);
  * any mandatory minimum non-parole period (gross violence; protected officials);
  * what that means for the available dispositions;
  * the gates the knowledge base requires before reasoning (audit.scope,
    audit.offence_date_and_historic_offending, audit.facts_and_proof,
    audit.manual_flags) and the Manual flags that apply.

READINGS. Where the Manual leaves a point open (e.g. it gives no commencement
date for the protected-official scheme), or a fact that would change the
framework is not proved, the framework records more than one reading. The hard
checks (core/checks.py) are run under each reading, and a breach is reported as
an error only if it is a breach under every reading — the knowledge base's rule
that "error" is only for results unlawful on every reading.

This is the "if-then" part of the app (as in Lab 1): fixed rules applied by
code, so the AI cannot get them wrong. The AI calls are given the result.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace
from datetime import date

from core import config
from core.coverage import (CATEGORY_START, DRIVING_PROTECTED, HISTORIC_MARKED_TABLE, MANSLAUGHTER,
                           MANSLAUGHTER_MINIMUM_FROM, MINIMUM_FACTS, OPEN_CATEGORY, RELATIONS_USER_SUPPLIED,
                           SSO_EFFECTIVE, contempt_kind, coverage_text, historic_marked_applies, is_school_condition,
                           licence_group, licence_map, relative_entry, untested_clauses)
from core.facts import CaseInput, ConfirmedFacts, offender_form
from core.kb import SEXUAL_TABLES_TO, KnowledgeBase
from core.results import CheckResult

CURRENT_SCALE_FROM = "1997-09-01"   # level/term pairs before this date do not follow the current s 109 table

# Mandatory minimum groups (VSM 9.1.2): the items that state each one, and how each is named.
MINIMUM_ITEMS = {"gross_violence": ["mmnpp.gross_violence_4y"], "protected_official": ["mmnpp.protected_officials"],
                 "aggravated_hi_cj": ["mmnpp.aggravated_home_invasion_carjacking_3y"],
                 "manslaughter": ["mmnpp.manslaughter_10y", "mmnpp.gross_violence_definition"],
                 "driving_protected": ["mmnpp.driving_protected_2y"]}
GROUP_LABELS = {"gross_violence": "gross violence", "protected_official": "protected official",
                "aggravated_hi_cj": "aggravated home invasion or carjacking",
                "manslaughter": "manslaughter by gross violence or a single punch",
                "driving_protected": "driving offence against a protected official"}
AGGRAVATED_HI_CJ = ("crimes-act-s-77b-aggravated-home-invasion", "crimes-act-s-79a-aggravated-carjacking")
# Chapters whose offences can attract the serious offender or continuing criminal enterprise schemes
# (serious_offender.regime: sexual, violent, drug and arson offences; cce.regime: chapters 27 and 28).
STATUS_SCHEME_CHAPTERS = {"21", "22", "24", "25", "26", "27", "28"}


# ─────────────────────────────────────────────────────────────────────────────
# Data the framework produces
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Maximum:
    months: int | None
    level: int | None
    text: str                      # the register's verbatim cell, e.g. "Level 4 imprisonment (15 years)"
    period: str                    # e.g. "from 1997-09-01"
    fine_penalty_units: int | None # the printed fine, else the general rule: 10 penalty units per month of the term
    unresolved: str = ""
    life: bool = False             # Level 1: life, or such other term as is fixed by the court (no numeric ceiling)
    at_large: bool = False         # no statutory maximum (scale.at_large): no numeric ceiling, never "inordinate"
    fine_only: bool = False        # the row prints a fine and no imprisonment: no term may be imposed
    fine_source: str = ""          # "printed" | "10 per month" | ""
    notes: list[str] = field(default_factory=list)   # cautions about the maximum (shown as a gate)
    scale_months: int | None = None   # the s 109 scale's term for the printed level, where it differs from the term
    historic_period: bool = False     # a period before 1 September 1997: the printed term is used (flag.historic_level_scale)
    historic_tables: list[str] = field(default_factory=list)   # a text-only table governs (VSM 24.1.6.x, 31.1.2)
    confirmed_by_user: bool = False   # the user entered the maximum from that table
    # A conditional maximum (audit.maximum_applicable (3); register conditional_maxima):
    conditional_status: str = ""      # one line on whether it is applied, for the framework text and the report
    conditional_applied: bool = False # the maximum above is the conditional one (its condition proved, as answered)
    conditional_ceiling_months: int | None = None   # ss 71AC, 71B within 500 m of a school, the statutory condition not
                                                    # confirmed: a term above `months` and up to this is unresolved
    conditional_flag: str = ""        # the Manual flag on that band (flag.school_500m_condition)
    # A maximum fixed by reference to another offence (audit.maximum_applicable (4)):
    company_fine_units: int | None = None   # a body corporate's maximum fine (fine.maximum): printed, else 5 × the natural
    company_fine_source: str = ""           # person's — "printed" | "5 × natural person" | "" (none available)
    relative_note: str = ""           # how it was fixed (from the offence identified, or entered by the user)
    caution_above_months: int | None = None   # incitement to an offence punishable by life (s 321I(1)(c)): above 15
    caution_flag: str = ""                    # years a caution, not an error (flag.inchoate_life_maximum_footnotes)
    company_fine_note: str = ""               # why a body corporate's maximum fine cannot be worked out
    relative_primary: str = ""                # a maximum fixed by reference to another offence: that offence's id
    relative_factor: float | None = None      # and the share of its maximum (s 321P(1)(b): 0.6; "equal": 1.0)

    def company_fine_text(self) -> str:
        """A body corporate's maximum fine in words (the framework text, Step 3 and the report)."""
        if self.at_large:
            return "at large (no statutory maximum fine; it must not be inordinate)"
        if self.company_fine_units is None:
            return "none the app can compute" + (f": {self.company_fine_note}" if self.company_fine_note else "")
        how = {"printed": "printed", "entered": "as you entered it from the Manual's table",
               "5 × natural person": "five times the natural person's maximum",
               "printed for the offence identified": ("the offence identified's printed company maximum"
                                                      + (f", {self.relative_factor:.0%} of it" if self.relative_factor
                                                         and self.relative_factor != 1 else ""))
               }.get(self.company_fine_source, self.company_fine_source)
        return f"{self.company_fine_units} penalty units ({how})"

    @property
    def ceiling_months(self) -> int | None:
        """The highest term that is not an error: the higher of the printed term and the scale's term (between the two
        a term is unresolved: audit.maximum_applicable (8), audit.level_term_mismatch), or a conditional maximum
        whose condition is not settled (between the base and it, unresolved)."""
        if self.months is None:
            return None
        return max(self.months, self.scale_months or 0, self.conditional_ceiling_months or 0)

    def describe(self) -> str:
        """The maximum in words, for the screen, the report and the AI calls."""
        if self.unresolved:
            return f"UNRESOLVED — {self.unresolved}"
        if self.life:
            what = "life imprisonment (or such other term as is fixed by the court)"
        elif self.at_large:
            what = "at large (no statutory maximum)"
        elif self.fine_only:
            what = f"a fine of {self.fine_penalty_units} penalty units (no imprisonment)"
        else:
            y, mo = divmod(int(self.months), 12)
            what = " ".join(([f"{y} year{'s' if y != 1 else ''}"] if y else [])
                            + ([f"{mo} month{'s' if mo != 1 else ''}"] if mo else [])) + " imprisonment"
        return (f"{self.text} — {what} ({self.period})" + (f"; {self.relative_note}" if self.relative_note else "")
                + (f"; {self.conditional_status}" if self.conditional_status else ""))


@dataclass
class Reading:
    """One way the framework applies. kind:
         "main"     — the reading the estimate is built on;
         "open"     — the Manual (or the dates) leave it open: a breach of the
                      category or minimum rules is an error only if the sentence
                      also breaches them under every open reading;
         "unproved" — applies only if adverse facts are proved: breaches under
                      it alone are cautions ("if proved …")."""
    kind: str
    label: str                     # starts with a lower-case "if …" (or "as entered" for the main reading)
    category: int | None
    category_text: str
    protected: str                 # the protected-official status this reading applies to the minimum: yes | no
    mm_group: str | None
    mnpp_months: int | None
    mnpp_text: str = ""
    mnpp_excluded: str = ""        # why the minimum does not apply on this reading (e.g. an accessory), or ""
    flags: list[str] = field(default_factory=list)
    cite: list[str] = field(default_factory=list)
    app_policy: bool = False       # a reading the app adds where the Manual's wording is ambiguous (no Manual flag)
    unparsed: str = ""             # a category clause the code cannot test (not assumed on this reading)

    @property
    def effective_mnpp(self) -> int | None:
        """The minimum that binds on this reading (None if none, or excluded)."""
        return None if self.mnpp_excluded else self.mnpp_months

    @property
    def title(self) -> str:
        """The label with a capital letter, for the start of a sentence."""
        return self.label[:1].upper() + self.label[1:]

    def summary(self) -> str:
        cat = f"Category {self.category}" if self.category else "no Category 1/2"
        if self.mnpp_months and self.mnpp_excluded:
            mm = f"the minimum non-parole period of {self.mnpp_months} months excluded"
        elif self.mnpp_months:
            mm = f"a minimum non-parole period of {self.mnpp_months} months"
        else:
            mm = "no mandatory minimum"
        return f"{cat}, {mm}"


@dataclass
class LicenceRule:
    """A mandatory licence order for this charge (VSM 16; order.licence_disqualification; audit.mandatory_ancillary_orders)."""
    group: str                      # the licence map's group, or "rsa_s61_3"
    applies: str                    # "yes" | "unknown" (a condition not established) | "no"
    minimum: int | None             # months (RSA s 61(3): with a conviction recorded; see minimum_no_conviction)
    minimum_no_conviction: int | None = None
    default: int | None = None      # theft: the three-month default when no period is specified
    condition: str = ""             # what it turns on (arising out of driving …), or why it does not apply
    conviction_only: bool = False   # s 319AA, theft: only on conviction
    cite: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        if self.applies == "no":
            return (f"not mandatory on these facts — it is mandatory only if {self.condition}, and on the answers "
                    "given that is not so." if self.condition else "not mandatory on these facts.")
        what = (f"at least {self.minimum} months" + (f" if a conviction is recorded, {self.minimum_no_conviction} "
                                                       "otherwise" if self.minimum_no_conviction else "")
                if self.minimum else f"a period, or the {self.default}-month default")
        return (("mandatory" if self.applies == "yes" else f"mandatory only if {self.condition} (not established)")
                + f": licence cancellation or disqualification of {what}, in the form the licence held requires"
                + (" (on conviction)" if self.conviction_only else "") + ".")


def licence_rule(kb: KnowledgeBase, case: CaseInput) -> LicenceRule | None:
    """The mandatory licence order for this charge, from the Manual's map (licence.serious_mv_offences,
    licence.other_mandatory) and its params (dangerous driving causing death or serious injury 18 months;
    substance-influenced offences other than aggravated carjacking 12; every other serious motor vehicle offence 2
    years; s 319AA 12 months on conviction; theft or attempted theft of a motor vehicle: an order, default 3 months),
    and RSA s 61(3) (audit.mandatory_ancillary_orders: 4 years if a conviction is recorded, 2 otherwise; a subsequent
    offence 8 or 4). Its conditions are the user's answers (arising out of driving; under the influence of alcohol, or
    alcohol and a drug — not a drug alone; the property a motor vehicle). None for a body corporate."""
    group = licence_group(kb, case.offence_id)
    if not group or case.body_corporate:
        return None
    params = (kb.get("order.licence_disqualification") or {}).get("params") or {}
    if group == "rsa_s61_3":
        previous = case.previous_s61_3_offence
        first = previous != "yes"
        return LicenceRule(group, "yes", 48 if first else 96, 24 if first else 48,
                           condition="a subsequent offence" if not first else "a first offence",
                           cite=["audit.mandatory_ancillary_orders", "licence.form_by_licence_status"],
                           notes=(["Whether the offender has a previous s 61(3) offence is unknown: the first-offence "
                                   "minimum is applied."] if previous == "unknown" else []))
    g = licence_map(kb)[group]
    raw = g.get("minimum")
    minimum = (int(params.get(raw, 0)) * 12 if raw and raw.endswith("_years") else int(params.get(raw, 0))) if raw \
        else None
    default = int(params.get(g.get("default"), 0)) if g.get("default") else None
    cite = [g.get("rule") or "licence.serious_mv_offences", "order.licence_disqualification",
            "licence.form_by_licence_status"]
    driving, alcohol = case.arising_from_driving, case.alcohol_influenced_driving
    if group in ("dangerous_driving", "other_serious_mv", "pursued_by_police"):
        state, cond = "yes", ""
    elif group == "arising_out_of_driving":
        state = driving if driving in ("yes", "no") else "unknown"
        cond = "the offence arose out of the driving of a motor vehicle"
    elif group.startswith("substance_influenced"):
        state = "no" if "no" in (driving, alcohol) else "yes" if driving == alcohol == "yes" else "unknown"
        cond = ("the offence arose out of driving a motor vehicle while under the influence of alcohol, or alcohol and "
                "a drug (not a drug alone)")
    else:                                               # theft or attempted theft of a motor vehicle
        state = case.motor_vehicle_stolen if case.motor_vehicle_stolen in ("yes", "no") else "unknown"
        cond = "the property stolen is a motor vehicle"
        if group == "attempted_motor_vehicle_theft":
            cond = "the offence attempted is theft and the property is a motor vehicle"
            if case.primary_offence_id is None:
                state = "unknown"                       # the offence attempted is not entered yet
            elif case.primary_offence_id != "crimes-act-s-74-theft":
                state = "no"
    return LicenceRule(group, state, minimum, default=default, condition=cond,
                       conviction_only=group in ("pursued_by_police", "motor_vehicle_theft",
                                                 "attempted_motor_vehicle_theft"), cite=cite)


def _printed_company(row: dict) -> int | None:
    return next((int(m["pu_company"]) for m in row.get("maxima") or [] if m.get("pu_company")), None)


def company_maximum_input_applies(kb: KnowledgeBase, offence_id: str | None, start, end=None) -> bool:
    """Whether a higher company maximum the user identifies can matter (fine.maximum: "caution where the user
    identifies a provision setting a higher company maximum", app policy): only where the app uses five times a
    person's maximum at the offence date — the row prints no company maximum, is not a "– body corporate" row, is
    not fixed by reference to another offence, and the maximum then in force is a resolved term or fine (not at large,
    not a historic table's)."""
    row = kb.offences.get(offence_id or "")
    if not row or start is None or _printed_company(row) or relative_entry(row) or offender_form(row.get("offence") or "")[1]:
        return False
    d = start.isoformat() if hasattr(start, "isoformat") else str(start)
    d_end = end.isoformat() if hasattr(end, "isoformat") else (str(end) if end else None)
    m = find_maximum(kb, row, d, d_end)
    return not (m.unresolved or m.at_large or m.historic_tables) and bool(m.fine_penalty_units)


def company_fine(row: dict, maximum: "Maximum", kb: KnowledgeBase | None = None) -> tuple[int | None, str, str]:
    """A body corporate's maximum fine (fine.maximum; flag.body_corporate_fine_multiplier), as (penalty units, source,
    why there is none):
      at large                      no statutory maximum (none);
      a historic table (31.1.2 …)   the company maximum the user entered from the table, else none — the current
                                    row's company maximum is not the one in force then;
      fixed by reference to another offence: that offence's printed company maximum (60% of it for s 321P(1)(b)),
                                    else five times the natural person's maximum;
      the printed company maximum; a "– body corporate" row prints the company maximum;
      otherwise five times the natural person's maximum fine (none if there is no fine to multiply)."""
    if maximum.unresolved:
        return None, "", ""
    if maximum.at_large:
        return None, "", "the maximum is at large"
    if maximum.historic_tables:
        if maximum.confirmed_by_user and maximum.fine_source == "entered" and maximum.fine_penalty_units:
            return int(maximum.fine_penalty_units), "entered", ""
        return None, "", ("the offence date falls under the Manual's historic table, which prints the company maximum: "
                          "enter it in Step 1 (“Historic offending”)")
    if maximum.relative_primary and kb is not None and maximum.relative_primary in kb.offences:
        printed = _printed_company(kb.offence(maximum.relative_primary))
        if printed:
            return math.floor((maximum.relative_factor or 1.0) * printed + 1e-9), "printed for the offence identified", ""
    elif maximum.relative_note and maximum.confirmed_by_user and maximum.fine_penalty_units:
        return int(maximum.fine_penalty_units) * 5, "5 × natural person", ""   # entered by the user (app policy)
    printed = _printed_company(row)
    if printed and not maximum.relative_note:
        return printed, "printed", ""
    if offender_form(row.get("offence") or "")[1] == "body corporate" and maximum.fine_penalty_units:
        return int(maximum.fine_penalty_units), "printed", ""     # a "– body corporate" row prints the company maximum
    if maximum.fine_penalty_units:
        return int(maximum.fine_penalty_units) * 5, "5 × natural person", ""
    return None, "", "the register prints no fine the app can multiply"


@dataclass
class StandardSentence:
    """The standard sentence scheme (VSM 9.2) as it applies to this charge (sso.scope)."""
    applies: str                   # "yes" | "unresolved" (flag.sso_effective_date) | "no"
    years: int                     # the standard sentence (the variant's, if its condition is proved)
    text: str                      # the register's text
    reason: str = ""               # why it does not apply, or why it is unresolved
    variant: str = ""              # a variant (murder: 30 years if …) and whether its condition is proved
    exclusion: str = ""            # an exclusion the register prints (s 71: not attempted trafficking)
    flags: list[str] = field(default_factory=list)

    def line(self) -> str:
        """The scheme in a line, for the AI calls and the report."""
        figure = f"standard sentence {self.years} years" + (f" ({self.variant})" if self.variant else "")
        if self.applies == "no":
            return f"does not apply — {self.reason} (the register: {self.text})."
        if self.applies == "unresolved":
            return (f"UNRESOLVED — {self.reason} ({', '.join(self.flags)}): {figure}. Treat it as possibly applying "
                    "(take it into account and state how the sentence relates to it); the app reports its non-parole "
                    "floor as unresolved.")
        return (f"applies — {figure}. It is a legislative guidepost like the maximum, not a starting point: take it into "
                "account, but do not start from it and adjust, do not classify the offending as below or above a "
                "mid-range, and do not give it too much weight; state how the sentence relates to it, naming the facts, "
                "matters and circumstances relied on (standard_sentence_statement). Non-parole period (s 11A(4)): at "
                "least 30 years for life, 70% of a term over 20 years, 60% of a term under 20 years (exactly 20 years: "
                "at least 60%, flag.sso_npp_20_year_overlap), unless that is not in the interests of justice — then say "
                "why (npp_interests_of_justice_reason). Only sentences imposed under the scheme are current sentencing "
                "practice." + (f" Exclusion: {self.exclusion}." if self.exclusion else ""))


def find_standard_sentence(row: dict, case: CaseInput, d: str, d_end: str | None) -> StandardSentence | None:
    """sso.scope: the row has a standard sentence (register standard_sentence_years), the offence was committed after
    1 February 2018, the offender was 18 or older, the matter is not heard summarily (the app's courts are the higher
    courts), and the charge is not a conspiracy, incitement or attempt (those rows carry no standard sentence). An
    offence on 1 February 2018 itself, or a period including that date, is unresolved (flag.sso_effective_date). A
    variant (murder: 30 years if the victim was a custodial officer or emergency worker on duty) applies only if its
    condition is proved (CaseInput.sso_variant_met)."""
    years = row.get("standard_sentence_years")
    if years is None:
        return None
    variants = row.get("standard_sentence_variants") or []
    exclusions = row.get("standard_sentence_exclusions") or []
    text = (variants[0].get("text") if variants else "") or (exclusions[0].get("text") if exclusions else "") \
        or f"standard sentence {years} years"
    variant = ""
    if variants:
        v = variants[0]
        if case.sso_variant_met == "yes":
            years, variant = int(v["years"]), f"{v['if']}: proved, as you answered"
        elif case.sso_variant_met == "no":
            variant = f"not {v['years']} years: you answered that it is not proved that {v['if']}"
        else:
            variant = f"{v['years']} years if {v['if']} — not established"
    exclusion = "; ".join(f"not for {x.get('excluded')}" for x in exclusions if x.get("excluded"))
    if case.body_corporate:
        return StandardSentence("no", int(years), text, reason="the offender is a body corporate: the scheme's "
                                                               "non-parole floor is a rule about imprisonment (app "
                                                               "policy)")
    if case.age_at_offence < 18:
        return StandardSentence("no", int(years), text, reason="the offender was under 18 at the offence")
    last = d_end or d
    if d > SSO_EFFECTIVE:
        return StandardSentence("yes", int(years), text, variant=variant, exclusion=exclusion)
    if last < SSO_EFFECTIVE:
        return StandardSentence("no", int(years), text, reason="the scheme applies only to offences committed after "
                                                               "1 February 2018")
    reason = ("the offence was committed on 1 February 2018 itself, and the scheme applies to offences committed after "
              "that date" if not d_end or d == last else "the offence period includes 1 February 2018")
    return StandardSentence("unresolved", int(years), text, reason=reason, variant=variant, exclusion=exclusion,
                            flags=["flag.sso_effective_date"])


@dataclass
class Framework:
    offence_id: str
    offence_label: str
    offence_date: str
    court: str
    maximum: Maximum
    level_term_note: str
    category: int | None           # main reading
    category_text: str
    protected_official: str        # the case's status: yes | no | unknown (both limbs proved?)
    protected_assumed_no: bool     # True when unknown and treated as not proved (app policy)
    mm_group: str | None           # main reading: "gross_violence" | "protected_official" | None
    mnpp_months: int | None
    mnpp_text: str
    mnpp_excluded: str             # reason the main reading's minimum does not apply (e.g. accessory)
    applied_protected: str = "no"  # the protected-official status the reading applies (yes | no); the checks use this
    alternatives: list[Reading] = field(default_factory=list)
    dispositions_note: str = ""
    gates: list[CheckResult] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    rule_items: list[str] = field(default_factory=list)   # items the framework relies on (cited)
    pu_value: str = ""
    category_unparsed: str = ""    # a category clause the code cannot test (on any reading)
    minimums_pending: list[str] = field(default_factory=list)     # minimums whose facts the app does not ask about
    sso: StandardSentence | None = None                           # the standard sentence scheme (VSM 9.2), or None
    licence: LicenceRule | None = None                            # a mandatory licence order (VSM 16), or None
    body_corporate: bool = False                                  # the offender is a body corporate
    company_maximum_units: int | None = None                      # a higher company maximum the user identifies

    @property
    def readings(self) -> list[Reading]:
        """The main reading and the others (for the gates)."""
        main = Reading("main", "as entered", self.category, self.category_text, self.applied_protected,
                       self.mm_group, self.mnpp_months, self.mnpp_text, self.mnpp_excluded)
        return [main] + list(self.alternatives)

    def under(self, reading: Reading) -> "Framework":
        """A copy of the framework under another reading (for the hard checks)."""
        return replace(self, category=reading.category, category_text=reading.category_text,
                       mm_group=reading.mm_group, mnpp_months=reading.mnpp_months, mnpp_text=reading.mnpp_text,
                       mnpp_excluded=reading.mnpp_excluded, applied_protected=reading.protected,
                       protected_official=reading.protected, protected_assumed_no=False, alternatives=[])

    def text(self) -> str:
        """The framework written out for the AI calls."""
        m = self.maximum
        lines = [
            f"Offence: {self.offence_label}; offence date {self.offence_date}; court: {self.court}.",
            f"Maximum at the offence date: {m.describe()}.",
            (f"Maximum fine ({ {'printed': 'as printed', 'entered': 'as entered by the user from the Manual\'s table'}.get(m.fine_source, 'general rule, 10 penalty units per month of the maximum term') }): "
             f"{m.fine_penalty_units} penalty units." if m.fine_penalty_units else "No fine maximum computed."),
            f"Category: {('Category ' + str(self.category)) if self.category else 'not a Category 1 or 2 offence on these facts'}"
            + ((f" — {self.category_text}" if self.category_text.startswith("Category ")      # confirmed by the user
                else f" — register: “{self.category_text}”") if self.category_text else ""),
            (f"Protected official (victim on duty AND offender knew or was reckless, both proved beyond reasonable "
             f"doubt): {self.protected_official}"
             + (" — not established, so treated as not proved (app policy, applying the rule that adverse facts "
                "need proof beyond reasonable doubt)" if self.protected_assumed_no else "")
             + (" — but the user says the protected-official scheme did not apply at the offence date, so its "
                "Category 1 status and minimum are not applied (app policy)"
                if self.protected_official == "yes" and self.applied_protected == "no" else ""))
            if self.offence_id in config.CAUSING_INJURY_OFFENCES else
            "Protected official scheme (VSM 9.1.2.3): not applicable to this charge (ss 15A–18 only).",
            "Mandatory minimum non-parole period: "
            + (f"{self.mnpp_months} months ({GROUP_LABELS.get(self.mm_group, self.mm_group)}) — {self.mnpp_text}"
               if self.mnpp_months else
               "none on the facts as entered" + (" (one applies only if further facts are proved: see the other "
                                                  "readings)" if any(a.mnpp_months for a in self.alternatives) else ""))
            + (f" EXCLUDED: {self.mnpp_excluded}" if self.mnpp_excluded else ""),
            f"Effect on dispositions: {self.dispositions_note}",
        ]
        if self.sso:
            lines.append(f"Standard sentence scheme (VSM 9.2): {self.sso.line()}")
        if self.licence:
            lines.append(f"Mandatory licence order (VSM 16): {self.licence.line()}")
        if self.body_corporate:
            m = self.maximum
            lines.append("Offender: a body corporate — no imprisonment, CCO or other order on a person; the maximum fine "
                         f"for a body corporate: {m.company_fine_text()}"
                         + (f"; a higher company maximum you identified: {self.company_maximum_units} penalty units "
                            "(app policy)" if self.company_maximum_units and m.company_fine_source == "5 × natural person"
                            else "") + ".")
        for a in self.alternatives:
            if a.kind == "unproved":
                how = "applies only if those facts are proved: it must not drive the sentence"
            elif a.app_policy:
                how = ("a reading the app adds where the Manual's wording is ambiguous (app policy): treat the point "
                       "as unresolved, but do not let it drive the sentence")
            else:
                how = "the Manual leaves this open, so treat the point as unresolved"
            lines.append(f"Other reading — {a.label}: {a.summary()}"
                         + (f" (EXCLUDED: {a.mnpp_excluded})" if a.mnpp_excluded else "")
                         + f" ({how}; flags: {', '.join(a.flags) or 'none'}).")
        if self.flags:
            lines.append("Manual flags engaged: " + ", ".join(self.flags))
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 1. The maximum at the offence date (audit.maximum_applicable, level_term_mismatch)
# ─────────────────────────────────────────────────────────────────────────────
def _months(entry: dict) -> int | None:
    """The printed term in months. Fractions count: 7½ years is 90 months (never truncated to 84)."""
    if entry.get("imprisonment_years") is not None:
        return round(float(entry["imprisonment_years"]) * 12)
    if entry.get("imprisonment_months") is not None:
        return int(entry["imprisonment_months"])
    return None


def _historic_maximum(kb: KnowledgeBase, row: dict, offence_date: str, offence_end: str | None,
                      confirmed_months: int | None, confirmed_fine: int | None = None) -> Maximum | None:
    """A period whose penalty table the Manual holds only as text (audit.offence_date_and_historic_offending;
    flag.historic_tables_unstructured): the user confirms the maximum from the table — a term, a fine, or both (the
    OHSA 1985 table at 31.1.2 gives fines, and a term only as an additional penalty after a prior conviction; for a
    body corporate the fine entered is the company's) — or it is unresolved."""
    hist = kb.historic_tables(row["id"], offence_date, offence_end)
    if not hist:
        return None
    kind, sections = hist
    if kind == "none_before_1959":
        return Maximum(None, None, "", "", None, unresolved="the Manual prints no penalty table for sexual offending "
                                                            "before 1 April 1959, so the maximum cannot be determined.")
    if kind == "no_table":
        return Maximum(None, None, "", "", None, unresolved="the Manual prints no historic penalty table for this kind "
                                                            "of sexual offence at this date — confirm the maximum.")
    where = ", ".join(f"VSM {x}" for x in sections)
    if confirmed_months or confirmed_fine:
        months = int(confirmed_months) if confirmed_months else None
        fine = int(confirmed_fine) if confirmed_fine else (months * 10 if months else None)
        return Maximum(months=months, level=None, text=f"Confirmed by you from the Manual's table at {where}",
                       period="the historic table", fine_penalty_units=fine,
                       fine_source="entered" if confirmed_fine else "10 per month", fine_only=months is None,
                       historic_tables=sections, confirmed_by_user=True,
                       notes=[f"You entered this maximum from the Manual's historic table at {where}"
                              + ("" if confirmed_fine else " (a term only: the fine is the general rule, 10 penalty "
                                                           "units a month)")
                              + "; the app cannot check it against the table, which the Manual's text holds only as "
                                "verbatim text (flag.historic_tables_unstructured)."])
    return Maximum(None, None, "", "", None, historic_tables=sections,
                   unresolved=f"historic offending: the maximum in force at the offence date is printed only in the "
                              f"Manual's table at {where}, which is held as text. Enter it in Step 1 (detailed input, "
                              f"“Historic offending”) from the table shown there, then extract the facts again.")


def _contains(entry: dict, d: str) -> bool:
    start, end = entry.get("from"), entry.get("to")
    return (start is None or start <= d) and (end is None or d <= end)


def _entry_maximum(kb: KnowledgeBase, e: dict) -> Maximum | None:
    """The Maximum one register entry prints (None if it gives no term or fine the app can read)."""
    period = f"from {e['from']}" if e.get("from") else "no start date printed"
    if e.get("to"):
        period += f" to {e['to']}"
    months, life, at_large = _months(e), bool(e.get("life")), bool(e.get("at_large"))
    printed = e.get("pu_individual") or e.get("fine_penalty_units")
    fine_only = months is None and not life and not at_large and bool(printed)
    if months is None and not (life or at_large or fine_only):
        return None
    if printed:
        fine, source = int(printed), "printed"
    elif months and not life:
        fine, source = months * 10, "10 per month"          # scale.fine_alternative ("generally"; never for life)
    else:
        fine, source = None, ""
    # Level and printed term: from 1 September 1997 the level prevails over a printed term that disagrees with the
    # s 109 scale (the band between the two is unresolved); before then the printed pairs do not follow the
    # current table, so the printed term is used and flagged (audit.level_term_mismatch; flag.historic_level_scale).
    historic_period = bool(e.get("to") and e["to"] < CURRENT_SCALE_FROM)
    scale = None if historic_period else _scale_months(kb, e.get("level"))
    return Maximum(months=months, level=e.get("level"), text=e.get("text", ""), period=period,
                   fine_penalty_units=fine, life=life, at_large=at_large, fine_only=fine_only, fine_source=source,
                   scale_months=scale if (scale and months and scale != months) else None,
                   historic_period=historic_period)


def _apply_conditional(kb: KnowledgeBase, base: Maximum, conditional: list[dict], d: str, met: str,
                       confirmed: str) -> Maximum:
    """A conditional maximum (audit.maximum_applicable (3); register conditional_maxima: ss 71AA, 71AC, 71B, 195H,
    195I). Its condition counts against the offender, so it is applied only when the user answers that it is proved
    ("yes"); "unknown" or "no" keeps the base maximum (a term above it is an error), with a note. Within 500 metres of
    a school (ss 71AC, 71B) the 20-year maximum is applied only when the user also confirms the statutory condition
    (flag.school_500m_condition); while that is unknown a term above the base and up to the conditional maximum is
    unresolved."""
    entries = [c for c in conditional if _contains(c, d)]
    if not entries:
        return base
    c = entries[0]
    school = is_school_condition(c)
    cond = c.get("condition") or c.get("condition_text") or ""
    if met == "yes" and (not school or confirmed == "yes"):
        applied = _entry_maximum(kb, c)
        if applied is None:
            base.notes.append(f"Conditional maximum “{c.get('text', '')}”: the register's entry gives no term or fine "
                              "the app can read, so the base maximum is used — confirm the maximum.")
            return base
        applied.notes = list(base.notes)
        applied.conditional_applied = True
        applied.conditional_status = (f"conditional maximum applied: “{cond}” is proved, as you answered (instead of "
                                      f"“{base.text}”)")
        if school:
            applied.notes.append("You confirmed the statutory condition for the 20-year maximum. The Manual describes "
                                 "the factor as supply or trafficking to children within 500 metres of a school, while "
                                 "the table prints only “within 500 metres of a school” (flag.school_500m_condition).")
            applied.conditional_flag = "flag.school_500m_condition"
        return applied
    other = _entry_maximum(kb, c)
    if met == "yes" and school:
        # not confirmed, or answered "no" (e.g. the trafficking was not to a child): the flag's Handling marks the
        # 20-year maximum unresolved, never settled either way
        base.conditional_ceiling_months = other.months if other and other.months else None
        base.conditional_flag = "flag.school_500m_condition"
        base.conditional_status = (f"conditional maximum unresolved: “{c.get('text', '')}” applies only if the statutory "
                                   "condition is met, which "
                                   + ("you answered is not met — the Manual's flag marks the 20-year maximum "
                                      "unresolved in that case" if confirmed == "no" else "you have not confirmed")
                                   + " (flag.school_500m_condition)")
        base.notes.append("Within 500 metres of a school, as you answered: the Manual's table prints “"
                          f"{c.get('text', '')}”, but describes the factor as supply or trafficking to children within "
                          "500 metres of a school (28.3.6), and the provisions it cites do not match the table "
                          "(flag.school_500m_condition). The 20-year maximum is applied only when you confirm the "
                          "statutory condition (Step 1): until then a term above the base maximum and up to 20 years is "
                          "unresolved, and above 20 years an error.")
        return base
    if met == "no":
        base.conditional_status = f"conditional maximum not applied: you answered that “{cond}” is not proved"
        return base
    base.conditional_status = f"conditional maximum not applied: “{cond}” is not established"
    base.notes.append(f"If “{cond}” were proved, the maximum would be “{c.get('text', '')}”. It is not established, so "
                      "the base maximum applies (adverse facts need proof beyond reasonable doubt); answer the "
                      "question in Step 1 if it is proved (audit.maximum_applicable (3)).")
    return base


# ── Maxima fixed by reference to another offence (chapter 32; s 247B): audit.maximum_applicable (4),
#    guide.inchoate.maxima_notes ──
MURDER = "common-law-common-law-murder"
DRUG_ATTEMPT_SECTIONS = {"s 71", "s 71AA", "s 71AB", "s 71AC", "s 72", "s 72A", "s 72B", "s 73"}
INCHOATE_CAP_MONTHS = 180         # fns 4805, 4807: life is not "a prescribed maximum penalty" — 15 years


def inchoate_kind(row: dict) -> str:
    """"attempt" | "conspiracy" | "incitement" | "intended" (s 247B) | "" for a row whose maximum is relative."""
    prov = row.get("provision") or ""
    if "321P" in prov or "ss 71-71AC" in prov.replace("–", "-"):
        return "attempt"
    if "321C" in prov or ("Drugs" in prov and " s 79" in prov):
        return "conspiracy"
    if "321I" in prov or ("Drugs" in prov and " s 80" in prov):
        return "incitement"
    entry = relative_entry(row)
    return "intended" if entry and entry.get("relation") == "equal_to_intended_offence" else ""


def _crimes_act(row: dict) -> bool:
    return (row.get("provision") or "").startswith("Crimes Act")


def _dpcsa(row: dict) -> bool:
    return "Drugs, Poisons" in (row.get("act") or row.get("provision") or "")


S79_S80_SECTIONS = DRUG_ATTEMPT_SECTIONS | {"s 71B"}  # trafficking, cultivation, supply to a child, possession


def _section(row: dict) -> str:
    """The section a register row's provision is in: "s 73(1)(c)" -> "s 73", "s 72B(b)" -> "s 72B"."""
    m = re.match(r"ss? (\d+[A-Z]*)", row.get("provision_norm") or "")
    return f"s {m.group(1)}" if m else ""


def _dpcsa_row_check(row: dict, kind: str, prow: dict, plabel: str) -> tuple[str, str]:
    """The DPCSA inchoate rows (s 79 conspiracy, s 80 incitement, the attempt row for ss 71–71AC, 72–72B, 73) carry
    the primary offence's maximum only for their own offences (guide.inchoate.maxima_notes; guide.drugs.
    extended_liability): ("choose", what to choose instead) for an offence outside the DPCSA, ("ask", why) for a DPCSA
    offence the Manual does not say the row covers, or ("", "")."""
    if not _dpcsa(row):
        return "", ""
    crimes = {"attempt": "an attempt (s 321P)", "conspiracy": "a conspiracy (s 321C)",
              "incitement": "an incitement (s 321I)"}.get(kind, "")
    plural = {"attempt": "attempts", "conspiracy": "conspiracies", "incitement": "incitements"}.get(kind, kind)
    covered = DRUG_ATTEMPT_SECTIONS if kind == "attempt" else S79_S80_SECTIONS
    if not _dpcsa(prow):
        return "choose", (f"This row covers {plural} to commit certain DPCSA offences only; {plabel} is not one: "
                          f"choose the Crimes Act row for {crimes}.")
    if _section(prow) not in covered:
        return "ask", (f"the Manual does not say that this row covers {plabel} (guide.drugs.extended_liability: ask "
                       "the user)")
    return "", ""


def _covering_row(row: dict, kind: str, prow: dict, pm: Maximum) -> str:
    """A more specific register row covers the charge (guide.inchoate.maxima_notes): what to choose instead, or ""."""
    if not _crimes_act(row):
        return ""
    if prow["id"] == MURDER:
        return {"conspiracy": "Conspiracy to commit murder has its own row (s 321C(1)(ba): Level 1 – life): choose "
                              "“Conspiracy to commit murder or treason”.",
                "incitement": "Incitement to commit murder has its own row (s 321I(1)(ba): Level 1 – life): choose "
                              "“Incitement to commit murder or treason”.",
                "attempt": "Attempted murder has its own row (s 321P(1A): Level 2 – 25 years): choose “Attempt to "
                           "commit murder or treason”."}.get(kind, "")
    if pm.at_large and kind in ("conspiracy", "incitement", "attempt"):
        name = {"conspiracy": "Conspiracy where penalty is at large” (s 321C(1)(b): Level 4 – 15 years",
                "incitement": "Incitement where penalty is at large” (s 321I(1)(b): Level 4 – 15 years",
                "attempt": "Attempts where penalty is at large” (s 321P(1)(c): Level 6 – 5 years"}[kind]
        return f"The offence identified is at large, which its own row covers: choose “{name})."
    if kind == "attempt" and _dpcsa(prow) and _section(prow) in DRUG_ATTEMPT_SECTIONS:
        return ("Attempts to commit DPCSA ss 71–71AC, 72–72B and 73 offences carry the primary offence's maximum: "
                "choose “Attempt to commit certain indictable drugs offences …”.")
    if kind in ("conspiracy", "incitement") and _dpcsa(prow) and pm.life:
        which = "s 79" if kind == "conspiracy" else "s 80"
        return (f"A {kind} to commit this drug offence is covered by DPCSA {which}, whose maximum is the primary "
                f"offence's (the 15-year rule for life offences does not apply where another row covers the charge): "
                f"choose the DPCSA {which} row.")
    return ""


def _user_maximum(text: str, where: str, why: str, months: int | None, life: bool) -> Maximum:
    """The maximum the user read from the legislation (app policy), or unresolved until it is entered."""
    if life or months:
        m = Maximum(months=None if life else int(months), level=None,
                    text=f"Entered by you: {'life imprisonment' if life else f'{int(months)} months'}",
                    period="as you entered it", fine_penalty_units=None if life else int(months) * 10,
                    fine_source="" if life else "10 per month", life=life, confirmed_by_user=True,
                    notes=[f"You entered this maximum. The register prints “{text}” {where}; {why}. The app cannot "
                           "check the figure (app policy)."])
        m.relative_note = "maximum entered by you (app policy)"
        return m
    return Maximum(None, None, text, "", None,
                   unresolved=f"the maximum is “{text}” {where}, and {why}: enter the maximum you read from the "
                              "legislation in Step 1 (app policy; audit.maximum_applicable (4)).")


def _relative_maximum(kb: KnowledgeBase, row: dict, entry: dict, d: str, d_end: str | None,
                      primary_id: str | None, user_months: int | None, user_life: bool) -> Maximum:
    """A maximum fixed by reference to another offence (the register's maxima[].relation), computed from the offence
    the user identifies (its maximum at the offence date) or entered by the user where the Manual cannot resolve it:
      equal_to_primary, equal_to_intended_offence  the primary offence's maximum (a Crimes Act s 321C(1)(c)
          conspiracy to commit an offence punishable by life: 15 years, error above; s 321I(1)(c) incitement: life,
          but above 15 years a caution — flag.inchoate_life_maximum_footnotes);
      fraction_of_primary (s 321P(1)(b))           60% of the primary offence's maximum;
      greater_of_primary_and_stated                the greater of the primary offence's maximum and 5 years;
      table_not_reproduced, lower_penalty_in_other_enactment, law_of_place_capped_at_life: entered by the user.
    An entered maximum always prevails (app policy: e.g. the accumulated maxima of several offences)."""
    text, relation = entry.get("text", ""), entry.get("relation", "")
    where = f"(VSM {row.get('section', '')})"
    kind = inchoate_kind(row)
    if user_life or user_months:
        why = RELATIONS_USER_SUPPLIED.get(relation) or ("the offences are several (the accumulated maxima)"
                                                       if entry.get("multiple_offences") else
                                                       "you entered the maximum rather than the offence")
        return _user_maximum(text, where, why, user_months, user_life)
    if relation in RELATIONS_USER_SUPPLIED:
        return _user_maximum(text, where, RELATIONS_USER_SUPPLIED[relation], None, False)
    what = {"attempt": "attempted", "conspiracy": "conspired", "incitement": "incited",
            "intended": "intended (the “serious offence”)"}.get(kind, "it refers to")
    if not primary_id or primary_id not in kb.offences:
        return Maximum(None, None, text, "", None,
                       unresolved=f"the maximum is “{text}” {where}: identify the offence {what} in Step 1 "
                                  "(audit.maximum_applicable (4)).")
    prow, plabel = kb.offence(primary_id), kb.offence_label(primary_id)
    if relative_entry(prow):
        return _user_maximum(text, where, f"the offence identified ({plabel}) has itself a maximum fixed by reference "
                                          "to another offence", None, False)
    drug, drug_why = _dpcsa_row_check(row, kind, prow, plabel)
    if drug == "choose":
        return Maximum(None, None, text, "", None, unresolved=drug_why)
    if drug == "ask":
        return _user_maximum(text, where, drug_why, None, False)
    pm = find_maximum(kb, prow, d, d_end, base_only=True)
    if pm.unresolved:
        hist = kb.historic_tables(prow["id"], d, d_end)
        why = (f"the maximum of the offence identified ({plabel}) at this date is printed only in the Manual's table "
               f"at VSM {', '.join(hist[1])}, held as text (open it on the Knowledge base page)" if hist and
               hist[0] == "table" else f"the maximum of the offence identified ({plabel}) is unresolved: "
                                       f"{pm.unresolved}")
        return _user_maximum(text, where, why, None, False)
    cover = _covering_row(row, kind, prow, pm)
    if cover:
        return Maximum(None, None, text, "", None, unresolved=cover)
    notes = list(pm.notes)
    if prow.get("conditional_maxima"):
        notes.append(f"The offence identified has a conditional maximum (“{prow['conditional_maxima'][0].get('text', '')}”): "
                     "the app uses its base maximum. If the condition applies, enter the maximum the legislation gives "
                     "(“Or enter the maximum”, Step 1; app policy).")
    base = replace(pm, text=f"{text} — {plabel}: {pm.text}", notes=notes,
                   relative_note=f"fixed by reference to the offence {what}: {plabel}", relative_primary=prow["id"],
                   relative_factor=1.0)
    # fns 4805, 4807: the 15-year operative maximum for a conspiracy or incitement to an offence punishable by life is
    # for the s 321C(1)(c) and s 321I(1)(c) rows only (guide.inchoate.maxima_notes)
    capped = (row.get("provision_norm") or "") in ("s 321C(1)(c)", "s 321I(1)(c)")
    if relation in ("equal_to_primary", "equal_to_intended_offence"):
        if pm.life and kind == "conspiracy" and capped:
            return replace(base, months=INCHOATE_CAP_MONTHS, life=False, level=None, scale_months=None,
                           fine_penalty_units=INCHOATE_CAP_MONTHS * 10, fine_source="10 per month",
                           text=f"15 years — {text}; the offence identified ({plabel}) is punishable by life, which is "
                                "not a prescribed maximum penalty (fn 4805)",
                           notes=notes + ["Conspiracy to commit an offence punishable by life (not murder or treason): "
                                          "the operative maximum is 15 years (fn 4805; guide.inchoate.maxima_notes; "
                                          "flag.inchoate_life_maximum_footnotes)."])
        if pm.life and kind == "incitement" and capped:
            return replace(base, caution_above_months=INCHOATE_CAP_MONTHS, caution_flag="flag.inchoate_life_maximum_footnotes",
                           notes=notes + ["Incitement to commit an offence punishable by life (not murder or treason): "
                                          "fn 4807 gives an operative maximum of 15 years but speaks of conspiracy, so a "
                                          "sentence above 15 years is a caution, not an error "
                                          "(flag.inchoate_life_maximum_footnotes)."])
        return base
    if relation == "fraction_of_primary":
        factor = float(entry.get("factor") or 0.6)
        if pm.life:
            return _user_maximum(text, where, f"the offence identified ({plabel}) is punishable by life, of which "
                                              f"{factor:.0%} is not a term", None, False)
        level_note = ([f"The offence identified's maximum is a penalty Level (Level {pm.level}). Section 321P(1)(a) "
                       "covers an attempt whose penalty the s 321P table specifies by reference to a penalty Level, and "
                       "the Manual does not reproduce that table (flag.attempt_table_not_reproduced): confirm that the "
                       "attempt falls under s 321P(1)(b) before relying on the 60% figure."] if pm.level else [])
        # "60% of the maximum": a share is never rounded up (a term or fine above it exceeds the maximum)
        if pm.fine_only:
            units = math.floor(factor * (pm.fine_penalty_units or 0) + 1e-9)
            return replace(base, fine_penalty_units=units, level=None, relative_factor=factor,
                           text=f"{text}: {units} penalty units ({factor:.0%} of {plabel}'s {pm.fine_penalty_units})",
                           notes=notes + level_note)
        months = math.floor(factor * pm.months + 1e-9)
        scale = math.floor(factor * pm.scale_months + 1e-9) if pm.scale_months else None
        fine = math.floor(factor * pm.fine_penalty_units + 1e-9) if pm.fine_source == "printed" and \
            pm.fine_penalty_units else months * 10
        return replace(base, months=months, level=None, scale_months=scale if scale != months else None,
                       fine_penalty_units=fine, fine_source="printed" if pm.fine_source == "printed" else "10 per month",
                       relative_factor=factor,
                       text=f"{text}: {factor:.0%} of {months_text_short(pm.months)} ({plabel})"
                            + (f"; the offence identified's level gives {months_text_short(pm.scale_months)}, "
                               f"{factor:.0%} of which is {months_text_short(scale)}" if scale and scale != months
                               else ""),
                       notes=notes + level_note)
    if relation == "greater_of_primary_and_stated":
        stated = int(entry.get("stated_years") or 5) * 12
        if pm.life or pm.at_large or (pm.months and pm.months >= stated):
            return base
        # the stated maximum governs: not the offence identified's, so nor is its company maximum (relative_primary)
        return replace(base, months=stated, level=entry.get("stated_level"), scale_months=None, life=False,
                       fine_only=False, fine_penalty_units=stated * 10, fine_source="10 per month", relative_factor=None,
                       relative_primary="",
                       text=f"{text}: Level {entry.get('stated_level', 6)} – {stated // 12} years (greater than "
                            f"{plabel}'s maximum)")
    return Maximum(None, None, text, "", None,
                   unresolved=f"the maximum is “{text}” {where}, a relation the app does not compute ({relation}): "
                              "enter the maximum in Step 1 (app policy).")


def months_text_short(m: int | None) -> str:
    if m is None:
        return "—"
    y, mo = divmod(int(m), 12)
    return " ".join(([f"{y} year{'s' if y != 1 else ''}"] if y else []) + ([f"{mo} month{'s' if mo != 1 else ''}"]
                                                                          if mo else [])) or "0 months"


def find_maximum(kb: KnowledgeBase, row: dict, offence_date: str, offence_end: str | None,
                 confirmed_months: int | None = None, condition_met: str = "unknown",
                 condition_confirmed: str = "unknown", primary_id: str | None = None,
                 user_months: int | None = None, user_life: bool = False, confirmed_fine: int | None = None,
                 base_only: bool = False) -> Maximum:
    """The maximum at the offence date (audit.maximum_applicable). Anything the code cannot read is
    returned as unresolved, never guessed. condition_met / condition_confirmed: the user's answers on the row's
    conditional maximum (CaseInput.conditional_condition_met, school_condition_confirmed); primary_id, user_months,
    user_life: a maximum fixed by reference to another offence (CaseInput.primary_offence_id, relative_maximum_*);
    confirmed_fine: a historic table's fine (CaseInput.historic_maximum_fine_units); base_only: the maximum of an
    offence another one refers to (no conditional maximum: the charge's own row decides what to ask)."""
    contains = _contains
    historic = _historic_maximum(kb, row, offence_date, offence_end, confirmed_months, confirmed_fine)
    if historic is not None:
        return historic

    relative = relative_entry(row)
    if relative:
        return _relative_maximum(kb, row, relative, offence_date, offence_end, primary_id, user_months, user_life)
    entries = [e for e in row.get("maxima") or [] if not e.get("unparsed")]
    first = [e for e in entries if contains(e, offence_date)]
    if offence_end:
        last = [e for e in entries if contains(e, offence_end)]
        if not first or not last or first[0] is not last[0]:
            return Maximum(None, None, "", "", None,
                           unresolved="the offence period straddles a change of maximum or a period the Manual "
                                      "does not cover — confirm the maximum (never interpolated).")
    if not first:
        return Maximum(None, None, "", "", None,
                       unresolved="the Manual's register gives no maximum for this date — confirm it.")
    e = first[0]
    base = _entry_maximum(kb, e)
    if base is None:
        period = f"from {e['from']}" if e.get("from") else "no start date printed"
        return Maximum(None, e.get("level"), e.get("text", ""), period, None,
                       unresolved="the register's entry gives no term or fine the app can read — confirm the maximum.")
    if e.get("current_only"):
        base.notes.append("The Manual prints only the current maximum for this offence, with no start date: if the "
                          "offence pre-dates it, confirm the maximum at the offence date (audit.maximum_applicable (5)).")
    if base.at_large:
        base.notes.append("No statutory maximum: the sentence must not be inordinate and may be guided by the maximum "
                          "for similar statutory offending (scale.at_large).")
    if row.get("conditional_maxima") and not base_only:
        base = _apply_conditional(kb, base, row["conditional_maxima"], offence_date, condition_met, condition_confirmed)
    return base


def _scale_months(kb: KnowledgeBase, level: int | None) -> int | None:
    """The s 109 scale's term for a level (penalty_scale.json), in months; None for life or no level."""
    if not level:
        return None
    for lvl in kb.penalty_scale["penalty_scale"]["imprisonment"]:
        if lvl.get("level") == level and not lvl.get("life"):
            return lvl.get("years", 0) * 12 or lvl.get("months")
    return None


def level_term_note(kb: KnowledgeBase, maximum: Maximum) -> str:
    """Compare the printed term with the s 109 scale for the printed level (audit.level_term_mismatch)."""
    if not maximum.level or not maximum.months:
        return ""
    if maximum.historic_period:
        scale = _scale_months(kb, maximum.level)
        if scale and scale != maximum.months:
            return (f"For a period before 1 September 1997 the Manual prints Level {maximum.level} with "
                    f"{maximum.months} months, which does not follow the current s 109 table; the Manual does not set "
                    "out the scale then in force, so the printed term is used. Confirm it.")
        return ""
    if maximum.scale_months:
        return (f"The Manual prints Level {maximum.level} with {maximum.months} months; the scale gives "
                f"{maximum.scale_months} months. The level prevails unless a contrary intention appears, so a term "
                "between the two figures is unresolved. Check the legislation.")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Category 1 / 2 (audit.category_1_2) from the register's printed conditions
# ─────────────────────────────────────────────────────────────────────────────
def find_category(row: dict, age_at_offence: int, protected: str, d: str,
                  assume_unparsed: bool = False, condition_met: str = "unknown") -> tuple[int | None, str, str]:
    """Read each printed category entry and test its conditions:
      offence date on or after 20 March 2017   (every entry)
      'victim was a protected worker'          -> protected
      'not a protected worker'                 -> not protected (footnote 3778 read as the victim)
      '18 or older' / '18 or more'             -> age at the offence ≥ 18
      'from' date                              -> offence on or after that date
    Any other clause (e.g. armed robbery's "the offender has a firearm, or …") is asked as its own question
    (condition_met, CaseInput.category_condition_met): "yes" satisfies it, "no" fails it. It counts against the
    offender, so "unknown" is never assumed: the entry is skipped and the clause is returned as the third value, so the
    framework can record an "unproved" reading — unless assume_unparsed (that reading itself)."""
    if d < CATEGORY_START:
        return None, "", ""
    unparsed = ""
    for entry in sorted(row.get("category") or [], key=lambda e: e.get("category", 9)):
        cond = (entry.get("condition") or "").lower()
        if "not a protected worker" in cond:
            if protected == "yes":
                continue
        elif "protected worker" in cond and protected != "yes":
            continue
        if ("18 or older" in cond or "18 or more" in cond) and age_at_offence < 18:
            continue
        if entry.get("from") and d < entry["from"]:
            continue
        unknown = untested_clauses(cond)
        if unknown and condition_met == "no":
            continue
        if unknown and condition_met != "yes" and not assume_unparsed:
            unparsed = unparsed or "; ".join(unknown)
            continue
        return entry.get("category"), entry.get("quote") or entry.get("condition") or "", ""
    return None, "", unparsed


# ─────────────────────────────────────────────────────────────────────────────
# 3. Mandatory minimum non-parole periods (audit.mandatory_minimum)
# ─────────────────────────────────────────────────────────────────────────────
def _mm_months(entry: dict) -> int:
    return int(entry["value"]) * (12 if entry.get("unit") == "years" else 1)


def minimum_facts(case: CaseInput) -> tuple[str, str]:
    """The facts a minimum turns on (core/coverage.MINIMUM_FACTS), as the user answered them:
      manslaughter (VSM 9.1.2.1): the prosecution gave notice, and gross violence or a single punch or strike was
        proved beyond reasonable doubt;
      ss 317AC, 317AD (VSM 9.1.2.4): the protected official was injured in the commission of the offence.
    ("yes" every fact is proved | "no" a fact is answered no | "unknown", what is not established — for the "if
    proved" reading's label). Charges without such facts: ("yes", "")."""
    if case.offence_id == MANSLAUGHTER:
        notice, how = case.prosecution_notice, case.manslaughter_circumstance
        if notice == "no" or how == "neither":
            return "no", ""
        missing = []
        if notice != "yes":
            missing.append("the prosecution gave notice that it would apply for the minimum")
        if how not in ("gross_violence", "single_punch"):
            missing.append("gross violence or a single punch or strike were proved beyond reasonable doubt")
        return ("unknown", " and ".join(missing)) if missing else ("yes", "")
    if case.offence_id in DRIVING_PROTECTED:
        injured = case.official_injured if case.official_injured in ("yes", "no") else "unknown"
        return injured, "the protected official was injured in the commission of the offence" if injured == "unknown" \
            else ""
    return "yes", ""


def pending_minimums(row: dict, case: CaseInput, d: str, d_end: str | None) -> list[str]:
    """The register's minimums whose facts are not established (core/coverage.MINIMUM_FACTS, answered "unknown"):
    they are shown only as "if proved" readings."""
    if case.body_corporate or row["id"] not in MINIMUM_FACTS or minimum_facts(case)[0] != "unknown":
        return []
    if row["id"] == MANSLAUGHTER and max(d, d_end or d) < MANSLAUGHTER_MINIMUM_FROM:
        return []
    return [e.get("text", "") for e in row.get("minimum_non_parole") or []]


def find_minimum(row: dict, offence_id: str, protected: str, d: str = "9999-12-31",
                 assume_facts: bool = False) -> tuple[str | None, int | None, str]:
    """The mandatory minimum non-parole period (VSM 9.1.2) on one reading:
       ss 15A/15B: 4 years (gross violence), 5 years if the victim is a protected official;
       ss 16–18:   only 'if victim is protected worker';
       ss 77B/79A: 3 years (an adult offender; special reasons excepted);
       manslaughter (from 1 November 2014: notice, gross violence or a single punch) and ss 317AC/317AD (the
       official injured): applied only if their facts are proved (assume_facts: make_reading passes the user's
       answers, or the "if proved" reading assumes them)."""
    entries = row.get("minimum_non_parole") or []
    if not entries:
        return None, None, ""
    if offence_id in AGGRAVATED_HI_CJ:
        return "aggravated_hi_cj", _mm_months(entries[0]), entries[0].get("text", "")
    if offence_id in MINIMUM_FACTS:
        if not assume_facts or (offence_id == MANSLAUGHTER and d < MANSLAUGHTER_MINIMUM_FROM):
            return None, None, ""
        group = "manslaughter" if offence_id == MANSLAUGHTER else "driving_protected"
        return group, _mm_months(entries[0]), entries[0].get("text", "")
    if str(row.get("chapter")) != "25":
        return None, None, ""          # a minimum the code does not know: never applied silently
    for entry in entries:
        months = _mm_months(entry)
        cond = (entry.get("condition_text") or "").lower()
        variant = entry.get("variant")
        if offence_id in config.GROSS_VIOLENCE_OFFENCES:
            if protected == "yes" and variant:
                return "protected_official", _mm_months(variant), entry.get("text", "")
            return "gross_violence", months, entry.get("text", "")
        if "protected worker" in cond:
            if protected == "yes":
                return "protected_official", months, entry.get("text", "")
            continue
        continue                       # a condition the code does not recognise: never applied silently
    return None, None, ""


def accessory_exclusion(case: CaseInput, group: str | None) -> str:
    """Why a minimum does not apply to an accessory, for the minimum's own group
    (the groups have different accessory rules):
      gross violence (VSM 9.1.2.2): any finding of guilt on the basis of
        directing, assisting or encouraging;
      protected officials (VSM 9.1.2.3): only if the offender also proves on the
        balance of probabilities that their involvement was minor;
      manslaughter (VSM 9.1.2.1): only for a single punch or strike (gross-violence
        manslaughter has no accessory exclusion in mmnpp.manslaughter_10y)."""
    if case.accessory_only != "yes" or not group:
        return ""
    if group == "gross_violence":
        return ("found guilty only on the basis of directing, assisting or encouraging: the gross-violence minimum "
                "does not apply (VSM 9.1.2.2).")
    if group == "protected_official" and case.minor_involvement_proved == "yes":
        return ("found guilty only as an accessory and minor involvement proved on the balance of probabilities: the "
                "protected-official minimum does not apply (VSM 9.1.2.3).")
    if group == "manslaughter" and case.manslaughter_circumstance == "single_punch":
        return ("found guilty only on the basis of directing, assisting or encouraging, and the minimum rests on a "
                "single punch or strike: the manslaughter minimum does not apply (VSM 9.1.2.1).")
    return ""


def make_reading(row, case: CaseInput, kind: str, label: str, protected: str, d: str,
                 category_protected: str | None = None, flags=None, cite=None, app_policy: bool = False,
                 assume_unparsed: bool = False, assume_minimum_facts: bool = False) -> Reading:
    """A reading: category from `category_protected` (defaults to `protected`),
    minimum (and its accessory exclusion) from `protected`. The facts a minimum or a category clause turns on are
    the user's answers, unless the reading assumes them (assume_minimum_facts, assume_unparsed)."""
    if case.body_corporate:          # the category and minimum rules are rules about imprisonment (app policy)
        return Reading(kind=kind, label=label, category=None, category_text="", protected="no", mm_group=None,
                       mnpp_months=None, flags=flags or [], cite=cite or [], app_policy=app_policy)
    cat, cat_text, unparsed = find_category(row, case.age_at_offence, category_protected or protected, d,
                                            assume_unparsed=assume_unparsed, condition_met=case.category_condition_met)
    group, mnpp, mnpp_text = find_minimum(row, case.offence_id, protected, d,
                                          assume_facts=assume_minimum_facts or minimum_facts(case)[0] == "yes")
    excluded = accessory_exclusion(case, group) if mnpp else ""
    return Reading(kind=kind, label=label, category=cat, category_text=cat_text, protected=protected,
                   mm_group=group, mnpp_months=mnpp, mnpp_text=mnpp_text, mnpp_excluded=excluded,
                   flags=flags or [], cite=cite or [], app_policy=app_policy, unparsed=unparsed)


STATUS_ALONE = "if Category 1 turns on the victim's status alone (the register's wording, fn 3777)"
RELATED_FLAGS_GATE = "Other Manual flags on the rules used"      # the gate listing flags related to the rules used


def protected_inputs(case: CaseInput) -> tuple[str, str, str, str]:
    """(victim on duty, offender knew, scheme confirmed, status). The protected-official scheme (VSM 9.1.2.3) is
    printed only for ss 15A–18 (chapter 25); for any other charge its questions do not apply ("no")."""
    if case.offence_id not in config.CAUSING_INJURY_OFFENCES or case.body_corporate:
        return "no", "no", "unknown", "no"
    return (case.victim_protected_worker, case.offender_knew_or_reckless, case.protected_scheme_confirmed,
            case.protected_official)


def build_readings(row, case: CaseInput, d: str, d_end: str | None,
                   kb: KnowledgeBase | None = None) -> tuple[Reading, list[Reading]]:
    """The main reading and the other readings of the protected-official scheme
    and the category date.

      victim on duty | offender knew | scheme applied (user) → readings
      yes            | yes           | yes      → main: protected
      yes            | yes           | unknown  → main: protected; open: the scheme did not yet apply
      any            | any           | no       → main: not protected (no other protected-official reading)
      yes            | no / unknown  | yes/unk. → main: not protected; open: Category 1 on the victim's
                                                  status alone (app policy, fn 3777)
      yes / unknown  | unknown       | yes/unk. → + unproved: if the missing limb(s) were proved
      unknown        | yes           | yes/unk. → main: not protected; unproved: if the victim's status were proved
      unknown        | no            | yes/unk. → unproved: if the victim's status were proved, Category 1
                                                  on the status alone (app policy)
    An offence period straddling 20 March 2017 adds an open reading at its end date, and every reading
    above is built again at the end date (the open points combine)."""
    if case.body_corporate:          # no category, minimum or protected-official scheme for a body corporate
        return make_reading(row, case, "main", "as entered", "no", d), []
    victim, knew, scheme, status = protected_inputs(case)
    scheme_off = scheme == "no"
    date_flag = [] if scheme == "yes" else ["flag.protected_official_minimum_dates"]

    applied = "yes" if status == "yes" and not scheme_off else "no"
    label = "as entered" + (" (you said the protected-official scheme did not apply)"
                            if scheme_off and victim != "no" else "")
    main = make_reading(row, case, "main", label, applied, d)
    # Each other reading as a spec: (kind, label, protected for the minimum, protected for the category or None,
    # flags, cite, app_policy). They are built at the offence date, and again at the end of a straddling period.
    specs: list[tuple] = []
    if not scheme_off:
        if status == "yes" and scheme != "yes":
            # The Manual gives no commencement date for the protected-official scheme.
            specs.append(("open", "if the protected-official scheme did not yet apply at the offence date", "no", None,
                          ["flag.protected_official_minimum_dates"], ["mmnpp.protected_officials"], False))
        if victim == "yes" and knew != "yes":
            # The register's Category 1 condition (fn 3777) speaks only of the victim's status; the minimum
            # needs knowledge or recklessness (VSM 9.1.2.3). No Manual flag records this, so app policy.
            specs.append(("open", STATUS_ALONE, "no", "yes", date_flag, ["cat1.custodial_required", "intake.victim"],
                          True))
        if status == "unknown":
            missing = ("if the offender's knowledge or recklessness were proved" if victim == "yes"
                       else "if the victim's status were proved" if knew == "yes"
                       else "if the victim's status and the offender's knowledge were proved")
            specs.append(("unproved", missing, "yes", None, date_flag, ["mmnpp.protected_officials", "findings.burden"],
                          False))
        elif victim == "unknown" and knew == "no":
            specs.append(("unproved", "if the victim's status were proved and Category 1 turns on that status alone "
                          "(fn 3777)", "no", "yes", date_flag, ["cat1.custodial_required", "findings.burden"], True))
    alternatives = [make_reading(row, case, kind, lab, prot, d, category_protected=cat_prot, flags=fl, cite=ct,
                                 app_policy=ap) for kind, lab, prot, cat_prot, fl, ct, ap in specs]
    unparsed_cite = ["audit.category_1_2", "findings.burden"]
    if main.unparsed:
        # A category clause the code cannot test (e.g. "the offender has a firearm, or …"): not relied on
        # (adverse facts need proof beyond reasonable doubt), but shown as the reading "if it were proved".
        alternatives.append(make_reading(row, case, "unproved", f"if “{main.unparsed}” were proved", main.protected, d,
                                         cite=unparsed_cite, app_policy=True, assume_unparsed=True))
    # A minimum that turns on facts (manslaughter; ss 317AC, 317AD), as the user answered them: proved → it applies
    # on the main reading (make_reading); any fact "no" → none; a fact not established → not relied on, but shown as
    # the reading "if it were proved" (at the end of a period straddling the manslaughter minimum's commencement).
    if case.offence_id in MINIMUM_FACTS:
        status, missing = minimum_facts(case)
        group = "manslaughter" if case.offence_id == MANSLAUGHTER else "driving_protected"
        later_text = "the later part of the offence period governs (on or after 1 November 2014)"
        if status == "unknown":
            at = next((x for x in (d, d_end) if x and find_minimum(row, case.offence_id, "no", x, assume_facts=True)[1]),
                      None)
            if at:
                later = "" if at == d else f", and {later_text}"
                alternatives.append(make_reading(row, case, "unproved", f"if {missing}{later}", main.protected, at,
                                                 cite=MINIMUM_ITEMS[group] + ["findings.burden"], app_policy=True,
                                                 assume_minimum_facts=True))
        elif status == "yes" and not main.mnpp_months and d_end and \
                find_minimum(row, case.offence_id, "no", d_end, assume_facts=True)[1]:
            # The facts are proved, but the period starts before the minimum commenced (1 November 2014).
            alternatives.append(make_reading(row, case, "open", f"if {later_text}", main.protected, d_end,
                                             cite=MINIMUM_ITEMS[group] + ["audit.maximum_applicable"]))
    # An offence period that straddles 20 March 2017: the category may differ, and that point combines with
    # each point above, so each of their readings is also built at the end of the period.
    if d_end and d < CATEGORY_START <= d_end:
        later = "the later part of the offence period governs (on or after 20 March 2017)"
        end_reading = make_reading(row, case, "open", "if " + later, main.protected, d_end)
        alternatives.append(end_reading)
        alternatives += [make_reading(row, case, kind, f"{lab}, and {later}", prot, d_end, category_protected=cat_prot,
                                      flags=fl, cite=ct, app_policy=ap)
                         for kind, lab, prot, cat_prot, fl, ct, ap in specs]
        if end_reading.unparsed:
            alternatives.append(make_reading(row, case, "unproved", f"if “{end_reading.unparsed}” were proved, and "
                                             f"{later}", main.protected, d_end, cite=unparsed_cite, app_policy=True,
                                             assume_unparsed=True))
    latest = max(d, d_end or d)
    # The Manual's own open category point for particular offences (each flag's Handling: unresolved, so a
    # non-custodial proposal is a caution, not an error). For ss 317AC/317AD the user may confirm Category 1
    # (audit.category_1_2 (a): "when the user confirms their Category 1 status"): "yes" makes it the main reading,
    # "no" applies the table (no category).
    if case.offence_id in OPEN_CATEGORY and not main.category and latest >= CATEGORY_START \
            and case.age_at_offence >= 18:
        cat, flag, lab = OPEN_CATEGORY[case.offence_id]
        confirmed = case.category1_confirmed if case.offence_id in DRIVING_PROTECTED else "unknown"
        if confirmed == "yes" and d >= CATEGORY_START:
            main = replace(main, category=cat, category_text=f"Category {cat}: you confirmed it (footnote 2005; {flag})")
        elif confirmed != "no":
            alternatives.append(replace(main, kind="open", label=lab, category=cat, category_text=f"open: {flag}",
                                        flags=[flag], cite=["audit.category_1_2"], app_policy=False, unparsed=""))
    # Historic offending under a text-only table (VSM 24.1.6): the provision then in force may not carry the
    # current offence's category, so the category is open until the user confirms it (flag.historic_tables_unstructured;
    # cat1.historic_sexual_provisions). For the table at VSM 24.1.6.2 the Manual marks the Category 1 provisions "#"
    # (fn 3556), so the user's answer settles it: "#" → Category 1, not marked → no category.
    hist = kb.historic_tables(case.offence_id, d, d_end) if kb is not None else None
    marked = case.historic_provision_marked if kb is not None and historic_marked_applies(kb, case.offence_id, d, None) \
        else None
    marked_cite = ["cat1.historic_sexual_provisions", "audit.category_1_2"]
    if marked is not None:
        if marked == "yes":
            main = replace(main, category=1, category_text=f"Category 1: you confirmed that the provision charged is "
                                                           f"marked “#” in the table at VSM {HISTORIC_MARKED_TABLE} "
                                                           "(fn 3556)")
        else:
            if marked == "unknown":
                alternatives.insert(0, replace(main, kind="open", label=f"if the provision charged is marked “#” in "
                                               f"the table at VSM {HISTORIC_MARKED_TABLE} (a Category 1 offence)",
                                               category=1, category_text=f"open: a provision marked “#” at VSM "
                                               f"{HISTORIC_MARKED_TABLE}", flags=["flag.historic_tables_unstructured"],
                                               cite=marked_cite, app_policy=False))
            main = replace(main, category=None, category_text="")
    elif hist and hist[0] == "table" and main.category:
        alternatives.insert(0, replace(main, kind="open", label="if the provision in force at the time carries the "
                                       "current offence's category (confirm it against the historic table)",
                                       flags=["flag.historic_tables_unstructured"], cite=marked_cite, app_policy=False))
        main = replace(main, category=None, category_text="")
    # A period running past 30 June 2017: from 1 July 2017 the current provision (and its category) governs the later
    # part, so that is an open reading (audit.maximum_applicable (1): a date range straddling a change).
    if hist and hist[0] == "table" and d_end and d <= SEXUAL_TABLES_TO < d_end:
        current = make_reading(row, case, "open", "if the later part of the offence period governs (from 1 July "
                               "2017, the current provision)", main.protected, d_end,
                               cite=["audit.maximum_applicable", "audit.category_1_2"])
        if current.category != main.category:              # stricter or not: the straddle is unresolved
            alternatives.append(current)
    # The end of a period straddling 20 March 2017, inside the 24.1.6.2 window: the same answer settles it.
    if kb is not None and d_end and d < CATEGORY_START and historic_marked_applies(kb, case.offence_id, d_end, None):
        for k, a in enumerate(alternatives):
            if a.kind == "open" and a.label.startswith("if the later part") and not a.mnpp_months:
                if case.historic_provision_marked == "yes":
                    alternatives[k] = replace(a, category=1, category_text="Category 1: a provision marked “#”")
                elif case.historic_provision_marked == "no":
                    alternatives[k] = replace(a, category=None, category_text="")
                elif a.category:
                    alternatives[k] = replace(a, label=a.label + f", and the provision charged is marked “#” in the "
                                              f"table at VSM {HISTORIC_MARKED_TABLE}", category=1,
                                              flags=["flag.historic_tables_unstructured"], cite=marked_cite)
    # The gate names a category clause the code cannot test on any reading (e.g. only at the end of a period
    # straddling 20 March 2017), even if that reading itself adds nothing and is dropped below.
    if not main.unparsed:
        main.unparsed = next((a.unparsed for a in alternatives if a.unparsed), "")
    # A reading that changes neither the category, nor the binding minimum, nor the minimum's group (which
    # decides, e.g., whether an MTMO is open) adds nothing; nor does a repeat of an earlier reading of its kind.
    def key(r: Reading):
        return r.category, r.effective_mnpp, r.mm_group
    seen = {"open": {key(main)}, "unproved": {key(main)}}
    kept = []
    for a in alternatives:
        if key(a) in seen[a.kind]:
            continue
        seen[a.kind].add(key(a))
        kept.append(a)
    return main, kept


# ─────────────────────────────────────────────────────────────────────────────
# 4. Build the framework and the gates
# ─────────────────────────────────────────────────────────────────────────────
def build_framework(kb: KnowledgeBase, facts: ConfirmedFacts) -> Framework:
    case = facts.case
    row = kb.offence(case.offence_id)
    d = case.offence_date.isoformat()
    d_end = case.offence_date_end.isoformat() if case.offence_date_end else None
    maximum = find_maximum(kb, row, d, d_end, confirmed_months=getattr(case, "historic_maximum_months", None),
                           condition_met=case.conditional_condition_met,
                           condition_confirmed=case.school_condition_confirmed, primary_id=case.primary_offence_id,
                           user_months=case.relative_maximum_months, user_life=case.relative_maximum_life,
                           confirmed_fine=case.historic_maximum_fine_units)
    status = protected_inputs(case)[3]                  # yes | no | unknown ("no" outside ss 15A–18)
    # App policy: an adverse fact not proved beyond reasonable doubt is not relied on (main reading "no").
    main, alternatives = build_readings(row, case, d, d_end, kb=kb)

    framework = Framework(
        offence_id=case.offence_id, offence_label=kb.offence_label(case.offence_id), offence_date=d,
        court=case.court, maximum=maximum, level_term_note=level_term_note(kb, maximum),
        category=main.category, category_text=main.category_text, protected_official=status,
        protected_assumed_no=(status == "unknown"), mm_group=main.mm_group, mnpp_months=main.mnpp_months,
        mnpp_text=main.mnpp_text, mnpp_excluded=main.mnpp_excluded, applied_protected=main.protected,
        alternatives=alternatives, dispositions_note=_dispositions_note(case, main, alternatives),
        category_unparsed=main.unparsed or next((a.unparsed for a in alternatives if a.unparsed), ""),
        minimums_pending=pending_minimums(row, case, d, d_end),
        sso=find_standard_sentence(row, case, d, d_end),
    )
    framework.body_corporate = case.body_corporate
    framework.company_maximum_units = case.company_maximum_units if case.body_corporate else None
    framework.licence = licence_rule(kb, case)
    maximum.company_fine_units, maximum.company_fine_source, maximum.company_fine_note = company_fine(row, maximum, kb)
    framework.rule_items = _rule_items(framework)
    framework.pu_value = _penalty_unit_value(kb, d)
    framework.gates = gates(kb, facts, framework)
    framework.flags = sorted({f for g in framework.gates for f in g.flags} | {f for a in alternatives for f in a.flags})
    return framework


def _dispositions_note(case: CaseInput, main: Reading, alternatives: list[Reading] | None = None) -> str:
    """What the category and the minimum leave open, for the main reading."""
    if case.body_corporate:
        return ("A body corporate: the dispositions open are those that are not orders on a person — a fine (up to the "
                "body corporate maximum), an adjourned undertaking, a discharge or a dismissal (app policy: no "
                "imprisonment or CCO).")
    gross = case.offence_id in config.GROSS_VIOLENCE_OFFENCES
    category1_with_minimum = gross or main.mm_group == "aggravated_hi_cj"
    parts = []
    if main.category == 1:
        parts.append("Category 1: imprisonment without a CCO.")
    elif main.category == 2:
        parts.append("Category 2: imprisonment without a CCO unless an exception is established (assistance; impaired "
                     "mental functioning reducing culpability or making imprisonment substantially more burdensome; a "
                     "court secure treatment or residential treatment order; substantial and compelling circumstances "
                     "that are exceptional and rare).")
    if main.mnpp_months and main.mnpp_excluded:
        parts.append(f"The mandatory minimum non-parole period does not apply: {main.mnpp_excluded}")
    elif main.mnpp_months:
        parts.append(f"Imprisonment with a non-parole period of at least {main.mnpp_months} months, unless a special "
                     "reason is found and recorded in writing.")
        if main.mm_group in ("protected_official", "driving_protected") and not gross:
            parts.append("If a special reason is found, the court must impose imprisonment (without a CCO) or make a "
                         "mandatory treatment and monitoring order (mtmo), a residential treatment order or a court "
                         "secure treatment order (the app can propose only imprisonment or an mtmo). A therapeutic "
                         "order such as an mtmo is open only if the offender proves on the balance of probabilities "
                         "impaired mental functioning at the offence, causally linked to it, that substantially and "
                         "materially reduces culpability and was not caused substantially by self-induced intoxication, "
                         "and the court has received a psychiatrist's or registered psychologist's report (VSM 9.1.2.3"
                         + ("; the same circumstances apply to ss 317AC and 317AD, VSM 9.1.2.4"
                            if main.mm_group == "driving_protected" else "") + "). "
                         "Whether an mtmo may be combined with imprisonment is unresolved in the Manual "
                         "(flag.mtmo_combination_sentence).")
        elif category1_with_minimum and (main.category == 1 or main.protected == "yes"):
            parts.append("A special reason may lower the minimum non-parole period; whether it opens any other "
                         "disposition is unresolved in the Manual (flag."
                         + ("gross_violence_protected_special_reason" if main.protected == "yes"
                            else "category1_special_reason") + ").")
    if not main.category and not main.effective_mnpp:
        parts.append("No category or binding mandatory minimum on the facts as entered: the full range of "
                     "dispositions is open, subject to the hierarchy of sanctions (imprisonment only as a last resort) "
                     "and each order's own limits.")
    others = [a for a in alternatives or [] if (a.category and not main.category) or (a.mnpp_months and not main.mnpp_months)]
    if others:
        parts.append("Other readings (a point the Manual leaves open, or facts not proved) would add a category or a "
                     "minimum: " + "; ".join(f"{a.label}: {a.summary()}" for a in others) + ".")
    return " ".join(parts)


def _rule_items(fw: Framework) -> list[str]:
    ids = ["audit.maximum_applicable", "scale.fine_alternative"]
    cats = {fw.category} | {a.category for a in fw.alternatives}
    groups = {fw.mm_group} | {a.mm_group for a in fw.alternatives}
    if 1 in cats:
        ids += ["cat1.custodial_required"]
    if 2 in cats:
        ids += ["cat2.custodial_unless", "cat2.substantial_compelling_test"]
    for group in [g for g in MINIMUM_ITEMS if g in groups]:
        ids += MINIMUM_ITEMS[group] + ["mm.special_reason"]
    if fw.maximum.historic_tables:
        ids += ["audit.offence_date_and_historic_offending"]
        if any("flag.historic_tables_unstructured" in a.flags for a in fw.alternatives):
            ids += ["cat1.historic_sexual_provisions"]
    if fw.protected_official != "no" or any(a.kind == "unproved" for a in fw.alternatives):
        ids += ["findings.burden"]
    if fw.sso and fw.sso.applies != "no":
        ids += ["sso.scope", "sso.list", "sso.meaning_and_use", "sso.reasons", "npp.sso_minimum"]
    if fw.maximum.life:
        ids += ["guide.murder.life_without_parole"] if fw.offence_id == "common-law-common-law-murder" else []
    if fw.maximum.relative_note:
        ids += ["guide.inchoate.maxima_notes"]
    if fw.licence and fw.licence.applies != "no":
        ids += [i for i in fw.licence.cite if i != "audit.mandatory_ancillary_orders"]
    if fw.body_corporate:
        ids += ["intake.offender_type", "fine.maximum", "order.fine.directors_liability"]
    return list(dict.fromkeys(ids))


def _penalty_unit_value(kb: KnowledgeBase, d: str) -> str:
    """The penalty unit value at the offence date, from the Manual's table
    (printed only up to 1 July 2025)."""
    months = {"January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6, "July": 7,
              "August": 8, "September": 9, "October": 10, "November": 11, "December": 12}
    best = None
    for start_text, value, _src in kb.penalty_scale.get("penalty_unit_values", {}).get("values", []):
        day, month, year = start_text.split()
        start = date(int(year), months[month], int(day)).isoformat()
        if start <= d and (best is None or start > best[0]):
            best = (start, value)
    if not best:
        return ""
    if d >= "2026-07-01":
        return f"{best[1]} (latest value printed, from {best[0]}; the value for this date must be confirmed)"
    return f"{best[1]} (from {best[0]})"


def relevant_flag(kb: KnowledgeBase, flag_id: str, offence_id: str) -> bool:
    """A flag may bear on this case unless it is linked only to other offences
    or cites only other offence chapters."""
    item = kb.get(flag_id) or {}
    linked = set(item.get("offence_ids") or []) | set(item.get("offence_ids_all") or [])
    if linked:
        return bool(linked & set(kb.offence_aliases(offence_id)))       # its own row, or its other listing
    meta = kb.meta.get(flag_id)
    return not (meta and kb._other_offence_chapter_only(meta, kb.offence_chapter(offence_id)))


# Flags about matters that cannot arise in a version-1 case (an adult natural person,
# one Victorian indictable causing-injury charge, the County or Supreme Court): children
# and the Children's Court, bodies corporate, continuing criminal enterprises, contempt,
# summary offences and the Magistrates' Court, Commonwealth offences, other offences'
# schemes (home invasion, supervision orders, drug offences), drug and alcohol treatment
# orders and court secure treatment orders (which version 1 cannot propose), and mixed
# pleas (version 1 has one charge). Matched on words in the flag id; widen version 1 →
# revisit this list.
OUT_OF_SCOPE_FLAG_WORDS = ("childrens_court", "cyfa", "serious_youth", "cce_",
                           "summary_", "mc_", "cth_", "commonwealth", "supervision_order",
                           "dato_", "csto_", "mixed_pleas")   # every register offence is now in scope: drug, home
                                                             # invasion and contempt flags reach their own offences


# Flags whose Handling confines them to particular charges, matched on words in the flag id: they are listed only
# for those charges (e.g. flag.home_invasion_mandatory_scheme_fn4044 never reaches an assault).
FLAG_WORDS_FOR = {
    "home_invasion": lambda row: "home invasion" in (row.get("offence") or "").lower(),
    "drug": lambda row: str(row.get("chapter")) == "28" or "drug" in (row.get("offence") or "").lower(),
    "contempt": lambda row: str(row.get("section") or "").startswith("29.1.3"),
}


# Flags about the parts of a sentence only a natural person can receive (categories, minimums, the standard sentence
# floors, CCOs and other orders on a person, parole, licence and alcohol exclusion orders, youth orders, a person's
# health or deprivation): not listed for a body corporate (app policy: no order on a person).
PERSON_SCHEME_FLAG_WORDS = ("aeo_", "category", "cco_", "csto_", "dato_", "gross_violence", "home_invasion_mandatory",
                            "ahi_minimum", "aggravated_hi_cj", "mm_exceptions", "mtmo_", "npp_", "parole_",
                            "protected_official", "sso_", "supervision_order", "youth", "yjc_", "licence_",
                            "indefinite_", "rto_", "justice_plan", "verdins", "bugmy", "physical_health",
                            "s317af_special_reason", "crushing", "custody", "imprisonment_express", "fine_work_order",
                            "police_child", "forensic_", "suspended")


def flag_applies(kb: KnowledgeBase, flag_id: str, case: CaseInput) -> bool:
    """A flag related to a rule the case uses is listed only if it can bear on
    this case: linked to this offence (or to none), within version 1's scope, and,
    for flags tied to a date or an age, only when that date or age applies."""
    if not relevant_flag(kb, flag_id, case.offence_id):
        return False
    if flag_id == "flag.penalty_unit_values_end_2025":            # the KB ties it to offending from 1 July 2026
        return case.offence_date.isoformat() >= "2026-07-01"
    if any(w in flag_id for w in OUT_OF_SCOPE_FLAG_WORDS):
        return False
    row = kb.offence(case.offence_id)
    if any(w in flag_id and not belongs(row) for w, belongs in FLAG_WORDS_FOR.items()):
        return False
    if "body_corporate" in flag_id and not case.body_corporate:
        return False                                               # bodies corporate: only for one
    primary = kb.offences.get(case.primary_offence_id or "") if relative_entry(row) else None
    if flag_id == "flag.body_corporate_fine_multiplier" and (_printed_company(row) or (primary and _printed_company(primary))
                                                             or offender_form(row.get("offence") or "")[1]):
        return False                                               # its Handling: the printed company maximum
    if case.body_corporate and any(w in flag_id for w in PERSON_SCHEME_FLAG_WORDS):
        return False                                               # a scheme for a person's sentence
    if flag_id == "flag.fine_unlabelled_life_25_maximum" and case.body_corporate:
        return False                                               # its Handling: "a body corporate → no caution"
    if ("youth_detention" in flag_id or "yjc_" in flag_id) and (case.body_corporate or case.age_at_sentence >= 21):
        return False                                               # youth justice centre orders: under 21 at sentence
    return True


def gates(kb: KnowledgeBase, facts: ConfirmedFacts, fw: Framework) -> list[CheckResult]:
    """The four gates (check: gate audit items), run before any reasoning."""
    case = facts.case
    out: list[CheckResult] = []
    row = kb.offence(case.offence_id)

    # ── audit.scope ──
    out.append(CheckResult("audit.scope", "Scope", "pass",
                           "Victorian offence in the Manual's register, adult offender, County or Supreme Court."))
    for w in facts.scope_warnings:
        out.append(CheckResult("audit.scope", "Scope — possible problem with the plea or the charge", "error",
                               f"{w} This app does not decide guilt: the estimate below assumes the plea or verdict "
                               "stands, and should not be relied on until this is resolved.",
                               cite=["findings.plea_acceptance"]))
    if case.serving_sentence_or_on_parole in ("yes", "unknown") and not case.body_corporate:
        out.append(CheckResult("audit.scope", "Existing sentence or parole", "caution",
                               "The offender may be serving a sentence or on parole. The app does not check "
                               "cumulation, a new single non-parole period or totality with an existing sentence "
                               "(outside its scope: one charge per run).", cite=["cum.presumption_cumulation", "npp.new_single_npp"],
                               app_policy=True))
    if case.body_corporate:
        m = fw.maximum
        out.append(CheckResult("audit.fine_rules", "Body corporate", "caution",
                               "The offender is a body corporate. The app applies no Category 1/2 status, mandatory "
                               "minimum or standard sentence floor (each is a rule about imprisonment or framed for a "
                               "natural person), and an order on a person (imprisonment, a CCO) is an error (app "
                               "policy). The maximum fine for a body corporate: " + m.company_fine_text()
                               + (f"; a higher company maximum you identified: {case.company_maximum_units} penalty "
                                  "units (a fine up to it is a caution: the app cannot check the provision)"
                                  if case.company_maximum_units and m.company_fine_source == "5 × natural person"
                                  else "")
                               + ". Directors may be declared liable for the fine (order.fine.directors_liability).",
                               flags=["flag.body_corporate_fine_multiplier"] if m.company_fine_source == "5 × natural person"
                               else [], cite=["intake.offender_type", "fine.maximum", "order.fine.directors_liability"],
                               app_policy=True))
    elif case.age_at_sentence < 21:
        out.append(CheckResult("audit.scope", "Offender under 21 at sentence", "caution",
                               "A youth justice centre order (and, for protected-official offences, the youth justice "
                               "centre alternative to the minimum) may be open for an offender under 21 at sentence. "
                               "The app cannot propose or check those orders (outside its scope)."
                               + (" The age at sentence is assumed (not stated): it was taken as the age at the "
                                  "offence." if getattr(case, "age_at_sentence_assumed", False) else ""),
                               cite=["order.youth_detention", "mmnpp.protected_officials"], app_policy=True))

    # ── audit.offence_date_and_historic_offending ──
    where = ", ".join(fw.maximum.historic_tables)
    if fw.maximum.historic_tables and not fw.maximum.confirmed_by_user:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Historic offending — confirm the maximum",
                               "error", f"This is historic offending: confirm the maximum in force at the time from the "
                               f"Manual's table at {where} (VSM 5.1.3, 5.2.9.2.1). The table is held only as text; Step 1 "
                               "(detailed input) shows it and takes the maximum. Until then the maximum is unresolved.",
                               flags=["flag.historic_tables_unstructured"],
                               cite=["audit.offence_date_and_historic_offending"]))
    elif fw.maximum.historic_tables:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Historic offending — maximum confirmed",
                               "caution", f"You confirmed the maximum from the Manual's table at {where}: "
                               f"{fw.maximum.describe()}.", flags=["flag.historic_tables_unstructured"],
                               cite=["audit.offence_date_and_historic_offending"]))
    elif fw.maximum.unresolved:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Offence date", "unresolved",
                               f"Maximum: {fw.maximum.unresolved}", cite=["audit.maximum_applicable"]))
    else:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Offence date", "pass",
                               f"The register's maximum for {fw.offence_date} is {fw.maximum.text} ({fw.maximum.period}). "
                               "No text-only historic penalty table covers this offence."))
    if fw.level_term_note:
        out.append(CheckResult("audit.level_term_mismatch", "Penalty level and term", "caution", fw.level_term_note,
                               flags=["flag.historic_level_scale"] if fw.maximum.historic_period else []))
    for note in fw.maximum.notes:
        out.append(CheckResult("audit.maximum_applicable", "Maximum — check", "caution", note,
                               flags=[f for f in dict.fromkeys(re.findall(r"flag\.[a-z0-9_]+", note)) if kb.exists(f)],
                               cite=["audit.maximum_applicable"]))

    # ── coverage: parts of this offence's scheme the hard checks do not implement yet (core/coverage.py) ──
    gap = coverage_text(kb, case.offence_id)
    if gap:
        out.append(CheckResult("audit.scope", "Not fully checked", "caution", gap, app_policy=True))
    if fw.category_unparsed:
        out.append(CheckResult("audit.category_1_2", "Category condition not established", "caution",
                               f"The register's category condition includes “{fw.category_unparsed}”. It is not "
                               "established (Step 1), so it is not relied on (adverse facts need proof beyond reasonable "
                               "doubt); the reading “if it were proved” is shown.",
                               cite=["audit.category_1_2", "findings.burden"], app_policy=True))
    for text in fw.minimums_pending:
        group = "manslaughter" if case.offence_id == MANSLAUGHTER else "driving_protected"
        missing = minimum_facts(case)[1]
        accessory = (" If it rests on a single punch or strike, it does not apply to an offender who intentionally "
                     "directed, assisted or encouraged the offence." if group == "manslaughter" else "")
        out.append(CheckResult("audit.mandatory_minimum", "Mandatory minimum — facts not established", "caution",
                               f"The register prints “{text}”. It applies only if {MINIMUM_FACTS[case.offence_id]}. It is "
                               f"not established that {missing.replace(' were proved', ' was proved')} (Step 1), so the "
                               "minimum is not applied on the facts as entered; the reading “if proved” shows it."
                               + accessory,
                               cite=MINIMUM_ITEMS[group] + ["mmnpp.overview"]
                               + (["intake.prosecution_notices"] if group == "manslaughter" else []), app_policy=True))
    out += _confirmation_gates(kb, case, fw, row)
    out += _sso_gates(case, fw, row)
    if fw.licence and fw.licence.applies != "no":
        lic = fw.licence
        out.append(CheckResult("audit.mandatory_ancillary_orders", "Mandatory licence order",
                               "pass" if lic.applies == "yes" and not lic.notes else "caution",
                               f"Licence order: {lic.line()}" + "".join(f" {n}" for n in lic.notes), cite=lic.cite))
    if case.prior_convictions != "none" and kb.offence_chapter(case.offence_id) in STATUS_SCHEME_CHAPTERS \
            and not case.body_corporate:
        cce = kb.offence_chapter(case.offence_id) in ("27", "28")
        out.append(CheckResult("audit.scope", "Offender-status schemes not checked", "caution",
                               "If the offender is a serious offender (sexual, violent, drug and arson offences: "
                               "community protection becomes the principal purpose and the status is recorded)"
                               + (", or a continuing criminal enterprise offender sentenced for a CCE offence (the "
                                  "maximum becomes the lesser of twice the maximum term and 25 years)" if cce else "")
                               + ", different rules apply. Those statuses turn on the offender's history, which the app "
                               "does not ask about yet (guide step 19).",
                               cite=["serious_offender.regime"] + (["cce.regime"] if cce else []), app_policy=True))

    # ── audit.facts_and_proof ──
    for f in facts.facts:
        if f["bearing"] == "aggravating" and f["basis"] in ("alleged", "disputed", "assumption"):
            out.append(CheckResult("audit.facts_and_proof", f"Fact {f['id']} needs proof", "caution",
                                   f"“{f['text']}” is adverse to the offender but its basis is {f['basis']}: it can be "
                                   "relied on only if proved beyond reasonable doubt.", cite=["findings.burden"]))
        if f["bearing"] == "mitigating" and f["basis"] in ("alleged", "disputed", "assumption"):
            out.append(CheckResult("audit.facts_and_proof", f"Fact {f['id']} needs evidence", "caution",
                                   f"“{f['text']}” is mitigating but its basis is {f['basis']}: it needs evidence "
                                   "(balance of probabilities); mitigation cannot be inferred from an absence of "
                                   "evidence.", cite=["findings.burden"]))
        if f["bearing"] == "unclear" and f["basis"] in ("alleged", "disputed", "assumption"):
            out.append(CheckResult("audit.facts_and_proof", f"Fact {f['id']}: bearing unclear", "caution",
                                   f"“{f['text']}” has an unclear bearing and its basis is {f['basis']}: used against "
                                   "the offender it needs proof beyond reasonable doubt; in mitigation, evidence on the "
                                   "balance of probabilities.", cite=["findings.burden"], app_policy=True))
        if f.get("quote_verified") is False:
            out.append(CheckResult("audit.facts_and_proof", f"Fact {f['id']}: quote not found", "caution",
                                   f"The words quoted for “{f['text']}” do not appear in the narrative. Check the fact."))

    # ── protected officials: proof, knowledge and commencement (ss 15A–18 only) ──
    if case.offence_id in config.CAUSING_INJURY_OFFENCES and not case.body_corporate:
        out += _protected_gates(case, fw)
    out += _accessory_and_flag_gates(kb, case, fw, row, out)
    return out


def _confirmation_gates(kb: KnowledgeBase, case: CaseInput, fw: Framework, row: dict) -> list[CheckResult]:
    """Gates for the answers that settle a point the Manual leaves to be confirmed for particular charges: the
    Category 1 status of ss 317AC/317AD, a historic provision marked "#", and contempt."""
    out: list[CheckResult] = []
    d, d_end = fw.offence_date, case.offence_date_end.isoformat() if case.offence_date_end else None
    if case.offence_id in DRIVING_PROTECTED and max(d, d_end or d) >= CATEGORY_START and not case.body_corporate:
        flag, cite = "flag.driving_protected_category1", ["audit.category_1_2"]
        if case.category1_confirmed == "yes":
            out.append(CheckResult("audit.category_1_2", "Category 1 — confirmed by you", "caution",
                                   "You confirmed that this is a Category 1 offence (footnote 2005); the penalty table "
                                   "marks no category. Category 1 is applied: imprisonment without a CCO, unless a "
                                   "special reason opens the orders VSM 9.1.2.4 lists.", flags=[flag],
                                   cite=["cat1.custodial_required"] + cite))
        elif case.category1_confirmed == "no":
            out.append(CheckResult("audit.category_1_2", "Category — the penalty table applied", "caution",
                                   "You said this is not a Category 1 offence, so the penalty table (no category) is "
                                   "applied, although footnote 2005 describes it as one (your answer settles the "
                                   "Manual's open point: app policy).", flags=[flag], cite=cite, app_policy=True))
        else:
            out.append(CheckResult("audit.category_1_2", "Category 1 — confirm it", "caution",
                                   "Footnote 2005 describes this as a Category 1 offence; the penalty table marks no "
                                   "category. Until you confirm it (Step 1), a sentence other than imprisonment is shown "
                                   "as unresolved, not as an error.", flags=[flag], cite=cite))
    if historic_marked_applies(kb, case.offence_id, d, d_end) and not case.body_corporate:
        cite, flag = ["cat1.historic_sexual_provisions", "audit.category_1_2"], ["flag.historic_tables_unstructured"]
        marked = case.historic_provision_marked
        if marked == "yes":
            out.append(CheckResult("audit.category_1_2", "Historic provision — Category 1, as you confirmed", "caution",
                                   f"You confirmed that the provision charged is marked “#” in the table at VSM "
                                   f"{HISTORIC_MARKED_TABLE}: a Category 1 offence where the offender was 18 or older "
                                   "(fn 3556).", flags=flag, cite=cite))
        elif marked == "no":
            out.append(CheckResult("audit.category_1_2", "Historic provision — not marked “#”", "caution",
                                   f"You said the provision charged is not marked “#” in the table at VSM "
                                   f"{HISTORIC_MARKED_TABLE}, so no category is applied for offending under it.",
                                   flags=flag, cite=cite))
        else:
            out.append(CheckResult("audit.category_1_2", "Historic provision — confirm the category", "caution",
                                   f"The table at VSM {HISTORIC_MARKED_TABLE} marks the Category 1 provisions “#” "
                                   "(fn 3556). Whether the provision charged is one is not confirmed (Step 1), so a "
                                   "sentence the Category 1 rules would exclude is shown as unresolved.",
                                   flags=flag, cite=cite))
    kind = contempt_kind(row)
    if kind:
        out.append(CheckResult("audit.scope", "Contempt — limits of the estimate", "caution",
                               "For contempt the Sentencing Act does not apply in its entirety (VSM 29.5.2), so the "
                               "app's hard checks based on it give cautions, not errors; a term or fine above the "
                               "contempt's own printed maximum stays an error. The other punishments the Manual "
                               "mentions for contempt — a suspended sentence in the Supreme Court (including on a "
                               "condition of unpaid community work), imprisonment for an indefinite term (coercive "
                               "punishment for a refusal to give evidence) and sequestration of assets — cannot be "
                               "proposed or checked by the app.",
                               flags=["flag.contempt_sentencing_act_and_orders"]
                               + (["flag.contempt_gravity_culpability_29_2_2_2"] if kind != "other" else []),
                               cite=["audit.scope"]))
    if kind == "other_body" and case.contempt_by_higher_court != "yes":
        out.append(CheckResult("audit.scope", "Contempt — which body is dealing with it?", "caution",
                               "Whether the County Court or Supreme Court is dealing with this contempt is not stated "
                               "(Step 1). The estimate assumes the court you chose is, applying the maximum the "
                               "Manual's table prints for the Act (VSM 29.1.3); the Manual does not say which body "
                               "deals with it.", app_policy=True))
    return out


SSO_COMPELLING = "crimes-act-s-39-2-rape-by-compelling-sexual-penetration"


def _sso_gates(case: CaseInput, fw: Framework, row: dict) -> list[CheckResult]:
    """The standard sentence scheme (sso.scope; audit.standard_sentence_use)."""
    out: list[CheckResult] = []
    sso = fw.sso
    cite = ["sso.scope", "sso.list"]
    if sso and sso.applies == "yes":
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence scheme applies", "pass",
                               f"Standard sentence {sso.years} years ({sso.text}): a guidepost, not a starting point "
                               "(VSM 9.2). The non-parole floor of s 11A(4) and the statement of how the sentence "
                               "relates to the standard sentence are checked in code.", cite=cite))
    elif sso and sso.applies == "unresolved":
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence scheme — commencement", "unresolved",
                               f"Whether the scheme applies is unresolved: {sso.reason} (flag.sso_effective_date). Its "
                               "non-parole floor and the statement of how the sentence relates to the standard sentence "
                               "are reported as unresolved, not as errors.", flags=list(sso.flags), cite=cite))
    elif sso:
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence scheme does not apply", "pass",
                               f"{sso.reason[:1].upper() + sso.reason[1:]}.", cite=["sso.scope"]))
    if sso and sso.applies != "no" and sso.variant.endswith("not established"):
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence — the higher figure not established",
                               "caution", f"The standard sentence would be {sso.variant.split(' years')[0]} years if "
                               f"{(row.get('standard_sentence_variants') or [{}])[0].get('if', '')}; that is not "
                               f"established (Step 1), so {sso.years} years is used (adverse facts need proof beyond "
                               "reasonable doubt).", cite=["sso.list", "findings.burden"], app_policy=True))
    if sso and sso.applies != "no" and sso.exclusion:
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence — exclusion", "caution",
                               f"The register prints “{sso.text}”. If the charge is attempted trafficking, choose “Attempt "
                               "to commit certain indictable drugs offences …” instead: an attempt is not a standard "
                               "sentence offence (sso.scope).", cite=["sso.scope"]))
    if case.offence_id == SSO_COMPELLING and max(fw.offence_date, (case.offence_date_end.isoformat()
                                                                  if case.offence_date_end else fw.offence_date)) \
            > SSO_EFFECTIVE:
        out.append(CheckResult("audit.standard_sentence_use", "Standard sentence — rape by compelling sexual penetration",
                               "caution", "The 9.2 list says “Rape 10 years”, but the register prints the standard "
                               "sentence only against ss 38(2)–(3), not s 39(2). The app keeps this charge outside the "
                               "scheme, as the register does; confirm it against the legislation.",
                               flags=["flag.sso_rape_by_compelling"], cite=["sso.list"]))
    if fw.maximum.relative_note or relative_entry(row):
        kind = inchoate_kind(row)
        if kind in ("attempt", "conspiracy", "incitement"):
            out.append(CheckResult("audit.standard_sentence_use", "Attempt, conspiracy or incitement", "pass",
                                   "Conspiring, inciting or attempting to commit a standard sentence offence is not "
                                   "itself a standard sentence offence (sso.scope), and the register prints no category "
                                   "or mandatory minimum for this row.", cite=["sso.scope"]))
    return out


def _protected_gates(case: CaseInput, fw: Framework) -> list[CheckResult]:
    out: list[CheckResult] = []
    alone = [a for a in fw.alternatives if a.label.startswith(STATUS_ALONE)]       # at either date of a period
    alone_text = ((" The register's Category 1 condition (fn 3777) speaks only of the victim's status, so whether "
                   "Category 1 applies on the victim's status alone is treated as unresolved (app policy)."
                   + "".join(f" {a.title}: {a.summary()}." for a in alone)) if alone else "")
    if fw.protected_assumed_no:
        unproved = [a for a in fw.alternatives if a.kind == "unproved"]
        out.append(CheckResult("audit.category_1_2", "Protected official — not established", "caution",
                               "Whether the victim was a protected official on duty, and whether the offender knew or "
                               "was reckless as to that, is not established. Adverse facts must be proved beyond "
                               "reasonable doubt, so the estimate proceeds as if not proved."
                               + "".join(f" {a.title}: {a.summary()}." for a in unproved) + alone_text,
                               cite=["mmnpp.protected_officials", "findings.burden"], app_policy=True))
    if case.victim_protected_worker == "yes" and case.offender_knew_or_reckless == "no":
        # The first sentence is the Manual's rule; only the status-alone point (if any) is app policy.
        out.append(CheckResult("audit.category_1_2", "Victim on duty, knowledge not proved", "caution",
                               "The minimum non-parole period needs proof that the offender knew or was reckless as to "
                               "the victim's status (VSM 9.1.2.3), so it is not applied." + alone_text,
                               cite=["mmnpp.protected_officials", "cat1.custodial_required"]))
    if case.victim_protected_worker == "unknown" and case.offender_knew_or_reckless == "no":
        if_status = [a for a in fw.alternatives if a.kind == "unproved"]
        if if_status:
            out.append(CheckResult("audit.category_1_2", "Victim's status not established", "caution",
                                   "Whether the victim was a protected official on duty is not established, so it is "
                                   "not relied on; the offender's knowledge is not proved, so the minimum cannot apply."
                                   + "".join(f" {a.title}: {a.summary()}." for a in if_status),
                                   cite=["cat1.custodial_required", "findings.burden"], app_policy=True))
    scheme = case.protected_scheme_confirmed
    if case.victim_protected_worker != "no" and scheme == "no":
        out.append(CheckResult("audit.manual_flags", "Protected-official scheme: commencement", "caution",
                               "You said the protected-official scheme did not apply at the offence date, so the "
                               "estimate applies neither its Category 1 status nor its minimum, on any reading (app "
                               "policy: the Manual does not say what applies instead).",
                               flags=["flag.protected_official_minimum_dates"], cite=["mmnpp.protected_officials"],
                               app_policy=True))
    elif case.protected_official == "yes" and scheme != "yes":
        out.append(CheckResult("audit.manual_flags", "Protected-official scheme: commencement", "caution",
                               "The Manual gives no commencement date for the protected-official minimums and Category 1 "
                               "status. Until you confirm they applied at the offence date (in Step 1, then extract the "
                               "facts again), breaches of the category or the minimum that depend on them are shown as "
                               "unresolved, not as errors.",
                               flags=["flag.protected_official_minimum_dates"], cite=["mmnpp.protected_officials"]))
    return out


def _accessory_and_flag_gates(kb: KnowledgeBase, case: CaseInput, fw: Framework, row: dict,
                              before: list[CheckResult]) -> list[CheckResult]:
    out: list[CheckResult] = []
    # ── accessories: each minimum has its own rule (worked out for every reading) ──
    readings = fw.readings
    if case.accessory_only == "yes" and case.minor_involvement_proved != "yes" and \
            any(r.mm_group == "protected_official" and r.mnpp_months for r in readings):
        out.append(CheckResult("audit.mandatory_minimum", "Accessory — minor involvement not proved", "caution",
                               "The protected-official minimum does not apply to an accessory only if the offender proves "
                               "on the balance of probabilities that their involvement was minor; that is not shown, so "
                               "that minimum is applied wherever it applies.", cite=["mmnpp.protected_officials"]))
    # Minimums that bind on the estimate's own readings, and those that apply only if adverse facts are proved —
    # only the groups that have an accessory rule (ss 77B/79A and ss 317AC/317AD have none in the Manual).
    effect = {"gross_violence": "the gross-violence minimum would not apply",
              "protected_official": "the protected-official minimum would not apply if the offender also proved "
                                    "that their involvement was minor",
              "manslaughter": "the manslaughter minimum would not apply if it rests on a single punch or strike"}
    if case.manslaughter_circumstance == "gross_violence":
        effect.pop("manslaughter")              # gross-violence manslaughter has no accessory exclusion (VSM 9.1.2.1)
    elif case.manslaughter_circumstance == "single_punch":
        effect["manslaughter"] = "the manslaughter minimum would not apply (it rests on a single punch or strike)"
    name = {"gross_violence": "The gross-violence minimum", "protected_official": "The protected-official minimum",
            "manslaughter": "The manslaughter minimum"}
    if case.accessory_only == "yes" and case.manslaughter_circumstance not in ("gross_violence", "single_punch") and \
            any(r.mm_group == "manslaughter" and r.mnpp_months for r in readings):
        out.append(CheckResult("audit.mandatory_minimum", "Accessory — single punch or gross violence?", "caution",
                               "The offender was found guilty only on the basis of directing, assisting or encouraging. "
                               "The manslaughter minimum does not apply to them if it rests on a single punch or strike, "
                               "but gross-violence manslaughter has no such exclusion (VSM 9.1.2.1); which, if either, is "
                               "proved is not established (Step 1).", cite=["mmnpp.manslaughter_10y"]))
    binding = sorted({r.mm_group for r in readings if r.mm_group in effect and r.mnpp_months and r.kind != "unproved"})
    if_proved = sorted({r.mm_group for r in readings if r.mm_group in effect and r.mnpp_months and r.kind == "unproved"}
                       - set(binding))
    if case.accessory_only == "unknown" and (binding or if_proved):
        parts = []
        if binding:
            parts.append("the minimum is applied. If so found, "
                         + "; and ".join(effect.get(g, "the minimum may not apply") for g in binding) + ".")
        for g in if_proved:
            parts.append(f"{name.get(g, 'A minimum')} applies here only if further facts are proved; if they were, "
                         f"and the offender were found guilty only as an accessory, "
                         f"{effect.get(g, 'the minimum may not apply')}.")
        out.append(CheckResult("audit.mandatory_minimum", "Accessory basis unknown", "caution",
                               "Whether the offender was found guilty only as an accessory (directing, assisting or "
                               "encouraging) is unknown; " + " ".join(parts) if binding else
                               "Whether the offender was found guilty only as an accessory (directing, assisting or "
                               "encouraging) is unknown. " + " ".join(parts),
                               cite=[MINIMUM_ITEMS[g][0] for g in binding + if_proved], app_policy=True))

    # ── audit.manual_flags ──
    if (row.get("confidence") and row["confidence"] != "high") or row.get("notes"):
        out.append(CheckResult("audit.manual_flags", "Register row note", "caution",
                               f"The register marks this offence's row “{row.get('confidence')}” confidence. "
                               + " ".join(row.get("notes") or [])))
    shown = {f for r in before + out for f in r.flags}
    for flag in kb.flags_for_offence(case.offence_id):            # flags linked to the offence
        if flag in shown or not flag_applies(kb, flag, case):
            continue
        out.append(CheckResult("audit.manual_flags", kb.title(flag), "caution", kb.statement(flag), flags=[flag]))
        shown.add(flag)
    related = [f for f in kb.flags_related_to([i for i in fw.rule_items if not i.startswith(("audit.", "scale."))])
               if f not in shown and flag_applies(kb, f, case)]
    if related:                                                 # flags related to the rules used
        out.append(CheckResult("audit.manual_flags", RELATED_FLAGS_GATE, "caution",
                               "The Manual is inconsistent or incomplete on related points: "
                               + "; ".join(kb.title(f) for f in related) + ".", flags=related))
    if fw.offence_date >= "2026-07-01":
        out.append(CheckResult("audit.manual_flags", "Penalty unit value", "caution",
                               kb.statement("flag.penalty_unit_values_end_2025"),
                               flags=["flag.penalty_unit_values_end_2025"]))
    return out
