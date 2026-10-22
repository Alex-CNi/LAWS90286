"""
core/coverage.py — WHICH PARTS OF AN OFFENCE'S STATUTORY SCHEME THE CODE CHECKS, AND WHICH QUESTIONS IT ASKS (no AI).

The knowledge base covers every offence in the Manual's register. The hard checks were written for chapter 25 first
and, in version 2.0, for every statutory scheme the register's rows carry (docs/ALL_OFFENCES_GUIDE.md, Part B), so
NOT_YET_CHECKED is empty. If a feature is added that the checks do not implement, add a detector and an entry: the
framework turns the list into a gate caution, “Not fully checked” (app policy), so an estimate is never presented as
fully checked when it is not; delete the entry when the feature is implemented and tested.

charge_questions() says which of the charge-specific questions (core/facts.py, CaseInput) a charge's statutory
scheme turns on: the form asks only those, and only those answers reach the AI calls and the report.
"""

from __future__ import annotations

from datetime import date

from core import config
from core.kb import SEXUAL_TABLES_TO, KnowledgeBase

# Dates the statutory schemes start from (the Manual's own dates).
CATEGORY_START = "2017-03-20"              # Category 1/2 apply to offences on or after this date (audit.category_1_2)
MANSLAUGHTER_MINIMUM_FROM = "2014-11-01"   # the manslaughter minimum (mmnpp.manslaughter_10y params.from_date)
HISTORIC_MARKED_TABLE = "24.1.6.2"         # the historic table whose provisions marked "#" are Category 1 (fn 3556)
SSO_EFFECTIVE = "2018-02-01"               # the standard sentence scheme: offences committed after this date (sso.scope)

# Relations of a maximum fixed by reference to another offence that the Manual cannot resolve (audit.maximum_applicable
# (4)): the user enters the maximum read from the legislation (app policy).
RELATIONS_USER_SUPPLIED = {"table_not_reproduced": "the s 321P table it refers to is not reproduced in the Manual "
                                                   "(flag.attempt_table_not_reproduced)",
                           "lower_penalty_in_other_enactment": "the lower penalty is in another enactment, which the "
                                                               "Manual does not give",
                           "law_of_place_capped_at_life": "it is the maximum in force in the place outside Victoria, "
                                                          "which the Manual does not give"}

# Mandatory minimums whose conditions turn on facts the user answers (guide step 9): "yes" to every fact applies the
# minimum on the main reading; any "no" means none; any "unknown" shows it as an "if proved" reading
# (core/framework.py, minimum_facts and find_minimum).
MANSLAUGHTER = "common-law-manslaughter"
DRIVING_PROTECTED = ("crimes-act-s-317ac-intentionally-exposing-an-emergency-worker-custodial-officer-or-a-youth",
                     "crimes-act-s-317ad-aggravated-intentionally-exposing-an-emergency-worker-custodial-officer")
MINIMUM_FACTS = {
    MANSLAUGHTER: "the prosecution gave notice that it would apply for the minimum, and gross violence or a single "
                  "punch or strike was proved beyond reasonable doubt",
    DRIVING_PROTECTED[0]: "the protected official was injured in the commission of the offence",
    DRIVING_PROTECTED[1]: "the protected official was injured in the commission of the offence",
}
# The Manual's own category point for particular offences (each flag's Handling): an "open" reading. For ss 317AC
# and 317AD the user can confirm Category 1 (audit.category_1_2 (a); category1_confirmed), which settles it.
OPEN_CATEGORY = {
    "crimes-act-s-6b-suicide-pact-manslaughter": (2, "flag.suicide_pact_category2",
                                                   "if suicide pact manslaughter is a Category 2 offence, as manslaughter "
                                                   "is (22.1.1)"),
    DRIVING_PROTECTED[0]: (1, "flag.driving_protected_category1", "if s 317AC is a Category 1 offence (footnote 2005)"),
    DRIVING_PROTECTED[1]: (1, "flag.driving_protected_category1", "if s 317AD is a Category 1 offence (footnote 2005)"),
}

# feature -> what the caution says is not checked (and the guide step that adds it)
NOT_YET_CHECKED: dict[str, str] = {}      # every feature of the register's rows is checked (guide steps 9–17)

# Category clauses the framework tests from the other answers (core/framework.py, find_category). Any other clause is
# asked as its own question (category_condition_met).
KNOWN_CATEGORY_CLAUSES = ("18 or older", "18 or more", "offence on or after 20 march 2017",
                          "victim was a protected worker", "not a protected worker")


def category_clauses(condition: str) -> list[str]:
    """A register category condition split into its clauses ("…; …; and …")."""
    return [c.strip() for c in (condition or "").lower().replace(" and ", "; ").split(";") if c.strip()]


def untested_clauses(condition: str) -> list[str]:
    """The clauses of a category condition that the other answers do not settle (e.g. armed robbery's "the offender
    has a firearm, or a victim suffers injury as a direct result, or the offence was committed in company")."""
    return [c for c in category_clauses(condition) if not any(k in c for k in KNOWN_CATEGORY_CLAUSES)]


def asked_category_clause(row: dict) -> tuple[int, str] | None:
    """(category, clause) for a register category entry whose clause is asked (category_condition_met), else None."""
    for entry in row.get("category") or []:
        unknown = untested_clauses(entry.get("condition") or "")
        if unknown:
            return entry.get("category"), "; ".join(unknown)
    return None


def contempt_kind(row: dict) -> str:
    """The rows of the Manual's contempt table (VSM 29.1.3): "higher_court" (contempt of the County Court or Supreme
    Court), "magistrates" (the Magistrates' Court Act 1989 provisions), "other_body" (the contempt provision of a
    tribunal's, commission's or other body's Act), "other" (an offence in the table that is not a contempt: sending
    false process); "" for every other row."""
    if not str(row.get("section") or "").startswith("29.1.3"):
        return ""
    name, provision = row.get("offence") or "", row.get("provision") or ""
    if "Magistrates" in provision:
        return "magistrates"
    if "County Court / Supreme Court" in name:
        return "higher_court"
    if name.lower().startswith("contempt"):
        return "other_body"
    return "other"


def is_school_condition(entry: dict) -> bool:
    """A conditional maximum that applies "within 500 metres of a school" (ss 71AC, 71B: flag.school_500m_condition)."""
    return "school" in (entry.get("condition") or entry.get("text") or "").lower()


def _iso(d) -> str | None:
    if d is None:
        return None
    return d.isoformat() if isinstance(d, date) else str(d)


def historic_marked_applies(kb: KnowledgeBase, offence_id: str, start, end=None) -> bool:
    """Offending the Manual's table at VSM 24.1.6.2 governs, on a date within the category scheme (20 March – 30 June
    2017): the category turns on whether the provision charged is marked "#" there (cat1.historic_sexual_provisions)."""
    s, e = _iso(start), _iso(end)
    if not s or offence_id not in kb.offences:
        return False
    hist = kb.historic_tables(offence_id, s, e)
    if not hist or hist[0] != "table" or HISTORIC_MARKED_TABLE not in hist[1]:
        return False
    return any(CATEGORY_START <= x <= SEXUAL_TABLES_TO for x in (s, e) if x)


# The charge-specific questions about a natural person's sentence (a minimum, a category, a standard sentence, a
# licence or alcohol exclusion order): not asked for a body corporate, for which none of those applies.
PERSON_QUESTIONS = {"manslaughter_circumstance", "prosecution_notice", "official_injured", "category1_confirmed",
                    "category_condition_met", "historic_provision_marked", "sso_variant_met", "motor_vehicle_stolen",
                    "previous_s61_3_offence", "aeo_relevant_offence"}


def charge_questions(kb: KnowledgeBase, offence_id: str | None, start=None, end=None,
                     body_corporate: bool = False) -> list[str]:
    """The charge-specific questions (CaseInput fields) this charge's statutory scheme turns on, in the order the form
    asks them. A date not yet entered does not hide a question. For a body corporate, only those that bear on its
    fine (a conditional maximum, contempt)."""
    if body_corporate:
        return [f for f in charge_questions(kb, offence_id, start, end) if f not in PERSON_QUESTIONS]
    if not offence_id or offence_id not in kb.offences:
        return []
    row = kb.offence(offence_id)
    dates = [x for x in (_iso(start), _iso(end)) if x]
    latest = max(dates) if dates else None
    out: list[str] = []
    if offence_id == MANSLAUGHTER and (latest is None or latest >= MANSLAUGHTER_MINIMUM_FROM):
        out += ["manslaughter_circumstance", "prosecution_notice"]
    if offence_id in DRIVING_PROTECTED:
        out += ["official_injured", "category1_confirmed"]
    if asked_category_clause(row) and (latest is None or latest >= CATEGORY_START):
        out.append("category_condition_met")
    if dates and historic_marked_applies(kb, offence_id, dates[0], dates[1] if len(dates) > 1 else None):
        out.append("historic_provision_marked")
    conditional = row.get("conditional_maxima") or []
    if conditional:
        out.append("conditional_condition_met")
        if any(is_school_condition(c) for c in conditional):
            out.append("school_condition_confirmed")
    if contempt_kind(row) == "other_body":
        out.append("contempt_by_higher_court")
    if row.get("standard_sentence_variants") and (latest is None or latest >= SSO_EFFECTIVE):
        out.append("sso_variant_met")
    group = licence_group(kb, offence_id)
    if group in ("motor_vehicle_theft", "attempted_motor_vehicle_theft"):
        out.append("motor_vehicle_stolen")
    if group == "rsa_s61_3":
        out.append("previous_s61_3_offence")
    return out


# The charges VSM 20.1.1 names as "relevant offences" for an alcohol exclusion order: murder or manslaughter, and the
# intentionally, negligently or recklessly causing injury offences. Any other charge: the user confirms
# (CaseInput.aeo_relevant_offence; anc.alcohol_exclusion_order — app policy).
AEO_PLAINLY_RELEVANT = set(config.CAUSING_INJURY_OFFENCES) | {
    "crimes-act-s-24-negligently-causing-serious-injury", "common-law-common-law-murder", "common-law-manslaughter"}


def licence_map(kb: KnowledgeBase) -> dict[str, dict]:
    """The Manual's mandatory licence-order groups (order.licence_disqualification → licence.serious_mv_offences,
    licence.other_mandatory): group -> {minimum, default, condition, offence_ids, rule}."""
    out: dict[str, dict] = {}
    for rule in (kb.get("order.licence_disqualification") or {}).get("rules") or []:
        for name, group in (rule.get("map") or {}).items():
            out[name] = dict(group, rule=rule.get("id"))
    return out


def licence_group(kb: KnowledgeBase, offence_id: str) -> str | None:
    """The licence-order group a charge belongs to ("rsa_s61_3" for RSA s 61(3): audit.mandatory_ancillary_orders),
    or None."""
    row = kb.offences.get(offence_id) or {}
    if (row.get("provision") or "").replace(" ", "") == "RSAs61(3)":
        return "rsa_s61_3"
    return next((name for name, group in licence_map(kb).items() if offence_id in (group.get("offence_ids") or [])),
                None)


def relative_entry(row: dict) -> dict | None:
    """The register entry of a maximum fixed by reference to another offence (relative_to_primary), or None."""
    return next((m for m in row.get("maxima") or [] if m.get("relative_to_primary")), None)


def unchecked_features(kb: KnowledgeBase, offence_id: str) -> list[str]:
    """The features of this offence's row that the hard checks do not implement yet (none since version 2.0: every
    feature of the register is checked; a detector goes here if a new one is found)."""
    out: list[str] = []
    return [f for f in out if f in NOT_YET_CHECKED]


def coverage_text(kb: KnowledgeBase, offence_id: str) -> str:
    """One sentence for the gate caution, or "" when every feature of the row is checked."""
    missing = unchecked_features(kb, offence_id)
    if not missing:
        return ""
    return ("The app's hard checks do not yet cover " + "; ".join(NOT_YET_CHECKED[f] for f in missing)
            + ". Treat those parts of the estimate as unchecked (app policy).")
