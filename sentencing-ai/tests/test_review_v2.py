"""Regression tests for the findings of the independent review of version 2.0 (three reviewers: A — the statutory
schemes, B — bodies corporate, licence orders, the case database, the test cases and the docs, C — the screens and
the simple input). Each test names its finding."""

from datetime import date

from core import config, schemas
from core.checks import run_hard_checks
from core.coverage import charge_questions
from core.facts import (CaseInput, ConfirmedFacts, case_details_text, sorted_input, suggestions_for_unknowns,
                        validate)
from core.framework import build_framework
from tests.test_checks import cco, proposal, setup
from tests.test_offence_schemes import (ATTEMPT_60, CONSPIRACY_SET, INCITEMENT_SET, S317AC, S3A, by_id_part, by_name,
                                        fine, fw_for, licence, life_proposal)

THEFT = "crimes-act-s-74-theft"
OHS_21 = "occupational-health-and-safety-act-2004-vic-21-1-failure-by-employers-to-provide-and-maint"
OHS_32 = "occupational-health-and-safety-act-2004-vic-32-recklessly-endangering-persons"
CORONERS = "coroners-act-2008-vic-coroner-s-act-2008-vic-s-103-contempt-coroner-s-act"
HIGHER_CONTEMPT = "common-law-supreme-court-act-1986-vic-s-61-contempt-of-court-county-court-supreme-court"
TABLE_ATTEMPT = "crimes-act-s-321p-1-a-attempts-where-a-penalty-is-specified-by-reference-to-a-penalty-leve"
SPECIAL = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="psychiatric report",
                                stated_in_writing=True)


def company(kb, oid, offence_date=date(2024, 5, 1), **kw):
    case = CaseInput(offence_id=oid, offence_date=offence_date, offender_type="body_corporate", age_at_offence=None,
                     age_at_sentence=None, narrative="x" * 100, presentence_detention_days=None, **kw)
    facts = ConfirmedFacts(case=case, summary="", facts=[])
    return facts, build_framework(kb, facts)


def results(kb, p, oid, **kw):
    facts, fw = setup(kb, oid, **kw)
    return fw, run_hard_checks(kb, facts, fw, p)


def of(res, check_id):
    return [r.outcome for r in res if r.check_id == check_id]


def titled(res, title):
    return [r.outcome for r in res if r.title == title]


# ── A1: ss 317AC/317AD never take the "special reason, other disposition" route ──
def test_a1_317ac_special_reason_cco_or_fine_is_an_error_on_category_1(kb):
    d = date(2019, 6, 1)
    for injured in ("no", "unknown"):
        _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24),
                                      special_reason=SPECIAL), S317AC, offence_date=d, official_injured=injured,
                         category1_confirmed="yes")
        assert "error" in of(res, "audit.category_1_2"), injured
    _, res = results(kb, fine(100, special_reason=SPECIAL), by_id_part("s-317ad"), offence_date=d,
                     official_injured="no", category1_confirmed="yes")
    assert "error" in of(res, "audit.category_1_2")
    # Category 1 not confirmed: the open Category 1 reading makes it unresolved, as the gate says
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24),
                                  special_reason=SPECIAL), S317AC, offence_date=d, official_injured="no")
    assert "unresolved" in of(res, "audit.category_1_2") and "error" not in of(res, "audit.category_1_2")


# ── A2 / B3: contempt — only the checks based on the Sentencing Act become cautions ──
def test_a2_contempt_keeps_the_errors_that_do_not_rest_on_the_sentencing_act(kb):
    facts, fw = company(kb, CORONERS, contempt_by_higher_court="yes")
    res = run_hard_checks(kb, facts, fw, fine(700))
    assert titled(res, "Fine above the body corporate maximum") == ["error"]          # the Act prints 600 p.u.
    _, res = results(kb, proposal(disposition_type="imprisonment", term_months=12, npp_months=None,
                                  npp_reason="short term"), HIGHER_CONTEMPT, offence_date=date(2024, 2, 13),
                     indication_given=True, indication_type="imprisonment", indication_max_months=6,
                     indication_plea_entered_on_it="yes", indication_same_judge="yes")
    assert "error" in of(res, "audit.sentence_indication")                         # Criminal Procedure Act 2009
    facts, fw = company(kb, HIGHER_CONTEMPT, contempt_by_higher_court="yes")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="imprisonment", term_months=6, npp_months=None))
    assert titled(res, "An order on a person for a body corporate") == ["error"]      # the app's own rule


# ── A3: the DPCSA inchoate rows take only their own primary offences ──
def test_a3_dpcsa_rows_refuse_a_primary_offence_outside_their_list(kb):
    attempt = by_id_part("ss-71-71ac-72-72b-73-attempt")
    m = fw_for(kb, attempt, date(2019, 6, 1), primary_offence_id=THEFT).maximum
    assert m.unresolved and "s 321P" in m.unresolved
    s79 = by_id_part("s-79-conspiring-to-commit-certain-drugs")
    assert fw_for(kb, s79, primary_offence_id=by_name(kb, "Murder")).maximum.unresolved
    assert fw_for(kb, s79, primary_offence_id=by_name(kb, "Trafficking")).maximum.months == 180
    items = fw_for(kb, s79, primary_offence_id=by_id_part("s-71a-possession-of-items-for-trafficking")).maximum
    assert items.unresolved and "guide.drugs.extended_liability" in items.unresolved          # ask the user


# ── A4: the 15-year cap for a life offence is for the s 321C(1)(c) and s 321I(1)(c) rows only ──
def test_a4_life_cap_only_on_the_1c_rows(kb):
    assert fw_for(kb, CONSPIRACY_SET, primary_offence_id=S3A).maximum.months == 180
    for part in ("s-321c-1-a-conspiracy", "s-321c-2-a-conspiracy", "s-321i-1-a-incitement"):
        m = fw_for(kb, by_id_part(part), primary_offence_id=S3A).maximum
        assert m.life and not m.caution_above_months, part


# ── A5, A6: 60% is never rounded up, and keeps the level/term band ──
def test_a5_a6_sixty_percent_rounds_down_and_keeps_the_band(kb):
    s319aa = by_id_part("s-319aa-dangerous-or-negligent-driving")
    m = fw_for(kb, ATTEMPT_60, primary_offence_id=s319aa).maximum
    assert m.months == 21 and m.fine_penalty_units == 210                          # 60% of 36 months is 21.6
    s49o = by_id_part("s-49o-2-failure-by-person-in-authority")
    facts, fw = setup(kb, ATTEMPT_60, primary_offence_id=s49o)
    assert fw.maximum.months == 43 and fw.maximum.scale_months == 36              # 60% of 72 and of 60 months
    for months, want in ((36, "pass"), (40, "unresolved"), (44, "error")):
        res = run_hard_checks(kb, facts, fw, proposal(term_months=months, npp_months=24))
        assert want in of(res, "audit.maximum_applicable"), months


# ── A7: under the standard sentence scheme, life with no non-parole period needs interests-of-justice reasons ──
def test_a7_sso_life_without_a_non_parole_period(kb):
    murder = by_name(kb, "Murder")
    _, res = results(kb, life_proposal(npp_months=None, npp_reason="the worst category"), murder,
                     offence_date=date(2019, 6, 1))
    assert titled(res, "Standard sentence — below the s 11A(4) floor") == ["error"]
    _, res = results(kb, life_proposal(npp_months=None, npp_reason="the worst category",
                                       npp_interests_of_justice_reason="the gravity of the offending"), murder,
                     offence_date=date(2019, 6, 1))
    assert titled(res, "Standard sentence — below the s 11A(4) floor, with reasons") == ["pass"]


# ── A8: life imprisonment on an offence at large is a caution ──
def test_a8_life_on_an_offence_at_large(kb):
    _, res = results(kb, life_proposal(standard_sentence_statement=""), HIGHER_CONTEMPT, offence_date=date(2024, 2, 13))
    assert titled(res, "Life imprisonment on an offence at large") == ["caution"]
    assert "error" not in of(res, "audit.maximum_applicable")


# ── A9: within 500 m of a school, the condition not confirmed — a fine is banded too ──
def test_a9_school_band_for_a_fine(kb):
    oid = by_name(kb, "Trafficking")
    for units, want in ((2000, "unresolved"), (2500, "error")):
        _, res = results(kb, fine(units), oid, conditional_condition_met="yes")
        assert want in of(res, "audit.fine_rules"), units


# ── A11, A12: historic "#" provisions ──
def test_a11_a_period_past_30_june_2017_brings_back_the_current_category(kb):
    s49a = by_id_part("ss-49a-2-3-sexual-penetration-of-a-child-under-the-age-of-12")
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24)), s49a,
                     offence_date=date(2017, 6, 1), offence_date_end=date(2017, 7, 15), historic_maximum_months=300,
                     historic_provision_marked="no")
    assert "unresolved" in of(res, "audit.category_1_2")


def test_a12_hash_unknown_is_an_open_category_1_reading_without_a_current_category(kb):
    s49b = by_id_part("ss-49b-2-3-sexual-penetration-of-a-child-under-the-age-of-16")
    fw, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24)), s49b,
                      offence_date=date(2017, 4, 15), historic_maximum_months=120)
    assert any(a.category == 1 for a in fw.alternatives)
    assert "unresolved" in of(res, "audit.category_1_2")


# ── A13: a fine on an incitement to a life offence is a caution with the flag ──
def test_a13_fine_on_an_incitement_to_a_life_offence(kb):
    _, res = results(kb, fine(1000), INCITEMENT_SET, primary_offence_id=S3A)
    r = [x for x in res if x.title == "Fine on an incitement to a life offence"]
    assert r and r[0].outcome == "caution" and "flag.inchoate_life_maximum_footnotes" in r[0].flags
    assert "error" not in of(res, "audit.fine_rules")


# ── A14: the notes of the offence identified give instructions the form can follow ──
def test_a14_no_instruction_to_a_question_the_form_does_not_ask(kb):
    attempt = by_id_part("ss-71-71ac-72-72b-73-attempt")
    fw = fw_for(kb, attempt, primary_offence_id=by_name(kb, "Trafficking"))
    text = " ".join(g.message for g in fw.gates) + " ".join(fw.maximum.notes)
    assert "answer the question in Step 1" not in text and "Or enter the maximum" in text
    s49a = by_id_part("ss-49a-2-3-sexual-penetration-of-a-child-under-the-age-of-12")
    m = fw_for(kb, ATTEMPT_60, date(2016, 6, 1), primary_offence_id=s49a).maximum
    assert m.unresolved and "Historic offending" not in m.unresolved and "Knowledge base page" in m.unresolved


# ── A15: the app's own decisions are labelled app policy ──
def test_a15_app_policy_labels(kb):
    m = by_name(kb, "Manslaughter")
    fw = fw_for(kb, m, manslaughter_circumstance="single_punch", prosecution_notice="yes", accessory_only="unknown")
    assert all(g.app_policy for g in fw.gates if g.title == "Accessory basis unknown")
    straddle = fw_for(kb, m, date(2014, 10, 1), date(2014, 12, 1), manslaughter_circumstance="single_punch",
                      prosecution_notice="yes")
    assert any("audit.maximum_applicable" in a.cite for a in straddle.alternatives if a.kind == "open")   # (1)
    no = fw_for(kb, S317AC, date(2019, 6, 1), category1_confirmed="no")
    assert all(g.app_policy for g in no.gates if g.title == "Category — the penalty table applied")
    _, res = results(kb, life_proposal(plea_reduced_sentence=True, s6aaa=schemas.NotionalSentence(
        stated=True, type="imprisonment", term_months=400, npp_months=300, cco_months=None, unpaid_hours=None,
        fine_penalty_units=None, conditions_reduced=None)), by_name(kb, "Murder"), plea="guilty")
    assert all(r.app_policy for r in res if r.title == "Section 6AAA type")


# ── B1: a company's historic OHSA 1985 offence: the company maximum comes from the table ──
def test_b1_historic_company_fine(kb):
    facts, fw = company(kb, OHS_21, offence_date=date(2003, 5, 1), historic_maximum_months=50)
    assert fw.maximum.company_fine_units is None
    assert titled(run_hard_checks(kb, facts, fw, fine(5000)), "Body corporate fine — no maximum computed") == ["unresolved"]
    facts, fw = company(kb, OHS_21, offence_date=date(2003, 5, 1), historic_maximum_fine_units=2500)
    assert fw.maximum.fine_only and fw.maximum.company_fine_units == 2500
    assert titled(run_hard_checks(kb, facts, fw, fine(5000)), "Fine above the body corporate maximum") == ["error"]
    person = fw_for(kb, OHS_21, date(2003, 5, 1), historic_maximum_fine_units=500).maximum
    assert person.fine_only and person.fine_penalty_units == 500 and person.fine_source == "entered"


# ── B4: conviction-only licence rules while the proposal does not say whether a conviction is recorded ──
def test_b4_licence_rules_with_the_conviction_not_stated(kb):
    s319aa = by_id_part("s-319aa-dangerous-or-negligent-driving")
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(18),
                                  conviction_recorded=None), s319aa, licence_status="victorian")
    assert titled(res, "Licence order missing") == ["caution"]
    rsa = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"] == "RSA s 61(3)")
    _, res = results(kb, proposal(conviction_recorded=None, ancillary_orders=[licence(months=30)]), rsa,
                     licence_status="victorian", previous_s61_3_offence="no")
    assert titled(res, "Licence order too short if convicted") == ["caution"] and "error" not in \
        of(res, "audit.mandatory_ancillary_orders")
    _, res = results(kb, proposal(conviction_recorded=None, ancillary_orders=[licence(months=20)]), rsa,
                     licence_status="victorian", previous_s61_3_offence="no")
    assert titled(res, "Licence order too short") == ["error"]


# ── B5: a relative maximum for a company uses the offence identified's printed company maximum ──
def test_b5_relative_company_maximum(kb):
    _, fw = company(kb, CONSPIRACY_SET, primary_offence_id=OHS_32)
    assert fw.maximum.company_fine_units == 20000
    _, fw = company(kb, ATTEMPT_60, primary_offence_id=OHS_32)
    assert fw.maximum.company_fine_units == 12000


# ── B6, B7: a company and an offence at large; no contradictory pass ──
def test_b6_b7_company_fines(kb):
    facts, fw = company(kb, HIGHER_CONTEMPT, offence_date=date(2024, 2, 13))
    res = run_hard_checks(kb, facts, fw, fine(5000))
    assert titled(res, "Body corporate fine — at large") == ["caution"]
    assert "unresolved" not in of(res, "audit.fine_rules")
    facts, fw = company(kb, OHS_21)
    res = run_hard_checks(kb, facts, fw, fine(9001))
    assert titled(res, "Fine above the body corporate maximum") == ["error"] and not titled(res, "Fine")


# ── B8 / C5: the licence held reaches the AI calls whenever a licence order can follow ──
def test_b8_the_licence_held_is_in_the_case_details(kb):
    case = CaseInput(offence_id=THEFT, offence_date=date(2024, 5, 1), narrative="x" * 100, arising_from_driving="no",
                     licence_status="none", motor_vehicle_stolen="yes")
    assert "Licence held (the form of any licence order follows it): none" in case_details_text(case, kb)


# ── B9: the alcohol exclusion order caution says what to answer ──
def test_b9_aeo_caution_points_to_the_intoxication_questions(kb):
    _, res = results(kb, proposal(), THEFT, intoxicated_by_liquor="unknown", intoxication_contributed="unknown",
                     motor_vehicle_stolen="no")
    r = [x for x in res if x.title == "Alcohol exclusion order — relevant offence?"]
    assert r and "answer the intoxication questions" in r[0].message


# ── B10 / C2 / C8: nothing about a person for a body corporate ──
def test_b10_a_body_corporate_gets_no_persons_questions_gates_or_flags(kb):
    m = by_name(kb, "Manslaughter")
    assert charge_questions(kb, m, date(2020, 5, 1), body_corporate=True) == []
    _, fw = company(kb, m, offence_date=date(2020, 5, 1))
    titles = [g.title for g in fw.gates]
    assert "Mandatory minimum — facts not established" not in titles and "Existing sentence or parole" not in titles
    _, fw = company(kb, S317AC, offence_date=date(2019, 6, 1))
    assert "Category 1 — confirm it" not in [g.title for g in fw.gates]
    facts, fw = company(kb, OHS_21, presentence_report="none", cco_consent="no", intoxicated_by_liquor="yes")
    text = case_details_text(facts.case, kb)
    assert "Pre-sentence report" not in text and "Intoxicated" not in text and "a body corporate" in text
    assert not {"flag.body_corporate_fine_multiplier", "flag.fine_unlabelled_life_25_maximum"} & set(fw.flags)
    sorted_case = schemas.SortedCase.model_construct(**{**{f: "unknown" for f in schemas.SortedCase.model_fields},
                                                        "offence_id": OHS_21, "court": "County Court",
                                                        "offence_date": "", "offence_date_end": "", "plea": "unknown",
                                                        "plea_timing": "unknown", "age_at_offence": None,
                                                        "age_at_sentence": None, "presentence_detention_days": 40,
                                                        "intoxicated_by_liquor": "yes",
                                                        "offender_type": "body_corporate", "case_name": "",
                                                        "quotes": [], "outside_version_1": [], "not_stated": [],
                                                        "offence_as_described": "", "offence_year": None})
    s = sorted_input(sorted_case, "Acme Pty Ltd. The manager had been drinking heavily. 40 days in custody.")
    assert "presentence_detention_days" not in s.values and "intoxicated_by_liquor" not in s.adverse


def test_c2_no_suggestions_about_a_person_for_a_body_corporate(kb):
    facts, _ = company(kb, OHS_21)
    extraction = type("E", (), {"suggested": schemas.SuggestedValues.model_construct(
        **{f: "unknown" for f in schemas.SuggestedValues.model_fields} | {"intoxicated_by_liquor": "yes",
                                                                         "cco_consent": "yes"})})()
    assert suggestions_for_unknowns(facts.case, extraction, kb) == {}


# ── B11: a higher company maximum the user identifies ──
def test_b11_a_provision_setting_a_higher_company_maximum(kb):
    facts, fw = company(kb, THEFT, company_maximum_units=20000, motor_vehicle_stolen="no")
    assert fw.maximum.company_fine_source == "5 × natural person" and fw.maximum.company_fine_units == 6000
    res = run_hard_checks(kb, facts, fw, fine(15000))
    r = [x for x in res if x.title == "Body corporate fine — the provision you identified"]
    assert r and r[0].outcome == "caution" and r[0].app_policy
    assert titled(run_hard_checks(kb, facts, fw, fine(25000)), "Fine above the body corporate maximum") == ["error"]


# ── C4: a "no" or "neither" from a description needs the user's tick ──
def test_c4_a_settling_answer_from_a_description_needs_a_tick(kb):
    desc = ("Two friends, each armed with a knife they had brought, stabbed him. The offender pleaded guilty to "
            "manslaughter. Neither a gross violence nor a single punch was involved in the death.")
    sorted_case = schemas.SortedCase.model_construct(**{**{f: "unknown" for f in schemas.SortedCase.model_fields},
                                                        "offence_id": by_name(kb, "Manslaughter"), "court": "unknown",
                                                        "offence_date": "", "offence_date_end": "", "plea": "unknown",
                                                        "plea_timing": "unknown", "age_at_offence": None,
                                                        "age_at_sentence": None, "presentence_detention_days": None,
                                                        "offender_type": "natural_person", "case_name": "",
                                                        "manslaughter_circumstance": "neither",
                                                        "quotes": [schemas.FieldQuote(
                                                            field="manslaughter_circumstance",
                                                            quote="Neither a gross violence nor a single punch")],
                                                        "outside_version_1": [], "not_stated": [],
                                                        "offence_as_described": "", "offence_year": None})
    s = sorted_input(sorted_case, desc)
    assert "manslaughter_circumstance" not in s.values and s.adverse.get("manslaughter_circumstance") == "neither"


# ── C6: a standard sentence statement where no scheme applies ──
def test_c6_a_standard_sentence_statement_without_a_scheme(kb):
    _, res = results(kb, proposal(standard_sentence_statement="below the standard sentence of 10 years"),
                     "crimes-act-s-17-recklessly-causing-serious-injury")
    r = [x for x in res if x.title == "Standard sentence — no scheme for this charge"]
    assert r and r[0].outcome == "caution" and r[0].app_policy


# ── C7: the maximum the Manual cannot give must be entered ──
def test_c7_the_entered_maximum_is_required(kb):
    case = CaseInput(offence_id=TABLE_ATTEMPT, offence_date=date(2024, 5, 1), narrative="x" * 100,
                     primary_offence_id=THEFT)
    assert any("enter the maximum you read from the legislation" in p for p in validate(case, kb))
    case.relative_maximum_months = 60
    assert not validate(case, kb)
    # B2 / C3: with the offence attempted named, the motor-vehicle licence order can be established
    case.motor_vehicle_stolen = "yes"
    fw = build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))
    assert fw.licence and fw.licence.applies == "yes"


# ── C9: the result card says when the maximum was entered by the user ──
def test_c9_the_card_marks_an_entered_maximum(kb):
    from app_pages.result_card import _maximum_text
    m = fw_for(kb, TABLE_ATTEMPT, relative_maximum_months=300).maximum
    assert "entered by you, app policy" in _maximum_text(m)


# ── the check of the fixes (reviewer B's second pass) ──
def test_bb_licence_caution_says_what_to_answer_and_the_form_is_always_checked(kb):
    _, res = results(kb, proposal(), TABLE_ATTEMPT, relative_maximum_months=60)          # offence attempted not named
    r = [x for x in res if x.title == "Licence order may be mandatory"]
    assert r and "Answer the offence attempted" in r[0].message
    rsa = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"] == "RSA s 61(3)")
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(18),
                                  conviction_recorded=None,
                                  ancillary_orders=[licence("licence_disqualification_from_obtaining", 30)]), rsa,
                     licence_status="victorian", previous_s61_3_offence="no")
    assert titled(res, "Licence order form") == ["error"] and titled(res, "Licence order too short if convicted") == ["caution"]
    s319aa = by_id_part("s-319aa-dangerous-or-negligent-driving")
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(18),
                                  conviction_recorded=None,
                                  ancillary_orders=[licence("licence_disqualification_from_obtaining", 24)]), s319aa,
                     licence_status="victorian")
    assert titled(res, "Licence order form") == ["caution"] and not titled(res, "Licence order")


def test_bb_an_entered_historic_fine_is_labelled_as_entered(kb):
    facts, fw = company(kb, OHS_21, offence_date=date(2003, 5, 1), historic_maximum_fine_units=2500)
    assert "as entered by the user from the Manual's table): 2500" in fw.text()
    from core.report import build_blocks, render_markdown
    from core.pipeline import Run
    run = Run(case=facts.case, models={}, efforts={})
    run.facts, run.framework = facts, fw
    md = render_markdown(build_blocks(run, kb))
    assert "Maximum fine (entered for the body corporate)" in md and "a natural person's" not in md


def test_bb_a_higher_company_maximum_only_where_five_times_is_used(kb):
    from core.framework import company_maximum_input_applies
    assert company_maximum_input_applies(kb, THEFT, date(2024, 5, 1))
    for oid in (OHS_21, CONSPIRACY_SET, HIGHER_CONTEMPT):              # printed, relative, at large
        assert not company_maximum_input_applies(kb, oid, date(2024, 5, 1)), oid
    kidnapping = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["offence"].startswith("Kidnapping")
                      and "common" in o)                                # at large only from 1992 to 1997
    assert company_maximum_input_applies(kb, kidnapping, date(2024, 5, 1))
    assert not company_maximum_input_applies(kb, kidnapping, date(1995, 6, 1))
    facts, _ = company(kb, CONSPIRACY_SET, primary_offence_id=OHS_32, company_maximum_units=30000)
    assert "higher maximum fine" not in case_details_text(facts.case, kb)


def test_bb_a_company_gets_no_flags_about_a_persons_sentence(kb):
    _, fw = company(kb, "crimes-act-s-17-recklessly-causing-serious-injury")
    assert not [f for f in fw.flags if "protected_official" in f or "mtmo" in f or "category" in f]
    _, fw = company(kb, CONSPIRACY_SET, primary_offence_id=OHS_32)
    assert "flag.body_corporate_fine_multiplier" not in fw.flags


def test_bb_the_card_for_a_company_on_an_entered_maximum(kb):
    from app_pages.result_card import _maximum_text
    _, fw = company(kb, TABLE_ATTEMPT, relative_maximum_months=60)
    assert "five times a person's maximum, which you entered" in _maximum_text(fw.maximum, body_corporate=True)


# ── the check of the fixes (reviewer A's second pass) ──
def test_aa_dpcsa_sub_paragraph_provisions_are_their_sections(kb):
    attempt = by_id_part("ss-71-71ac-72-72b-73-attempt")
    possession = next(o for o in config.IN_SCOPE_OFFENCES if (kb.offence(o).get("provision_norm") or "") == "s 73(1)(c)")
    m = fw_for(kb, attempt, date(2019, 6, 1), primary_offence_id=possession).maximum
    assert not m.unresolved and m.relative_note
    crimes = fw_for(kb, ATTEMPT_60, date(2019, 6, 1), primary_offence_id=possession).maximum
    assert crimes.unresolved and "Attempt to commit certain indictable drugs offences" in crimes.unresolved
    s79 = by_id_part("s-79-conspiring-to-commit-certain-drugs")
    assert "conspiracies" in fw_for(kb, s79, primary_offence_id=THEFT).maximum.unresolved


def test_aa_a_fine_on_an_incitement_to_a_life_offence_above_either_reading(kb):
    _, res = results(kb, fine(100000), INCITEMENT_SET, primary_offence_id=S3A)
    assert titled(res, "Fine above the maximum") == ["error"]
    _, res = results(kb, fine(1800), INCITEMENT_SET, primary_offence_id=S3A)
    assert titled(res, "Fine on an incitement to a life offence") == ["caution"]


def test_aa_contempt_downgrades_sentencing_act_checks_even_with_an_app_policy_label(kb):
    from core import schemas as sc
    notional = sc.NotionalSentence(stated=True, type="imprisonment", term_months=400, npp_months=300, cco_months=None,
                                   unpaid_hours=None, fine_penalty_units=None, conditions_reduced=None)
    _, res = results(kb, life_proposal(standard_sentence_statement="", plea_reduced_sentence=True, s6aaa=notional),
                     HIGHER_CONTEMPT, offence_date=date(2024, 2, 13), plea="guilty")
    assert titled(res, "Section 6AAA type") == ["caution"]


def test_aa_the_manslaughter_straddle_reading_is_a_date_point_not_app_policy(kb):
    m = by_name(kb, "Manslaughter")
    fw = fw_for(kb, m, date(2014, 10, 1), date(2014, 12, 1), manslaughter_circumstance="single_punch",
                prosecution_notice="yes")
    straddle = [a for a in fw.alternatives if a.kind == "open"]
    assert straddle and not straddle[0].app_policy and "audit.maximum_applicable" in straddle[0].cite
    assert "the Manual leaves this open" in fw.text()


def test_aa_the_school_band_for_a_company(kb):
    oid = by_name(kb, "Trafficking")
    facts, fw = company(kb, oid, conditional_condition_met="yes")
    assert titled(run_hard_checks(kb, facts, fw, fine(10000)),
                  "Body corporate fine — conditional maximum unresolved") == ["unresolved"]
    assert titled(run_hard_checks(kb, facts, fw, fine(12500)), "Fine above the body corporate maximum") == ["error"]


def test_aa_a_period_past_30_june_2017_either_way(kb):
    s49b = by_id_part("ss-49b-2-3-sexual-penetration-of-a-child-under-the-age-of-16")
    _, res = results(kb, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(24)), s49b,
                     offence_date=date(2017, 6, 1), offence_date_end=date(2017, 7, 15), historic_maximum_months=120,
                     historic_provision_marked="yes")
    assert "unresolved" in of(res, "audit.category_1_2") and "error" not in of(res, "audit.category_1_2")


def test_aa_the_school_messages_follow_the_answer(kb):
    oid = by_name(kb, "Trafficking")
    _, res = results(kb, proposal(term_months=200, npp_months=130), oid, conditional_condition_met="yes",
                     school_condition_confirmed="no")
    r = [x for x in res if x.title == "Above the base maximum — conditional maximum unresolved"]
    assert r and "you answered is not met" in r[0].message
    _, res = results(kb, fine(2500), oid, conditional_condition_met="yes")
    assert any("2400, the most on any reading" in x.message for x in res if x.title == "Fine above the maximum")


# ── reviewer B's final check ──
def test_bbb_flags_linked_to_the_offences_other_listing(kb):
    from core.framework import relevant_flag
    cd = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence_chapters(o) == {"27", "32"})
    assert relevant_flag(kb, "flag.inchoate_life_maximum_footnotes", cd)


# ── reviewer C's second pass ──
def test_cc_step2_leaves_a_charge_questions_no_unticked(kb, retriever):
    from core.llm import LLM
    from core.pipeline import Run, step_extract
    from tests.fake_openai import FakeOpenAI
    from tests.test_pipeline import KEY, load_case

    class SaysNo(FakeOpenAI):
        def answer_FactsExtraction(self, text, body):
            out = super().answer_FactsExtraction(text, body)
            out.suggested.manslaughter_circumstance, out.suggested.prosecution_notice = "neither", "no"
            return out
    case = load_case("tc4_single_punch_manslaughter.json")
    case.manslaughter_circumstance = case.prosecution_notice = "unknown"
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, LLM(api_key=KEY, http_client=SaysNo().client()), kb)
    assert run.suggestions["manslaughter_circumstance"] == "neither" and run.suggestions["prosecution_notice"] == "no"
    assert not run.suggestion_choices["manslaughter_circumstance"] and not run.suggestion_choices["prosecution_notice"]


def test_cc_an_unverified_body_corporate_keeps_a_persons_verified_answers(kb):
    desc = "John Smith, aged 52, pleaded guilty in the County Court. He had been drinking heavily that night."
    sorted_case = schemas.SortedCase.model_construct(**{**{f: "unknown" for f in schemas.SortedCase.model_fields},
                                                        "offence_id": THEFT, "court": "County Court",
                                                        "offence_date": "", "offence_date_end": "", "plea": "unknown",
                                                        "plea_timing": "unknown", "age_at_offence": 52,
                                                        "age_at_sentence": None, "presentence_detention_days": None,
                                                        "offender_type": "body_corporate", "case_name": "",
                                                        "quotes": [schemas.FieldQuote(field="offender_type",
                                                                                      quote="Smith Plumbing Pty Ltd"),
                                                                   schemas.FieldQuote(field="age_at_offence",
                                                                                      quote="John Smith, aged 52")],
                                                        "outside_version_1": [], "not_stated": [],
                                                        "offence_as_described": "", "offence_year": None})
    s = sorted_input(sorted_case, desc)
    assert "offender_type" not in s.values and s.values.get("age_at_offence") == 52
