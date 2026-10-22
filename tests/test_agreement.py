"""Tests of the comparison with the most similar cases (core/agreement.py): which comparable cases can be compared with the
estimate, where each falls against the proposal's band, and the similarity-weighted share within it. The cases are
invented."""

import pytest

from core import config
from core.agreement import (DISCLAIMER, band_agreement, band_text, case_outcome, place, read_order)
from core.cases import Comparables
from core.schemas import Band

S17 = "crimes-act-s-17-recklessly-causing-serious-injury"
S16 = "crimes-act-s-16-intentionally-causing-serious-injury"
AFFRAY = "crimes-act-s-195h-affray"


def band(types=("imprisonment",), lo=30, hi=54):
    return Band(disposition_types=list(types), term_low_months=lo, term_high_months=hi, npp_low_months=None,
                npp_high_months=None, cco_low_months=None, cco_high_months=None, main_uncertainties=[])


def entry(cid="C1", sim=0.6, principal=S17, offences=None, own=None, head_months=None, head_text="", note="",
          match="same offence"):
    if offences is None:
        offences = [{"name": "Recklessly causing serious injury", "offence_id": principal, "sentence_text": "",
                     "sentence_months": own}]
    case = {"id": cid, "citation": f"R v {cid} [2021] VCC 1", "court": "County Court", "year": 2021,
            "principal_offence_id": principal, "principal_offence": "Recklessly causing serious injury",
            "offences": offences, "head_sentence_text": head_text, "head_sentence_months": head_months,
            "npp_months": None, "figures_note": note}
    return {"id": cid, "similarity": sim, "match": match, "case": case, "offence_sentence": own, "same_as": []}


def comparables(entries, score_label="similarity", available=20):
    return Comparables(selected=entries, available=available, score_label=score_label,
                       method="cosine similarity of the embeddings" if score_label == "similarity" else "keyword")


# ── the band in words ────────────────────────────────────────────────────────
def test_band_text():
    assert band_text(band()) == "imprisonment of 2 years 6 months to 4 years 6 months"
    assert band_text(band(("imprisonment", "imprisonment_and_cco", "cco"), 6, 18)) == (
        "imprisonment or imprisonment combined with a CCO of 6 months to 1 year 6 months, or community correction order")
    assert band_text(band(("imprisonment",), None, 24)) == "imprisonment of up to 2 years"
    assert band_text(band(("cco", "fine"), None, None)) == "community correction order, or fine"
    assert band_text(band((), None, None)) == "no disposition named"


# ── reading an order from a head sentence ───────────────────────────────────
@pytest.mark.parametrize("text, kind", [
    ("CCO 2 years with 200 hours of unpaid community work", "cco"),
    ("Appeal dismissed; community correction order of 18 months", "cco"),     # not a "dismissal"
    ("Fined $2,000 without conviction", "fine"),
    ("CCO 12 months and a fine of 20 penalty units", "cco"),                 # the more severe order counts
    ("Adjourned undertaking for 12 months", "adjourned_undertaking"),
    ("Discharged without conviction", "discharge"),
    ("Charge dismissed (s 76)", "dismissal"),
    ("Sentenced to 5 years (originally 7 years); resentenced on appeal to a CCO of 2 years", "cco"),
    ("Life imprisonment, non-parole period 25 years", "life"),
])
def test_orders_read_from_a_head_sentence(text, kind):
    assert read_order(text)[0] == kind


@pytest.mark.parametrize("text, why", [
    ("Youth justice centre order of 2 years", "does not rank"),
    ("Wholly suspended sentence of 2 years", "does not rank"),
    ("Imprisonment for 3 years 6 months", "does not record in months"),
    ("Appeal allowed", "no sentence"),
    ("", "no sentence"),
])
def test_orders_not_read(text, why):
    kind, reason = read_order(text)
    assert kind is None and why in reason


# ── which sentence is compared ──────────────────────────────────────────────
def test_the_individual_sentence_for_this_offence_is_compared():
    row = case_outcome(entry(own=36), S17)
    assert (row.kind, row.months, row.source) == ("term", 36.0, "its sentence for this offence")
    assert row.outcome == "3 years imprisonment"


def test_the_head_sentence_only_when_this_offence_is_the_only_charge():
    row = case_outcome(entry(head_months=60, head_text="5 years imprisonment"), S17)
    assert (row.kind, row.months) == ("term", 60.0) and "only charge" in row.source
    several = entry(head_months=60, offences=[
        {"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": "", "sentence_months": None},
        {"name": "Affray", "offence_id": AFFRAY, "sentence_text": "", "sentence_months": None}])
    row = case_outcome(several, S17)
    assert row.kind is None and "several charges" in row.reason
    order = case_outcome(entry(head_text="CCO 2 years, 150 hours unpaid community work"), S17)
    assert order.kind == "cco" and order.outcome == "community correction order"


def test_an_order_written_for_this_offence_is_compared_even_among_several_charges():
    e = entry(head_months=24, offences=[
        {"name": "Recklessly causing serious injury", "offence_id": S17,
         "sentence_text": "RCSI – CCO 18 months / Affray – 2 years imprisonment", "sentence_months": None},
        {"name": "Affray", "offence_id": AFFRAY, "sentence_text": "2 years imprisonment", "sentence_months": 24}])
    row = case_outcome(e, S17)
    assert row.kind == "cco" and row.source == "its sentence for this offence"    # the part naming this offence


def test_a_case_for_another_offence_is_not_compared():
    row = case_outcome(entry(principal=S16, own=None, head_months=48, match="related injury offence"), S17)
    assert row.kind is None and row.reason.startswith("a different offence")


def test_figures_to_check_stop_the_comparison_only_when_they_bear_on_it():
    doubtful = entry(head_months=60, note="total term recorded as 60 months, but the head sentence reads 54")
    assert "need checking" in case_outcome(doubtful, S17).reason
    npp_only = entry(head_months=60, note="non-parole period recorded as 40 months, but the head sentence reads 42")
    assert case_outcome(npp_only, S17).kind == "term"
    other = entry(own=36, offences=[
        {"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": "", "sentence_months": 36},
        {"name": "Affray", "offence_id": AFFRAY, "sentence_text": "6 months", "sentence_months": 9}],
        note="Affray: recorded as 9 months, but written “6 months”")
    assert case_outcome(other, S17).kind == "term"                     # another offence's figure
    mine = entry(own=36, note="Recklessly causing serious injury: recorded as 36 months, but written “3 years 6 months”")
    assert "need checking" in case_outcome(mine, S17).reason


# ── where it falls against the band ─────────────────────────────────────────
@pytest.mark.parametrize("months, position", [(24, "below"), (30, "within"), (42, "within"), (54, "within"),
                                              (54.5, "above"), (80, "above")])
def test_a_term_against_the_term_range(months, position):
    assert place(case_outcome(entry(own=months), S17), band()).position == position


def test_orders_against_the_band():
    cco = case_outcome(entry(head_text="CCO 2 years"), S17)
    assert place(cco, band()).position == "below"
    assert place(case_outcome(entry(head_text="CCO 2 years"), S17), band(("imprisonment_and_cco", "cco"))).position \
        == "within"
    fine = case_outcome(entry(head_text="Fined 30 penalty units"), S17)
    assert place(fine, band(("imprisonment", "fine"))).position == "within"
    between = case_outcome(entry(head_text="CCO 2 years"), S17)
    assert place(between, band(("imprisonment", "fine"))).position == "outside"   # neither below nor above
    prison = case_outcome(entry(own=12), S17)
    assert place(prison, band(("cco",), None, None)).position == "above"
    unranked = case_outcome(entry(head_text="Discharged"), S17)
    assert place(unranked, band(("adjourned_undertaking",), None, None)).position == "outside"
    life = case_outcome(entry(head_text="Life imprisonment"), S17)
    assert place(life, band()).position == "above"
    assert place(case_outcome(entry(own=40), S17), band(("imprisonment",), None, None)).position == "within"


# ── the share within the band ───────────────────────────────────────────────
def test_the_share_is_weighted_by_similarity_and_counts_only_cases_compared():
    comp = comparables([entry("C1", 0.8, own=36), entry("C2", 0.6, own=60), entry("C3", 0.5, own=40),
                        entry("C4", 0.4, principal=S16, own=None, head_months=50, match="related injury offence"),
                        entry("C5", 0.3, head_text="CCO 2 years")])
    a = band_agreement(comp, band(), S17)
    assert (a.compared, a.within, a.above, a.below, a.given) == (4, 2, 1, 1, 5)
    assert a.percent == round(100 * (0.8 + 0.5) / (0.8 + 0.6 + 0.5 + 0.3))
    assert a.weighted and "similarity weight" in a.note and "by count, 2 of the 4" in a.note
    assert "cases were sentenced" not in a.note                  # a weighted share is not worded as a count
    assert a.counts_text == "2 within · 1 below · 1 above"
    assert [r.position for r in a.rows] == ["within", "above", "within", "not compared", "below"]


def test_keyword_ranking_gives_equal_weights():
    comp = comparables([entry("C1", 1.0, own=36), entry("C2", 0.2, own=60), entry("C3", 0.1, own=70)],
                       score_label="keyword score")
    a = band_agreement(comp, band(), S17)
    assert not a.weighted and a.percent == 33 and "equal weights" in a.note


def test_no_percentage_from_too_few_cases_or_without_a_database():
    a = band_agreement(comparables([entry("C1", 0.9, own=36), entry("C2", 0.8, own=40)]), band(), S17)
    assert a.percent is None and a.compared == 2 and f"at least {config.AGREEMENT_MIN_CASES}" in a.note
    assert band_agreement(None, band(), S17).note.startswith("No case database")
    assert band_agreement(Comparables(available=0), band(), S17).percent is None
    empty = Comparables(available=5, note="none of the 5 cases in use is for this offence.")
    assert band_agreement(empty, band(), S17).note == "No comparable case was given: " + empty.note
    none = band_agreement(comparables([entry("C1", 0.5, principal=S16, match="related injury offence")]), band(), S17)
    assert none.percent is None and none.note.startswith("None of the 1 cases")


def test_negative_or_zero_similarities_do_not_break_the_share():
    comp = comparables([entry("C1", -0.1, own=36), entry("C2", 0.0, own=60), entry("C3", 0.0, own=40)])
    a = band_agreement(comp, band(), S17)
    assert a.percent == 67 and not a.weighted                # all weights 0: equal weights
    few = band_agreement(comparables([entry("C1", 0.5, own=36), entry("C2", -0.1, own=60), entry("C3", -0.2, own=70)]),
                         band(), S17)
    assert few.percent == 33 and not few.weighted and "positive similarity" in few.note   # never "100% (1 of 3)"


def test_the_wording_never_calls_it_a_probability_of_the_sentence():
    a = band_agreement(comparables([entry(f"C{k}", 0.5, own=36) for k in range(1, 4)]), band(), S17)
    assert a.percent == 100
    assert "not the probability" in DISCLAIMER and "VSM 2.1" in DISCLAIMER and "5.2.9.3.1" in DISCLAIMER
    assert "probab" not in a.note and "chance" not in a.note


# ── review R7: regressions ───────────────────────────────────────────────────
@pytest.mark.parametrize("text, kind", [
    ("Imprisonment for 200 days (reckoned as already served) and a CCO of 2 years", None),   # a term is never outranked
    ("Imprisonment (time served) and a fine of $1,000", None),
    ("CCO 2 years: supervision, drug and alcohol treatment, 150 hours of unpaid community work", "cco"),
    ("CCO 18 months with a residential treatment program", "cco"),
    ("CCO 12 months; licence suspended for 12 months", "cco"),
    ("Drug and alcohol treatment order of 2 years", None),
    ("Wholly suspended sentence of 2 years", None),
    ("Appeal against sentence dismissed; CCO of 2 years", "cco"),
    ("Appeal against sentence dismissed", None),                 # the result of an appeal is not a dismissal
    ("CCO 2 years with a life skills program", "cco"),           # not life imprisonment
    ("Life, NPP 30 years", "life"),
    ("Sentence of 3 years imprisonment set aside; CCO of 2 years substituted", "cco"),
    ("Appeal allowed; sentence of 3 years' imprisonment set aside and a CCO of 2 years substituted", "cco"),
    ("Sentence of 2 years imprisonment quashed and a fine of $3,000 imposed", "fine"),
    ("Original sentence: 4 years imprisonment. Resentenced to a CCO of 18 months", "cco"),
])
def test_orders_read_after_the_review(text, kind):
    assert read_order(text)[0] == kind


def two_offences(text_s17, text_other, other="Intentionally causing serious injury", other_id=S16, own=None):
    return entry(head_months=36, offences=[
        {"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": text_s17,
         "sentence_months": own},
        {"name": other, "offence_id": other_id, "sentence_text": text_other, "sentence_months": None}])


def test_the_part_of_a_shared_written_sentence_that_names_this_offence():
    shared = "Intentionally causing serious injury – CCO 2 years / Recklessly causing serious injury – fine $2,000"
    assert case_outcome(two_offences(shared, shared), S17).kind == "fine"          # not the first part sharing words
    abbreviated = "RCSI – fine $5,000 / Affray – CCO 12 months"
    assert case_outcome(two_offences(abbreviated, abbreviated, "Affray", AFFRAY), S17).kind == "fine"
    unclear = "Causing injury – CCO 2 years / Causing injury – fine $2,000"
    row = case_outcome(two_offences(unclear, unclear), S17)
    assert row.kind is None and "cannot be identified" in row.reason


def test_an_offence_name_is_not_read_as_an_order():
    FIREARM = "firearms-act-discharging"
    e = entry(principal=FIREARM, own=12, offences=[{"name": "Discharging a firearm", "offence_id": FIREARM,
                                                    "sentence_text": "Discharging a firearm – 12 months",
                                                    "sentence_months": 12}])
    assert case_outcome(e, FIREARM).kind == "term"                                  # not a "discharge"


def test_an_order_recorded_in_months_is_not_a_term_of_imprisonment():
    e = entry(own=18, offences=[{"name": "Recklessly causing serious injury", "offence_id": S17,
                                 "sentence_text": "CCO 18 months", "sentence_months": 18}])
    row = case_outcome(e, S17)
    assert row.kind == "cco" and place(row, band(("cco",), None, None)).position == "within"


def test_an_only_charge_whose_two_figures_disagree_is_not_compared():
    e = entry(own=36, head_months=54, head_text="4 years 6 months imprisonment")
    row = case_outcome(e, S17)
    assert row.kind is None and "differ" in row.reason


def test_bands_with_a_mandatory_treatment_and_monitoring_order():
    mtmo = band(("mtmo",), 6, 12)
    assert band_text(mtmo) == "mandatory treatment and monitoring order with imprisonment of 6 months to 1 year"
    a = band_agreement(comparables([entry(f"C{k}", 0.5, own=m) for k, m in enumerate((8, 10, 11))]), mtmo, S17)
    assert a.compared == 0 and a.percent is None and all(r.band_blocked for r in a.rows)
    assert "mandatory treatment and monitoring order" in a.note and "no share is given" in a.note
    # review R7b: placing only the cases within an MTMO band gave 100% whatever the other cases were
    both = band(("imprisonment", "mtmo"), 12, 24)
    cases = [entry(f"C{k}", 0.9 - k / 20, own=m) for k, m in enumerate((96, 84, 72, 60, 12, 18, 20))]
    b = band_agreement(comparables(cases), both, S17)
    assert b.percent is None and b.compared == 0 and "no share is given" in b.note


def test_a_short_term_between_the_bands_orders_is_outside():
    mixed = band(("imprisonment", "cco"), 6, 18)
    assert place(case_outcome(entry(own=3), S17), mixed).position == "outside"
    assert place(case_outcome(entry(own=24), S17), mixed).position == "above"
    assert place(case_outcome(entry(head_text="CCO 2 years"), S17), mixed).position == "within"
    assert place(case_outcome(entry(head_text="Fined $500"), S17), mixed).position == "below"


def test_the_cco_range_and_life_against_a_non_custodial_band():
    assert band_text(Band(disposition_types=["cco"], term_low_months=None, term_high_months=None, npp_low_months=None,
                          npp_high_months=None, cco_low_months=12, cco_high_months=24, main_uncertainties=[])) == \
        "community correction order of 1 year to 2 years"
    combo = Band(disposition_types=["imprisonment_and_cco"], term_low_months=3, term_high_months=9,
                 npp_low_months=None, npp_high_months=None, cco_low_months=12, cco_high_months=24,
                 main_uncertainties=[])
    assert band_text(combo) == "imprisonment combined with a CCO of 3 months to 9 months (CCO of 1 year to 2 years)"
    life = case_outcome(entry(head_text="Life imprisonment"), S17)
    assert place(life, band(("cco",), None, None)).position == "above"


# ── review R7b: regressions ──────────────────────────────────────────────────
@pytest.mark.parametrize("text, kind", [
    ("Crown appeal against the CCO of 2 years dismissed", "cco"),      # the appeal's words go, the order stays
    ("CCO 2 years (no previous convictions)", "cco"),                   # not a clause about an earlier sentence
    ("Imprisoned for life with a non-parole period of 30 years", "life"),
    ("Fine of $2,000 substituted for the CCO", "fine"),
    ("Set aside the CCO and imposed a fine of $2,000", "fine"),
])
def test_orders_read_after_the_second_review(text, kind):
    assert read_order(text)[0] == kind


def test_a_combination_written_without_imprisonment_keeps_its_term():
    for written in ("RCSI – 9 months + CCO 2 years", "9 months and a 2-year CCO"):
        e = entry(own=9, offences=[{"name": "Recklessly causing serious injury", "offence_id": S17,
                                    "sentence_text": written, "sentence_months": 9}])
        row = case_outcome(e, S17)
        assert (row.kind, row.months) == ("term", 9.0), written
        assert place(row, band(("imprisonment_and_cco",), 3, 12)).position == "within"


@pytest.mark.parametrize("written", ["Youth justice centre order of 2 years", "Intensive correction order 24 months",
                                     "2 years' imprisonment wholly suspended"])
def test_unranked_orders_recorded_in_months_are_not_compared(written):
    e = entry(own=24, offences=[{"name": "Recklessly causing serious injury", "offence_id": S17,
                                 "sentence_text": written, "sentence_months": 24}])
    row = case_outcome(e, S17)
    assert row.kind is None and "does not rank" in row.reason
    head = entry(head_months=24, head_text=written)                  # the only charge, read from the head sentence
    assert case_outcome(head, S17).kind is None
    recorded = entry(own=24, head_months=24, head_text=written)     # …even with the offence's own figure recorded
    assert case_outcome(recorded, S17).kind is None


def test_the_more_specific_offence_name_wins_a_shared_written_sentence():
    shared = "Recklessly causing serious injury – CCO 2 years / Recklessly causing injury – fine $2,000"
    s18 = "crimes-act-s-18-recklessly-causing-injury"
    e = two_offences(shared, shared, "Recklessly causing injury", s18)
    assert case_outcome(e, S17).kind == "cco"
    e18 = entry(principal=s18, head_months=36, offences=[
        {"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": shared, "sentence_months": None},
        {"name": "Recklessly causing injury", "offence_id": s18, "sentence_text": shared, "sentence_months": None}])
    assert case_outcome(e18, s18).kind == "fine"
