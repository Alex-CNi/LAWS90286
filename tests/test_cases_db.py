"""Tests of the case database (core/cases.py): reading documents, chunking with
the AI (through the fake OpenAI service), checking the figures, saving and
loading, choosing the most similar cases, and their use in a run. The cases used
here are invented (tests/fixtures/invented_case_summaries.txt)."""

import io
import json
import re
import threading
from collections import Counter

import httpx
import numpy as np
import pytest

from core import cases as cs
from core import config, schemas
from core.llm import LLM
from core.pipeline import Run, run_remaining, step_confirm, step_extract
from core.report import build_blocks, render_markdown
from core.retrieval import Retriever
from tests.fake_openai import FakeOpenAI, fake_vector
from tests.test_pipeline import KEY, load_case

FIXTURE = config.ROOT / "tests" / "fixtures" / "invented_case_summaries.txt"
S17 = "crimes-act-s-17-recklessly-causing-serious-injury"
S16 = "crimes-act-s-16-intentionally-causing-serious-injury"
S18I = "crimes-act-s-18-intentionally-causing-injury"
ASSAULT = "crimes-act-ss-31-1-320-at-common-law-assault"


def llm_with(fake):
    return LLM(api_key=KEY, http_client=fake.client())


def chunked(kb, fake=None, segment=1500, overlap=700, monkeypatch=None, text=None):
    fake = fake or FakeOpenAI()
    if monkeypatch:
        monkeypatch.setattr(config, "CHUNK_SEGMENT_CHARS", segment)
        monkeypatch.setattr(config, "CHUNK_OVERLAP_CHARS", overlap)
    text = text or FIXTURE.read_text(encoding="utf-8")
    return cs.chunk_document(llm_with(fake), kb, FIXTURE.name, text), fake, text


def docx_lines(doc):
    buf = io.BytesIO()
    doc.save(buf)
    return cs.to_lines(cs.read_document("x.docx", buf.getvalue()))


# ── reading documents ───────────────────────────────────────────────────────
def test_word_tables_and_headings():
    from docx import Document
    from docx.oxml import OxmlElement
    d = Document()
    d.add_heading("4 – Causing injury offences", 1)
    d.add_heading("4.4 – Recklessly causing serious injury", 2)
    t = d.add_table(rows=4, cols=5)
    for j, v in enumerate(["Case and Appeal Status", "Head Sentence /\nOrders", "Individual Sentences", "Plea",
                           "Offence"]):
        t.rows[0].cells[j].text = v                                  # a heading cell with a line break
    for j, v in enumerate(["DPP v Lees [2019] VCC 9234", "90 months (7 years 6 months) imprisonment\nNPP – 66 months",
                           "RCSI – 6 years\nAffray – 6 months", "G", "Recklessly causing serious injury\nAssault"]):
        t.rows[1].cells[j].text = v
    top = t.rows[2].cells[0].merge(t.rows[3].cells[0])               # co-offenders: the case name merged down
    top.text = "R v Pair [2021] VSCA 9003"
    for r, (head, plea) in ((2, ("4 years imprisonment", "G")), (3, ("5 years imprisonment", "G"))):
        t.rows[r].cells[1].text, t.rows[r].cells[3].text = head, plea
        t.rows[r].cells[2].text = t.rows[r].cells[4].text = "RCSI"   # equal neighbouring cells are both kept
    inner = t.rows[1].cells[3].add_table(rows=1, cols=2)             # a table nested in a cell
    inner.rows[0].cells[0].text, inner.rows[0].cells[1].text = "co-offender", "NG"
    p = d.add_paragraph("R v Control [2021] VSCA 9006 — 3 years imprisonment")   # inside a content control
    sdt, content = OxmlElement("w:sdt"), OxmlElement("w:sdtContent")
    p._p.addprevious(sdt)
    sdt.append(content)
    content.append(p._p)
    lines = docx_lines(d)
    assert lines[:2] == ["# 4 – Causing injury offences", "## 4.4 – Recklessly causing serious injury"]
    assert "Head Sentence / Orders: 90 months (7 years 6 months) imprisonment / NPP – 66 months" in lines
    assert "Individual Sentences: RCSI – 6 years / Affray – 6 months" in lines
    assert "Offence: Recklessly causing serious injury / Assault" in lines        # one line: never a heading
    assert "Plea: G / co-offender: NG" in lines
    assert lines.count("Case and Appeal Status: R v Pair [2021] VSCA 9003") == 2   # each co-offender row
    assert lines.count("Individual Sentences: RCSI") == 2 and lines.count("Offence: RCSI") == 2
    assert "R v Control [2021] VSCA 9006 — 3 years imprisonment" in lines
    assert not any(cs.heading_level(ln) for ln in lines[2:])


def test_word_merged_title_row_inside_a_table():
    from docx import Document
    d = Document()
    t = d.add_table(rows=3, cols=4)
    for j, v in enumerate(["Case name", "Head Sentence / Orders", "Offences", "Plea"]):
        t.rows[0].cells[j].text = v
    t.rows[1].cells[0].merge(t.rows[1].cells[3]).text = "Intentionally causing serious injury"
    for j, v in enumerate(["R v A [2021] VSCA 9001", "5 years imprisonment", "ICSI", "G"]):
        t.rows[2].cells[j].text = v
    assert docx_lines(d) == ["###### Intentionally causing serious injury", "Case name: R v A [2021] VSCA 9001",
                             "Head Sentence / Orders: 5 years imprisonment", "Offences: ICSI", "Plea: G"]


HEAD = "<tr><th>Case name</th><th>Head Sentence / Orders</th><th>Offences</th><th>Plea</th></tr>"


@pytest.mark.parametrize("html, expected", [
    # closing tags left out (valid HTML5)
    ("<h2>Recklessly causing serious injury</h2><table>" + HEAD + "<tr><td>R v A [2021] VSCA 1<td>4 years imprisonment"
     "<td>RCSI<td>G</table><p>End</p>",
     ["## Recklessly causing serious injury", "Case name: R v A [2021] VSCA 1",
      "Head Sentence / Orders: 4 years imprisonment", "Offences: RCSI", "Plea: G", "End"]),
    # co-offenders: rowspan
    ("<table>" + HEAD + "<tr><td rowspan=2>R v Pair [2021] VSCA 3</td><td>4 years imprisonment</td><td>RCSI</td>"
     "<td>G</td></tr><tr><td>5 years imprisonment</td><td>RCSI</td><td>NG</td></tr></table>",
     ["Case name: R v Pair [2021] VSCA 3", "Head Sentence / Orders: 4 years imprisonment", "Offences: RCSI", "Plea: G",
      "Case name: R v Pair [2021] VSCA 3", "Head Sentence / Orders: 5 years imprisonment", "Offences: RCSI",
      "Plea: NG"]),
    # a sub-heading row (colspan), a nested table, a line break in a cell, scripts and entities
    ("<script>var x = '<table>';</script><table>" + HEAD + "<tr><td colspan=4>Intentionally causing injury</td></tr>"
     "<tr><td>R v&nbsp;C [2021] VSCA 4</td><td>CCO 2 years<br>200 hours</td><td><table><tr><td>ICI</td>"
     "<td>CCO</td></tr></table></td><td>G</td></tr></table>",
     ["###### Intentionally causing injury", "Case name: R v C [2021] VSCA 4",
      "Head Sentence / Orders: CCO 2 years / 200 hours", "Offences: ICI: CCO", "Plea: G"]),
])
def test_html_pages(html, expected):
    assert cs.to_lines(cs.read_document("page.html", ("<html><body>" + html + "</body></html>").encode())) == expected


def test_text_encodings_and_long_lines():
    assert cs.read_document("a.txt", "R v Smith – “quoted”".encode("cp1252")) == "R v Smith – “quoted”"
    assert cs.read_document("a.txt", "﻿R v Smith".encode("utf-8")) == "R v Smith"
    long = ("The offender struck the victim. " * 400).strip()
    lines = cs.to_lines(long)
    assert len(lines) > 1 and all(len(ln) <= cs.LONG_LINE for ln in lines) and " ".join(lines) == long


@pytest.mark.parametrize("line, level", [
    ("4.4 – Recklessly causing serious injury", 2), ("4.4.2 – County Court", 3), ("4 – Causing injury offences", 1),
    ("4.4 Recklessly causing serious injury", 2), ("## Recklessly causing serious injury", 2),
    ("Assault", 0), ("1. The offender pleaded guilty", 0), ("3 years imprisonment", 0),
    ("2.5 Grams of methylamphetamine were found.", 0), ("4.4 – R v Smith [2019] VSCA 1", 0),
])
def test_heading_levels(line, level):
    assert cs.heading_level(line) == level


def test_heading_path():
    lines = ["# 4 – Causing injury", "## 4.4 – Recklessly causing serious injury", "### 4.4.2 – County Court", "x",
             "## 4.5 – Intentionally causing injury", "y"]
    h = cs.HeadingIndex(lines)
    assert h.before(3) == ("4 – Causing injury", "4.4 – Recklessly causing serious injury", "4.4.2 – County Court")
    assert h.before(5) == ("4 – Causing injury", "4.5 – Intentionally causing injury")
    assert h.before(0) == ()


# ── the figures ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text, term, npp, nil", [
    ("132 months (11 years) imprisonment, NPP – 90 months", 132, 90, False),
    ("90 months (7 years 6 months) imprisonment / NPP – 66 months (5 years 6 months)", 90, 66, False),
    ("11 years imprisonment, NPP 8 years", 132, 96, False),
    ("3 years 6 months imprisonment", 42, None, False),
    ("3 years’ imprisonment, wholly suspended", 36, None, False),
    ("Imprisonment for 2 years and 3 months; no NPP fixed", 27, None, True),
    ("12 months imprisonment, no NPP; 2 years CCO with 150 hours unpaid community work", 12, None, True),
    ("12 months imprisonment, NPP – Nil; CCO 2 years", 12, None, True),
    ("9 months imprisonment, NPP not fixed, followed by a 2 year CCO", 9, None, True),
    ("CCO 18 months with 200 hours unpaid community work", None, None, False),
    ("CCO 18 months; 50 days in custody declared", None, None, False),
    ("Hospital security order 5 years (custodial supervision order)", None, None, False),
    ("Adjourned undertaking 12 months; the offender had served 3 months in custody", None, None, False),
    ("CCO of 2 years combined with 3 months imprisonment", 3, None, False),
    ("ICSI: 4 years imprisonment; RCSI: 2 years imprisonment; TES 5 years, NPP 3 years", 60, 36, False),
    ("ICSI: 4 years imprisonment; RCSI: 2 years imprisonment", None, None, False),    # several terms, no total
    ("Life imprisonment, NPP 30 years", None, 360, False),
    ("Imprisonment for 4 years with a non-parole period of 2 years 6 months", 48, 30, False),
])
def test_figures_read_from_the_head_sentence(text, term, npp, nil):
    f = cs.figures_from_text(text)
    assert (f.term, f.npp, f.npp_nil) == (term, npp, nil)


def record(cid="", label=S17, citation="R v Test [2021] VCC 1", text="glass hotel", offences=(), head="",
           term=None, npp=None, offender=""):
    if offences and isinstance(offences[0], dict):
        offs = [dict(o) for o in offences]
    else:
        offs = [{"name": "o", "offence_id": o, "sentence_text": "", "sentence_months": None} for o in offences]
    return cs.CaseRecord(id=cid, citation=citation, court="County Court", year=2021, section="",
                         principal_offence_id=label, principal_offence="x", offences=offs,
                         head_sentence_text=head, head_sentence_months=term, npp_months=npp, plea="guilty",
                         offender=offender, summary="", text=text, source="t", lines=[1, 1])


def test_figures_are_checked_never_filled():
    head = "12 months imprisonment, no NPP; 2 years CCO"
    text = f"Case name: R v Test [2021] VCC 1\nHead Sentence / Orders: {head}\nOffences: RCSI – 12 months"
    off = [{"name": "RCSI", "offence_id": S17, "sentence_text": "RCSI – 12 months", "sentence_months": 12.0}]
    assert cs.check_figures(record(text=text, head=head, term=12, npp=None, offences=off)) == ""
    wrong = record(text=text, head=head, term=12, npp=24, offences=off)
    assert "says there is none" in cs.check_figures(wrong)
    blank = record(text=text, head=head, term=None, npp=None, offences=off)
    assert "states a term of 12 months, but none was recorded" in cs.check_figures(blank)
    assert blank.head_sentence_months is None                              # not filled in
    off_wrong = [dict(off[0], sentence_months=18.0)]
    assert "recorded as 18 months" in cs.check_figures(record(text=text, head=head, term=12, offences=off_wrong))
    invented = record(text=text, head="6 years imprisonment, NPP 4 years", term=72, npp=48, offences=off)
    assert "not in the case's text" in cs.check_figures(invented)
    assert "not shorter than the term" in cs.check_figures(record(text=text, head=head, term=12, npp=12,
                                                                  offences=off))
    cco = [{"name": "ICI", "offence_id": S18I, "sentence_text": "CCO 2 years", "sentence_months": None}]
    assert cs.check_figures(record(text="CCO 2 years", head="CCO 2 years", offences=cco)) == ""


# ── parts and chunking ──────────────────────────────────────────────────────
def test_parts_cover_every_line_once():
    lines = [f"line {k} " + "x" * (k % 70) for k in range(900)]
    segs = cs.segments(lines, 3000, 1000)
    owned = [k for s in segs for k in range(s.start, s.own_end)]
    assert owned == list(range(900))
    assert all(s.shown_end >= s.own_end for s in segs) and segs[-1].shown_end == 900
    halves = cs.split_segment(lines, segs[0])
    assert [k for h in halves for k in range(h.start, h.own_end)] == list(range(segs[0].start, segs[0].own_end))
    assert all(h.depth == 1 for h in halves)


def test_chunking_finds_every_case_word_for_word(kb, monkeypatch):
    report, fake, text = chunked(kb, monkeypatch=monkeypatch)
    lines = cs.to_lines(text)
    assert report.segments > 2 and not report.errors
    assert len(report.cases) == 11
    for c in report.cases:
        first, last = c.lines
        assert c.text == "\n".join(lines[first - 1:last])            # copied from the document, not rewritten
        assert c.text.startswith("Case name:") and c.citation in c.text and c.citation_found
        assert "## " not in c.text                                    # never runs into the next heading
        assert c.key.split("|")[1] == str(first)
    by_cit = {c.citation.split(" (")[0]: c for c in report.cases}
    assert by_cit["R v Alder"].principal_offence_id == S17
    assert by_cit["DPP v Elm"].principal_offence_id == S16
    assert by_cit["DPP v Kauri"].principal_offence_id is None          # burglary: not a chapter 25 label
    assert by_cit["R v Juniper"].principal_offence_id is None          # armed robbery
    assert by_cit["R v Alder"].section == "Recklessly causing serious injury"
    # the figures check: the fake gives 60 months for "NPP 5 years 6 months"; nothing is changed
    assert "non-parole period recorded as 60 months, but the head sentence reads 66" in by_cit["DPP v Elm"].figures_note
    assert by_cit["DPP v Elm"].npp_months == 60
    # the fake leaves out the term of "12 months imprisonment": flagged, not filled in
    hazel = by_cit["R v Hazel"]
    assert hazel.head_sentence_months is None and "states a term of 12 months" in hazel.figures_note


def test_numbered_headings_label_the_cases(kb):
    """The Judicial College's own layout: "4.4 – Offence" with "4.4.2 – County Court" below it."""
    text = "\n".join([
        "4 – Causing injury offences", "4.4 – Recklessly causing serious injury", "4.4.2 – County Court",
        "Case and Appeal Status: R v One [2021] VCC 9401", "Head Sentence / Orders: 3 years imprisonment",
        "Offence: Recklessly causing serious injury / Assault",
        "Case and Appeal Status: R v Two [2021] VCC 9402", "Head Sentence / Orders: 4 years imprisonment",
        "Offence: Recklessly causing serious injury",
        "4.5 – Intentionally causing injury", "4.5.2 – County Court",
        "Case and Appeal Status: R v Three [2021] VCC 9403", "Head Sentence / Orders: CCO 2 years", "Offence: ICI"])
    report = cs.chunk_document(llm_with(FakeOpenAI()), kb, "jcv.txt", text)
    labels = {c.citation: c.principal_offence_id for c in report.cases}
    assert labels == {"Case and Appeal Status: R v One [2021] VCC 9401": S17,
                      "Case and Appeal Status: R v Two [2021] VCC 9402": S17,
                      "Case and Appeal Status: R v Three [2021] VCC 9403": S18I}
    one = next(c for c in report.cases if "One" in c.citation)
    assert one.section == ("4 – Causing injury offences › 4.4 – Recklessly causing serious injury › "
                           "4.4.2 – County Court")


class FailingParts(FakeOpenAI):
    """Chunking calls for the given line ranges fail the first `times` times."""
    def __init__(self, bad, times=99, **kw):
        super().__init__(**kw)
        self.bad, self.times, self.n, self.lock = set(bad), times, Counter(), threading.Lock()

    def handle(self, request):
        body = json.loads(request.content or b"{}")
        if "text" in body and body["text"]["format"]["name"] == "ChunkResult":
            rng = re.search(r"START on lines (L\d+–L\d+)", body["input"]).group(1)
            with self.lock:
                self.n[rng] += 1
                k = self.n[rng]
            if rng in self.bad and k <= self.times:
                return httpx.Response(400, json={"error": {"message": "Simulated failure.", "type": "invalid_request"}})
        return super().handle(request)


def test_a_failed_part_is_retried_alone(kb, monkeypatch):
    report, fake, _ = chunked(kb, FakeOpenAI(chunk_fail_first=2), monkeypatch=monkeypatch)
    assert not report.errors and len(report.cases) == 11
    assert sum(1 for c in report.calls if not c.ok) == 2 and sum("retry" in c.step for c in report.calls) == 2


def test_a_part_cut_short_is_split_and_only_a_failed_half_retried(kb, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_SEGMENT_CHARS", 100000)
    text = FIXTURE.read_text(encoding="utf-8")
    lines = cs.to_lines(text)
    first_half, second_half = cs.split_segment(lines, cs.segments(lines)[0])
    fake = FailingParts({f"L{second_half.start + 1}–L{second_half.own_end}"}, times=1, chunk_max_cases=4)
    report = cs.chunk_document(llm_with(fake), kb, FIXTURE.name, text)
    assert report.segments == 1 and not report.errors and len(report.cases) == 11
    assert len({c.citation for c in report.cases}) == 11                 # no case twice
    assert fake.n[f"L{first_half.start + 1}–L{first_half.own_end}"] == 1  # the half that worked is not asked again
    assert fake.n[f"L{second_half.start + 1}–L{second_half.own_end}"] == 2


def test_a_continuing_case_stops_at_a_part_that_failed(kb, monkeypatch):
    text = "\n".join(ln for ln in FIXTURE.read_text(encoding="utf-8").splitlines() if not ln.startswith("## "))
    monkeypatch.setattr(config, "CHUNK_SEGMENT_CHARS", 700)
    monkeypatch.setattr(config, "CHUNK_OVERLAP_CHARS", 60)
    bad = cs.segments(cs.to_lines(text))[2]
    report = cs.chunk_document(llm_with(FailingParts({f"L{bad.start + 1}–L{bad.own_end}"})), kb, "x.txt", text)
    assert report.errors and all(c.text.count("Case name:") == 1 for c in report.cases)
    assert any("could not be chunked" in w for w in report.warnings)


@pytest.mark.parametrize("shift", [-1, 1])
def test_a_first_line_one_off_at_a_cut_is_corrected(kb, monkeypatch, shift):
    text = FIXTURE.read_text(encoding="utf-8")
    lines = cs.to_lines(text)
    size = next(size for size in range(300, 3000, 10)
                if any(lines[s.own_end - (1 if shift > 0 else 0)].startswith("Case name:")
                       for s in cs.segments(lines, size, 700)[:-1]))
    monkeypatch.setattr(config, "CHUNK_SEGMENT_CHARS", size)
    monkeypatch.setattr(config, "CHUNK_OVERLAP_CHARS", 700)

    class OffAtTheCut(FakeOpenAI):
        def answer_ChunkResult(self, text, body):
            lo, hi = map(int, re.search(r"START on lines L(\d+)–L(\d+)", text).groups())
            res = super().answer_ChunkResult(text.replace(f"L{lo}–L{hi}", f"L{lo}–L{hi + 1}") if shift < 0 else text,
                                             body)
            for c in res.cases:
                if shift > 0 and c.start_line == hi:          # the case on the last line owned, one line late
                    c.start_line += 1
                if shift < 0 and c.start_line == hi + 1:      # the next part's first case, one line early
                    c.start_line -= 1
            return res
    report = cs.chunk_document(llm_with(OffAtTheCut()), kb, FIXTURE.name, text)
    assert len(report.cases) == 11 and all(c.citation_found for c in report.cases)
    assert all(c.text.count("Case name:") == 1 and c.text.startswith("Case name:") for c in report.cases)


def test_co_offenders(kb):
    class Pair(FakeOpenAI):
        def __init__(self, same_sentence):
            super().__init__()
            self.same = same_sentence

        def answer_ChunkResult(self, text, body):
            res = super().answer_ChunkResult(text, body)
            first = res.cases[0]
            res.cases[0] = first.model_copy(update={"offender": "", "head_sentence_text": "Offender 1: 4 years"})
            res.cases.insert(1, first.model_copy(update={
                "offender": "", "head_sentence_text": "Offender 1: 4 years" if self.same else "Offender 2: 6 years"}))
            return res
    text = "## Recklessly causing serious injury\nCase name: R v Pair [2021] VSCA 9100\nOffender 1: 4 years\n" \
           "Offender 2: 6 years\nFacts: Two offenders."
    two = cs.chunk_document(llm_with(Pair(False)), kb, "pair.txt", text)
    assert len(two.cases) == 2 and two.cases[0].text == two.cases[1].text
    assert [c.key.split("|")[-1] for c in two.cases] == ["1", "2"]
    assert len(cs.chunk_document(llm_with(Pair(True)), kb, "pair.txt", text).cases) == 1   # the same answer twice


def test_labels_are_checked(kb):
    class BadLabels(FakeOpenAI):
        def answer_ChunkResult(self, text, body):
            res = super().answer_ChunkResult(text, body)
            res.cases[0].principal_offence_id = "made-up-offence"
            res.cases[0].principal_offence = "Recklessly causing serious injury"   # its name: the label is recovered
            res.cases[0].offences[0].offence_id = "another-made-up-id"
            res.cases[0].offences[0].name = "Something else"
            return res
    text = "## Recklessly causing serious injury\nCase name: R v Label [2021] VSCA 9102\nPlea: G\nFacts: x."
    c = cs.chunk_document(llm_with(BadLabels()), kb, "l.txt", text).cases[0]
    assert c.principal_offence_id == S17 and c.offences[0]["offence_id"] is None


def test_interrupting_chunking_cancels_the_parts_not_started(kb, monkeypatch):
    monkeypatch.setattr(config, "CHUNK_SEGMENT_CHARS", 300)
    text = FIXTURE.read_text(encoding="utf-8")
    fake = FakeOpenAI()

    def stop(fraction, message):
        raise KeyboardInterrupt                                     # what a page interruption looks like here
    with pytest.raises(KeyboardInterrupt):
        cs.chunk_document(llm_with(fake), kb, "f.txt", text, progress=stop, parallel=1)
    assert fake.chunk_calls < len(cs.segments(cs.to_lines(text)))


# ── the database ────────────────────────────────────────────────────────────
def test_chunking_again_adds_nothing_even_if_the_model_words_things_differently(kb, monkeypatch):
    class Reworded(FakeOpenAI):
        def answer_ChunkResult(self, text, body):
            res = super().answer_ChunkResult(text, body)
            for c in res.cases:
                c.offender = c.offender.replace("y ", " years, ")
                c.end_line -= 1                                   # and a different last line
            return res
    db = cs.CaseDB()
    assert db.add(chunked(kb, monkeypatch=monkeypatch)[0].cases) == (11, 0)
    assert db.add(chunked(kb, fake=Reworded(), monkeypatch=monkeypatch)[0].cases) == (0, 11)
    renamed = cs.chunk_document(llm_with(FakeOpenAI()), kb, "a copy.txt", FIXTURE.read_text(encoding="utf-8"))
    assert db.add(renamed.cases) == (0, 11)                       # the same document under another name


def test_database_round_trip_and_ids(kb, monkeypatch):
    report, fake, _ = chunked(kb, monkeypatch=monkeypatch)
    db = cs.CaseDB()
    db.add(report.cases)
    assert [c.id for c in db.cases] == [f"C{k}" for k in range(1, 12)]
    db.embed_missing(llm_with(fake))
    assert db.has_vectors() and not db.missing_vectors()
    again = cs.CaseDB.from_json(db.to_json())
    assert [c.to_dict() for c in again.cases] == [c.to_dict() for c in db.cases]
    assert all(np.allclose(again.vectors[k], db.vectors[k]) for k in db.vectors)
    # ids are never reused after a deletion (a finished run's snapshot keeps naming the right case)
    db.remove({"C11"})
    db.add([record(citation="R v New [2022] VCC 9500", text="new case text")])
    assert db.cases[-1].id == "C12"
    assert cs.CaseDB.from_json(db.to_json()).next_number() == 13
    # vectors made with another embedding model are dropped (they cannot be compared)
    data = json.loads(db.to_json())
    data["embedding_model"] = "another-model"
    assert not cs.CaseDB.from_json(json.dumps(data)).vectors
    # merging keeps vectors with their cases under new ids
    other = cs.CaseDB()
    other.merge(again)
    assert other.has_vectors() and len(other.cases) == 11


@pytest.mark.parametrize("change, message", [
    (lambda d: d.update(cases={"a": 1}), "should be a list"),
    (lambda d: d["cases"][0].pop("text"), "has no “text”"),
    (lambda d: d["cases"][0].update(head_sentence_months="ten"), "should be a number"),
    (lambda d: d["cases"][0].update(offences="x"), "list of objects"),
    (lambda d: d["cases"][1].update(id=d["cases"][0]["id"]), "same id"),
    (lambda d: d.update(format="something else"), "not a case database"),
])
def test_a_damaged_file_gives_a_message(kb, monkeypatch, change, message):
    db = cs.CaseDB()
    db.add(chunked(kb, monkeypatch=monkeypatch)[0].cases)
    data = json.loads(db.to_json())
    change(data)
    with pytest.raises(ValueError, match=message):
        cs.CaseDB.from_json(json.dumps(data))
    with pytest.raises(ValueError):
        cs.CaseDB.from_json("{not json")


def test_relabelling_keeps_the_sentences_with_their_offences():
    c = record(label=S17, offences=[{"name": "ICSI", "offence_id": S16, "sentence_text": "5 years",
                                     "sentence_months": 60.0},
                                    {"name": "RCSI", "offence_id": S17, "sentence_text": "3 years",
                                     "sentence_months": 36.0}])
    cs.relabel_case(c, S16)                         # the principal offence was mislabelled s 17
    assert c.principal_offence_id == S16 and [o["offence_id"] for o in c.offences] == [S16, S17]
    assert c.principal_sentence_months == 60 and c.sentence_for(S17) == 36
    d = record(label=S17, offences=[{"name": "RCSI", "offence_id": S17, "sentence_text": "", "sentence_months": 36.0}])
    cs.relabel_case(d, S16)                         # the only offence follows its principal label
    assert d.offences[0]["offence_id"] == S16 and d.principal_sentence_months == 36
    assert cs.set_principal_sentence(d, 0.0) and d.principal_sentence_months == 0.0      # 0 is a value, not "none"
    assert not cs.set_principal_sentence(record(label=None), 12.0)


# ── choosing the comparable cases ───────────────────────────────────────────
def small_db(with_vectors=True):
    db = cs.CaseDB()
    db.add([record("", S17, "R v Same [2021] VCC 1", "glass hotel face scar cut drinking argument"),
            record("", S17, "R v Far [2021] VCC 2", "bottle nightclub skull fracture prior violence"),
            record("", None, "R v Robbery [2021] VCC 3", "knife service station attendant arm robbery", [S17]),
            record("", S16, "R v Other [2021] VCC 4", "glass hotel face scar stabbing"),
            record("", None, "R v Burglary [2021] VCC 5", "glass hotel face scar burglary house"),
            record("", S17, "DPP v Moretti [2023] VCC 6", "unrelated words entirely"),
            record("", ASSAULT, "R v Push [2021] VCC 7", "glass hotel face scar push")])
    if with_vectors:
        for c in db.cases:
            db.vectors[c.id] = np.array(fake_vector(cs.embed_text(c)), dtype=np.float32)
    return db


def test_the_same_offence_comes_first_and_other_offences_are_not_used():
    db = small_db()
    q = np.array(fake_vector("glass hotel face scar"), dtype=np.float32)
    comp = cs.select_comparables(db, S17, query_vector=q, query_text="glass hotel face scar",
                                 case_name="DPP v Moretti (test case 1)")
    ids = [e["id"] for e in comp.selected]
    assert comp.method.startswith("cosine") and comp.score_label == "similarity"
    assert ids[:2] == ["C1", "C2"]                       # same offence, most similar first
    assert ids[2:] == ["C3", "C4"]                       # charged in that case; then a related injury offence
    assert "C5" not in ids and "C7" not in ids           # burglary and common assault: never yardsticks here
    assert [(e["id"], e["reason"]) for e in comp.left_out] == [("C6", "its party names match the case being "
                                                                       "estimated")]
    assert comp.selected[2]["offence_sentence"] is None
    assert "[C1]" in comp.block() and "never a source of law" in comp.block() and "app policy" in comp.block()


def test_keyword_ranking_on_the_text_when_a_case_has_no_vector():
    db = small_db()
    del db.vectors["C2"]
    q = np.array(fake_vector("bottle nightclub skull"), dtype=np.float32)
    comp = cs.select_comparables(db, S17, query_vector=q, query_text="bottle nightclub skull fracture")
    assert comp.method == "keyword ranking (BM25), because some cases in use have no embeddings"
    assert comp.score_label == "keyword score" and comp.selected[0]["id"] == "C2" and comp.selected[0]["similarity"] == 1
    tied = cs.select_comparables(small_db(False), S17, query_text="nothing matches here")
    assert [e["id"] for e in tied.selected][:2] == ["C1", "C2"]


def test_ties_are_broken_by_the_id_number():
    db = cs.CaseDB()
    db.add([record(citation=f"R v N{k} [2021] VCC {k}", text=f"text {k}") for k in range(1, 12)])
    comp = cs.select_comparables(db, S17, query_text="nothing in common", k=11)
    assert [e["id"] for e in comp.selected] == [f"C{k}" for k in range(1, 12)]      # C2 before C10


def test_what_is_left_out():
    db = small_db()
    q = np.array(fake_vector(cs.embed_text(db.cases[0])), dtype=np.float32)   # identical: similarity 1.0
    comp = cs.select_comparables(db, S17, query_vector=q)
    assert [e["id"] for e in comp.left_out] == ["C1"] and "similarity" in comp.left_out[0]["reason"]
    comp = cs.select_comparables(db, S17, query_text="x", exclude_ids=["C2"])
    assert [(e["id"], e["reason"]) for e in comp.left_out] == [("C2", "you left it out")]
    # a citation in the case name leaves out that case only (whole citation: "[2021] VCC 1" is not "… VCC 12")
    db.add([record(citation="R v Twelve [2021] VCC 12", text="glass hotel twelve")])
    comp = cs.select_comparables(db, S17, query_text="glass", case_name="Gencev [2021] VCC 12")
    assert [e["id"] for e in comp.left_out] == ["C8"]
    # the party names match in any order of the parties ("R v Kellway" / "Kellway (a pseudonym) v The King")
    db.add([record(citation="Kellway (a pseudonym) v The King [2023] VSCA 9109", text="glass")])
    comp = cs.select_comparables(db, S17, query_text="glass", case_name="R v Kellway")
    assert [e["id"] for e in comp.left_out] == ["C9"]


def test_a_pasted_case_is_left_out_but_a_case_in_the_same_layout_is_not(kb, monkeypatch):
    db = cs.CaseDB()
    db.add(chunked(kb, monkeypatch=monkeypatch)[0].cases)
    alder = next(c for c in db.cases if "Alder" in c.citation)
    narrative = "The prosecution summary reads: " + alder.text.replace("\n", " ") + " The plea was entered early."
    comp = cs.select_comparables(db, S17, query_text="glass hotel", narrative=narrative)
    assert [e["id"] for e in comp.left_out] == [alder.id] and "narrative" in comp.left_out[0]["reason"]
    birch = next(c for c in db.cases if "Birch" in c.citation)
    layout = birch.text.replace("Birch", "Zed").replace("bottle", "glass").replace("nightclub", "hotel")
    assert cs.text_overlap(alder.text, layout) < config.SAME_CASE_TEXT_OVERLAP


def test_the_same_decision_listed_twice_is_used_once():
    db = small_db()
    db.add([record(citation="R v Same (overview table) [2021] VCC 1", text="glass hotel face scar again")])
    twin = db.cases[-1].id
    db.vectors[twin] = db.vectors["C1"]
    assert db.same_decision() == {twin: "C1"}
    q = np.array(fake_vector("glass hotel face scar"), dtype=np.float32)
    comp = cs.select_comparables(db, S17, query_vector=q)
    assert "C1" in comp.ids and twin not in comp.ids
    assert next(e for e in comp.selected if e["id"] == "C1")["same_as"] == [twin]


def test_an_empty_database_says_nothing_to_the_model():
    comp = cs.select_comparables(cs.CaseDB(), S17, query_text="x")
    assert comp.available == 0 and comp.block() == "" and comp.note


# ── the database in a run ───────────────────────────────────────────────────
def new_run(kb, llm):
    run = Run(case=load_case("tc1_glassing_rcsi.json"), models=dict(config.DEFAULT_MODELS),
              efforts=dict(config.DEFAULT_EFFORT))
    step_extract(run, llm, kb)
    step_confirm(run, kb, run.fact_rows, run.suggestions)
    return run


def test_a_run_uses_the_most_similar_cases(kb, retriever, monkeypatch):
    report, fake, _ = chunked(kb, monkeypatch=monkeypatch)
    db = cs.CaseDB()
    db.add(report.cases)
    llm = llm_with(fake)
    db.embed_missing(llm)
    run = new_run(kb, llm)
    run_remaining(run, llm, kb, retriever, case_db=db)
    comp = run.comparables
    assert comp.method.startswith("cosine") and comp.selected
    assert all(e["match"] != cs.MATCHES[3] for e in comp.selected)
    assert [e["case"]["principal_offence_id"] for e in comp.selected][:4] == [S17] * 4   # s 17 cases first
    seen = {}                    # only the comparables stage, the synthesis and the audit see the cases
    for r in fake.requests:
        body = r["body"]
        if "text" in body:
            seen.setdefault(body["text"]["format"]["name"], []).append(
                "Comparable cases from your case database" in body["input"])
    assert seen["StageAnalysis"].count(True) == 1 and all(seen["Proposal"]) and all(seen["AuditResult"])
    st = run.stages["comparables"]
    assert any(x == "case C999" for x in st.invalid)                 # an id the model was not given is removed
    assert st.analysis.points[0].case_ids == [e["id"] for e in comp.selected[:2]]
    assert all(not pt.case_ids for k in ("gravity", "offender", "purposes") for pt in run.stages[k].analysis.points)
    assert any(x == "case C998" for x in run.current.proposal.invalid)
    md = render_markdown(build_blocks(run, kb))
    assert "## 9. Comparable cases from your case database" in md and "## 10. Sources" in md
    assert "never as law" in md and comp.selected[0]["case"]["citation"] in md and "app policy" in md
    assert "no case was averaged" not in md
    assert len([r for r in fake.requests if r["path"].endswith("/embeddings")]) == 2   # the cases, then the run


def test_with_the_index_built_a_case_without_a_vector_means_keyword_ranking(kb, fake_index, monkeypatch):
    """R4 M1: the case query must not be embedded and then ranked on an empty text."""
    retriever = Retriever(kb, fake_index)
    assert retriever.uses_vectors
    report, fake, _ = chunked(kb, monkeypatch=monkeypatch)
    db = cs.CaseDB()
    db.add(report.cases)
    llm = llm_with(fake)
    db.embed_missing(llm)
    del db.vectors["C2"]
    run = new_run(kb, llm)
    run_remaining(run, llm, kb, retriever, case_db=db)
    comp = run.comparables
    assert comp.method.startswith("keyword") and any(e["similarity"] > 0 for e in comp.selected)
    assert any("ranked by keywords" in m for m in run.messages)
    embeds = [r for r in fake.requests if r["path"].endswith("/embeddings")]
    assert len(embeds[-1]["body"]["input"]) == 5                     # the 5 search queries; no case query


def test_a_bracketed_case_id_is_accepted(kb, retriever, monkeypatch):
    class Brackets(FakeOpenAI):
        def answer_StageAnalysis(self, text, body):
            res = super().answer_StageAnalysis(text, body)
            for pt in res.points:
                pt.case_ids = [f"[{c}]" for c in pt.case_ids if c != "C999"]
            return res
    report, fake, _ = chunked(kb, fake=Brackets(bad_first_proposal=False), monkeypatch=monkeypatch)
    db = cs.CaseDB()
    db.add(report.cases)
    llm = llm_with(fake)
    run = new_run(kb, llm)
    run_remaining(run, llm, kb, retriever, case_db=db)
    pt = run.stages["comparables"].analysis.points[0]
    assert pt.case_ids and all(re.fullmatch(r"C\d+", c) for c in pt.case_ids)
    assert not any(x.startswith("case") for x in run.stages["comparables"].invalid)


def test_a_run_without_a_database_is_unchanged(kb, retriever):
    fake = FakeOpenAI(bad_first_proposal=False)
    llm = llm_with(fake)
    run = new_run(kb, llm)
    run_remaining(run, llm, kb, retriever, case_db=cs.CaseDB())
    assert run.ai_calls == 7 and run.comparables.available == 0
    assert not any("Comparable cases from your case database" in r["body"].get("input", "")
                   for r in fake.requests if "text" in r["body"])
    assert "Comparable cases from your case database" not in render_markdown(build_blocks(run, kb))


def test_chunk_schema_is_strict():
    from openai.lib._pydantic import to_strict_json_schema
    schema = to_strict_json_schema(schemas.ChunkResult)
    assert schema["additionalProperties"] is False


# ── regressions from the second review of the case database (R4b) ──────────
@pytest.mark.parametrize("head, term, npp", [
    # the Manual's own case tables write the term as a bare duration (no "imprisonment")
    ("57 months (4 years 9 months) / NPP - 42 months (3 years 6 months)", 57, 42),
    ("63 months (5 years 3 months) / NPP – 39 months (3 years 3 months) / 60 months (5 years) licence "
     "disqualification", 63, 39),
    ("60 months (5 years) / NPP – 36 months (3 years)", 60, 36),
    ("70 months (5 years and 10 months)", 70, None),
    ("Head sentence 4 years 3 months; NPP 2 years 9 months", 51, 33),
    ("Imprisonment for a term of 5 years, NPP set at 3 years", 60, 36),
    ("5 years 6 months imprisonment; 4 years NPP", 66, 48),
    ("Appeal allowed. Original sentence 7 years imprisonment, NPP 5 years; resentenced to 5 years imprisonment, "
     "NPP 3 years", 60, 36),
])
def test_real_table_formats_give_no_false_check(head, term, npp):
    f = cs.figures_from_text(head)
    assert (f.term, f.npp) == (term, npp)
    assert cs.check_figures(record(text=head, head=head, term=term, npp=npp)) == ""


def test_orders_that_are_not_imprisonment():
    assert cs.figures_from_text("33 months (2 years 9 months) YJCO").term is None
    assert cs.check_figures(record(text="33 months (2 years 9 months) YJCO", head="33 months (2 years 9 months) YJCO")) == ""
    assert cs.figures_from_text("2 years CCO with 200 hours").term is None
    # a CCO's length recorded as a term of imprisonment is reported
    wrong = record(text="CCO 2 years with 200 hours", head="CCO 2 years with 200 hours", term=24)
    assert "an order other than imprisonment" in cs.check_figures(wrong)
    # and so is a youth justice centre order, an MTMO or another order that is not imprisonment (R5 m4)
    for head, term in [("33 months (2 years 9 months) YJCO", 33), ("YJCO 2 years", 24),
                       ("Hospital security order 5 years", 60), ("Mandatory treatment and monitoring order 2 years", 24),
                       ("MTMO 2 years", 24), ("Residential treatment order 18 months", 18),
                       ("2 years detention in a youth justice centre", 24),
                       ("Licence disqualification 12 months; fine $2,000", 12)]:
        assert "an order other than imprisonment" in cs.check_figures(record(text=head, head=head, term=term)), head
    # an appeal's original sentence in brackets is not read (R5 n7)
    head = "Resentenced to 5 years imprisonment, NPP 3 years (originally 7 years imprisonment, NPP 5 years)"
    assert cs.figures_from_text(head).term == 60
    assert "reads 60" in cs.check_figures(record(text=head, head=head, term=84, npp=36))
    # a combination sentence is read as its term of imprisonment
    assert cs.figures_from_text("6 months imprisonment combined with a 2 year CCO").term == 6


def test_individual_sentences_copied_with_the_whole_cell_or_naming_a_licence_order():
    cell = "RCSI – 6 years / Affray – 6 months"
    offs = [{"name": "Recklessly causing serious injury", "offence_id": S17, "sentence_text": cell,
             "sentence_months": 72.0},
            {"name": "Affray", "offence_id": None, "sentence_text": cell, "sentence_months": 6.0}]
    assert cs.check_figures(record(text=cell, offences=offs)) == ""
    lic = "Negligently causing serious injury (licence disqualification ending 1 year after O released from custody)"
    offs = [{"name": "NCSI", "offence_id": None, "sentence_text": lic, "sentence_months": None}]
    assert cs.check_figures(record(text=lic, offences=offs)) == ""


def test_the_case_name_line_above_its_citation_is_kept():
    lines = ["Earlier case text", "DPP v Lees", "[2019] VCC 1234", "Head Sentence / Orders: 3 years imprisonment"]
    assert cs._citation_line(lines, 1, "DPP v Lees [2019] VCC 1234", 0, 4) == 1
    # a first line one off into the previous case is still moved onto the citation
    lines = ["R v Prior [2019] VCC 1", "Plea: G", "DPP v Lees [2019] VCC 1234", "Head: 3 years imprisonment"]
    assert cs._citation_line(lines, 1, "DPP v Lees [2019] VCC 1234", 0, 4) == 2
    # … and onto the name line above the citation, never taking the previous case's last line (R5 m5)
    lines = ["DPP v Prior", "[2019] VCC 1", "Offence: O punched V", "DPP v Lees", "[2019] VCC 1234", "Offence: O kicked V"]
    assert cs._citation_line(lines, 2, "DPP v Lees [2019] VCC 1234", 0, 6) == 3
    assert cs._citation_line(lines, 4, "DPP v Lees [2019] VCC 1234", 0, 6) == 3       # the model gave the citation line
    # name, court, citation on three lines (R5b): the case starts at its name, never in the previous case
    lines = ["Offence: Lees punched V", "DPP v Lees", "County Court", "[2019] VCC 1234", "Head: 3 years imprisonment"]
    for model_line in (0, 2, 3):
        assert cs._citation_line(lines, model_line, "DPP v Lees [2019] VCC 1234", 0, 5) == 1


class TwoOnOneLine(FakeOpenAI):
    """Two co-offenders on one line, the whole cell copied for both: only their figures differ."""

    def answer_ChunkResult(self, text, body):
        res = super().answer_ChunkResult(text, body)
        a = res.cases[0]
        a.offender, a.head_sentence_text, a.head_sentence_months = "", "O1: 4 years imprisonment; O2: 6 years", 48.0
        res.cases = [a, a.model_copy(update={"head_sentence_months": 72.0})]
        return res


class Unreported(FakeOpenAI):
    def __init__(self, wording):
        super().__init__()
        self.wording = wording

    def answer_ChunkResult(self, text, body):
        return schemas.ChunkResult(cases=[schemas.ChunkedCase(
            start_line=2, end_line=3, continues_past_shown_lines=False, citation=self.wording, court="County Court",
            year=2010, principal_offence_id=S17, principal_offence="Recklessly causing serious injury", offences=[],
            head_sentence_text="3 years imprisonment", head_sentence_months=36.0, npp_months=None, plea="guilty",
            offender="", summary="s")])


def test_keys_keep_co_offenders_apart_and_ignore_a_reworded_unreported_citation(kb):
    text = ("## Recklessly causing serious injury\nCase name: R v Pair [2021] VSCA 9100\n"
            "Head Sentence / Orders: O1: 4 years imprisonment; O2: 6 years\nPlea: G")
    rep = cs.chunk_document(llm_with(TwoOnOneLine()), kb, "pair.txt", text)
    assert sorted(c.head_sentence_months for c in rep.cases) == [48.0, 72.0]
    text = ("## Recklessly causing serious injury\nR v Smith (unreported, County Court of Victoria, 12 March 2010)\n"
            "Head Sentence / Orders: 3 years imprisonment")
    db = cs.CaseDB()
    first = cs.chunk_document(llm_with(Unreported("R v Smith (unreported, County Court of Victoria, 12 March 2010)")),
                              kb, "u.txt", text)
    again = cs.chunk_document(llm_with(Unreported("R v Smith (County Court, 12 March 2010)")), kb, "u.txt", text)
    assert db.add(first.cases) == (1, 0) and db.add(again.cases) == (0, 1)


def test_a_file_from_the_previous_version_is_checked_again_and_keeps_co_offenders():
    db = cs.CaseDB()
    head = "12 months imprisonment, no NPP; 2 years CCO"
    db.add([record(citation="R v Combo [2021] VCC 9600", text=head, head=head, term=12, npp=24),
            record(citation="R v Pair [2021] VSCA 9100", text="O1 4 years; O2 6 years", head="O1 4 years; O2 6 years",
                   term=48, offender="O1"),
            record(citation="R v Pair [2021] VSCA 9100", text="O1 4 years; O2 6 years", head="O1 4 years; O2 6 years",
                   term=72, offender="O2")])
    data = json.loads(db.to_json())
    for d in data["cases"]:                       # as the previous version saved them: no key, no check note
        d["key"], d["figures_note"] = "", ""
    loaded = cs.CaseDB.from_json(json.dumps(data))
    assert "says there is none" in loaded.get("C1").figures_note
    fresh = cs.CaseDB()
    assert fresh.add(loaded.copy().cases) == (3, 0)


@pytest.mark.parametrize("bad", ['{"head_sentence_months": NaN}', '{"year": Infinity}', '{"lines": [Infinity, 2]}'])
def test_numbers_that_are_not_finite(bad):
    data = json.loads(small_db(False).to_json())
    data["cases"][0].update(json.loads(bad))
    text = json.dumps(data)
    if "head_sentence_months" in bad:
        with pytest.raises(ValueError, match="should be a number"):
            cs.CaseDB.from_json(text)
    else:                                          # a year or line number that is not finite is simply dropped
        c = cs.CaseDB.from_json(text).get("C1")
        assert c.year is None or c.lines == [0, 0]


def test_a_case_without_a_label_in_a_saved_file_loads():
    data = json.loads(small_db(False).to_json())
    del data["cases"][0]["principal_offence_id"]
    assert cs.CaseDB.from_json(json.dumps(data)).get("C1").principal_offence_id is None


def test_leaving_a_case_out_leaves_out_the_same_decision_listed_again():
    db = small_db()
    db.add([record(citation="R v Same (overview table) [2021] VCC 1", text="glass hotel face scar again", term=30)])
    twin = db.cases[-1].id
    comp = cs.select_comparables(db, S17, query_text="glass", exclude_ids=["C1"])
    reasons = {e["id"]: e["reason"] for e in comp.left_out}
    assert reasons["C1"] == "you left it out" and reasons[twin].startswith("the same decision as C1")
    assert "C1" not in comp.ids and twin not in comp.ids


def test_a_joint_decision_naming_the_offender_is_left_out():
    # every party name of the case being estimated must be in the case's name (R5 n5: a joint decision is left out;
    # the price is that "R v Smith" also leaves out "Paul Smith", which is listed under "Left out")
    db = cs.CaseDB()
    db.add([record(citation="DPP v Pair & Grey [2021] VCC 3", text="glass"),
            record(citation="R v Other [2020] VCC 9", text="glass")])
    comp = cs.select_comparables(db, S17, query_text="glass", case_name="DPP v Pair")
    assert [e["id"] for e in comp.left_out] == ["C1"] and comp.ids == {"C2"}


def test_only_a_neutral_citation_makes_two_listings_one_decision():
    db = cs.CaseDB()
    db.add([record(citation="Smith (CoA)", text="glass one"), record(citation="Smith (CoA)", text="glass two")])
    comp = cs.select_comparables(db, S17, query_text="glass", exclude_ids=["C1"])
    assert [e["id"] for e in comp.left_out] == ["C1"] and "C2" in comp.ids


def test_numbered_sentences_and_running_headers_are_not_headings():
    lines = ["4 – Causing injury offences", "4.4 – Recklessly causing serious injury", "R v A [2021] VCC 1",
             "10.30 – O arrived at the hotel and began drinking", "2.5 – Mitigating factors include an early plea",
             "4 – Causing Injury Offences 23", "4.4.2 – County Court", "R v B [2021] VCC 2",
             "4.5. Intentionally causing injury", "R v C [2021] VCC 3", "4.6—Negligently causing serious injury", "x"]
    h = cs.HeadingIndex(lines)
    assert [k for k in range(len(lines)) if h.is_heading(k)] == [0, 1, 6, 8, 10]
    assert h.before(4) == ("4 – Causing injury offences", "4.4 – Recklessly causing serious injury")
    assert h.before(9) == ("4 – Causing injury offences", "4.5. Intentionally causing injury")
    assert cs.heading_level("1 – O was 24 years old and had no prior convictions") == 0
    assert cs.heading_level("4 – Causing Injury Offences 23") == 0
    assert cs.heading_level("4.4 – Offences under s 17") == 2
    # real headings out of order, or restarting their numbering, are still headings (R5 m6)
    lines = ["4 – Causing injury offences", "4.7 – Negligently causing serious injury", "R v Fox [2019] VSCA 10",
             "4.4 – Recklessly causing serious injury", "R v Lees [2020] VSCA 5", "1 – Appendix",
             "1.1 – County Court", "DPP v Fox [2019] VCC 10"]
    h = cs.HeadingIndex(lines)
    assert h.before(4) == ("4 – Causing injury offences", "4.4 – Recklessly causing serious injury")
    assert h.before(7) == ("1 – Appendix", "1.1 – County Court")
    # a numbered sentence before the first heading does not hide the headings after it
    h = cs.HeadingIndex(["12.5 – Mitigating factors noted in the summaries below", "4 – Causing injury offences",
                         "4.4 – Recklessly causing serious injury", "R v Lees [2020] VSCA 5"])
    assert h.before(3) == ("4 – Causing injury offences", "4.4 – Recklessly causing serious injury")
    for line in ["4.7 – Negligently causing serious injury – Table 1", "5 – Appendix 1",
                 "4.4.2 – County Court decisions – Group 2"]:
        assert cs.heading_level(line), line


def test_html_caption_and_stray_text_stay_apart():
    lines = cs.to_lines(cs.read_document("p.html", (
        "<html><body><table><caption>Table 4.4.2</caption><tr><th>Case</th><th>Head Sentence / Orders</th>"
        "<th>Plea</th></tr><tr><td>R v Pair [2021] VSCA 3</td>stray<td>4 years imprisonment</td><td>G</td></tr>"
        "</table></body></html>").encode()))
    assert lines[:2] == ["Table 4.4.2", "stray"] and "Case: R v Pair [2021] VSCA 3" in lines


def test_word_skipped_columns_text_boxes_styles_and_long_merged_rows():
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from lxml import etree
    d = Document()
    t = d.add_table(rows=3, cols=3)
    for j, v in enumerate(["Case name", "Head Sentence / Orders", "Plea"]):
        t.rows[0].cells[j].text = v
    for j, v in enumerate(["R v Lees [2020] VCC 375", "90 months imprisonment", "G"]):
        t.rows[1].cells[j].text = v
    for j, v in enumerate(["", "45 months imprisonment", "NG"]):
        t.rows[2].cells[j].text = v
    tr = t.rows[2]._tr                             # the third row starts in the second column (w:gridBefore)
    tr.remove(tr.tc_lst[0])
    gb = OxmlElement("w:gridBefore")
    gb.set(qn("w:val"), "1")
    tr.get_or_add_trPr().insert(0, gb)
    p = d.add_paragraph("Before the box. ")
    p.add_run()._r.append(etree.fromstring(
        '<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><mc:Choice Requires="wps"><w:r>'
        '<w:t>BOX</w:t></w:r></mc:Choice><mc:Fallback><w:r><w:t>BOX</w:t></w:r></mc:Fallback></mc:AlternateContent>'))
    style = d.styles.add_style("JCV Offence", 1)
    ol = OxmlElement("w:outlineLvl")
    ol.set(qn("w:val"), "1")
    style.element.get_or_add_pPr().append(ol)
    d.add_paragraph("Recklessly causing serious injury", style="JCV Offence")
    t2 = d.add_table(rows=2, cols=3)
    for j, v in enumerate(["Case name", "Head Sentence / Orders", "Plea"]):
        t2.rows[0].cells[j].text = v
    t2.rows[1].cells[0].merge(t2.rows[1].cells[2]).text = ("Comment: the Court noted the offending was at the lower "
                                                           "end of the range for glassing and the plea was early.")
    lines = docx_lines(d)
    assert "Head Sentence / Orders: 45 months imprisonment" in lines and "Plea: NG" in lines
    assert "Before the box. BOX" in lines
    assert "## Recklessly causing serious injury" in lines
    assert lines[-1].startswith("Comment: the Court noted") and not cs.heading_level(lines[-1])


def test_merged_rows_are_sub_headings_unless_they_read_as_a_sentence():
    rows = [["Case", "Head Sentence / Orders", "Plea"], ["R v A [2021] VSCA 12", "4 years imprisonment", "G"],
            ["He was refused leave to appeal", None, None], ["County Court", None, None],
            ["Appeals allowed", None, None], ["Cases where the victim was a police officer", None, None]]
    lines = cs._table_lines(rows)
    assert "He was refused leave to appeal" in lines and "###### County Court" in lines
    assert "###### Appeals allowed" in lines                                  # R5b MINOR-7: real sub-headings
    assert "###### Cases where the victim was a police officer" in lines
    # the first numbered heading of a document is never taken for a sentence
    h = cs.HeadingIndex(["1 – Cases where the victim was an emergency worker", "1.1 – Court of Appeal",
                         "R v Lees [2020] VSCA 5"])
    assert h.before(2) == ("1 – Cases where the victim was an emergency worker", "1.1 – Court of Appeal")


@pytest.mark.parametrize("head, term", [
    ("8 months, licence cancelled and disqualified for 2 years", 8),
    ("10 months (pre-sentence detention of 120 days declared)", 10),
    ("18 months, 200 days pre-sentence detention reckoned", 18),
])
def test_a_bare_term_with_other_orders_after_it_gives_no_false_check(head, term):
    assert cs.figures_from_text(head).term == term                           # R5b NOTE-1
    assert cs.check_figures(record(text=head, head=head, term=term)) == ""
