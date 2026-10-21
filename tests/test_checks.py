"""Tests of the hard checks (core/checks.py) on hand-made proposals."""

from datetime import date

from core import config, schemas
from core.checks import run_hard_checks
from core.facts import CaseInput, ConfirmedFacts
from core.framework import build_framework

S15A, S15B, S16, S17, S18I, S18R = config.IN_SCOPE_OFFENCES


def setup(kb, oid=S17, **case_kw):
    base = dict(offence_id=oid, offence_date=date(2024, 5, 1), victim_protected_worker="no",
                offender_knew_or_reckless="no", age_at_offence=25, age_at_sentence=26, narrative="x" * 100,
                presentence_report="available", cco_consent="yes", presentence_detention_days=0,
                intoxicated_by_liquor="no", intoxication_contributed="no", previous_aeo="no")
    base.update(case_kw)
    facts = ConfirmedFacts(case=CaseInput(**base), summary="", facts=[])
    return facts, build_framework(kb, facts)


def cco(months=18, conditions=None, commences="on_making"):
    return schemas.CCOTerms(months=months, commences=commences, months_after_order=None, conditions=conditions or [
        schemas.CCOCondition(kind="unpaid_community_work", details="", hours=200, completion_months=12,
                             hours_per_day=None, duration_months=None, monitors=None)])


def proposal(**kw):
    data = dict(
        disposition_type="imprisonment", term_months=36, npp_months=24, npp_reason=None, cco=None,
        fine_penalty_units=None, undertaking_months=None, conviction_recorded=True,
        special_reason=schemas.SpecialReason(found=False, limb=None, evidential_basis="", stated_in_writing=False),
        category2_exception=schemas.Category2Exception(relied_on=False, exception=None, evidential_basis=""),
        plea_reduced_sentence=False,
        s6aaa=schemas.NotionalSentence(stated=False, type=None, term_months=None, npp_months=None, cco_months=None,
                                       unpaid_hours=None, fine_penalty_units=None, conditions_reduced=None),
        cooperation=schemas.Cooperation(less_severe_sentence=False, basis="none", announced=False, undertaking_recorded=False),
        ancillary_orders=[],
        band=schemas.Band(disposition_types=["imprisonment"], term_low_months=30, term_high_months=42,
                          npp_low_months=20, npp_high_months=28, cco_low_months=None, cco_high_months=None,
                          main_uncertainties=[]),
        reasons=[], assumptions=[], unresolved=[], flags_shown=[], revision_notes=[])
    data.update(kw)
    return schemas.Proposal(**data)


def outcomes(results, check_id):
    return [r.outcome for r in results if r.check_id == check_id]


def test_plain_imprisonment_passes(kb):
    facts, fw = setup(kb)
    res = run_hard_checks(kb, facts, fw, proposal())
    assert not [r for r in res if r.outcome == "error"], [(r.check_id, r.message) for r in res if r.outcome == "error"]


def test_above_maximum_is_error(kb):
    facts, fw = setup(kb, S18R)                       # maximum 5 years
    res = run_hard_checks(kb, facts, fw, proposal(term_months=72, npp_months=48))
    assert "error" in outcomes(res, "audit.maximum_applicable")


def test_category_1_excludes_cco(kb):
    facts, fw = setup(kb, S15A)
    p = proposal(disposition_type="imprisonment_and_cco", term_months=12, npp_months=None, cco=cco(),
                 band=schemas.Band(disposition_types=["imprisonment_and_cco"], term_low_months=6, term_high_months=12,
                                   npp_low_months=None, npp_high_months=None, cco_low_months=12, cco_high_months=24,
                                   main_uncertainties=[]))
    res = run_hard_checks(kb, facts, fw, p)
    assert "error" in outcomes(res, "audit.category_1_2")
    assert "error" in outcomes(res, "audit.mandatory_minimum")       # 4-year minimum, no special reason


def test_minimum_non_parole_period(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes")
    assert fw.mnpp_months == 36
    low = proposal(term_months=48, npp_months=30,
                   band=schemas.Band(disposition_types=["imprisonment"], term_low_months=42, term_high_months=60,
                                     npp_low_months=30, npp_high_months=40, cco_low_months=None,
                                     cco_high_months=None, main_uncertainties=[]))
    res = run_hard_checks(kb, facts, fw, low)
    assert outcomes(res, "audit.mandatory_minimum").count("error") == 1      # the sentence
    assert "caution" in outcomes(res, "audit.mandatory_minimum")             # the band (app policy)
    # Not confirmed that the scheme applied at the offence date: unresolved, not an error
    facts_u, fw_u = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes")
    res = run_hard_checks(kb, facts_u, fw_u, low)
    assert "error" not in outcomes(res, "audit.mandatory_minimum")
    assert "unresolved" in outcomes(res, "audit.mandatory_minimum")
    res = run_hard_checks(kb, facts, fw, proposal(term_months=54, npp_months=36,
                                                  band=schemas.Band(disposition_types=["imprisonment"], term_low_months=48,
                                                                    term_high_months=60, npp_low_months=36,
                                                                    npp_high_months=42, cco_low_months=None,
                                                                    cco_high_months=None, main_uncertainties=[])))
    assert "error" not in outcomes(res, "audit.mandatory_minimum")


def test_special_reason_routes(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", mental_impairment_evidence="yes")
    sr = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="psychiatrist's report",
                               stated_in_writing=True)
    mtmo_conditions = [schemas.CCOCondition(kind="judicial_monitoring", details="", hours=None, completion_months=None,
                                            hours_per_day=None, duration_months=None, monitors=None),
                       schemas.CCOCondition(kind="treatment_and_rehabilitation", details="", hours=None,
                                            completion_months=None, hours_per_day=None, duration_months=None, monitors=None)]
    band = schemas.Band(disposition_types=["mtmo"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=24, cco_high_months=36, main_uncertainties=[])
    ok = proposal(disposition_type="mtmo", term_months=None, npp_months=None, cco=cco(30, mtmo_conditions), special_reason=sr, band=band)
    res = run_hard_checks(kb, facts, fw, ok)
    assert "error" not in outcomes(res, "audit.category_1_2") + outcomes(res, "audit.mandatory_minimum")
    combined = proposal(disposition_type="mtmo", term_months=6, npp_months=None, cco=cco(30, mtmo_conditions, "on_release_from_prison"),
                        special_reason=sr, band=band)
    res = run_hard_checks(kb, facts, fw, combined)
    assert "caution" in outcomes(res, "audit.category_1_2")
    assert any("flag.mtmo_combination_sentence" in r.flags for r in res)
    plain_cco = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(), special_reason=sr, band=band)
    res = run_hard_checks(kb, facts, fw, plain_cco)
    assert "error" in outcomes(res, "audit.mandatory_minimum")


def test_non_parole_rules(kb):
    facts, fw = setup(kb)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=30, npp_months=None)), "audit.non_parole_rules")
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=10, npp_months=6)), "audit.non_parole_rules")
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=24, npp_months=20)), "audit.non_parole_rules")
    assert "caution" in outcomes(run_hard_checks(kb, facts, fw, proposal(term_months=48, npp_months=40)), "audit.non_parole_rules")


def test_cco_limits(kb):
    facts, fw = setup(kb, presentence_detention_days=30)
    band = schemas.Band(disposition_types=["imprisonment_and_cco"], term_low_months=6, term_high_months=14,
                        npp_low_months=None, npp_high_months=None, cco_low_months=12, cco_high_months=24,
                        main_uncertainties=[])
    over = proposal(disposition_type="imprisonment_and_cco", term_months=14, npp_months=None,
                    cco=cco(24, commences="on_release_from_prison"), band=band)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, over), "audit.cco_limits")      # 14 months − 1 month PSD > 12
    ok = proposal(disposition_type="imprisonment_and_cco", term_months=13, npp_months=None,
                  cco=cco(24, commences="on_release_from_prison"), band=band)
    near = outcomes(run_hard_checks(kb, facts, fw, ok), "audit.cco_limits")                   # 13 months − 30 days
    assert "error" not in near and "caution" in near
    facts31, fw31 = setup(kb, presentence_detention_days=40)
    res31 = run_hard_checks(kb, facts31, fw31, ok)
    assert "error" not in outcomes(res31, "audit.cco_limits")
    assert not any(r.title == "Combination sentence at the one-year limit" for r in res31)
    assert any(r.title == "Excluded offence?" and "flag.cco_excluded_offence_undefined" in r.flags for r in res31)
    bad_curfew = cco(24, [schemas.CCOCondition(kind="curfew", details="", hours=None, completion_months=None,
                                               hours_per_day=14, duration_months=8, monitors=None)])
    p = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=bad_curfew,
                 band=schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None,
                                   npp_low_months=None, npp_high_months=None, cco_low_months=12, cco_high_months=24,
                                   main_uncertainties=[]))
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, p), "audit.cco_limits")
    facts2, fw2 = setup(kb, cco_consent="no")
    p2 = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(), band=p.band)
    assert "error" in outcomes(run_hard_checks(kb, facts2, fw2, p2), "audit.cco_limits")


def test_s6aaa(kb):
    facts, fw = setup(kb)
    missing = proposal(plea_reduced_sentence=True)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, missing), "audit.s6aaa_statement")
    stated = proposal(plea_reduced_sentence=True, s6aaa=schemas.NotionalSentence(
        stated=True, type="imprisonment", term_months=48, npp_months=32, cco_months=None, unpaid_hours=None,
        fine_penalty_units=None, conditions_reduced=None))
    assert outcomes(run_hard_checks(kb, facts, fw, stated), "audit.s6aaa_statement") == ["pass"]
    less = proposal(plea_reduced_sentence=True, s6aaa=schemas.NotionalSentence(
        stated=True, type="cco", term_months=None, npp_months=None, cco_months=36, unpaid_hours=None,
        fine_penalty_units=None, conditions_reduced=None))
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, less), "audit.s6aaa_statement")


def test_conviction_and_alcohol_exclusion_order(kb):
    facts, fw = setup(kb, intoxicated_by_liquor="yes", intoxication_contributed="yes", previous_aeo="no")
    res = run_hard_checks(kb, facts, fw, proposal())
    assert "error" in outcomes(res, "audit.mandatory_ancillary_orders")
    with_aeo = proposal(ancillary_orders=[schemas.AncillaryOrder(type="alcohol_exclusion_order", status="mandatory",
                                                                 months=24, details="", item_ids=[])])
    assert "error" not in outcomes(run_hard_checks(kb, facts, fw, with_aeo), "audit.mandatory_ancillary_orders")
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(conviction_recorded=False)),
                               "audit.conviction_requirements")


def test_licence_order_when_driving_under_the_influence(kb):
    facts, fw = setup(kb, arising_from_driving="yes", alcohol_influenced_driving="yes", licence_status="victorian")
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal()), "audit.mandatory_ancillary_orders")
    ok = proposal(ancillary_orders=[schemas.AncillaryOrder(type="licence_cancellation_and_disqualification",
                                                           status="mandatory", months=12, details="", item_ids=[])])
    assert "error" not in outcomes(run_hard_checks(kb, facts, fw, ok), "audit.mandatory_ancillary_orders")


def test_citations_in_results_exist(kb):
    facts, fw = setup(kb)
    for r in run_hard_checks(kb, facts, fw, proposal(plea_reduced_sentence=True)):
        assert all(kb.exists(c) for c in r.cite)


# ── Tests added after the first independent review ──────────────────────────

def mtmo_cco(term_release=False):
    conditions = [schemas.CCOCondition(kind="judicial_monitoring", details="", hours=None, completion_months=None,
                                       hours_per_day=None, duration_months=None, monitors=None),
                  schemas.CCOCondition(kind="treatment_and_rehabilitation", details="", hours=None,
                                       completion_months=None, hours_per_day=None, duration_months=None, monitors=None)]
    return cco(30, conditions, "on_release_from_prison" if term_release else "on_making")


SR = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="report", stated_in_writing=True)
MTMO_BAND = schemas.Band(disposition_types=["mtmo"], term_low_months=None, term_high_months=None, npp_low_months=None,
                         npp_high_months=None, cco_low_months=24, cco_high_months=36, main_uncertainties=[])


def test_mtmo_comparisons_are_cautions(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", mental_impairment_evidence="yes",
                      indication_given=True, indication_type="cco", indication_plea_entered_on_it="yes",
                      indication_same_judge="yes")
    p = proposal(disposition_type="mtmo", term_months=10, npp_months=None, cco=mtmo_cco(True), special_reason=SR,
                 band=MTMO_BAND, plea_reduced_sentence=True,
                 s6aaa=schemas.NotionalSentence(stated=True, type="imprisonment", term_months=60, npp_months=40,
                                                cco_months=None, unpaid_hours=None, fine_penalty_units=None,
                                                conditions_reduced=None))
    res = run_hard_checks(kb, facts, fw, p)
    assert outcomes(res, "audit.s6aaa_statement") == ["caution"]
    assert "error" not in outcomes(res, "audit.sentence_indication")


def test_mtmo_needs_a_report(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", mental_impairment_evidence="no", cooperation="past_assistance")
    sr = schemas.SpecialReason(found=True, limb="assistance", evidential_basis="gave evidence", stated_in_writing=True)
    p = proposal(disposition_type="mtmo", term_months=None, npp_months=None, cco=mtmo_cco(), special_reason=sr,
                 band=MTMO_BAND)
    res = run_hard_checks(kb, facts, fw, p)
    assert any(r.title == "Therapeutic order conditions" and r.outcome == "error" for r in res)


def test_protected_official_before_2017(kb):
    facts, fw = setup(kb, S18I, offence_date=date(2015, 6, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="yes")
    assert fw.category is None                       # Category 1/2 only from 20 March 2017
    assert fw.mnpp_months == 6 and fw.alternatives   # the minimum, but its commencement is unconfirmed
    p = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(),
                 band=schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None,
                                   npp_low_months=None, npp_high_months=None, cco_low_months=12, cco_high_months=24,
                                   main_uncertainties=[]))
    res = run_hard_checks(kb, facts, fw, p)
    assert "error" not in outcomes(res, "audit.category_1_2") + outcomes(res, "audit.mandatory_minimum")
    assert "unresolved" in outcomes(res, "audit.mandatory_minimum")


def test_gross_violence_protected_unconfirmed(kb):
    facts, fw = setup(kb, S15A, victim_protected_worker="yes", offender_knew_or_reckless="yes")
    assert fw.mnpp_months == 60
    between = proposal(term_months=84, npp_months=52,
                       band=schemas.Band(disposition_types=["imprisonment"], term_low_months=78, term_high_months=96,
                                         npp_low_months=52, npp_high_months=62, cco_low_months=None,
                                         cco_high_months=None, main_uncertainties=[]))
    res = run_hard_checks(kb, facts, fw, between)
    assert "error" not in outcomes(res, "audit.mandatory_minimum") and "unresolved" in outcomes(res, "audit.mandatory_minimum")
    below = proposal(term_months=72, npp_months=40, band=between.band)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, below), "audit.mandatory_minimum")   # below 48 on every reading


def test_accessory_needs_minor_involvement_proved(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", accessory_only="yes")
    p = proposal(term_months=30, npp_months=12, band=schemas.Band(
        disposition_types=["imprisonment"], term_low_months=24, term_high_months=36, npp_low_months=12,
        npp_high_months=24, cco_low_months=None, cco_high_months=None, main_uncertainties=[]))
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, p), "audit.mandatory_minimum")
    facts2, fw2 = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                        protected_scheme_confirmed="yes", accessory_only="yes", minor_involvement_proved="yes")
    assert "error" not in outcomes(run_hard_checks(kb, facts2, fw2, p), "audit.mandatory_minimum")


def test_justice_plan_and_curfew_terms(kb):
    facts, fw = setup(kb)
    band = schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=24, cco_high_months=36, main_uncertainties=[])
    jp = cco(36, [schemas.CCOCondition(kind="justice_plan", details="", hours=None, completion_months=None,
                                       hours_per_day=None, duration_months=30, monitors=None)])
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=jp, band=band))
    assert any(r.title == "Justice plan condition" and r.outcome == "error" for r in res)
    curfew = cco(24, [schemas.CCOCondition(kind="curfew", details="", hours=None, completion_months=None,
                                           hours_per_day=None, duration_months=None, monitors=None)])
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=curfew, band=band))
    assert any(r.title == "Curfew terms" and r.outcome == "caution" and r.app_policy for r in res)


def test_exception_must_fit_the_facts(kb):
    facts, fw = setup(kb, S16, cooperation="admissions_only")
    assert fw.category == 2
    band = schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=24, cco_high_months=36, main_uncertainties=[])
    p = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(), band=band,
                 category2_exception=schemas.Category2Exception(relied_on=True, exception="assistance",
                                                                evidential_basis="admissions"))
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, p), "audit.category_1_2")
    p2 = p.model_copy(update={"category2_exception": schemas.Category2Exception(
        relied_on=True, exception="therapeutic_order", evidential_basis="report")})
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, p2), "audit.category_1_2")


def test_alcohol_exclusion_order_edges(kb):
    facts, fw = setup(kb, intoxicated_by_liquor="yes", intoxication_contributed="yes", previous_aeo="no")
    band = schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=12, cco_high_months=24, main_uncertainties=[])
    unknown_conviction = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(), band=band,
                                  conviction_recorded=None)
    res = run_hard_checks(kb, facts, fw, unknown_conviction)
    assert "caution" in outcomes(res, "audit.mandatory_ancillary_orders")
    conflict = proposal(disposition_type="cco", term_months=None, npp_months=None, band=band,
                        cco=cco(24, [schemas.CCOCondition(kind="alcohol_exclusion", details="", hours=None,
                                                          completion_months=None, hours_per_day=None,
                                                          duration_months=None, monitors=None)]),
                        ancillary_orders=[schemas.AncillaryOrder(type="alcohol_exclusion_order", status="mandatory",
                                                                 months=24, details="", item_ids=[])])
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, conflict), "audit.mandatory_ancillary_orders")


def test_reasons_relying_on_an_assumed_adverse_fact(kb):
    facts, fw = setup(kb)
    facts.facts = [{"id": "F1", "text": "The victim was elderly", "topic": "victim", "bearing": "aggravating",
                    "basis": "assumption", "source_quote": "", "quote_verified": None}]
    p = proposal(reasons=[schemas.ReasonParagraph(heading="Gravity", text="…", item_ids=[], passage_ids=[],
                                                  fact_ids=["F1"], case_ids=[])])
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, p), "audit.facts_and_proof")


def test_imprisonment_needs_a_recorded_conviction(kb):
    facts, fw = setup(kb)
    assert "error" in outcomes(run_hard_checks(kb, facts, fw, proposal(conviction_recorded=None)),
                               "audit.conviction_requirements")
