"""Tests of the knowledge base loader and the legal framework (code only)."""

from datetime import date

import pytest

from core import config
from core.facts import CaseInput, ConfirmedFacts, validate
from core.framework import build_framework

S15A, S15B, S16, S17, S18I, S18R = config.IN_SCOPE_OFFENCES


def fw_for(kb, oid, d=date(2024, 5, 1), prot="no", knew="no", age=25, accessory="no"):
    case = CaseInput(offence_id=oid, offence_date=d, victim_protected_worker=prot, offender_knew_or_reckless=knew,
                     age_at_offence=age, age_at_sentence=age + 1, accessory_only=accessory, narrative="x" * 100)
    return build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))


def test_kb_loads(kb):
    assert len(kb.meta) == 801 and len(kb.chunks) == 888 and len(kb.offences) == 337
    assert kb.exists("cco.maximum_term") and kb.get("cco.maximum_term")["_parent"] == "order.cco"
    assert kb.get("scale.fine_alternative")["refs"][0]["s"] == "5.1.1"


def test_citations_are_support_quotes(kb):
    cits = kb.citations_for("guide.injury.icsi_current_practice", 3)
    assert cits and all(not c.residual for c in cits)
    assert cits[0].label.startswith("VSM 25.1.3.2")
    assert cits[0].footnotes and cits[0].footnotes[0].authorities


def test_candidates_exclude_commonwealth_and_other_offence_chapters(kb):
    ids = kb.candidates(["gravity"], S17)
    assert "flag.ohs_outcome_relevance" not in ids
    assert all(kb.meta[i].get("jurisdiction") != "cth" for i in ids)
    assert "guide.injury.icsi_rcsi_gravity" in ids


@pytest.mark.parametrize("oid,prot,knew,cat,mnpp,maximum", [
    (S15A, "no", "no", 1, 48, 240), (S15A, "yes", "yes", 1, 60, 240),
    (S15B, "no", "no", 1, 48, 180), (S15B, "yes", "yes", 1, 60, 180),
    (S16, "no", "no", 2, None, 240), (S16, "yes", "yes", 1, 36, 240),
    (S17, "no", "no", None, None, 180), (S17, "yes", "yes", 1, 24, 180),
    (S18I, "no", "no", None, None, 120), (S18I, "yes", "yes", 1, 6, 120),
    (S18R, "no", "no", None, None, 60), (S18R, "yes", "yes", 1, 6, 60),
])
def test_framework_category_minimum_maximum(kb, oid, prot, knew, cat, mnpp, maximum):
    fw = fw_for(kb, oid, prot=prot, knew=knew)
    assert fw.category == cat and fw.mnpp_months == mnpp and fw.maximum.months == maximum
    assert fw.maximum.fine_penalty_units == maximum * 10


def test_unknown_protected_status_is_not_assumed(kb):
    fw = fw_for(kb, S17, prot="yes", knew="unknown")
    assert fw.protected_official == "unknown" and fw.protected_assumed_no
    assert fw.category is None and fw.mnpp_months is None
    other = " ".join(a.summary() for a in fw.alternatives)
    assert "Category 1" in other and "24 months" in other
    assert any(g.app_policy and g.outcome == "caution" for g in fw.gates)


def test_category_dates(kb):
    assert fw_for(kb, S16, d=date(2016, 5, 1)).category is None          # Category 2 only from 20 March 2017
    fw = fw_for(kb, S15A, d=date(2016, 5, 1))
    assert fw.category is None and fw.mnpp_months == 48                  # minimum, but no category before 2017


def test_flags_surface_as_cautions(kb):
    fw = fw_for(kb, S16)
    assert "flag.icsi_category_footnote" in fw.flags
    assert any(g.check_id == "audit.manual_flags" and g.outcome == "caution" for g in fw.gates)


def test_validation_blocks_out_of_scope_before_any_ai_call(kb):
    c = CaseInput(offence_id=S15A, offence_date=date(2012, 1, 1), age_at_offence=17, age_at_sentence=18,
                  court="County Court", narrative="short")
    problems = validate(c, kb)
    assert any("adult" in p for p in problems)
    assert any("2013-07-01" in p for p in problems)
    assert any("narrative" in p.lower() for p in problems)


def test_offence_linked_flags_are_gated(kb):
    fw = fw_for(kb, S17)
    assert {"flag.protected_official_minimum_dates", "flag.mtmo_combination_sentence"} <= set(fw.flags)
    fw16 = fw_for(kb, S16, prot="yes", knew="yes")
    assert {"flag.icsi_category_footnote", "flag.mtmo_combination_sentence"} <= set(fw16.flags)


def test_knowledge_not_proved_is_an_open_reading(kb):
    fw = fw_for(kb, S17, prot="yes", knew="no")
    assert fw.category is None and fw.mnpp_months is None
    assert any(a.kind == "open" and a.category == 1 and a.mnpp_months is None for a in fw.alternatives)


def test_offence_period_straddling_the_category_date(kb):
    case = CaseInput(offence_id=S16, offence_date=date(2017, 1, 10), offence_date_end=date(2017, 6, 1),
                     victim_protected_worker="no", offender_knew_or_reckless="no", age_at_offence=30,
                     age_at_sentence=31, narrative="x" * 100)
    fw = build_framework(kb, ConfirmedFacts(case=case, summary="", facts=[]))
    assert fw.category is None and any(a.category == 2 for a in fw.alternatives)


def test_under_21_caution(kb):
    fw = fw_for(kb, S18I, age=19)
    assert any(g.title == "Offender under 21 at sentence" for g in fw.gates)
