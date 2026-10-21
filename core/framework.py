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

from dataclasses import dataclass, field, replace
from datetime import date

from core import config
from core.facts import CaseInput, ConfirmedFacts
from core.kb import KnowledgeBase
from core.results import CheckResult

CATEGORY_START = "2017-03-20"   # Category 1/2 apply to offences on or after this date (audit.category_1_2; fn 3774)


# ─────────────────────────────────────────────────────────────────────────────
# Data the framework produces
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Maximum:
    months: int | None
    level: int | None
    text: str                      # the register's verbatim cell, e.g. "Level 4 imprisonment (15 years)"
    period: str                    # e.g. "from 1997-09-01"
    fine_penalty_units: int | None # the general fine alternative: 10 penalty units per month of the term
    unresolved: str = ""


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
            f"Maximum at the offence date: {m.text} ({m.months} months), {m.period}."
            + (f" UNRESOLVED: {m.unresolved}" if m.unresolved else ""),
            f"Maximum fine (general rule, 10 penalty units per month of the maximum term): "
            f"{m.fine_penalty_units} penalty units." if m.fine_penalty_units else "No fine maximum computed.",
            f"Category: {('Category ' + str(self.category)) if self.category else 'not a Category 1 or 2 offence on these facts'}"
            + (f" — register: “{self.category_text}”" if self.category_text else ""),
            f"Protected official (victim on duty AND offender knew or was reckless, both proved beyond reasonable "
            f"doubt): {self.protected_official}"
            + (" — not established, so treated as not proved (app policy, applying the rule that adverse facts "
               "need proof beyond reasonable doubt)" if self.protected_assumed_no else "")
            + (" — but the user says the protected-official scheme did not apply at the offence date, so its "
               "Category 1 status and minimum are not applied (app policy)"
               if self.protected_official == "yes" and self.applied_protected == "no" else ""),
            "Mandatory minimum non-parole period: "
            + (f"{self.mnpp_months} months ({self.mm_group.replace('_', ' ')}) — {self.mnpp_text}" if self.mnpp_months
               else "none on these facts")
            + (f" EXCLUDED: {self.mnpp_excluded}" if self.mnpp_excluded else ""),
            f"Effect on dispositions: {self.dispositions_note}",
        ]
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
def find_maximum(kb: KnowledgeBase, row: dict, offence_date: str, offence_end: str | None) -> Maximum:
    def contains(entry, d):
        start, end = entry.get("from"), entry.get("to")
        return (start is None or start <= d) and (end is None or d <= end)

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
    months = None
    if e.get("imprisonment_years"):
        months = int(e["imprisonment_years"]) * 12
    elif e.get("imprisonment_months"):
        months = int(e["imprisonment_months"])
    period = f"from {e['from']}" if e.get("from") else "no start date printed"
    if e.get("to"):
        period += f" to {e['to']}"
    return Maximum(months=months, level=e.get("level"), text=e.get("text", ""), period=period,
                   fine_penalty_units=(months * 10 if months else None))


def level_term_note(kb: KnowledgeBase, maximum: Maximum) -> str:
    """Compare the printed term with the s 109 scale for the printed level."""
    if not maximum.level or not maximum.months:
        return ""
    for lvl in kb.penalty_scale["penalty_scale"]["imprisonment"]:
        if lvl.get("level") == maximum.level:
            scale = lvl.get("years", 0) * 12 or lvl.get("months")
            if scale and scale != maximum.months:
                return (f"The Manual prints Level {maximum.level} with {maximum.months} months; the scale gives "
                        f"{scale} months. The level prevails unless a contrary intention appears.")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# 2. Category 1 / 2 (audit.category_1_2) from the register's printed conditions
# ─────────────────────────────────────────────────────────────────────────────
def find_category(row: dict, age_at_offence: int, protected: str, d: str) -> tuple[int | None, str]:
    """Read each printed category entry and test its conditions:
      offence date on or after 20 March 2017   (every entry)
      'victim was a protected worker'          -> protected
      'not a protected worker'                 -> not protected (footnote 3778 read as the victim)
      '18 or older'                            -> age at the offence ≥ 18
      'from' date                              -> offence on or after that date"""
    if d < CATEGORY_START:
        return None, ""
    for entry in sorted(row.get("category") or [], key=lambda e: e.get("category", 9)):
        cond = (entry.get("condition") or "").lower()
        if "not a protected worker" in cond:
            if protected == "yes":
                continue
        elif "protected worker" in cond and protected != "yes":
            continue
        if "18 or older" in cond and age_at_offence < 18:
            continue
        if entry.get("from") and d < entry["from"]:
            continue
        return entry.get("category"), entry.get("quote") or entry.get("condition") or ""
    return None, ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. Mandatory minimum non-parole periods (audit.mandatory_minimum)
# ─────────────────────────────────────────────────────────────────────────────
def find_minimum(row: dict, offence_id: str, protected: str) -> tuple[str | None, int | None, str]:
    """From the register's minimum_non_parole entries:
       ss 15A/15B: 4 years (gross violence), 5 years if the victim is a protected official;
       ss 16–18:   only 'if victim is protected worker'."""
    for entry in row.get("minimum_non_parole") or []:
        months = int(entry["value"]) * (12 if entry.get("unit") == "years" else 1)
        cond = (entry.get("condition_text") or "").lower()
        variant = entry.get("variant")
        if offence_id in config.GROSS_VIOLENCE_OFFENCES:
            if protected == "yes" and variant:
                vm = int(variant["value"]) * (12 if variant.get("unit") == "years" else 1)
                return "protected_official", vm, entry.get("text", "")
            return "gross_violence", months, entry.get("text", "")
        if "protected worker" in cond:
            if protected == "yes":
                return "protected_official", months, entry.get("text", "")
            continue
        return "other", months, entry.get("text", "")
    return None, None, ""


def accessory_exclusion(case: CaseInput, group: str | None) -> str:
    """Why a minimum does not apply to an accessory, for the minimum's own group
    (the two groups have different accessory rules):
      gross violence (VSM 9.1.2.2): any finding of guilt on the basis of
        directing, assisting or encouraging;
      protected officials (VSM 9.1.2.3): only if the offender also proves on the
        balance of probabilities that their involvement was minor."""
    if case.accessory_only != "yes" or not group:
        return ""
    if group == "gross_violence":
        return ("found guilty only on the basis of directing, assisting or encouraging: the gross-violence minimum "
                "does not apply (VSM 9.1.2.2).")
    if group == "protected_official" and case.minor_involvement_proved == "yes":
        return ("found guilty only as an accessory and minor involvement proved on the balance of probabilities: the "
                "protected-official minimum does not apply (VSM 9.1.2.3).")
    return ""


def make_reading(row, case: CaseInput, kind: str, label: str, protected: str, d: str,
                 category_protected: str | None = None, flags=None, cite=None, app_policy: bool = False) -> Reading:
    """A reading: category from `category_protected` (defaults to `protected`),
    minimum (and its accessory exclusion) from `protected`."""
    cat, cat_text = find_category(row, case.age_at_offence, category_protected or protected, d)
    group, mnpp, mnpp_text = find_minimum(row, case.offence_id, protected)
    excluded = accessory_exclusion(case, group) if mnpp else ""
    return Reading(kind=kind, label=label, category=cat, category_text=cat_text, protected=protected,
                   mm_group=group, mnpp_months=mnpp, mnpp_text=mnpp_text, mnpp_excluded=excluded,
                   flags=flags or [], cite=cite or [], app_policy=app_policy)


STATUS_ALONE = "if Category 1 turns on the victim's status alone (the register's wording, fn 3777)"
RELATED_FLAGS_GATE = "Other Manual flags on the rules used"      # the gate listing flags related to the rules used


def build_readings(row, case: CaseInput, d: str, d_end: str | None) -> tuple[Reading, list[Reading]]:
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
    victim, knew, scheme = case.victim_protected_worker, case.offender_knew_or_reckless, case.protected_scheme_confirmed
    status = case.protected_official                         # yes | no | unknown
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
    # An offence period that straddles 20 March 2017: the category may differ, and that point combines with
    # each point above, so each of their readings is also built at the end of the period.
    if d_end and d < CATEGORY_START <= d_end:
        later = "the later part of the offence period governs (on or after 20 March 2017)"
        alternatives.append(make_reading(row, case, "open", "if " + later, main.protected, d_end))
        alternatives += [make_reading(row, case, kind, f"{lab}, and {later}", prot, d_end, category_protected=cat_prot,
                                      flags=fl, cite=ct, app_policy=ap)
                         for kind, lab, prot, cat_prot, fl, ct, ap in specs]
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
    maximum = find_maximum(kb, row, d, d_end)
    status = case.protected_official                    # yes | no | unknown
    # App policy: an adverse fact not proved beyond reasonable doubt is not relied on (main reading "no").
    main, alternatives = build_readings(row, case, d, d_end)

    framework = Framework(
        offence_id=case.offence_id, offence_label=kb.offence_label(case.offence_id), offence_date=d,
        court=case.court, maximum=maximum, level_term_note=level_term_note(kb, maximum),
        category=main.category, category_text=main.category_text, protected_official=status,
        protected_assumed_no=(status == "unknown"), mm_group=main.mm_group, mnpp_months=main.mnpp_months,
        mnpp_text=main.mnpp_text, mnpp_excluded=main.mnpp_excluded, applied_protected=main.protected,
        alternatives=alternatives, dispositions_note=_dispositions_note(case, main),
    )
    framework.rule_items = _rule_items(framework)
    framework.pu_value = _penalty_unit_value(kb, d)
    framework.gates = gates(kb, facts, framework)
    framework.flags = sorted({f for g in framework.gates for f in g.flags} | {f for a in alternatives for f in a.flags})
    return framework


def _dispositions_note(case: CaseInput, main: Reading) -> str:
    """What the category and the minimum leave open, for the main reading."""
    gross = case.offence_id in config.GROSS_VIOLENCE_OFFENCES
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
        if main.mm_group == "protected_official" and not gross:
            parts.append("If a special reason is found, the court must impose imprisonment (without a CCO) or make a "
                         "mandatory treatment and monitoring order (mtmo), a residential treatment order or a court "
                         "secure treatment order (version 1 can propose only imprisonment or an mtmo). A therapeutic "
                         "order such as an mtmo is open only if the offender proves on the balance of probabilities "
                         "impaired mental functioning at the offence, causally linked to it, that substantially and "
                         "materially reduces culpability and was not caused substantially by self-induced intoxication, "
                         "and the court has received a psychiatrist's or registered psychologist's report (VSM 9.1.2.3). "
                         "Whether an mtmo may be combined with imprisonment is unresolved in the Manual "
                         "(flag.mtmo_combination_sentence).")
        elif gross and (main.category == 1 or main.protected == "yes"):
            parts.append("A special reason may lower the minimum non-parole period; whether it opens any other "
                         "disposition is unresolved in the Manual (flag."
                         + ("gross_violence_protected_special_reason" if main.protected == "yes"
                            else "category1_special_reason") + ").")
    if not main.category and not main.effective_mnpp:
        parts.append("No category or binding mandatory minimum: the full range of dispositions is open, subject to the "
                     "hierarchy of sanctions (imprisonment only as a last resort) and each order's own limits.")
    return " ".join(parts)


def _rule_items(fw: Framework) -> list[str]:
    ids = ["audit.maximum_applicable", "scale.fine_alternative"]
    cats = {fw.category} | {a.category for a in fw.alternatives}
    groups = {fw.mm_group} | {a.mm_group for a in fw.alternatives}
    if 1 in cats:
        ids += ["cat1.custodial_required"]
    if 2 in cats:
        ids += ["cat2.custodial_unless", "cat2.substantial_compelling_test"]
    if "gross_violence" in groups:
        ids += ["mmnpp.gross_violence_4y", "mm.special_reason"]
    if "protected_official" in groups:
        ids += ["mmnpp.protected_officials", "mm.special_reason"]
    if fw.protected_official != "no":
        ids += ["findings.burden"]
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
        return offence_id in linked
    meta = kb.meta.get(flag_id)
    return not (meta and kb._other_offence_chapter_only(meta))


# Flags about matters that cannot arise in a version-1 case (an adult natural person,
# one Victorian indictable causing-injury charge, the County or Supreme Court): children
# and the Children's Court, bodies corporate, continuing criminal enterprises, contempt,
# summary offences and the Magistrates' Court, Commonwealth offences, other offences'
# schemes (home invasion, supervision orders, drug offences), drug and alcohol treatment
# orders and court secure treatment orders (which version 1 cannot propose), and mixed
# pleas (version 1 has one charge). Matched on words in the flag id; widen version 1 →
# revisit this list.
OUT_OF_SCOPE_FLAG_WORDS = ("childrens_court", "cyfa", "serious_youth", "body_corporate", "cce_", "contempt",
                           "summary_", "mc_", "cth_", "commonwealth", "home_invasion", "supervision_order", "drug",
                           "dato_", "csto_", "mixed_pleas")


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
    if ("youth_detention" in flag_id or "yjc_" in flag_id) and case.age_at_sentence >= 21:
        return False                                               # youth justice centre orders: under 21 at sentence
    return True


def gates(kb: KnowledgeBase, facts: ConfirmedFacts, fw: Framework) -> list[CheckResult]:
    """The four gates (check: gate audit items), run before any reasoning."""
    case = facts.case
    out: list[CheckResult] = []
    row = kb.offence(case.offence_id)

    # ── audit.scope ──
    out.append(CheckResult("audit.scope", "Scope", "pass",
                           "Victorian offence, adult offender, County or Supreme Court: within version 1."))
    for w in facts.scope_warnings:
        out.append(CheckResult("audit.scope", "Scope — possible problem with the plea or the charge", "error",
                               f"{w} This app does not decide guilt: the estimate below assumes the plea or verdict "
                               "stands, and should not be relied on until this is resolved.",
                               cite=["findings.plea_acceptance"]))
    if case.serving_sentence_or_on_parole in ("yes", "unknown"):
        out.append(CheckResult("audit.scope", "Existing sentence or parole", "caution",
                               "The offender may be serving a sentence or on parole. Version 1 does not check "
                               "cumulation, a new single non-parole period or totality with an existing sentence "
                               "(outside version 1).", cite=["cum.presumption_cumulation", "npp.new_single_npp"],
                               app_policy=True))
    if case.age_at_sentence < 21:
        out.append(CheckResult("audit.scope", "Offender under 21 at sentence", "caution",
                               "A youth justice centre order (and, for protected-official offences, the youth justice "
                               "centre alternative to the minimum) may be open for an offender under 21 at sentence. "
                               "Version 1 cannot propose or check those orders (outside version 1)."
                               + (" The age at sentence is assumed (not stated): it was taken as the age at the "
                                  "offence." if getattr(case, "age_at_sentence_assumed", False) else ""),
                               cite=["order.youth_detention", "mmnpp.protected_officials"], app_policy=True))

    # ── audit.offence_date_and_historic_offending ──
    if fw.maximum.unresolved:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Offence date", "unresolved",
                               f"Maximum: {fw.maximum.unresolved}", cite=["audit.maximum_applicable"]))
    else:
        out.append(CheckResult("audit.offence_date_and_historic_offending", "Offence date", "pass",
                               f"The register's maximum for {fw.offence_date} is {fw.maximum.text} ({fw.maximum.period}). "
                               "No text-only historic penalty table covers this offence."))
    if fw.level_term_note:
        out.append(CheckResult("audit.level_term_mismatch", "Penalty level and term", "caution", fw.level_term_note))

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

    # ── protected officials: proof, knowledge and commencement ──
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
    # ── accessories: each minimum has its own rule (worked out for every reading) ──
    readings = fw.readings
    if case.accessory_only == "yes" and case.minor_involvement_proved != "yes" and \
            any(r.mm_group == "protected_official" and r.mnpp_months for r in readings):
        out.append(CheckResult("audit.mandatory_minimum", "Accessory — minor involvement not proved", "caution",
                               "The protected-official minimum does not apply to an accessory only if the offender proves "
                               "on the balance of probabilities that their involvement was minor; that is not shown, so "
                               "that minimum is applied wherever it applies.", cite=["mmnpp.protected_officials"]))
    # Minimums that bind on the estimate's own readings, and those that apply only if adverse facts are proved.
    binding = sorted({r.mm_group for r in readings if r.mm_group and r.mnpp_months and r.kind != "unproved"})
    if_proved = sorted({r.mm_group for r in readings if r.mm_group and r.mnpp_months and r.kind == "unproved"}
                       - set(binding))
    if case.accessory_only == "unknown" and (binding or if_proved):
        effect = {"gross_violence": "the gross-violence minimum would not apply",
                  "protected_official": "the protected-official minimum would not apply if the offender also proved "
                                        "that their involvement was minor"}
        name = {"gross_violence": "The gross-violence minimum", "protected_official": "The protected-official minimum"}
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
                               cite=[{"gross_violence": "mmnpp.gross_violence_4y"}.get(g, "mmnpp.protected_officials")
                                     for g in binding + if_proved]))

    # ── audit.manual_flags ──
    if (row.get("confidence") and row["confidence"] != "high") or row.get("notes"):
        out.append(CheckResult("audit.manual_flags", "Register row note", "caution",
                               f"The register marks this offence's row “{row.get('confidence')}” confidence. "
                               + " ".join(row.get("notes") or [])))
    shown = {f for r in out for f in r.flags}
    for flag in kb.flags_for_offence(case.offence_id):            # flags linked to the offence
        if flag in shown:
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
