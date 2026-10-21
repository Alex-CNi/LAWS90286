"""Tests of the framework's readings and the other points found in the second
code review (R2): which results are compared across readings, the accessory
rule for each reading, the user's "the scheme did not apply" answer, knowledge
"unknown" versus "no", and several hard-check edge cases."""

import random
import typing
from datetime import date

import pytest

from core import config, schemas
from core.checks import run_hard_checks
from tests.test_checks import S15A, S16, S17, S18I, cco, proposal, setup

YNU = ["yes", "no", "unknown"]
NO_SR = schemas.SpecialReason(found=False, limb=None, evidential_basis="", stated_in_writing=False)
SR_MI = schemas.SpecialReason(found=True, limb="mental_impairment", evidential_basis="psychiatric report",
                              stated_in_writing=True)


def cond(kind, **kw):
    d = dict(kind=kind, details="", hours=None, completion_months=None, hours_per_day=None, duration_months=None,
             monitors=None)
    d.update(kw)
    return schemas.CCOCondition(**d)


MTMO = dict(disposition_type="mtmo", term_months=None, npp_months=None,
            cco=cco(30, [cond("judicial_monitoring"), cond("treatment_and_rehabilitation")]))


def errors(res):
    return [r for r in res if r.outcome == "error"]


def titles(res, outcome=None):
    return [r.title for r in res if outcome is None or r.outcome == outcome]


# ─────────────────────────────────────────────────────────────────────────────
# Only the reading-dependent checks are compared across readings (R2-M1, R2-m1)
# ─────────────────────────────────────────────────────────────────────────────
def test_an_open_reading_never_downgrades_errors_that_do_not_depend_on_it(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes")   # scheme: unknown
    assert [a.kind for a in fw.alternatives] == ["open"]
    res = run_hard_checks(kb, facts, fw, proposal(term_months=300, npp_months=200, conviction_recorded=None))
    assert {"audit.maximum_applicable", "audit.conviction_requirements"} <= {r.check_id for r in errors(res)}
    # a non-parole period below the 3-year minimum depends on the scheme: unresolved, not an error
    res = run_hard_checks(kb, facts, fw, proposal(term_months=48, npp_months=30))
    assert "unresolved" in [r.outcome for r in res if r.check_id == "audit.mandatory_minimum"]
    assert not errors(res)


def _random_proposal(rng: random.Random) -> schemas.Proposal:
    kinds = list(typing.get_args(schemas.DispositionType))

    def opt(x, p=0.3):
        return None if rng.random() < p else x
    conditions = [cond(rng.choice(list(typing.get_args(schemas.CCOConditionKind))), hours=opt(rng.randint(0, 800)),
                       completion_months=opt(rng.randint(0, 30)), duration_months=opt(rng.randint(0, 40)))
                  for _ in range(rng.randint(0, 3))]
    t = rng.choice(kinds)
    return proposal(
        disposition_type=t, term_months=opt(rng.randint(0, 300)), npp_months=opt(rng.randint(0, 250)),
        cco=opt(schemas.CCOTerms(months=rng.randint(0, 70), conditions=conditions, months_after_order=None,
                                 commences=rng.choice(["on_making", "on_release_from_prison", "not_stated"])), 0.4),
        fine_penalty_units=opt(rng.randint(0, 3000), 0.8), conviction_recorded=rng.choice([True, False, None]),
        special_reason=rng.choice([NO_SR, SR_MI, schemas.SpecialReason(found=True, limb=None, evidential_basis="",
                                                                       stated_in_writing=False)]),
        category2_exception=schemas.Category2Exception(relied_on=rng.random() < 0.3, exception=opt("impaired_culpability"),
                                                       evidential_basis=rng.choice(["", "report"])),
        plea_reduced_sentence=rng.random() < 0.5)


@pytest.mark.parametrize("oid", config.IN_SCOPE_OFFENCES)
def test_an_unconfirmed_scheme_errs_exactly_when_both_readings_err(kb, oid):
    """Errors when the scheme's application is 'unknown' (two readings) iff the
    sentence errs both when it applied ('yes') and when it did not ('no')."""
    rng = random.Random(oid)
    for _ in range(40):
        p = _random_proposal(rng)
        kw = dict(victim_protected_worker="yes", offender_knew_or_reckless="yes",
                  mental_impairment_evidence=rng.choice(YNU), cco_consent=rng.choice(["yes", "no"]),
                  offence_date=date(rng.choice([2015, 2020, 2024]), 5, 1))
        err = {}
        for scheme in ("yes", "no", "unknown"):
            facts, fw = setup(kb, oid, protected_scheme_confirmed=scheme, **kw)
            err[scheme] = errors(run_hard_checks(kb, facts, fw, p))
        assert bool(err["unknown"]) == bool(err["yes"] and err["no"]), (oid, p.disposition_type)


def test_a_breach_on_every_reading_under_different_checks_is_an_error(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      mental_impairment_evidence="no")
    res = run_hard_checks(kb, facts, fw, proposal(**MTMO, special_reason=SR_MI))
    assert "Therapeutic order conditions" in titles(res, "error")              # main reading (scheme applied)
    assert "Category 2 — no exception established" in titles(res, "unresolved")  # the other reading's own breach


# ─────────────────────────────────────────────────────────────────────────────
# Each reading has its own accessory rule (R2-m2)
# ─────────────────────────────────────────────────────────────────────────────
def test_the_accessory_rule_is_worked_out_for_each_reading(kb):
    facts, fw = setup(kb, S15A, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      accessory_only="yes", minor_involvement_proved="no")
    assert fw.mnpp_months == 60 and not fw.mnpp_excluded                    # protected official: no exclusion
    other = fw.alternatives[0]
    assert other.mm_group == "gross_violence" and other.mnpp_excluded       # gross violence: an accessory escapes it
    res = run_hard_checks(kb, facts, fw, proposal(term_months=60, npp_months=30))
    assert "unresolved" in [r.outcome for r in res if r.check_id == "audit.mandatory_minimum"] and not errors(res)

    facts, fw = setup(kb, S15A, victim_protected_worker="unknown", offender_knew_or_reckless="unknown",
                      accessory_only="yes", minor_involvement_proved="no")
    assert fw.mnpp_excluded                                                 # main: gross violence, excluded
    res = run_hard_checks(kb, facts, fw, proposal(term_months=60, npp_months=30))
    assert "If proved: Below the minimum non-parole period" in titles(res, "caution")

    facts, fw = setup(kb, S16, offence_date=date(2016, 5, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="unknown", accessory_only="yes", minor_involvement_proved="yes")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco()))
    assert not [r for r in res if r.check_id == "audit.mandatory_minimum" and r.title.startswith("If proved")]


# ─────────────────────────────────────────────────────────────────────────────
# "The scheme did not apply", knowledge unknown v no, the app's own reading
# (R2-m3, R2-m4, R2-n5, R2-n1, R2-n7)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_scheme_did_not_apply_answer_applies_to_every_reading(kb):
    for kw in (dict(victim_protected_worker="unknown", offender_knew_or_reckless="unknown"),
               dict(victim_protected_worker="yes", offender_knew_or_reckless="no"),
               dict(victim_protected_worker="yes", offender_knew_or_reckless="yes")):
        facts, fw = setup(kb, S17, protected_scheme_confirmed="no", **kw)
        assert fw.alternatives == [] and fw.category is None and fw.mnpp_months is None, kw
    facts, fw = setup(kb, S15A, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="no", mental_impairment_evidence="yes")
    assert fw.applied_protected == "no" and fw.mm_group == "gross_violence"
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(),
                                                  special_reason=SR_MI))
    flags = {f for r in res for f in r.flags}
    assert "flag.category1_special_reason" in flags and "flag.gross_violence_protected_special_reason" not in flags


def test_knowledge_unknown_is_not_treated_more_favourably_than_no(kb):
    p = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco())
    found = {}
    for knew in ("no", "unknown"):
        facts, fw = setup(kb, S17, victim_protected_worker="yes", offender_knew_or_reckless=knew)
        found[knew] = [r for r in run_hard_checks(kb, facts, fw, p)
                       if r.check_id == "audit.category_1_2" and r.outcome == "unresolved"]
    assert found["no"] and found["unknown"]
    assert all(r.app_policy for r in found["no"] + found["unknown"])          # the status-alone reading is the app's


def test_the_status_alone_reading_appears_only_where_it_changes_the_category(kb):
    facts, fw = setup(kb, S17, offence_date=date(2015, 5, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="no")
    assert not fw.alternatives                                              # no category before 20 March 2017
    gate = next(g for g in fw.gates if g.title == "Victim on duty, knowledge not proved")
    assert "Category 1" not in gate.message


def test_every_if_proved_breach_is_reported_and_reads_properly(kb):
    facts, fw = setup(kb, S16, victim_protected_worker="yes", offender_knew_or_reckless="unknown",
                      mental_impairment_evidence="no")
    bad_sr = schemas.SpecialReason(found=True, limb=None, evidential_basis="", stated_in_writing=False)
    exc = schemas.Category2Exception(relied_on=True, exception="impaired_culpability", evidential_basis="report")
    res = run_hard_checks(kb, facts, fw, proposal(**MTMO, special_reason=bad_sr, category2_exception=exc))
    assert {"If proved: Special reason not properly identified", "If proved: Therapeutic order conditions"} \
        <= set(titles(res, "caution"))
    assert not [r for r in res if "If if" in r.message]


# ─────────────────────────────────────────────────────────────────────────────
# Hard-check edge cases (R2-m7, R2-m8, R2-m9, R2-n6, R1 m6)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_justice_plan_without_a_period_is_a_caution(kb):
    facts, fw = setup(kb)
    jp = cco(36, [cond("justice_plan")])
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=jp))
    assert any(r.title == "Justice plan terms" and r.outcome == "caution" and r.app_policy for r in res)
    assert "Justice plan condition" not in titles(res, "error")


def test_an_mtmo_needs_a_protected_official_and_a_special_reason(kb):
    facts, fw = setup(kb, S17)                                              # victim not a protected official
    for sr in (NO_SR, SR_MI):
        assert "MTMO not available" in titles(run_hard_checks(kb, facts, fw, proposal(**MTMO, special_reason=sr)),
                                               "error")
    facts, fw = setup(kb, S17, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", mental_impairment_evidence="yes")
    assert "MTMO not available" not in titles(run_hard_checks(kb, facts, fw, proposal(**MTMO, special_reason=SR_MI)))


def test_a_combination_sentence_meeting_the_minimum_is_not_an_error(kb):
    facts, fw = setup(kb, S18I, offence_date=date(2015, 5, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="yes", protected_scheme_confirmed="yes")
    assert fw.category is None and fw.mnpp_months == 6
    combo = dict(disposition_type="imprisonment_and_cco", term_months=12, cco=cco(24, commences="on_release_from_prison"))
    res = run_hard_checks(kb, facts, fw, proposal(**combo, npp_months=6))
    assert not [r for r in res if r.check_id == "audit.mandatory_minimum" and r.outcome == "error"]
    assert "Below the minimum non-parole period" in titles(run_hard_checks(kb, facts, fw, proposal(**combo,
                                                                                                    npp_months=None)))


def test_the_dispositions_note_for_a_protected_official_before_2017(kb):
    facts, fw = setup(kb, S16, offence_date=date(2015, 6, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="yes", protected_scheme_confirmed="yes")
    assert fw.category is None and fw.mnpp_months == 36
    assert "mandatory treatment and monitoring order" in fw.dispositions_note and "9.1.2.3" in fw.dispositions_note


def test_reasons_relying_on_an_assumed_or_unproved_mitigating_fact(kb):
    facts, fw = setup(kb)
    facts.facts = [{"id": "F1", "text": "genuine remorse", "topic": "other", "bearing": "mitigating",
                    "basis": "assumption", "source_quote": "", "quote_verified": None},
                   {"id": "F2", "text": "a difficult childhood", "topic": "other", "bearing": "mitigating",
                    "basis": "disputed", "source_quote": "", "quote_verified": None}]
    reasons = [schemas.ReasonParagraph(heading="h", text="t", item_ids=[], passage_ids=[], fact_ids=["F1", "F2"], case_ids=[])]
    res = run_hard_checks(kb, facts, fw, proposal(reasons=reasons))
    assert "Reasons rely on an assumed mitigating fact (F1)" in titles(res, "error")
    assert "Mitigating fact needs evidence (F2)" in titles(res, "caution")


# ─────────────────────────────────────────────────────────────────────────────
# Third review (R3)
# ─────────────────────────────────────────────────────────────────────────────
def test_a_straddling_period_combines_with_the_other_readings(kb):
    period = dict(offence_date=date(2016, 12, 1), offence_date_end=date(2017, 8, 1))
    cco_only = proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco())
    facts, fw = setup(kb, S17, victim_protected_worker="yes", offender_knew_or_reckless="no",
                      protected_scheme_confirmed="yes", **period)
    res = run_hard_checks(kb, facts, fw, cco_only)
    assert [r for r in res if r.check_id == "audit.category_1_2" and r.outcome == "unresolved" and r.app_policy]
    gate = next(g for g in fw.gates if g.title == "Victim on duty, knowledge not proved")
    assert "Category 1" in gate.message and "(app policy)" in gate.message and not gate.app_policy
    facts, fw = setup(kb, S18I, victim_protected_worker="unknown", offender_knew_or_reckless="no",
                      protected_scheme_confirmed="yes", **period)
    assert [r for r in run_hard_checks(kb, facts, fw, cco_only) if r.title.startswith("If proved")]


def test_a_special_reason_must_be_properly_identified_even_when_the_minimum_is_excluded(kb):
    facts, fw = setup(kb, S17, victim_protected_worker="yes", offender_knew_or_reckless="yes",
                      protected_scheme_confirmed="yes", accessory_only="yes", minor_involvement_proved="yes",
                      mental_impairment_evidence="yes")
    assert fw.mnpp_excluded
    bare = schemas.SpecialReason(found=True, limb=None, evidential_basis="", stated_in_writing=False)
    res = run_hard_checks(kb, facts, fw, proposal(**MTMO, special_reason=bare))
    assert "Special reason not properly identified" in titles(res, "error")
    assert any(r.title == "Special reason with the minimum excluded" and r.app_policy for r in res)
    res = run_hard_checks(kb, facts, fw, proposal(term_months=36, npp_months=24))     # imprisonment: no special reason needed
    assert "Special reason not properly identified" not in titles(res)


def test_a_cco_on_a_gross_violence_special_reason_shows_the_excluded_offence_caution(kb):
    facts, fw = setup(kb, S15A, mental_impairment_evidence="yes")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="cco", term_months=None, npp_months=None, cco=cco(),
                                                  special_reason=SR_MI))
    assert any("flag.cco_excluded_offence_undefined" in r.flags for r in res)


def test_no_non_parole_period_is_described_plainly(kb):
    facts, fw = setup(kb, S18I, offence_date=date(2015, 5, 1), victim_protected_worker="yes",
                      offender_knew_or_reckless="yes", protected_scheme_confirmed="yes")
    res = run_hard_checks(kb, facts, fw, proposal(disposition_type="imprisonment_and_cco", term_months=9,
                                                  npp_months=None, cco=cco(24, commences="on_release_from_prison")))
    msg = next(r.message for r in res if r.title == "Below the minimum non-parole period")
    assert msg.startswith("No non-parole period is fixed (none can be for a term under 12 months)") and "(—)" not in msg
