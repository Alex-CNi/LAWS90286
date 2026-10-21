"""Property test (review R3): the merged reading-dependent results agree with the full
cross-product of the open points, each built as a single-reading framework.

Open points (proved facts): the scheme's commencement (scheme "unknown"), Category 1 on
the victim's status alone (victim "yes", knowledge not "yes"), and which end of an offence
period straddling 20 March 2017 governs. "If proved": the "unknown" limbs taken as proved.
"""

import itertools
import random
from datetime import date

import pytest

from core import config
from core.checks import READING_CHECKS, Ctx, merge_readings
from core.framework import CATEGORY_START, make_reading
from tests.test_checks import setup
from tests.test_readings import YNU, _random_proposal


def _combos(case, proved: bool):
    v, k, s = case.victim_protected_worker, case.offender_knew_or_reckless, case.protected_scheme_confirmed
    vp, kp = (v == "yes", k == "yes") if proved else (v != "no", k != "no")
    d = case.offence_date.isoformat()
    de = case.offence_date_end.isoformat() if case.offence_date_end else None
    for sch, alone, dt in itertools.product([True] if s == "yes" else [False] if s == "no" else [True, False],
                                            [True, False] if (vp and not kp) else [False],
                                            [d, de] if (de and d < CATEGORY_START <= de) else [d]):
        yield ("yes" if vp and kp and sch else "no"), ("yes" if sch and vp and (kp or alone) else "no"), dt


def _errs(kb, facts, fw, p, reading):
    ctx = Ctx(kb=kb, facts=facts, fw=fw.under(reading), p=p)
    return any(r.outcome == "error" for c in READING_CHECKS for r in c(ctx))


@pytest.mark.parametrize("seed", range(4))
def test_merged_readings_match_the_cross_product(kb, seed):
    rng = random.Random(seed)
    starts = [date(2014, 6, 1), date(2016, 12, 1), date(2017, 3, 20), date(2024, 5, 1)]
    for _ in range(400):
        d = rng.choice(starts)
        end = rng.choice([None, None, date(2017, 8, 1), date(2025, 1, 1)])
        kw = dict(offence_date=d, offence_date_end=end if end and end >= d else None,
                  victim_protected_worker=rng.choice(YNU), offender_knew_or_reckless=rng.choice(YNU),
                  protected_scheme_confirmed=rng.choice(YNU), accessory_only=rng.choice(YNU),
                  minor_involvement_proved=rng.choice(YNU), mental_impairment_evidence=rng.choice(YNU))
        oid = rng.choice(config.IN_SCOPE_OFFENCES)
        facts, fw = setup(kb, oid, **kw)
        p = _random_proposal(rng)
        row, case = kb.offence(oid), facts.case
        truth = {w: [_errs(kb, facts, fw, p, make_reading(row, case, "x", "x", mp, dt, category_protected=cp))
                     for mp, cp, dt in set(_combos(case, w == "proved"))] for w in ("proved", "if_proved")}
        ctx = Ctx(kb=kb, facts=facts, fw=fw, p=p)
        alts = [(rd, [r for c in READING_CHECKS for r in c(Ctx(kb=kb, facts=facts, fw=fw.under(rd), p=p))])
                for rd in fw.alternatives]
        merged = merge_readings([r for c in READING_CHECKS for r in c(ctx)], alts)
        outcomes = {r.outcome for r in merged}
        if_proved = any(r.title.startswith("If proved") for r in merged)
        info = (oid, kw, p.disposition_type)
        assert ("error" in outcomes) == all(truth["proved"]), info
        if any(truth["proved"]) and not all(truth["proved"]):
            assert "unresolved" in outcomes, info
        if not any(truth["proved"]):
            assert not outcomes & {"error", "unresolved"}, info
            assert if_proved == any(truth["if_proved"]), info
