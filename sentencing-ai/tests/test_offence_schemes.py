"""The statutory schemes outside chapter 25 (docs/ALL_OFFENCES_GUIDE.md, Part B), one family at a time: each rule is
tested limb by limb with hand-made proposals (setup() and proposal() from tests/test_checks.py)."""

from datetime import date

import pytest

from core import config, schemas
from core.checks import run_hard_checks
from core.coverage import NOT_YET_CHECKED, charge_questions, contempt_kind, unchecked_features
from core.facts import (ADVERSE_VALUES, CaseInput, ConfirmedFacts, case_details_text, charge_question, is_adverse,
                        suggestions_for_unknowns, validate)
from core.framework import build_framework
from tests.fake_openai import FakeOpenAI
from tests.test_checks import cco, proposal, setup


def by_name(kb, name):
    return next(oid for oid in config.IN_SCOPE_OFFENCES if kb.offence(oid)["offence"] == name)


def by_id_part(part):
    return next(oid for oid in config.IN_SCOPE_OFFENCES if part in oid)


def fw_for(kb, oid, d=date(2024, 5, 1), end=None, **kw):
    case = CaseInput(offence_id=oid, offence_date=d, offence_date_end=end, age_at_offence=30, age_at_sentence=31,
                     narrative="x" * 100, **kw)
    return build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))


def outcomes(results, check_id):
    return [r.outcome for r in results if r.check_id == check_id]


def errors(results):
    return [r for r in results if r.outcome == "error"]


BAND_CCO = schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=18, cco_high_months=24, main_uncertainties=[])
SPECIAL = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="psychiatric report",
                                stated_in_writing=True)
MANSLAUGHTER_FACTS = dict(manslaughter_circumstance="single_punch", prosecution_notice="yes")
S317AC = "crimes-act-s-317ac-intentionally-exposing-an-emergency-worker-custodial-officer-or-a-youth"


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — mandatory minimums outside chapter 25: the facts are asked (VSM 9.1.2)
# ─────────────────────────────────────────────────────────────────────────────
def test_manslaughter_minimum_applies_when_its_facts_are_proved(kb):
    """mmnpp.manslaughter_10y: from 1 November 2014, notice + gross violence or a single punch proved → NPP ≥ 10 years."""
    oid = by_name(kb, "Manslaughter")
    facts, fw = setup(kb, oid, offence_date=date(2015, 6, 1), **MANSLAUGHTER_FACTS)
    assert fw.mm_group == "manslaughter" and fw.mnpp_months == 120 and not fw.alternatives
    assert not any(g.title.startswith("Mandatory minimum —") for g in fw.gates)
    res = run_hard_checks(kb, facts, fw, proposal(term_months=144, npp_months=108))     # NPP 9 years
    mm = [r for r in res if r.check_id == "audit.mandatory_minimum"]
    assert any(r.outcome == "error" and r.title == "Below the minimum non-parole period" for r in mm)
    assert all("mmnpp.manslaughter_10y" in r.cite for r in mm if r.outcome == "error")
    ok = run_hard_checks(kb, facts, fw, proposal(term_months=180, npp_months=126))
    assert "pass" in outcomes(ok, "audit.mandatory_minimum") and not errors(ok)
    gross = setup(kb, oid, offence_date=date(2015, 6, 1), manslaughter_circumstance="gross_violence",
                  prosecution_notice="yes")[1]
    assert gross.mnpp_months == 120


def test_manslaughter_minimum_needs_notice_and_the_date(kb):
    oid = by_name(kb, "Manslaughter")
    no_notice = fw_for(kb, oid, date(2015, 6, 1), manslaughter_circumstance="single_punch", prosecution_notice="no")
    assert no_notice.mnpp_months is None and not [a for a in no_notice.alternatives if a.mnpp_months]
    assert not no_notice.minimums_pending
    neither = fw_for(kb, oid, date(2015, 6, 1), manslaughter_circumstance="neither", prosecution_notice="unknown")
    assert neither.mnpp_months is None and not [a for a in neither.alternatives if a.mnpp_months]
    early = fw_for(kb, oid, date(2013, 6, 1), **MANSLAUGHTER_FACTS)
    assert early.mnpp_months is None and not [a for a in early.alternatives if a.mnpp_months]
    assert charge_questions(kb, oid, date(2013, 6, 1)) == []
    assert charge_questions(kb, oid, date(2015, 6, 1)) == ["manslaughter_circumstance", "prosecution_notice"]


def test_manslaughter_facts_not_established_give_an_if_proved_reading(kb):
    oid = by_name(kb, "Manslaughter")
    fw = fw_for(kb, oid, date(2015, 6, 1), manslaughter_circumstance="unknown", prosecution_notice="yes")
    assert fw.mnpp_months is None and fw.minimums_pending
    unproved = [a for a in fw.alternatives if a.kind == "unproved" and a.mnpp_months == 120]
    assert unproved and unproved[0].label.startswith("if gross violence or a single punch")
    gate = next(g for g in fw.gates if g.title == "Mandatory minimum — facts not established")
    assert "not established that gross violence or a single punch or strike was proved" in gate.message
    facts, fw = setup(kb, oid, offence_date=date(2015, 6, 1), prosecution_notice="unknown",
                      manslaughter_circumstance="gross_violence")
    res = run_hard_checks(kb, facts, fw, proposal(term_months=144, npp_months=108))
    mm = [r for r in res if r.check_id == "audit.mandatory_minimum"]
    assert not any(r.outcome == "error" for r in mm)
    assert any(r.title.startswith("If proved") and r.outcome == "caution" for r in mm)
    assert "intake.prosecution_notices" in next(g for g in fw.gates
                                                if g.title == "Mandatory minimum — facts not established").cite


def test_manslaughter_accessory_exclusion_is_for_a_single_punch_only(kb):
    oid = by_name(kb, "Manslaughter")
    punch = fw_for(kb, oid, date(2015, 6, 1), accessory_only="yes", **MANSLAUGHTER_FACTS)
    assert punch.mnpp_months == 120 and "single punch" in punch.mnpp_excluded
    facts, fw = setup(kb, oid, offence_date=date(2015, 6, 1), accessory_only="yes", **MANSLAUGHTER_FACTS)
    assert not errors([r for r in run_hard_checks(kb, facts, fw, proposal(term_months=144, npp_months=108))
                       if r.check_id == "audit.mandatory_minimum"])
    gross = fw_for(kb, oid, date(2015, 6, 1), accessory_only="yes", manslaughter_circumstance="gross_violence",
                   prosecution_notice="yes")
    assert gross.mnpp_months == 120 and not gross.mnpp_excluded
    unknown = fw_for(kb, oid, date(2015, 6, 1), accessory_only="yes", prosecution_notice="yes")
    assert any(g.title == "Accessory — single punch or gross violence?" for g in unknown.gates)
    basis = fw_for(kb, oid, date(2015, 6, 1), accessory_only="unknown", manslaughter_circumstance="gross_violence",
                   prosecution_notice="yes")
    assert not any(g.title == "Accessory basis unknown" for g in basis.gates)       # gross violence: no exclusion
    basis = fw_for(kb, oid, date(2015, 6, 1), accessory_only="unknown", **MANSLAUGHTER_FACTS)
    gate = next(g for g in basis.gates if g.title == "Accessory basis unknown")
    assert "it rests on a single punch or strike" in gate.message


def test_manslaughter_period_straddling_the_commencement(kb):
    oid = by_name(kb, "Manslaughter")
    fw = fw_for(kb, oid, date(2014, 6, 1), end=date(2015, 1, 1), **MANSLAUGHTER_FACTS)
    assert fw.mnpp_months is None
    later = [a for a in fw.alternatives if a.kind == "open" and a.mnpp_months == 120]
    assert later and "1 November 2014" in later[0].label
    case = CaseInput(offence_id=oid, offence_date=date(2014, 6, 1), offence_date_end=date(2015, 1, 1),
                     age_at_offence=30, age_at_sentence=31, narrative="x" * 100, **MANSLAUGHTER_FACTS)
    res = run_hard_checks(kb, ConfirmedFacts(case=case, summary="", facts=[]), fw, proposal(term_months=144,
                                                                                         npp_months=108))
    mm = [r for r in res if r.check_id == "audit.mandatory_minimum" and r.outcome != "info"]
    assert mm and not any(r.outcome == "error" for r in mm) and any(r.outcome == "unresolved" for r in mm)


def test_driving_against_a_protected_official_minimum(kb):
    """mmnpp.driving_protected_2y: ss 317AC/317AD with the official injured → NPP ≥ 2 years."""
    facts, fw = setup(kb, S317AC, official_injured="yes", category1_confirmed="no")
    assert fw.mm_group == "driving_protected" and fw.mnpp_months == 24
    res = run_hard_checks(kb, facts, fw, proposal(term_months=30, npp_months=18))
    assert any(r.outcome == "error" and r.title == "Below the minimum non-parole period" for r in res)
    not_injured = fw_for(kb, S317AC, official_injured="no")
    assert not_injured.mnpp_months is None and not [a for a in not_injured.alternatives if a.mnpp_months]
    unknown = fw_for(kb, S317AC)
    assert any(a.kind == "unproved" and a.mnpp_months == 24 for a in unknown.alternatives)
    assert any(g.title == "Mandatory minimum — facts not established" for g in unknown.gates)


def test_driving_against_a_protected_official_special_reason_routes(kb):
    """VSM 9.1.2.4: with a special reason, imprisonment (without a CCO) or an MTMO (on the 9.1.2.3 conditions);
    a CCO is an error; imprisonment combined with an MTMO is unresolved (flag.mtmo_combination_sentence)."""
    mtmo_conditions = [
        schemas.CCOCondition(kind="judicial_monitoring", details="", hours=None, completion_months=None,
                             hours_per_day=None, duration_months=None, monitors=None),
        schemas.CCOCondition(kind="treatment_and_rehabilitation", details="", hours=None, completion_months=None,
                             hours_per_day=None, duration_months=None, monitors=None)]
    band = schemas.Band(disposition_types=["mtmo"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=18, cco_high_months=24, main_uncertainties=[])
    facts, fw = setup(kb, S317AC, official_injured="yes", category1_confirmed="yes", mental_impairment_evidence="yes")
    assert fw.category == 1
    mtmo = proposal(disposition_type="mtmo", term_months=None, npp_months=None, cco=cco(24, mtmo_conditions),
                    special_reason=SPECIAL, band=band)
    res = run_hard_checks(kb, facts, fw, mtmo)
    assert not errors([r for r in res if r.check_id in ("audit.category_1_2", "audit.mandatory_minimum")])
    plain = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24), special_reason=SPECIAL,
                     band=BAND_CCO)
    res = run_hard_checks(kb, facts, fw, plain)
    assert any(r.outcome == "error" and r.title == "Special reason — disposition not permitted" for r in res)
    combined = proposal(disposition_type="mtmo", term_months=6, npp_months=None, cco=cco(24, mtmo_conditions),
                        special_reason=SPECIAL, band=band)
    res = run_hard_checks(kb, facts, fw, combined)
    assert any("flag.mtmo_combination_sentence" in r.flags and r.outcome == "caution" for r in res)


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 — category conditions (audit.category_1_2)
# ─────────────────────────────────────────────────────────────────────────────
def test_armed_robbery_category_condition_is_asked(kb):
    oid = by_name(kb, "Armed robbery")
    assert charge_questions(kb, oid, date(2024, 5, 1)) == ["category_condition_met"]
    assert charge_questions(kb, oid, date(2016, 5, 1)) == []                   # before the category scheme
    question = charge_question(kb, "category_condition_met", oid)[0]
    assert "firearm" in question and "in company" in question
    facts, fw = setup(kb, oid, category_condition_met="yes")
    assert fw.category == 2 and not fw.category_unparsed and not fw.alternatives
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), band=BAND_CCO))
    assert "error" in outcomes(res, "audit.category_1_2")
    no = fw_for(kb, oid, category_condition_met="no")
    assert no.category is None and not no.category_unparsed and not no.alternatives
    assert not any(g.title == "Category condition not established" for g in no.gates)
    unknown = fw_for(kb, oid)
    assert any(a.kind == "unproved" and a.category == 2 for a in unknown.alternatives)


def test_driving_offences_category1_confirmation(kb):
    """flag.driving_protected_category1: the user confirms Category 1 → the main reading; "no" → the table."""
    yes = fw_for(kb, S317AC, category1_confirmed="yes")
    assert yes.category == 1 and not [a for a in yes.alternatives if a.kind == "open"]
    assert any(g.title == "Category 1 — confirmed by you" and "flag.driving_protected_category1" in g.flags
               for g in yes.gates)
    assert "you confirmed it" in yes.text() and "register: “Category 1" not in yes.text()
    facts, fw = setup(kb, S317AC, category1_confirmed="yes", official_injured="no")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), band=BAND_CCO))
    assert "error" in outcomes(res, "audit.category_1_2")                        # no route without the minimum
    no = fw_for(kb, S317AC, category1_confirmed="no")
    assert no.category is None and not [a for a in no.alternatives if a.kind == "open"]
    unknown = fw_for(kb, S317AC)
    assert any(a.kind == "open" and a.category == 1 for a in unknown.alternatives)
    assert any(g.title == "Category 1 — confirm it" for g in unknown.gates)
    facts, fw = setup(kb, S317AC, official_injured="no")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), band=BAND_CCO))
    cat = [r for r in res if r.check_id == "audit.category_1_2" and r.outcome != "info"]
    assert cat and not any(r.outcome == "error" for r in cat)                    # unresolved until confirmed


def test_home_invasion_is_category_2_as_the_register_says(kb):
    """flag.home_invasion_mandatory_scheme_fn4044: the register's Category 2 status for s 77A governs."""
    fw = fw_for(kb, by_name(kb, "Home invasion"))
    assert fw.category == 2 and fw.mnpp_months is None
    assert "flag.home_invasion_mandatory_scheme_fn4044" in fw.flags


def test_historic_child_sexual_offence_marked_provisions(kb):
    """cat1.historic_sexual_provisions: offending 20 March – 30 June 2017 under table 24.1.6.2 — the provision
    marked “#” is Category 1 once the user confirms it; not marked → no category; unknown → an open reading."""
    oid = by_name(kb, "Sexual penetration of a child under the age of 12")
    d = date(2017, 5, 1)
    assert "historic_provision_marked" in charge_questions(kb, oid, d)
    assert "historic_provision_marked" not in charge_questions(kb, oid, date(2016, 5, 1))
    assert "historic_provision_marked" not in charge_questions(kb, by_name(kb, "Rape"), d)    # adults: 24.1.6.1
    yes = fw_for(kb, oid, d, historic_maximum_months=300, historic_provision_marked="yes")
    assert yes.category == 1 and not [a for a in yes.alternatives if a.kind == "open" and a.category]
    assert any(g.title == "Historic provision — Category 1, as you confirmed" for g in yes.gates)
    no = fw_for(kb, oid, d, historic_maximum_months=300, historic_provision_marked="no")
    assert no.category is None and not [a for a in no.alternatives if a.category]
    unknown = fw_for(kb, oid, d, historic_maximum_months=300)
    assert unknown.category is None
    assert any(a.kind == "open" and a.category == 1 and "“#”" in a.label for a in unknown.alternatives)
    rape = fw_for(kb, by_name(kb, "Rape"), d, historic_maximum_months=300)       # no "#" marks: still open
    assert rape.category is None and any(a.kind == "open" and a.category == 1 for a in rape.alternatives)


# ─────────────────────────────────────────────────────────────────────────────
# Step 15 — conditional maxima (audit.maximum_applicable (3))
# ─────────────────────────────────────────────────────────────────────────────
def test_school_conditional_maximum(kb):
    """ss 71AC, 71B: 20 years within 500 metres of a school, applied only when the user confirms the statutory
    condition (flag.school_500m_condition)."""
    oid = by_name(kb, "Trafficking")
    assert charge_questions(kb, oid) == ["conditional_condition_met", "school_condition_confirmed"]
    facts, fw = setup(kb, oid)
    assert fw.maximum.months == 180 and fw.maximum.ceiling_months == 180
    assert any("would be “Level 3 – 20 years" in g.message for g in fw.gates)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130)),
                               "audit.maximum_applicable")
    facts, fw = setup(kb, oid, conditional_condition_met="yes", school_condition_confirmed="yes")
    assert fw.maximum.months == 240 and fw.maximum.level == 3 and fw.maximum.conditional_applied
    assert any("flag.school_500m_condition" in g.flags for g in fw.gates)
    res = run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130))
    assert outcomes(res, "audit.maximum_applicable") == ["pass"]
    facts, fw = setup(kb, oid, conditional_condition_met="yes")                # condition not confirmed
    assert fw.maximum.months == 180 and fw.maximum.ceiling_months == 240
    res = run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130))
    band = [r for r in res if r.check_id == "audit.maximum_applicable"]
    assert [r.outcome for r in band] == ["unresolved"] and "flag.school_500m_condition" in band[0].flags
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=250, npp_months=160)),
                               "audit.maximum_applicable")
    # "No" to the statutory condition (e.g. not to a child): the flag's Handling keeps the 20-year maximum unresolved
    facts, fw = setup(kb, oid, conditional_condition_met="yes", school_condition_confirmed="no")
    assert fw.maximum.months == 180 and fw.maximum.ceiling_months == 240
    assert "you answered is not met" in fw.maximum.conditional_status
    assert outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130)),
                    "audit.maximum_applicable") == ["unresolved"]
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=250, npp_months=160)),
                               "audit.maximum_applicable")
    no = fw_for(kb, oid, conditional_condition_met="no")
    assert no.maximum.months == 180 and not any("would be" in g.message for g in no.gates)
    assert "not proved" in no.maximum.describe()
    supply = fw_for(kb, by_name(kb, "Supplying to a child for sale or use"), conditional_condition_met="yes",
                    school_condition_confirmed="yes")
    assert supply.maximum.months == 240


def test_criminal_organisation_and_face_covering_maxima(kb):
    oid = by_name(kb, "Trafficking commercial quantity")                       # s 71AA
    org = fw_for(kb, oid, conditional_condition_met="yes")
    assert org.maximum.life and org.maximum.level == 1 and org.maximum.fine_penalty_units == 5000
    assert org.maximum.fine_source == "printed" and "criminal organisation" in org.maximum.describe()
    assert fw_for(kb, oid).maximum.months == 300                                # Level 2 – 25 years
    facts, fw = setup(kb, oid, conditional_condition_met="yes")
    band = schemas.Band(disposition_types=["imprisonment"], term_low_months=120, term_high_months=180,
                        npp_low_months=80, npp_high_months=120, cco_low_months=None, cco_high_months=None,
                        main_uncertainties=[])
    res = run_hard_checks(kb, facts, fw, proposal(term_months=150, npp_months=100, fine_penalty_units=1000, band=band))
    assert any("flag.level1_fine_dpcsa" in r.flags for r in res if r.check_id == "audit.fine_rules")
    affray = by_name(kb, "Affray")
    assert fw_for(kb, affray).maximum.months == 60
    assert fw_for(kb, affray, conditional_condition_met="yes").maximum.months == 84
    facts, fw = setup(kb, affray)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=70, npp_months=46)),
                               "audit.maximum_applicable")
    facts, fw = setup(kb, affray, conditional_condition_met="yes")
    assert "error" not in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=70, npp_months=46)),
                                   "audit.maximum_applicable")
    assert fw_for(kb, by_name(kb, "Violent disorder"), conditional_condition_met="yes").maximum.months == 180
    assert "face covering" in charge_question(kb, "conditional_condition_met", affray)[0]
    assert "criminal organisation" in charge_question(kb, "conditional_condition_met", oid)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Step 17 — contempt (the 29.1.3 rows)
# ─────────────────────────────────────────────────────────────────────────────
def contempt_rows(kb, kind):
    return [o for o in config.IN_SCOPE_OFFENCES if contempt_kind(kb.offence(o)) == kind]


def test_contempt_rows_by_kind(kb):
    assert len(contempt_rows(kb, "higher_court")) == 2 and len(contempt_rows(kb, "magistrates")) == 2
    assert len(contempt_rows(kb, "other_body")) == 11 and len(contempt_rows(kb, "other")) == 1


def test_magistrates_and_other_body_contempt_rows(kb):
    for oid in contempt_rows(kb, "magistrates"):
        case = CaseInput(offence_id=oid, offence_date=date(2024, 5, 1), age_at_offence=30, age_at_sentence=31,
                         narrative="x" * 100)
        assert any("Magistrates' Court Act 1989's contempt provision" in p for p in validate(case, kb))
    ibac = by_name(kb, "Contempt of the IBAC")
    assert charge_questions(kb, ibac) == ["contempt_by_higher_court"]
    case = CaseInput(offence_id=ibac, offence_date=date(2024, 5, 1), age_at_offence=30, age_at_sentence=31,
                     narrative="x" * 100, contempt_by_higher_court="no")
    assert any("tribunal or body itself" in p for p in validate(case, kb))
    case.contempt_by_higher_court = "unknown"
    assert not validate(case, kb)
    assert any(g.title == "Contempt — which body is dealing with it?" for g in fw_for(kb, ibac).gates)
    assert not any(g.title == "Contempt — which body is dealing with it?"
                   for g in fw_for(kb, ibac, contempt_by_higher_court="yes").gates)


def test_contempt_limits_are_stated_and_the_row_is_checked(kb):
    oid = by_name(kb, "Contempt of court (County Court / Supreme Court)")
    fw = fw_for(kb, oid)
    assert not unchecked_features(kb, oid) and not any(g.title == "Not fully checked" for g in fw.gates)
    gate = next(g for g in fw.gates if g.title == "Contempt — limits of the estimate")
    assert "flag.contempt_sentencing_act_and_orders" in gate.flags
    assert "flag.contempt_gravity_culpability_29_2_2_2" in gate.flags
    assert "suspended sentence" in gate.message and "sequestration of assets" in gate.message
    assert "flag.contempt_gravity_culpability_29_2_2_2" in fw.flags
    facts, fw = setup(kb, oid, cco_consent="no")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), band=BAND_CCO))
    assert not errors(res) and any("flag.contempt_sentencing_act_and_orders" in r.flags for r in res)


# ─────────────────────────────────────────────────────────────────────────────
# The charge-specific questions: asked, shown to the AI and suggested only for their own charges
# ─────────────────────────────────────────────────────────────────────────────
def test_charge_questions_reach_only_their_own_charges(kb):
    assert charge_questions(kb, config.CAUSING_INJURY_OFFENCES[3]) == []
    assert charge_questions(kb, S317AC) == ["official_injured", "category1_confirmed"]
    case = CaseInput(offence_id=by_name(kb, "Manslaughter"), offence_date=date(2020, 5, 1), narrative="x" * 100,
                     manslaughter_circumstance="single_punch", prosecution_notice="yes")
    text = case_details_text(case, kb)
    assert "single punch or strike? A single punch or strike (proved beyond reasonable doubt)" in text
    assert "give notice that it would apply for the 10-year minimum non-parole period? yes" in text
    theft = CaseInput(offence_id=by_name(kb, "Theft"), offence_date=date(2020, 5, 1), narrative="x" * 100,
                      manslaughter_circumstance="single_punch", official_injured="yes")
    assert "single punch" not in case_details_text(theft, kb) and "injured" not in case_details_text(theft, kb)
    school = CaseInput(offence_id=by_name(kb, "Trafficking"), offence_date=date(2020, 5, 1), narrative="x" * 100)
    assert "statutory condition" not in case_details_text(school, kb)            # asked once within 500 m
    school.conditional_condition_met = "yes"
    assert "statutory condition for the 20-year maximum is met? unknown" in case_details_text(school, kb)


def test_charge_specific_answers_count_against_the_offender(kb):
    for name in ("manslaughter_circumstance", "prosecution_notice", "official_injured", "category1_confirmed",
                 "category_condition_met", "historic_provision_marked", "conditional_condition_met",
                 "school_condition_confirmed"):
        assert name in ADVERSE_VALUES and getattr(CaseInput(), name) == "unknown"
    assert is_adverse("manslaughter_circumstance", "single_punch") and not is_adverse("manslaughter_circumstance",
                                                                                      "neither")


def test_suggestions_are_made_only_for_the_charges_questions(kb):
    extraction = FakeOpenAI().answer_FactsExtraction("## Case narrative\nHe killed the man with a single punch to "
                                                     "the head outside the hotel at night.", {})
    assert extraction.suggested.manslaughter_circumstance == "single_punch"
    m = CaseInput(offence_id=by_name(kb, "Manslaughter"), offence_date=date(2020, 5, 1), narrative="x" * 100)
    assert suggestions_for_unknowns(m, extraction, kb).get("manslaughter_circumstance") == "single_punch"
    s17 = CaseInput(offence_id=config.CAUSING_INJURY_OFFENCES[3], offence_date=date(2020, 5, 1), narrative="x" * 100)
    assert "manslaughter_circumstance" not in suggestions_for_unknowns(s17, extraction, kb)


def test_coverage_after_batch_1(kb):
    """Steps 9, 10, 15 and 17 are implemented: their features are no longer "Not fully checked"."""
    for feature in ("minimum_other_group", "category_condition", "conditional_maximum", "contempt"):
        assert feature not in NOT_YET_CHECKED
    full = [o for o in config.IN_SCOPE_OFFENCES if not unchecked_features(kb, o)]
    assert len(full) >= 281


@pytest.mark.parametrize("field_name", ["manslaughter_circumstance", "prosecution_notice", "official_injured",
                                        "category1_confirmed", "category_condition_met", "historic_provision_marked",
                                        "conditional_condition_met", "school_condition_confirmed",
                                        "contempt_by_higher_court"])
def test_every_charge_question_has_wording(kb, field_name):
    oid = {"manslaughter_circumstance": "common-law-manslaughter", "prosecution_notice": "common-law-manslaughter",
           "official_injured": S317AC, "category1_confirmed": S317AC,
           "category_condition_met": by_name(kb, "Armed robbery"),
           "historic_provision_marked": by_name(kb, "Sexual penetration of a child under the age of 12"),
           "conditional_condition_met": by_name(kb, "Trafficking"),
           "school_condition_confirmed": by_name(kb, "Trafficking"),
           "contempt_by_higher_court": by_name(kb, "Contempt of the IBAC")}[field_name]
    question, help_text = charge_question(kb, field_name, oid)
    assert question.endswith("?") and len(help_text) > 60


def test_register_category_quotes_never_read_as_a_confirmation(kb):
    """Framework.text() shows a category the user confirmed without “register:” (its text starts “Category ”): no
    register quote starts so."""
    for row in kb.offences.values():
        for entry in row.get("category") or []:
            assert not (entry.get("quote") or entry.get("condition") or "").startswith("Category ")


# ─────────────────────────────────────────────────────────────────────────────
# Step 11 — the standard sentence scheme (VSM 9.2)
# ─────────────────────────────────────────────────────────────────────────────
RAPE = "crimes-act-ss-38-2-3-rape"
MURDER = "common-law-common-law-murder"
STATEMENT = "The sentence is below the standard sentence because the offending was brief and unplanned (F1)."


def sso_results(kb, oid, p, **kw):
    facts, fw = setup(kb, oid, **kw)
    return fw, [r for r in run_hard_checks(kb, facts, fw, p)
                if r.title.startswith("Standard sentence") or r.check_id == "audit.standard_sentence_use"]


def test_standard_sentence_scheme_applies_after_1_february_2018(kb):
    fw = fw_for(kb, RAPE, date(2019, 5, 1))
    assert fw.sso.applies == "yes" and fw.sso.years == 10
    assert "Standard sentence scheme (VSM 9.2): applies — standard sentence 10 years" in fw.text()
    assert {"sso.meaning_and_use", "sso.reasons", "npp.sso_minimum"} <= set(fw.rule_items)
    assert any(g.title == "Standard sentence scheme applies" for g in fw.gates)
    before = fw_for(kb, RAPE, date(2017, 12, 1))
    assert before.sso.applies == "no" and "npp.sso_minimum" not in before.rule_items
    assert fw_for(kb, by_name(kb, "Theft")).sso is None


def test_standard_sentence_non_parole_floor(kb):
    """npp.sso_minimum: under 20 years ≥ 60% of the term, over 20 years ≥ 70%, below it error without reasons."""
    band = schemas.Band(disposition_types=["imprisonment"], term_low_months=84, term_high_months=108,
                        npp_low_months=58, npp_high_months=70, cco_low_months=None, cco_high_months=None,
                        main_uncertainties=[])
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=50, band=band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    assert any(r.outcome == "error" and r.title == "Standard sentence — below the s 11A(4) floor" for r in res)
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=58, band=band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    assert not errors(res) and any(r.title == "Standard sentence — non-parole floor" for r in res)
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=50, band=band, standard_sentence_statement=STATEMENT,
                                            npp_interests_of_justice_reason="Exceptional rehabilitation prospects."),
                         offence_date=date(2019, 5, 1))
    assert not errors(res) and any("with reasons" in r.title for r in res)
    long_band = band.model_copy(update={"term_low_months": 280, "term_high_months": 320})
    _, res = sso_results(kb, RAPE, proposal(term_months=300, npp_months=200, band=long_band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    assert any(r.outcome == "error" for r in res)                                    # 70% of 25 years = 210
    _, res = sso_results(kb, RAPE, proposal(term_months=300, npp_months=210, band=long_band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    assert not errors(res)


def test_standard_sentence_twenty_years_short_terms_and_combinations(kb):
    band = schemas.Band(disposition_types=["imprisonment"], term_low_months=200, term_high_months=260,
                        npp_low_months=140, npp_high_months=170, cco_low_months=None, cco_high_months=None,
                        main_uncertainties=[])
    _, res = sso_results(kb, RAPE, proposal(term_months=240, npp_months=150, band=band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    twenty = [r for r in res if r.title == "Standard sentence — a term of exactly 20 years"]
    assert twenty and twenty[0].outcome == "caution" and "flag.sso_npp_20_year_overlap" in twenty[0].flags
    _, res = sso_results(kb, RAPE, proposal(term_months=240, npp_months=140, band=band,
                                            standard_sentence_statement=STATEMENT), offence_date=date(2019, 5, 1))
    assert any(r.outcome == "error" for r in res)                                    # below 60% (12 years)
    child = by_name(kb, "Sexual assault of child under the age of 16")             # 4 years, no category
    _, res = sso_results(kb, child, proposal(term_months=12, npp_months=None, standard_sentence_statement=STATEMENT),
                         offence_date=date(2019, 5, 1))
    short = [r for r in res if r.title == "Standard sentence — short term"]
    assert short and short[0].outcome == "unresolved" and "flag.sso_npp_short_terms" in short[0].flags
    combo = proposal(disposition_type="imprisonment_and_cco", term_months=18, npp_months=None, cco=cco(24),
                     standard_sentence_statement=STATEMENT,
                     band=schemas.Band(disposition_types=["imprisonment_and_cco"], term_low_months=12,
                                       term_high_months=18, npp_low_months=None, npp_high_months=None,
                                       cco_low_months=18, cco_high_months=24, main_uncertainties=[]))
    _, res = sso_results(kb, child, combo, offence_date=date(2019, 5, 1))
    assert any(r.title == "Standard sentence — combination sentence" and r.outcome == "unresolved" for r in res)


def test_standard_sentence_statement_is_required(kb):
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=60), offence_date=date(2019, 5, 1))
    assert any(r.outcome == "error" and r.title == "Standard sentence — relationship not stated" for r in res)
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=60, standard_sentence_statement=STATEMENT),
                         offence_date=date(2019, 5, 1))
    assert any(r.outcome == "pass" and r.title == "Standard sentence — relationship stated" for r in res)


def test_standard_sentence_commencement_is_unresolved(kb):
    """flag.sso_effective_date: an offence on 1 February 2018, or a period including it."""
    on_the_day = fw_for(kb, RAPE, date(2018, 2, 1))
    assert on_the_day.sso.applies == "unresolved" and "flag.sso_effective_date" in on_the_day.sso.flags
    assert any(g.outcome == "unresolved" and "flag.sso_effective_date" in g.flags for g in on_the_day.gates)
    spanning = fw_for(kb, RAPE, date(2018, 1, 10), end=date(2018, 3, 1))
    assert spanning.sso.applies == "unresolved" and "includes 1 February 2018" in spanning.sso.reason
    _, res = sso_results(kb, RAPE, proposal(term_months=96, npp_months=50), offence_date=date(2018, 2, 1))
    assert not errors(res) and any(r.outcome == "unresolved" for r in res)
    assert any(r.outcome == "caution" and r.title == "Standard sentence — relationship not stated" for r in res)


def test_murder_standard_sentence_variant_and_life(kb):
    """sso.list: murder 25 years, or 30 if the victim was a custodial officer or emergency worker on duty; life:
    a non-parole period of at least 30 years (npp.sso_minimum)."""
    assert "sso_variant_met" in charge_questions(kb, MURDER, date(2019, 5, 1))
    assert "sso_variant_met" not in charge_questions(kb, MURDER, date(2016, 5, 1))
    on_duty = fw_for(kb, MURDER, date(2019, 5, 1), sso_variant_met="yes")
    assert on_duty.sso.years == 30 and "proved, as you answered" in on_duty.sso.variant
    unknown = fw_for(kb, MURDER, date(2019, 5, 1))
    assert unknown.sso.years == 25
    assert any(g.title == "Standard sentence — the higher figure not established" for g in unknown.gates)
    band = schemas.Band(disposition_types=["life_imprisonment"], term_low_months=None, term_high_months=None,
                        npp_low_months=300, npp_high_months=360, cco_low_months=None, cco_high_months=None,
                        main_uncertainties=[])
    life = dict(disposition_type="life_imprisonment", term_months=None, band=band, standard_sentence_statement=STATEMENT)
    _, res = sso_results(kb, MURDER, proposal(npp_months=300, **life), offence_date=date(2019, 5, 1))
    assert any(r.outcome == "error" and "30 years (life)" in r.message for r in res)
    _, res = sso_results(kb, MURDER, proposal(npp_months=360, **life), offence_date=date(2019, 5, 1))
    assert not errors(res)


def test_rape_by_compelling_and_attempted_trafficking_are_outside_the_scheme(kb):
    compelling = fw_for(kb, "crimes-act-s-39-2-rape-by-compelling-sexual-penetration", date(2019, 5, 1))
    assert compelling.sso is None
    gate = next(g for g in compelling.gates if g.title == "Standard sentence — rape by compelling sexual penetration")
    assert gate.outcome == "caution" and "flag.sso_rape_by_compelling" in gate.flags
    s71 = "drugs-poisons-and-controlled-substances-act-s-71-trafficking-large-commercial-quantity"
    trafficking = fw_for(kb, s71, date(2019, 5, 1))
    assert trafficking.sso.applies == "yes" and trafficking.sso.years == 16
    assert any(g.title == "Standard sentence — exclusion" for g in trafficking.gates)
    attempt = by_id_part("ss-71-71ac-72-72b-73-attempt")
    fw = fw_for(kb, attempt, date(2019, 5, 1), primary_offence_id=s71)
    assert fw.sso is None and fw.maximum.life
    assert any(g.title == "Attempt, conspiracy or incitement" for g in fw.gates)


# ─────────────────────────────────────────────────────────────────────────────
# Step 12 — life imprisonment
# ─────────────────────────────────────────────────────────────────────────────
LIFE_BAND = schemas.Band(disposition_types=["imprisonment", "life_imprisonment"], term_low_months=300,
                         term_high_months=420, npp_low_months=240, npp_high_months=360, cco_low_months=None,
                         cco_high_months=None, main_uncertainties=[])


def life_proposal(**kw):
    data = dict(disposition_type="life_imprisonment", term_months=None, npp_months=360, band=LIFE_BAND,
                standard_sentence_statement=STATEMENT)
    data.update(kw)
    return proposal(**data)


def test_life_imprisonment_where_the_maximum_is_life(kb):
    facts, fw = setup(kb, MURDER)
    res = run_hard_checks(kb, facts, fw, life_proposal())
    assert not errors(res), [(r.title, r.message) for r in errors(res)]
    assert "pass" in outcomes(res, "audit.category_1_2") and "pass" in outcomes(res, "audit.maximum_applicable")
    res = run_hard_checks(kb, facts, fw, life_proposal(npp_months=None))
    assert any(r.title == "Non-parole period required" and r.outcome == "error" for r in res)
    res = run_hard_checks(kb, facts, fw, life_proposal(npp_months=None, npp_reason="The worst category of murder."))
    assert any(r.title == "Life without parole" and r.outcome == "caution" for r in res)
    res = run_hard_checks(kb, facts, fw, life_proposal(cco=cco(24)))
    assert any(r.title == "CCO with life imprisonment" and r.outcome == "error" for r in res)
    res = run_hard_checks(kb, facts, fw, life_proposal(fine_penalty_units=100))
    assert "error" in outcomes(res, "audit.fine_rules")                              # no fine for Level 1


def test_life_imprisonment_on_a_lesser_maximum_is_an_error(kb):
    facts, fw = setup(kb, by_name(kb, "Manslaughter"))
    res = run_hard_checks(kb, facts, fw, life_proposal())
    assert any(r.title == "Life imprisonment above the maximum" and r.outcome == "error" for r in res)


def test_life_with_the_dpcsa_printed_fine_and_section_6aaa(kb):
    s71 = "drugs-poisons-and-controlled-substances-act-s-71-trafficking-large-commercial-quantity"
    facts, fw = setup(kb, s71)
    res = run_hard_checks(kb, facts, fw, life_proposal(fine_penalty_units=1000))
    fine = [r for r in res if r.check_id == "audit.fine_rules"]
    assert fine and not any(r.outcome == "error" for r in fine) and any("flag.level1_fine_dpcsa" in r.flags for r in fine)
    facts, fw = setup(kb, MURDER, plea="guilty")
    notional = schemas.NotionalSentence(stated=True, type="life_imprisonment", term_months=None, npp_months=384,
                                        cco_months=None, unpaid_hours=None, fine_penalty_units=None,
                                        conditions_reduced=None)
    res = run_hard_checks(kb, facts, fw, life_proposal(plea_reduced_sentence=True, s6aaa=notional))
    assert "pass" in outcomes(res, "audit.s6aaa_statement")
    term = notional.model_copy(update={"type": "imprisonment", "term_months": 420, "npp_months": 300})
    res = run_hard_checks(kb, facts, fw, life_proposal(plea_reduced_sentence=True, s6aaa=term))
    assert "error" in outcomes(res, "audit.s6aaa_statement")
    facts, fw = setup(kb, MURDER, indication_given=True, indication_type="imprisonment", indication_max_months=360,
                      indication_plea_entered_on_it="yes", indication_same_judge="yes")
    res = run_hard_checks(kb, facts, fw, life_proposal())
    assert any(r.title == "Longer than indicated" and r.outcome == "error" for r in res)


def test_life_on_the_result_card_and_against_the_cases(kb):
    from app_pages import result_card as rc
    from core.agreement import CaseOutcome, band_text, place
    p = life_proposal(npp_months=330)
    assert rc.headline(p) == ("Life imprisonment", "", ["Non-parole period 27 years 6 months"])
    assert band_text(p.band) == "imprisonment of 25 years to 35 years, or life imprisonment"
    assert rc.formulated_within(p)

    def row(kind, months=None):
        return CaseOutcome(id="C1", citation="R v X", court="", year=2020, similarity=0.5, match="", outcome="",
                           source="", kind=kind, months=months, position="", reason="")
    assert place(row("life"), p.band).position == "within"
    assert place(row("term", 360), p.band).position == "within"
    assert place(row("term", 480), p.band).position == "outside"          # above the term, below life
    only_life = p.band.model_copy(update={"disposition_types": ["life_imprisonment"], "term_low_months": None,
                                          "term_high_months": None})
    assert place(row("term", 360), only_life).position == "below"
    assert place(row("cco"), only_life).position == "below"
    from core.agreement import band_agreement
    strip = rc.strip_html(band_agreement(None, p.band, MURDER), p)
    assert "Estimate · life" in strip and "life imprisonment" in strip


# ─────────────────────────────────────────────────────────────────────────────
# Step 13 — maxima fixed by reference to another offence (chapter 32; s 247B)
# ─────────────────────────────────────────────────────────────────────────────
ATTEMPT_60 = "crimes-act-s-321p-1-b-attempts-where-penalty-is-not-specified-by-reference-to-a-penalty-le"
CONSPIRACY_SET = "crimes-act-s-321c-1-c-conspiracy-to-commit-an-offence-or-multiple-offences-where-maximum-p"
INCITEMENT_SET = "crimes-act-s-321i-1-c-incitement-to-commit-an-offence-or-multiple-offences-where-maximum-p"
S3A = "crimes-act-s-3a-1-unintentional-killing-in-the-furtherance-of-a-crime-of-violence"


def test_an_attempt_is_60_percent_of_the_offence_attempted(kb):
    theft = by_name(kb, "Theft")
    assert fw_for(kb, ATTEMPT_60).maximum.unresolved                              # until the offence is identified
    fw = fw_for(kb, ATTEMPT_60, primary_offence_id=theft)
    assert fw.maximum.months == 72 and "Theft" in fw.maximum.relative_note       # 60% of Level 5 – 10 years
    assert any("flag.attempt_table_not_reproduced" in g.flags for g in fw.gates)  # the primary's maximum is a Level
    cannabis = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"].startswith("s 75(a)"))
    fine = fw_for(kb, ATTEMPT_60, primary_offence_id=cannabis).maximum
    assert fine.fine_only and fine.fine_penalty_units == 3                       # 60% of 5 penalty units
    murder = fw_for(kb, ATTEMPT_60, primary_offence_id=MURDER).maximum
    assert murder.unresolved and "Attempt to commit murder or treason" in murder.unresolved


def test_conspiracy_and_incitement_to_an_offence_punishable_by_life(kb):
    """fns 4805, 4807 (flag.inchoate_life_maximum_footnotes): conspiracy — 15 years, error above; incitement — above
    15 years a caution."""
    conspiracy = fw_for(kb, CONSPIRACY_SET, primary_offence_id=S3A)
    assert conspiracy.maximum.months == 180 and not conspiracy.maximum.life
    facts, fw = setup(kb, CONSPIRACY_SET, primary_offence_id=S3A)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130)),
                               "audit.maximum_applicable")
    incitement = fw_for(kb, INCITEMENT_SET, primary_offence_id=S3A)
    assert incitement.maximum.life and incitement.maximum.caution_above_months == 180
    facts, fw = setup(kb, INCITEMENT_SET, primary_offence_id=S3A)
    res = [r for r in run_hard_checks(kb, facts, fw, proposal(term_months=200, npp_months=130))
           if r.check_id == "audit.maximum_applicable"]
    assert [r.outcome for r in res] == ["caution"] and "flag.inchoate_life_maximum_footnotes" in res[0].flags
    s71 = "drugs-poisons-and-controlled-substances-act-s-71-trafficking-large-commercial-quantity"
    drug = fw_for(kb, CONSPIRACY_SET, primary_offence_id=s71).maximum
    assert drug.unresolved and "DPCSA s 79" in drug.unresolved
    s79 = by_id_part("s-79-conspiring-to-commit-certain-drugs")
    assert fw_for(kb, s79, primary_offence_id=s71).maximum.life
    assert "Conspiracy to commit murder" in fw_for(kb, CONSPIRACY_SET, primary_offence_id=MURDER).maximum.unresolved


def test_other_relations_and_the_users_maximum(kb):
    summary_only = by_id_part("s-321c-1-d-conspiracy")
    cannabis = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"].startswith("s 75(a)"))
    greater = fw_for(kb, summary_only, primary_offence_id=cannabis).maximum
    assert greater.months == 60 and greater.level == 6                            # Level 6 – 5 years is the greater
    intended = fw_for(kb, by_id_part("s-247b"), primary_offence_id=by_name(kb, "Theft")).maximum
    assert intended.months == 120
    table = by_id_part("s-321p-1-a-attempts")
    unresolved = fw_for(kb, table).maximum
    assert unresolved.unresolved and "not reproduced" in unresolved.unresolved
    entered = fw_for(kb, table, relative_maximum_months=120).maximum
    assert entered.months == 120 and entered.confirmed_by_user and "app policy" in entered.relative_note
    life = fw_for(kb, by_id_part("s-321p-3-attempting"), relative_maximum_life=True).maximum
    assert life.life and life.confirmed_by_user
    accumulated = fw_for(kb, CONSPIRACY_SET, primary_offence_id=by_name(kb, "Theft"), relative_maximum_months=240)
    assert accumulated.maximum.months == 240                                      # the entered maximum prevails
    drug_attempt = by_id_part("ss-71-71ac-72-72b-73-attempt")
    assert fw_for(kb, drug_attempt, primary_offence_id=by_name(kb, "Trafficking")).maximum.months == 180
    assert "Attempt to commit certain indictable drugs offences" in \
        fw_for(kb, ATTEMPT_60, primary_offence_id=by_name(kb, "Trafficking")).maximum.unresolved


def test_relative_maximum_inputs_are_validated_and_shown(kb):
    case = CaseInput(offence_id=ATTEMPT_60, offence_date=date(2024, 5, 1), age_at_offence=30, age_at_sentence=31,
                     narrative="x" * 100, primary_offence_id=ATTEMPT_60)
    assert any("cannot be the charge itself" in p for p in validate(case, kb))
    case.primary_offence_id = by_name(kb, "Theft")
    assert not validate(case, kb)
    assert "the offence attempted, conspired, incited or intended: " in case_details_text(case, kb)
    assert "Theft" in case_details_text(case, kb)


def test_coverage_after_batch_2(kb):
    for feature in ("standard_sentence", "life", "relative_maximum"):
        assert feature not in NOT_YET_CHECKED
    full = [o for o in config.IN_SCOPE_OFFENCES if not unchecked_features(kb, o)]
    assert len(full) >= 312


def test_a_whole_run_with_life_imprisonment_and_the_standard_sentence(kb, retriever):
    """Murder of an emergency worker on duty (2019): a life sentence whose first non-parole period (20 years) is
    below the s 11A(4) floor is revised to 30 years; the report and the result card show life and the scheme."""
    from app_pages import result_card as rc
    from core.llm import LLM
    from core.pipeline import Run, run_remaining, step_confirm, step_extract
    from core.report import build_blocks, render_docx, render_markdown
    case = CaseInput(offence_id=MURDER, offence_date=date(2019, 5, 1), court="Supreme Court", plea="guilty",
                     age_at_offence=30, age_at_sentence=32, sso_variant_met="yes", cco_consent="no",
                     narrative="The offender stabbed a paramedic who was treating a patient. The paramedic died. The "
                               "offender pleaded guilty to murder at the first opportunity. " * 2)
    fake = FakeOpenAI(bad_first_proposal=True, proposal_kind="life")
    llm = LLM(api_key="sk-test-key-0123456789", http_client=fake.client())
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    assert run.finished and len(run.attempts) == 2
    assert any(c.title == "Standard sentence — below the s 11A(4) floor" and c.outcome == "error"
               for c in run.attempts[0].checks)
    p = run.current.proposal.proposal
    assert p.disposition_type == "life_imprisonment" and p.npp_months == 360
    assert not [c for c in run.current.checks if c.outcome == "error"], \
        [(c.title, c.message) for c in run.current.checks if c.outcome == "error"]
    text = render_markdown(build_blocks(run, kb))
    assert "Life imprisonment, with a non-parole period of 30 years" in text
    assert "Standard sentence (VSM 9.2)" in text and "**Standard sentence.**" in text
    assert render_docx(build_blocks(run, kb))
    card = rc.card_html(run, kb)
    assert 'class="vr-sentence">Life imprisonment<' in card and "Standard sentence</span> 30 years" in card


# ─────────────────────────────────────────────────────────────────────────────
# Step 14 — bodies corporate and the company fine
# ─────────────────────────────────────────────────────────────────────────────
OHS_21 = "occupational-health-and-safety-act-2004-vic-21-1-failure-by-employers-to-provide-and-maint"
FINE_BAND = schemas.Band(disposition_types=["fine"], term_low_months=None, term_high_months=None, npp_low_months=None,
                         npp_high_months=None, cco_low_months=None, cco_high_months=None, main_uncertainties=[])


def company(kb, oid, **kw):
    case = CaseInput(offence_id=oid, offence_date=date(2024, 5, 1), offender_type="body_corporate",
                     age_at_offence=None, age_at_sentence=None, narrative="x" * 100, presentence_detention_days=None,
                     **kw)
    facts = ConfirmedFacts(case=case, summary="", facts=[])
    return facts, build_framework(kb, facts)


def fine(units, **kw):
    return proposal(disposition_type="fine", term_months=None, npp_months=None, fine_penalty_units=units,
                    band=FINE_BAND, **kw)


def test_a_body_corporate_needs_no_age_and_is_fined_within_the_company_maximum(kb):
    case = CaseInput(offence_id=OHS_21, offence_date=date(2024, 5, 1), court="County Court", plea="guilty",
                     offender_type="body_corporate", age_at_offence=None, age_at_sentence=None, narrative="x" * 100)
    assert not validate(case, kb)
    facts, fw = company(kb, OHS_21)
    assert fw.body_corporate and fw.maximum.company_fine_units == 9000 and fw.maximum.company_fine_source == "printed"
    assert any(g.title == "Body corporate" for g in fw.gates) and "a body corporate" in fw.text()
    assert not errors(run_hard_checks(kb, facts, fw, fine(9000)))
    assert any(r.title == "Fine above the body corporate maximum" for r in errors(run_hard_checks(kb, facts, fw,
                                                                                                fine(9001))))
    res = run_hard_checks(kb, facts, fw, proposal(conviction_recorded=True))
    assert any(r.title == "An order on a person for a body corporate" and r.app_policy for r in errors(res))
    assert "Offender: a body corporate" in case_details_text(case, kb)


def test_the_body_corporate_multiplier(kb):
    """flag.body_corporate_fine_multiplier: none printed → five times the natural person's maximum, error above; outside
    the Crimes Act a fine above the natural person's maximum is a caution."""
    facts, fw = company(kb, by_name(kb, "Theft"))
    assert fw.maximum.company_fine_units == 6000 and fw.maximum.company_fine_source == "5 × natural person"
    assert not errors(run_hard_checks(kb, facts, fw, fine(6000)))
    assert errors(run_hard_checks(kb, facts, fw, fine(6001)))
    facts, fw = company(kb, by_name(kb, "Trafficking"))                         # DPCSA: outside the Crimes Act
    res = run_hard_checks(kb, facts, fw, fine(3000))
    assert not errors(res) and any("flag.body_corporate_fine_multiplier" in r.flags and r.outcome == "caution"
                                   for r in res if r.check_id == "audit.fine_rules")
    psychoactive = by_id_part("s-56d-producing-psychoactive-substance-body-co")
    facts, fw = company(kb, psychoactive)
    assert fw.maximum.company_fine_units == 1200 and fw.maximum.company_fine_source == "printed"
    natural = by_id_part("s-56d-producing-psychoactive-substance-natural")
    case = CaseInput(offence_id=natural, offence_date=date(2024, 5, 1), offender_type="body_corporate",
                     age_at_offence=None, age_at_sentence=None, narrative="x" * 100)
    assert any("natural person form" in p for p in validate(case, kb))


def test_a_body_corporate_has_no_category_minimum_or_level_2_rule(kb):
    facts, fw = company(kb, by_name(kb, "Armed robbery"), category_condition_met="yes")       # Level 2, Category 2
    assert fw.category is None and not fw.alternatives and fw.mnpp_months is None
    res = run_hard_checks(kb, facts, fw, fine(5000))
    assert not errors(res), [(r.title, r.message) for r in errors(res)]                     # 12.1: a body corporate
    workplace = by_name(kb, "Workplace manslaughter (after 1 July 2020)")
    facts, fw = company(kb, workplace)
    assert fw.maximum.company_fine_units == 100000
    res = run_hard_checks(kb, facts, fw, fine(50000))
    assert not any("flag.fine_unlabelled_life_25_maximum" in r.flags for r in res)          # no caution for a company
    assert not [r for r in res if r.check_id == "audit.mandatory_ancillary_orders"]


# ─────────────────────────────────────────────────────────────────────────────
# Step 16 — mandatory licence orders and the alcohol exclusion order's "relevant offences"
# ─────────────────────────────────────────────────────────────────────────────
def licence(kind="licence_cancellation_and_disqualification", months=24):
    return schemas.AncillaryOrder(type=kind, status="mandatory", months=months, details="", item_ids=[])


def licence_results(kb, oid, p, **kw):
    facts, fw = setup(kb, oid, **kw)
    return fw, [r for r in run_hard_checks(kb, facts, fw, p) if r.check_id == "audit.mandatory_ancillary_orders"
                and r.title.lower().startswith(("licence", "rsa"))]


def test_serious_motor_vehicle_offences_need_their_licence_order(kb):
    """licence.serious_mv_offences: culpable driving 2 years; dangerous driving causing death 18 months."""
    culpable = "crimes-act-s-318-culpable-driving-causing-death"
    fw, res = licence_results(kb, culpable, proposal(), licence_status="victorian")
    assert fw.licence.group == "other_serious_mv" and fw.licence.minimum == 24
    assert any(r.title == "Licence order missing" and r.outcome == "error" for r in res)
    _, res = licence_results(kb, culpable, proposal(ancillary_orders=[licence(months=12)]), licence_status="victorian")
    assert any(r.title == "Licence order too short" for r in res)
    _, res = licence_results(kb, culpable, proposal(ancillary_orders=[licence()]), licence_status="victorian")
    assert [r.outcome for r in res] == ["pass"]
    _, res = licence_results(kb, culpable, proposal(ancillary_orders=[licence()]), licence_status="other_jurisdiction")
    assert any(r.title == "Licence order form" and r.outcome == "error" for r in res)
    _, res = licence_results(kb, culpable, proposal(ancillary_orders=[licence()]), licence_status="unknown")
    assert any(r.title == "Licence status unknown" and r.outcome == "caution" for r in res)
    _, res = licence_results(kb, culpable, proposal(ancillary_orders=[licence()], conviction_recorded=None),
                             licence_status="victorian")
    assert any("flag.licence_guilty_or_convicted" in r.flags for r in res)
    ddcd = "crimes-act-s-319-dangerous-driving-causing-death"
    fw, res = licence_results(kb, ddcd, proposal(ancillary_orders=[licence(months=18)]), licence_status="victorian")
    assert fw.licence.minimum == 18 and not [r for r in res if r.outcome == "error"]
    assert not any(g.title == "Not fully checked" for g in fw.gates)


def test_offences_arising_out_of_driving_and_substance_influenced(kb):
    m = by_name(kb, "Manslaughter")
    fw, res = licence_results(kb, m, proposal(), arising_from_driving="yes", licence_status="victorian")
    assert fw.licence.minimum == 24 and any(r.title == "Licence order missing" for r in res)
    _, res = licence_results(kb, m, proposal(), arising_from_driving="unknown")
    assert [r.title for r in res] == ["Licence order may be mandatory"]
    _, res = licence_results(kb, m, proposal(), arising_from_driving="no")
    assert not res
    s17 = config.CAUSING_INJURY_OFFENCES[3]
    fw, res = licence_results(kb, s17, proposal(), arising_from_driving="yes", alcohol_influenced_driving="yes",
                              licence_status="victorian")
    assert fw.licence.minimum == 12 and any(r.title == "Licence order missing" for r in res)
    _, res = licence_results(kb, s17, proposal(), arising_from_driving="yes", alcohol_influenced_driving="no")
    assert not res                                                               # a drug alone: not substance-influenced
    fw, _ = licence_results(kb, "crimes-act-s-79a-aggravated-carjacking", proposal(), arising_from_driving="yes",
                            alcohol_influenced_driving="yes")
    assert fw.licence.minimum == 24


def test_licence_orders_that_follow_a_conviction(kb):
    """licence.other_mandatory: s 319AA ≥ 12 months on conviction; theft or attempted theft of a motor vehicle."""
    pursued = "crimes-act-s-319aa-dangerous-or-negligent-driving-while-pursued-by-police"
    fw, res = licence_results(kb, pursued, proposal(), licence_status="victorian")
    assert fw.licence.minimum == 12 and any(r.title == "Licence order missing" for r in res)
    _, res = licence_results(kb, pursued, proposal(disposition_type="fine", term_months=None, npp_months=None,
                                                   fine_penalty_units=10, conviction_recorded=False, band=FINE_BAND),
                             licence_status="victorian")
    assert not res
    theft = by_name(kb, "Theft")
    assert "motor_vehicle_stolen" in charge_questions(kb, theft)
    _, res = licence_results(kb, theft, proposal(), motor_vehicle_stolen="yes", licence_status="victorian")
    assert any(r.title == "Licence order missing" for r in res)
    _, res = licence_results(kb, theft, proposal(ancillary_orders=[licence(months=None)]), motor_vehicle_stolen="yes",
                             licence_status="victorian")
    assert [r.outcome for r in res] == ["pass"]                                    # the three-month default applies
    _, res = licence_results(kb, theft, proposal(), motor_vehicle_stolen="unknown")
    assert [r.title for r in res] == ["Licence order may be mandatory"]
    _, res = licence_results(kb, ATTEMPT_60, proposal(), primary_offence_id=theft, motor_vehicle_stolen="yes",
                             licence_status="victorian")
    assert any(r.title == "Licence order missing" for r in res)
    _, res = licence_results(kb, ATTEMPT_60, proposal(), primary_offence_id=by_name(kb, "Burglary"),
                             motor_vehicle_stolen="yes", licence_status="victorian")
    assert not res


def test_failing_to_stop_after_an_accident_rsa_s61_3(kb):
    rsa = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"] == "RSA s 61(3)")
    assert "previous_s61_3_offence" in charge_questions(kb, rsa)
    _, res = licence_results(kb, rsa, proposal(ancillary_orders=[licence(months=36)]), previous_s61_3_offence="no",
                             licence_status="victorian")
    assert any(r.title == "Licence order too short" for r in res)                   # 4 years with a conviction
    no_conviction = proposal(disposition_type="fine", term_months=None, npp_months=None, fine_penalty_units=100,
                             conviction_recorded=False, band=FINE_BAND, ancillary_orders=[licence(months=36)])
    _, res = licence_results(kb, rsa, no_conviction, previous_s61_3_offence="no", licence_status="victorian")
    assert not [r for r in res if r.outcome == "error"]                            # 2 years without one
    _, res = licence_results(kb, rsa, proposal(ancillary_orders=[licence(months=60)]), previous_s61_3_offence="yes",
                             licence_status="victorian")
    assert any(r.title == "Licence order too short" for r in res)                   # a subsequent offence: 8 years
    _, res = licence_results(kb, rsa, proposal(ancillary_orders=[licence(months=48)]), licence_status="victorian")
    assert any(r.title == "RSA s 61(3) — previous offence unknown" for r in res)
    _, res = licence_results(kb, rsa, proposal(ancillary_orders=[licence("licence_disqualification_from_obtaining", 48)]),
                             previous_s61_3_offence="no", licence_status="none")
    assert any(r.title == "RSA s 61(3) — no Victorian licence" for r in res)


def test_the_alcohol_exclusion_order_relevant_offence_question(kb):
    from core.facts import aeo_question_applies
    drunk = dict(intoxicated_by_liquor="yes", intoxication_contributed="yes", previous_aeo="no",
                 motor_vehicle_stolen="no")
    theft = by_name(kb, "Theft")
    case = CaseInput(offence_id=theft, intoxicated_by_liquor="yes", intoxication_contributed="yes")
    assert aeo_question_applies(case)
    assert not aeo_question_applies(CaseInput(offence_id=MURDER, intoxicated_by_liquor="yes"))       # VSM 20.1.1
    facts, fw = setup(kb, theft, aeo_relevant_offence="yes", **drunk)
    res = run_hard_checks(kb, facts, fw, proposal())
    assert any(r.title == "Alcohol exclusion order missing" and r.outcome == "error" for r in res)
    facts, fw = setup(kb, theft, aeo_relevant_offence="no", **drunk)
    assert not [r for r in run_hard_checks(kb, facts, fw, proposal()) if r.check_id == "audit.mandatory_ancillary_orders"]
    facts, fw = setup(kb, theft, **drunk)
    assert any(r.title == "Alcohol exclusion order — relevant offence?"
               for r in run_hard_checks(kb, facts, fw, proposal()))


def test_every_offence_is_fully_checked(kb):
    """Guide Part B done: no register offence shows "Not fully checked" (335 of 335)."""
    assert not NOT_YET_CHECKED
    assert not [o for o in config.IN_SCOPE_OFFENCES if unchecked_features(kb, o)]
    for oid in config.IN_SCOPE_OFFENCES:
        assert not any(g.title == "Not fully checked" for g in fw_for(kb, oid).gates), oid


def test_the_simple_input_finds_a_body_corporate(kb):
    from core.facts import sorted_input
    from core.schemas import FieldQuote
    from tests.test_simple_input import answer
    text = "Acme Pty Ltd pleaded guilty in the County Court to failing to provide a safe working environment."
    srt = sorted_input(answer(offender_type="body_corporate", quotes=[FieldQuote(field="offender_type",
                                                                                 quote="Acme Pty Ltd")]), text)
    assert srt.values.get("offender_type") == "body_corporate"
    assert "the offender's age at the offence" not in srt.missing
    person = sorted_input(answer(offender_type="natural_person"), text)
    assert "offender_type" not in person.values and "the offender's age at the offence" in person.missing
