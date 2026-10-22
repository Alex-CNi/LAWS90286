"""Tests of the result card (app_pages/result_card.py): the estimate in large type, the range, the comparable cases
against the range, the chips and the disclaimer — from runs made with the fake OpenAI service. The cases are
invented."""

import re

import numpy as np
import pytest

from app_pages import result_card as rc
from core import cases as cs
from core.agreement import band_agreement
from core.pipeline import run_remaining
from core.results import CheckResult
from tests.fake_openai import FakeOpenAI, fake_vector
from tests.test_cases_db import S17, llm_with, new_run, record


def db_with(sentences, citations=None):
    """A case database of s 17 cases, one per sentence (months of imprisonment, or "CCO")."""
    db = cs.CaseDB()
    recs = []
    for k, m in enumerate(sentences):
        cit = (citations or {}).get(k, f"R v Case{k} [2021] VCC {100 + k}")
        head = f"{m} months imprisonment" if isinstance(m, int) else "Community correction order of 2 years"
        written = f"Recklessly causing serious injury – {head}"
        offs = [{"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": written,
                 "sentence_months": float(m) if isinstance(m, int) else None}]
        recs.append(record(label=S17, citation=cit, text=f"{cit}\nglass hotel face scar drinking\n{written}\n{head}",
                           offences=offs, head=head, term=float(m) if isinstance(m, int) else None))
    db.add(recs)
    for c in db.cases:
        db.vectors[c.id] = np.array(fake_vector(cs.embed_text(c)), dtype=np.float32)
    return db


def finished_run(kb, retriever, db):
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = llm_with(fake)
    run = new_run(kb, llm)
    run_remaining(run, llm, kb, retriever, case_db=db)
    assert run.current and run.current.proposal.proposal
    return run


def text_of(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ── the sentence in words ────────────────────────────────────────────────────
@pytest.fixture()
def base():
    return FakeOpenAI()._proposal("Plea: guilty", True)        # imprisonment combined with a CCO


def test_headline_for_each_kind_of_sentence(base):
    big, kind, details = rc.headline(base)
    assert (big, kind) == ("9 months", "Imprisonment, then a community correction order")
    assert details[0] == "No non-parole period"
    assert details[1].startswith("CCO of 2 years: unpaid community work (150 hours), treatment and rehabilitation")
    prison = base.model_copy(update={"disposition_type": "imprisonment", "term_months": 42, "npp_months": 28, "cco": None})
    assert rc.headline(prison) == ("3 years 6 months", "Imprisonment", ["Non-parole period 2 years 4 months"])
    cco = base.model_copy(update={"disposition_type": "cco", "term_months": None,
                                  "cco": base.cco.model_copy(update={"months": 18})})
    big, kind, details = rc.headline(cco)
    assert (big, kind) == ("1 year 6 months", "Community correction order (CCO)")
    assert details == ["Conditions: unpaid community work (150 hours), treatment and rehabilitation",
                       "Conviction recorded"]
    fine = base.model_copy(update={"disposition_type": "fine", "term_months": None, "cco": None,
                                   "fine_penalty_units": 50, "conviction_recorded": False})
    assert rc.headline(fine) == ("50 penalty units", "Fine", ["No conviction recorded"])
    discharge = base.model_copy(update={"disposition_type": "discharge", "term_months": None, "cco": None,
                                        "conviction_recorded": None})
    assert rc.headline(discharge) == ("Discharge", "", [])
    bare_fine = fine.model_copy(update={"fine_penalty_units": None})
    assert rc.headline(bare_fine)[:2] == ("Fine", "")                     # the figure is not repeated as the kind


# ── the card from a run ─────────────────────────────────────────────────────
def test_the_card_shows_the_estimate_the_range_and_the_cases_against_it(kb, retriever):
    run = finished_run(kb, retriever, db_with([6, 12, 24, "CCO"]))
    p = run.current.proposal.proposal
    ag = band_agreement(run.comparables, p.band, run.case.offence_id)
    assert (ag.within, ag.above, ag.below, ag.compared) == (2, 1, 1, 4)          # band: 3 to 15 months
    html = rc.card_html(run, kb)
    words = text_of(html)
    assert 'class="vse-result"' in html and 'class="vr-sentence">9 months<' in html
    assert "Indicative range:" in words and "3 months to 1 year 3 months" in words
    assert f'class="vr-value">{ag.percent}<small>%</small>' in html and "2 of 4 cases" in words
    assert "not the probability that a court will impose this sentence" in words and "VSM 2.1" in words
    assert "Recklessly causing serious injury — Crimes Act 1958 s 17" in words
    assert html.count('class="vse-dot') == 4                                   # every case compared is drawn
    assert 'class="band"' in html and "Estimate · 9m" in words
    assert "The 4 most similar cases, one by one" in words
    assert "Errors remain" not in words


def test_a_run_without_a_case_database_still_shows_the_estimate(kb, retriever):
    run = finished_run(kb, retriever, cs.CaseDB())
    html = rc.card_html(run, kb)
    assert 'class="vr-value none">—<' in html and "No case database" in text_of(html)
    assert 'class="vse-dot' not in html and 'class="band"' in html             # the range and the estimate only


def test_errors_are_shown_at_the_top_of_the_card(kb, retriever):
    run = finished_run(kb, retriever, cs.CaseDB())
    run.current.checks.append(CheckResult(check_id="test", title="Test", outcome="error", message="x"))
    words = text_of(rc.card_html(run, kb))
    assert "Errors remain — do not rely on this estimate" in words and "Hard checks 1 error" in words


def test_text_from_the_case_database_is_escaped(kb, retriever):
    run = finished_run(kb, retriever, db_with([6, 12, 24], {0: "R v <script>alert(1)</script> [2021] VCC 9"}))
    html = rc.card_html(run, kb)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_no_strip_without_a_term_to_draw(base):
    cco = base.model_copy(update={"disposition_type": "cco", "term_months": None,
                                  "band": base.band.model_copy(update={"disposition_types": ["cco"],
                                                                       "term_low_months": None,
                                                                       "term_high_months": None})})
    empty = band_agreement(None, cco.band, S17)
    assert rc.strip_html(empty, cco) == ""
    prison = base.model_copy(update={"disposition_type": "imprisonment", "term_months": 40, "npp_months": 26})
    assert 'class="band"' in rc.strip_html(empty, prison)


def test_the_card_presents_the_range_as_the_estimate_with_its_uncertainties(kb, retriever):
    run = finished_run(kb, retriever, cs.CaseDB())
    p = run.current.proposal.proposal
    words = text_of(rc.card_html(run, kb))
    assert "The estimate is the range. The large figure is the formulated sentence within it" in words
    assert "Main uncertainties: " + "; ".join(p.band.main_uncertainties) in words
    assert f"Assumptions and unresolved points ({len(p.assumptions) + len(p.unresolved)})" in words
    assert "VSM 2.2.3.1" not in words
    run.case.user_role = "prosecution"
    assert "not on the range of permissible durations (VSM 2.2.3.1)" in text_of(rc.card_html(run, kb))


def test_the_tile_and_the_case_list_say_what_the_numbers_are(kb, retriever):
    run = finished_run(kb, retriever, db_with([6, 12, 24, "CCO"]))
    words = text_of(rc.card_html(run, kb))
    assert "By count: 2 of 4 cases within" in words and "weighted by cosine similarity" in words
    assert "not a sentencing practice" in words and "Similarity" in words
    keywords = db_with([6, 12, 24, "CCO"])
    del keywords.vectors["C1"]                                   # one case without a vector: keyword ranking
    run = finished_run(kb, retriever, keywords)
    assert run.comparables.score_label == "keyword score"
    html = rc.card_html(run, kb)
    assert "<th>Keyword score</th>" in html and "keyword score 0." in html and "Equal weights, because" in html


def test_the_card_says_when_the_formulated_sentence_lies_outside_its_range(kb, retriever):
    run = finished_run(kb, retriever, cs.CaseDB())
    p = run.current.proposal.proposal
    assert rc.formulated_within(p)
    run.current.proposal.proposal = p.model_copy(update={"term_months": (p.band.term_high_months or 0) + 6})
    words = text_of(rc.card_html(run, kb))
    assert "it lies outside the range (see Step 6)" in words and "formulated sentence within it" not in words


def test_the_score_label_is_shown_on_phones():
    from app_pages.theme import theme_css
    css = theme_css()
    assert css.index(".vse-cases .lbl { display: none; }") < css.index(".vse-cases .lbl { display: inline; }")
