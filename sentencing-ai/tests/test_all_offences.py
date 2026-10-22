"""Every offence in the register: the framework never guesses (guide Part A)."""

from datetime import date

import pytest

from core import config
from core.coverage import NOT_YET_CHECKED, unchecked_features
from core.facts import CaseInput, ConfirmedFacts, validate
from core.framework import build_framework

DATES = [date(1996, 6, 1), date(2010, 6, 1), date(2018, 6, 1), date(2024, 5, 1)]


def fw_for(kb, oid, d=date(2024, 5, 1), **kw):
    case = CaseInput(offence_id=oid, offence_date=d, age_at_offence=30, age_at_sentence=31, narrative="x" * 100, **kw)
    return build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))


def by_name(kb, name):
    return next(oid for oid in config.IN_SCOPE_OFFENCES if kb.offence(oid)["offence"] == name)


def test_every_register_offence_is_offered_once(kb):
    assert len(config.IN_SCOPE_OFFENCES) == len(set(config.IN_SCOPE_OFFENCES)) == 335
    assert set(config.CAUSING_INJURY_OFFENCES) <= set(config.IN_SCOPE_OFFENCES)
    codes = kb.offence_codes()
    assert sorted(codes.values()) == sorted(config.IN_SCOPE_OFFENCES)


@pytest.mark.parametrize("d", DATES)
def test_the_maximum_is_read_or_marked_unresolved(kb, d):
    for oid in config.IN_SCOPE_OFFENCES:
        m = fw_for(kb, oid, d).maximum
        known = m.unresolved or m.life or m.at_large or m.fine_only or (m.months and m.months > 0)
        assert known, f"{oid} on {d}: no maximum and no reason"
        assert m.describe()
        assert set(unchecked_features(kb, oid)) <= set(NOT_YET_CHECKED)


def test_fractional_years_are_not_truncated(kb):
    assert fw_for(kb, by_name(kb, "Taking away a child"), date(1995, 6, 1)).maximum.months == 90   # 7½ years
    assert fw_for(kb, by_name(kb, "Blackmail"), date(1995, 6, 1)).maximum.months == 150           # 12½ years


def test_a_category_clause_not_established_is_not_assumed(kb):
    fw = fw_for(kb, by_name(kb, "Armed robbery"))
    assert fw.category is None and "firearm" in fw.category_unparsed
    assert any(a.kind == "unproved" and a.category == 2 for a in fw.alternatives)
    assert any(g.title == "Category condition not established" for g in fw.gates)


def test_minimums_outside_chapter_25(kb):
    """ss 77B/79A: the 3-year minimum applies; manslaughter and ss 317AC/317AD: while their facts are unknown the
    minimum is not applied on the main reading but shown as "if proved" (mmnpp.* items)."""
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    for name in ("Aggravated home invasion", "Aggravated carjacking"):
        fw = fw_for(kb, by_name(kb, name))
        assert fw.mm_group == "aggravated_hi_cj" and fw.mnpp_months == 36 and fw.category == 1
        facts, fw = setup(kb, by_name(kb, name))
        res = run_hard_checks(kb, facts, fw, proposal(term_months=30, npp_months=18))
        mm = [r for r in res if r.check_id == "audit.mandatory_minimum"]
        assert any(r.outcome == "error" for r in mm)
        assert all("mmnpp.aggravated_home_invasion_carjacking_3y" in r.cite for r in mm if r.outcome == "error")
    fw = fw_for(kb, by_name(kb, "Manslaughter"))
    assert fw.mnpp_months is None and fw.minimums_pending
    unproved = [a for a in fw.alternatives if a.kind == "unproved"]
    assert unproved and unproved[0].mnpp_months == 120 and unproved[0].mm_group == "manslaughter"
    assert any(g.title == "Mandatory minimum — facts not established" for g in fw.gates)
    assert "none on the facts as entered (one applies only if further facts are proved" in fw.text()
    early = fw_for(kb, by_name(kb, "Manslaughter"), date(2013, 6, 1))
    assert not early.minimums_pending and not [a for a in early.alternatives if a.mnpp_months]
    s317ac = next(o for o in config.IN_SCOPE_OFFENCES if "317ac" in o)
    fw = fw_for(kb, s317ac)
    assert fw.mnpp_months is None
    assert any(a.kind == "unproved" and a.mnpp_months == 24 and a.mm_group == "driving_protected" for a in fw.alternatives)
    assert any(a.kind == "open" and a.category == 1 and "flag.driving_protected_category1" in a.flags
               for a in fw.alternatives)
    facts, fw = setup(kb, s317ac)
    res = run_hard_checks(kb, facts, fw, proposal(term_months=30, npp_months=18))
    mm = [r for r in res if r.check_id == "audit.mandatory_minimum"]
    assert mm and not any(r.outcome == "error" for r in mm)          # only "if proved"
    assert any("If proved" in r.title or "if proved" in r.message.lower() for r in mm)


def test_unchecked_features_are_shown(kb):
    fw = fw_for(kb, by_name(kb, "Murder"))
    assert fw.maximum.life
    gap = next((g for g in fw.gates if g.title == "Not fully checked"), None)
    assert gap is None or ("standard sentence" not in gap.message and gap.app_policy)


def test_fine_only_and_relative_maxima(kb):
    cannabis = next(oid for oid in config.IN_SCOPE_OFFENCES if kb.offence(oid)["provision"].startswith("s 75(a)"))
    m = fw_for(kb, cannabis).maximum
    assert m.fine_only and m.fine_penalty_units == 5 and m.fine_source == "printed"
    attempt = next(oid for oid in config.IN_SCOPE_OFFENCES if "321p-1-b" in oid)
    assert fw_for(kb, attempt).maximum.unresolved


def test_body_corporate_rows_need_a_body_corporate(kb):
    """Guide step 14: a "– body corporate" row is refused for a natural person (and the reverse)."""
    oid = next(o for o in config.IN_SCOPE_OFFENCES if "body corporate" in kb.offence(o)["offence"].lower())
    case = CaseInput(offence_id=oid, offence_date=date(2024, 5, 1), age_at_offence=30, age_at_sentence=31,
                     narrative="x" * 100)
    assert any("body corporate form of the offence" in p for p in validate(case, kb))
    case.offender_type = "body_corporate"
    assert not validate(case, kb)


def test_retrieval_follows_the_charge_chapter(kb):
    theft = by_name(kb, "Theft")
    ids = kb.candidates(["gravity"], theft)
    for i in ids:                        # nothing that cites only another offence's chapter (e.g. OHS guidance)
        chapters = {str(s).split(".")[0] for s in kb.meta[i].get("sections") or []}
        assert kb.meta[i].get("offence_ids") or not chapters or not chapters <= set(config.PART_E_CHAPTERS) - {"27"}
    assert any(i.startswith("guide.") and "27" in {str(s).split(".")[0] for s in kb.meta[i].get("sections") or []}
               for i in ids)
    passages = kb.comparable_passages("27")
    assert passages and all(str(kb.chunks[c]["chapter"]) in ("27", "5") for c in passages)


def test_a_run_for_another_chapter(kb, retriever):
    """A whole run (fake OpenAI) for a property offence: the framework, the stages and the checks work."""
    from tests.test_pipeline import KEY
    from tests.fake_openai import FakeOpenAI
    from core.llm import LLM
    from core.pipeline import Run, run_remaining, step_confirm, step_extract
    case = CaseInput(offence_id=by_name(kb, "Burglary"), offence_date=date(2024, 5, 1), court="County Court",
                     plea="guilty", age_at_offence=30, age_at_sentence=31,
                     narrative="The offender broke into a house at night and took a laptop. He pleaded guilty. " * 3)
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = LLM(api_key=KEY, http_client=fake.client())
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    assert run.finished and run.ai_calls >= config.MIN_AI_CALLS_PER_RUN
    assert run.framework.maximum.months == 120                        # Level 5 - 10 years
    assert not any(g.title == "Not fully checked" for g in run.framework.gates)


def test_the_protected_official_questions_apply_only_to_ss_15a_to_18(kb):
    fw = fw_for(kb, by_name(kb, "Theft"), victim_protected_worker="unknown", offender_knew_or_reckless="unknown")
    assert fw.protected_official == "no" and not fw.protected_assumed_no and not fw.alternatives
    assert not any("rotected" in g.title for g in fw.gates)


@pytest.mark.parametrize("name", ["Murder", "Trafficking", "Armed robbery", "Using or attempting to use cannabis or "
                                  "tetrahydrocannabinol", "Contempt of court (County Court / Supreme Court)"])
def test_runs_and_reports_for_other_families(kb, retriever, name):
    """Whole runs (fake OpenAI) for families whose schemes are not all implemented: they finish, the report
    renders, and the gaps are shown rather than guessed."""
    from tests.test_pipeline import KEY
    from tests.fake_openai import FakeOpenAI
    from core.llm import LLM
    from core.pipeline import Run, run_remaining, step_confirm, step_extract
    from core.report import build_blocks, render_docx, render_markdown
    case = CaseInput(offence_id=by_name(kb, name), offence_date=date(2024, 5, 1), court="County Court",
                     plea="guilty", age_at_offence=30, age_at_sentence=31,
                     narrative="The offender committed the offence and pleaded guilty at the first opportunity. " * 3)
    llm = LLM(api_key=KEY, http_client=FakeOpenAI(bad_first_proposal=False).client())
    run = Run(case=case, models=dict(config.DEFAULT_MODELS), efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    run_remaining(run, llm, kb, retriever)
    assert run.finished
    text = render_markdown(build_blocks(run, kb))
    assert run.framework.maximum.describe().split(" — ")[0] in text
    assert render_docx(build_blocks(run, kb))


def test_hard_checks_on_other_offences(kb):
    """The chapter 25 ancillary rules are not applied to other charges; fine-only offences admit no imprisonment."""
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    theft = by_name(kb, "Theft")
    facts, fw = setup(kb, theft, intoxicated_by_liquor="yes", intoxication_contributed="yes", previous_aeo="no",
                      arising_from_driving="yes", alcohol_influenced_driving="yes", licence_status="victorian")
    res = run_hard_checks(kb, facts, fw, proposal())
    anc = [r for r in res if r.check_id == "audit.mandatory_ancillary_orders"]
    assert anc and all(r.outcome == "caution" for r in anc)              # AEO: confirm "relevant offence"; no licence error
    cannabis = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"].startswith("s 75(a)"))
    facts, fw = setup(kb, cannabis)
    res = run_hard_checks(kb, facts, fw, proposal(term_months=3, npp_months=None))
    assert "error" in outcomes_of(res, "audit.maximum_applicable")


def outcomes_of(results, check_id):
    return [r.outcome for r in results if r.check_id == check_id]


def test_arson_combination_sentence_is_a_caution(kb):
    from core.checks import run_hard_checks
    from tests.test_checks import cco, proposal, setup
    from core import schemas
    facts, fw = setup(kb, by_name(kb, "Criminal damage by arson"))
    p = proposal(disposition_type="imprisonment_and_cco", term_months=18, npp_months=None, cco=cco(24),
                 band=schemas.Band(disposition_types=["imprisonment_and_cco"], term_low_months=12, term_high_months=18,
                                   npp_low_months=None, npp_high_months=None, cco_low_months=18, cco_high_months=24,
                                   main_uncertainties=[]))
    p.cco.commences = "on_release_from_prison"
    res = [r for r in run_hard_checks(kb, facts, fw, p) if r.check_id == "audit.cco_limits"]
    assert not any(r.outcome == "error" and "one year" in r.title for r in res)
    assert any("arson" in r.title for r in res)


def test_category1_minimum_special_reason_and_contempt_are_cautions(kb):
    """audit.category_1_2 route (d) reaches ss 77B/79A; contempt errors become cautions (audit.scope, VSM 29.5.2)."""
    from core import schemas
    from core.checks import run_hard_checks
    from tests.test_checks import cco, proposal, setup
    band = schemas.Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=18, cco_high_months=24, main_uncertainties=[])
    sr = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="psychiatric report",
                               stated_in_writing=True)
    facts, fw = setup(kb, by_name(kb, "Aggravated home invasion"))
    assert fw.category == 1
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), special_reason=sr, band=band))
    cat = [r for r in res if r.check_id == "audit.category_1_2"]
    assert cat and not any(r.outcome == "error" for r in cat) and any("category1_special_reason" in f
                                                                       for r in cat for f in r.flags)
    facts, fw = setup(kb, by_name(kb, "Contempt of court (County Court / Supreme Court)"), cco_consent="no")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None,
                                                   cco=cco(24), band=band))
    assert not [r for r in res if r.outcome == "error"]
    assert any("flag.contempt_sentencing_act_and_orders" in r.flags for r in res)


# ── Review R6 (Part A) regressions ──────────────────────────────────────────
def test_historic_tables_need_the_users_maximum(kb):
    """audit.offence_date_and_historic_offending: sexual offences 1 Apr 1959 – 30 Jun 2017 and OHSA 1985 offences
    before 1 Jul 2005 — show the table, ask the user to confirm the maximum (error until confirmed)."""
    rape = by_name(kb, "Rape")
    fw = fw_for(kb, rape, date(2010, 6, 1))
    assert fw.maximum.unresolved and fw.maximum.historic_tables == ["24.1.6.1"]
    gate = next(g for g in fw.gates if g.title == "Historic offending — confirm the maximum")
    assert gate.outcome == "error" and "flag.historic_tables_unstructured" in gate.flags
    assert not any(g.outcome == "pass" and "No text-only historic penalty table" in g.message for g in fw.gates)
    fw = fw_for(kb, rape, date(2010, 6, 1), historic_maximum_months=300)
    assert fw.maximum.months == 300 and fw.maximum.confirmed_by_user
    assert any(g.title == "Historic offending — maximum confirmed" and g.outcome == "caution" for g in fw.gates)
    assert fw_for(kb, by_name(kb, "Sexual assault"), date(2016, 6, 1)).maximum.historic_tables == ["24.1.6.1"]
    assert fw_for(kb, by_name(kb, "Recklessly endangering persons"), date(2003, 6, 1)).maximum.historic_tables == ["31.1.2"]
    early = fw_for(kb, rape, date(1955, 6, 1)).maximum
    assert early.unresolved and "1 April 1959" in early.unresolved and not early.historic_tables
    assert kb.historic_tables(rape, "2017-07-01") is None                      # the current provisions
    assert "24.1.6.1" in kb.section_text("24.1.6.1") or kb.section_text("24.1.6.1")
    # the window 20 March – 30 June 2017: the category is open until confirmed (cat1.historic_sexual_provisions)
    fw = fw_for(kb, rape, date(2017, 5, 1), historic_maximum_months=300)
    assert fw.category is None and any(a.kind == "open" and a.category == 1 for a in fw.alternatives)
    case = CaseInput(offence_id=rape, offence_date=date(2010, 6, 1), age_at_offence=30, age_at_sentence=31,
                     narrative="x" * 100)
    assert not any("silent" in p for p in validate(case, kb))


def test_level_and_term_disagree(kb):
    """audit.maximum_applicable (8): error above the higher figure, unresolved between; before 1 September 1997
    the printed term is used and flagged (flag.historic_level_scale)."""
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    loiter = by_name(kb, "Loitering near schools etc. by sexual offender")
    facts, fw = setup(kb, loiter)
    assert fw.maximum.months == 72 and fw.maximum.scale_months == 60 and fw.maximum.ceiling_months == 72
    assert outcomes_of(run_hard_checks(kb, facts, fw, proposal(term_months=66, npp_months=48)),
                       "audit.maximum_applicable") == ["unresolved"]
    assert "error" in outcomes_of(run_hard_checks(kb, facts, fw, proposal(term_months=73, npp_months=48)),
                                  "audit.maximum_applicable")
    fw = fw_for(kb, by_name(kb, "Kidnapping (for ransom or gain)"), date(1995, 6, 1))
    assert fw.maximum.historic_period and fw.maximum.scale_months is None and fw.maximum.months == 240
    gate = next(g for g in fw.gates if g.check_id == "audit.level_term_mismatch")
    assert "flag.historic_level_scale" in gate.flags and "before 1 September 1997" in gate.message


def test_pre_1997_manslaughter_is_not_refused(kb):
    for name, months in (("Manslaughter", 180), ("Suicide pact manslaughter", 90)):
        oid = by_name(kb, name)
        case = CaseInput(offence_id=oid, offence_date=date(1995, 6, 1), age_at_offence=30, age_at_sentence=31,
                         narrative="x" * 100)
        assert not any("silent" in p for p in validate(case, kb))
        assert fw_for(kb, oid, date(1995, 6, 1)).maximum.months == months


def test_protected_official_answers_do_not_reach_other_charges(kb):
    from core.facts import case_details_text
    case = CaseInput(offence_id=by_name(kb, "Theft"), offence_date=date(2024, 5, 1), victim_protected_worker="yes",
                     offender_knew_or_reckless="yes", protected_scheme_confirmed="yes", narrative="x" * 100)
    text = case_details_text(case, kb)
    assert "not applicable to this charge" in text and "offender knew or was reckless" not in text
    assert "not applicable to this charge" in fw_for(kb, by_name(kb, "Theft")).text()


def test_alcohol_exclusion_order_relevant_offences(kb):
    """VSM 20.1.1 names murder or manslaughter and the causing injury offences; other charges: confirm."""
    from core import schemas
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    drunk = dict(intoxicated_by_liquor="yes", intoxication_contributed="yes", previous_aeo="no")
    for name in ("Murder", "Manslaughter", "Negligently causing serious injury"):
        facts, fw = setup(kb, by_name(kb, name), **drunk)
        res = run_hard_checks(kb, facts, fw, proposal())
        assert any(r.title == "Alcohol exclusion order missing" and r.outcome == "error" for r in res), name
    aeo = schemas.AncillaryOrder(type="alcohol_exclusion_order", status="available", months=12, details="",
                                 item_ids=[])
    facts, fw = setup(kb, by_name(kb, "Theft"), motor_vehicle_stolen="no", **drunk)
    res = [r for r in run_hard_checks(kb, facts, fw, proposal(ancillary_orders=[aeo]))
           if r.check_id == "audit.mandatory_ancillary_orders"]
    assert [r.title for r in res] == ["Alcohol exclusion order — relevant offence?"]


def test_contempt_keeps_its_own_printed_maximum(kb):
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    from core import schemas
    oid = by_name(kb, "Contempt of Tribunal (Mental Health Act)")
    facts, fw = setup(kb, oid)
    assert fw.maximum.fine_only and fw.maximum.fine_penalty_units == 120
    band = schemas.Band(disposition_types=["fine"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=None, cco_high_months=None, main_uncertainties=[])
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="fine", term_months=None, npp_months=None,
                                                   fine_penalty_units=121, band=band))
    assert any(r.title == "Fine above the maximum" and r.outcome == "error" for r in res)


def test_category_clause_at_the_end_of_a_straddling_period(kb):
    case = CaseInput(offence_id=by_name(kb, "Armed robbery"), offence_date=date(2017, 1, 1),
                     offence_date_end=date(2017, 6, 1), age_at_offence=30, age_at_sentence=31, narrative="x" * 100)
    from core.framework import build_framework
    fw = build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))
    assert "firearm" in fw.category_unparsed
    assert any(a.kind == "unproved" and a.category == 2 and "later part" in a.label for a in fw.alternatives)
    assert any(g.title == "Category condition not established" for g in fw.gates)


def test_flags_confined_to_their_own_charges(kb):
    s17 = config.CAUSING_INJURY_OFFENCES[3]
    assert "flag.home_invasion_mandatory_scheme_fn4044" not in fw_for(kb, s17).flags
    assert "flag.home_invasion_mandatory_scheme_fn4044" not in fw_for(kb, by_name(kb, "Manslaughter")).flags


def test_open_category_points_and_unlabelled_fines(kb):
    from core.checks import run_hard_checks
    from tests.test_checks import proposal, setup
    from core import schemas
    fw = fw_for(kb, by_name(kb, "Suicide pact manslaughter"))
    assert fw.category is None and any(a.kind == "open" and a.category == 2 and "flag.suicide_pact_category2" in a.flags
                                       for a in fw.alternatives)
    band = schemas.Band(disposition_types=["fine"], term_low_months=None, term_high_months=None, npp_low_months=None,
                        npp_high_months=None, cco_low_months=None, cco_high_months=None, main_uncertainties=[])
    facts, fw = setup(kb, by_name(kb, "Workplace manslaughter (after 1 July 2020)"))
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="fine", term_months=None, npp_months=None,
                                                   fine_penalty_units=1000, band=band))
    assert any("flag.fine_unlabelled_life_25_maximum" in r.flags and r.outcome == "caution" for r in res)


def test_aliases_coverage_and_offender_status(kb):
    cd = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["offence"] == "Conspiracy to defraud")
    assert kb.offence_chapters(cd) == {"27", "32"}
    assert "flag.inchoate_life_maximum_footnotes" in kb.flags_for_offence(cd)
    rsa = next(o for o in config.IN_SCOPE_OFFENCES if kb.offence(o)["provision"] == "RSA s 61(3)")
    assert not unchecked_features(kb, rsa) and fw_for(kb, rsa).licence.group == "rsa_s61_3"
    burglary = by_name(kb, "Burglary")
    assert any(g.title == "Offender-status schemes not checked" for g in fw_for(kb, burglary).gates)    # priors unknown
    assert not any(g.title == "Offender-status schemes not checked"
                   for g in fw_for(kb, burglary, prior_convictions="none").gates)


def test_the_related_tier_is_the_same_section_of_the_register(kb):
    import core.cases as cs
    from tests.test_cases_db import small_db
    s17 = "crimes-act-s-17-recklessly-causing-serious-injury"
    assert cs.related_offences(kb, s17) == {o for o in config.IN_SCOPE_OFFENCES
                                            if kb.offence(o)["section"] == "25.1.1"} - {s17}
    robbery = cs.related_offences(kb, by_name(kb, "Armed robbery"))
    assert by_name(kb, "Burglary") in robbery and by_name(kb, "Aggravated home invasion") in robbery
    assert by_name(kb, "Theft") not in robbery                                     # 27.1.1.1, not 27.1.1.2
    for name in ("Theft", "Murder"):                     # no injury-offence cases as yardsticks for these charges
        oid = by_name(kb, name)
        comp = cs.select_comparables(small_db(), oid, query_text="glass hotel face scar",
                                     related=cs.related_offences(kb, oid))
        assert not comp.selected and "none of the" in comp.note and "chapter 25" not in comp.note
