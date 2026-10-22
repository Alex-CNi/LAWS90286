"""
core/cases.py — THE CASE DATABASE: COMPARABLE CASES THE USER UPLOADS.

The user uploads documents of sentencing case summaries (for example the
Judicial College's "Sentencing Manual case summaries"). This module:

  read_document()       turns an uploaded file into lines of text (.docx with its tables, .pdf, .txt/.md,
                        saved .html); headings are marked "#", table cells keep their column heading
  chunk_document()      AI calls: split the text into individual cases and label each by the offences charged.
                        The model answers with LINE NUMBERS, and the code copies each case's lines from the
                        document word for word: the model never writes a case's text
  check_figures()       compares the months the model gave with the head sentence and individual sentences as
                        written in the case; differences are marked for the user to check (never silently fixed)
  CaseDB                the cases and their vectors (embeddings), saved to and loaded from a JSON file
  select_comparables()  the cases most similar to the confirmed facts, by cosine similarity (the Day 3
                        method), the same offence first
  Comparables           what one run was given: the selected cases, the cases left out and why

The Manual stays the only source of law. These cases are used only as
comparable cases — yardsticks of current sentencing practice, the way the
Manual says comparable cases are used (csp.comparable_cases,
audit.comparable_cases_use) — and are cited by their ids (C1, C2 …).
"""

from __future__ import annotations

import base64
import bisect
import hashlib
import io
import json
import math
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser

import numpy as np

from core import config, prompts
from core.kb import KnowledgeBase
from core.llm import LLM, CallRecord, LLMError
from core.schemas import ChunkResult

DB_FORMAT = "sentencing-ai case database"


# ─────────────────────────────────────────────────────────────────────────────
# 1. One case
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CaseRecord:
    id: str                            # C1, C2 … (unique within the database; never reused)
    citation: str                      # e.g. "Gencev v The Queen [2021] VSCA 188"
    court: str                         # Court of Appeal | County Court | Supreme Court | unknown
    year: int | None
    section: str                       # the document headings the case sits under ("4.4 – … › 4.4.2 – County Court")
    principal_offence_id: str | None   # a register id (any chapter of config.CASE_LABEL_CHAPTERS), or None = another
    principal_offence: str             # its name as written in the document
    offences: list[dict]               # every offence charged: [{name, offence_id, sentence_text, sentence_months}]
    head_sentence_text: str            # the head sentence / orders as the model copied it
    head_sentence_months: float | None  # total effective term of imprisonment (None: none, or life)
    npp_months: float | None
    plea: str                          # guilty_early | guilty | guilty_late | not_guilty | mixed | unknown
    offender: str                      # e.g. "34y male"
    summary: str                       # the model's one- or two-sentence summary (for lists and the embedding)
    text: str                          # the case's lines, word for word from the document
    source: str                        # the file it came from
    lines: list[int]                   # [first, last] line numbers in that file (as numbered by to_lines)
    citation_found: bool = True        # False: the citation is not in the case's text (check the case)
    figures_note: str = ""             # what to check: figures that do not match the text as written
    use: bool = True                   # the user can leave a case out of the comparisons
    edited: bool = False               # the user changed a label or a figure
    key: str = ""                      # identifies the case in its document (document hash | line | citation | n)

    @property
    def principal_sentence_months(self) -> float | None:
        """The individual sentence on the principal offence, where the summary gives one."""
        return self.sentence_for(self.principal_offence_id)

    def sentence_for(self, offence_id: str | None) -> float | None:
        """The individual sentence on a given offence (by register id), where the summary gives one."""
        for o in self.offences:
            if offence_id and o.get("offence_id") == offence_id and o.get("sentence_months") is not None:
                return o["sentence_months"]
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CaseRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def relabel_case(c: CaseRecord, new_id: str | None) -> None:
    """The user corrects a case's principal offence. If no offence charged carries the new label yet, the
    offence that carried the old one (or, for "other", the one named like the principal offence) takes it, so
    its individual sentence stays with the principal offence; an offence already labelled is never retagged."""
    old = c.principal_offence_id
    if new_id and not any(o.get("offence_id") == new_id for o in c.offences):
        for o in c.offences:
            if (old and o.get("offence_id") == old) or (
                    not old and not o.get("offence_id") and _norm(o.get("name", "")) == _norm(c.principal_offence)):
                o["offence_id"] = new_id
                break
    c.principal_offence_id = new_id


def set_principal_sentence(c: CaseRecord, months: float | None) -> bool:
    """The user corrects the individual sentence on the principal offence. Returns False if the case has no
    principal offence label to attach it to (an "other" offence)."""
    if not c.principal_offence_id:
        return False
    for o in c.offences:
        if o.get("offence_id") == c.principal_offence_id:
            o["sentence_months"] = months
            return True
    c.offences.insert(0, {"name": c.principal_offence, "offence_id": c.principal_offence_id, "sentence_text": "",
                          "sentence_months": months})
    return True


def case_labels(kb: KnowledgeBase, chapters=None) -> list[tuple[str, str]]:
    """The offences a case can be labelled with: every register offence of config.CASE_LABEL_CHAPTERS (the Manual's
    Part E), or of the chapters given, as (id, the charge box's label: unique, e.g. "27 · Armed robbery — …")."""
    wanted = {str(c) for c in (chapters or config.CASE_LABEL_CHAPTERS)}
    return [(oid, kb.charge_label(oid)) for oid in config.IN_SCOPE_OFFENCES if kb.offence_chapters(oid) & wanted]


def unique_names(kb: KnowledgeBase, ids) -> dict[str, str]:
    """normalised offence name -> register id, for the names only one of these offences has (a fallback when a model
    gives the name instead of the code; a name two offences share is never used)."""
    counts = Counter(_norm(kb.offence(oid).get("offence") or "") for oid in ids)
    return {_norm(kb.offence(oid).get("offence") or ""): oid for oid in ids
            if counts[_norm(kb.offence(oid).get("offence") or "")] == 1}


def related_offences(kb: KnowledgeBase, offence_id: str) -> set[str]:
    """The "related offences" tier of comparable cases (app policy, POLICY): the other offences in the same section
    of the Manual's register as the charge (e.g. 27.1.1.2: robbery, armed robbery, burglary, home invasion …; 25.1.1:
    the causing injury offences)."""
    section = str((kb.offences.get(offence_id) or {}).get("section") or "")
    if not section:
        return set()
    return {oid for oid in config.IN_SCOPE_OFFENCES if oid != offence_id
            and str(kb.offence(oid).get("section") or "") == section}


def relabel_by_name(db: "CaseDB", kb: KnowledgeBase) -> int:
    """Cases labelled "other" (e.g. by version 1.x, which labelled only chapter 25 offences): each principal offence or
    offence charged whose name as written is exactly one register offence's name takes that offence's label. No AI
    and no guessing: a name that is not exactly one offence's name stays "other". Returns how many cases changed."""
    names = unique_names(kb, config.IN_SCOPE_OFFENCES)
    changed = 0
    for c in db.cases:
        touched = False
        for o in c.offences:
            if not o.get("offence_id") and _norm(o.get("name", "")) in names:
                o["offence_id"] = names[_norm(o.get("name", ""))]
                touched = True
        if not c.principal_offence_id and _norm(c.principal_offence) in names:
            c.principal_offence_id = names[_norm(c.principal_offence)]
            touched = True
        if touched:
            c.figures_note = check_figures(c)
            c.edited = True                   # the labels are no longer the AI's (the user asked for this)
            changed += 1
    return changed


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


_NEUTRAL = re.compile(r"\[(?:19|20)\d\d\]\s*[A-Za-z]{2,6}\s*\d+")


def citation_key(citation: str) -> str:
    """The neutral citation ("[2021] VSCA 188" -> "2021 vsca 188") if there is one, else the whole citation,
    normalised. Used to find a case in its text and to recognise the same decision listed twice."""
    m = _NEUTRAL.search(citation or "")
    return _norm(m.group(0)) if m else _norm(citation)


def _contains(haystack_norm: str, needle: str) -> bool:
    """Whole-word containment of a normalised phrase."""
    n = _norm(needle)
    return bool(n) and f" {n} " in f" {haystack_norm} "


# ─────────────────────────────────────────────────────────────────────────────
# 2. Reading an uploaded document into lines
# ─────────────────────────────────────────────────────────────────────────────
def decode_text(raw: bytes) -> str:
    """Bytes of a text file as text: UTF-8 (with or without a byte-order mark), UTF-16, else Windows-1252."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def read_document(name: str, raw: bytes) -> str:
    """The text of an uploaded file. Headings are marked "#" (one per level); each table cell becomes one
    line "Column heading: text" (a cell's own lines joined with " / "), so each case reads on its own."""
    lower = name.lower()
    if lower.endswith(".docx"):
        return _docx_text(raw)
    if lower.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(raw)).pages)
    text = decode_text(raw)
    if lower.endswith((".html", ".htm")) or text.lstrip()[:300].lower().startswith(("<!doctype html", "<html")):
        return _html_text(text)
    return text


def _clean_cell(text: str | None, joiner: str = " / ") -> str | None:
    """A cell's text on one line: its own lines joined by `joiner` (" / " for content, " " for a heading)."""
    if text is None:
        return None
    parts = [" ".join(p.split()) for p in str(text).splitlines()]
    return joiner.join(p for p in parts if p)


def _is_title_row(row: list[str | None]) -> bool:
    """One cell merged across the whole table (the rest are None)."""
    real = [c for c in row if c is not None]
    return len(row) > 1 and len(real) == 1 and bool(real[0])


def _table_lines(rows: list[list[str | None]]) -> list[str]:
    """Table rows as lines. A cell is a string; None marks a column covered by the cell to its left (a
    horizontal merge). A row merged across the whole table is a heading line. If the first other row looks like
    column headings (3 or more short, different cells), every later cell is written "Heading: text"."""
    raw = [row for row in rows if any((c or "").strip() for c in row if c is not None)]
    rows = [[_clean_cell(c) for c in row] for row in raw]
    out: list[str] = []
    header: list[str] | None = None
    first_body = next((j for j, r in enumerate(rows) if not _is_title_row(r)), -1)
    for k, row in enumerate(rows):
        if _is_title_row(row):
            title = next(_clean_cell(c, " ") for c in raw[k] if c)
            if len(title) <= 100 and not title.endswith(".") and not _sentence_like(title):
                out.append("###### " + title)                    # a sub-heading in the table
            else:
                out.append(next(c for c in row if c))           # a paragraph across the table: content
            continue
        if header is None and k == first_body:
            real = [_clean_cell(c, " ") for c in raw[k] if c is not None]
            if len(real) >= 3 and all(0 < len(c) <= 60 for c in real) and len(set(real)) == len(real):
                header, last = [], ""
                for c in raw[k]:                                  # a merged heading covers the columns it spans
                    last = _clean_cell(c, " ") if c is not None else last
                    header.append(last)
                continue
        cells = [(header[j] if header and j < len(header) else "", c) for j, c in enumerate(row) if c]
        if not header and len(cells) == 2:
            out.append(f"{cells[0][1]}: {cells[1][1]}")
            continue
        for label, c in cells:
            out.append(f"{label}: {c}" if label else c)
    return out


# ── Word (.docx): read from the document's XML, so nothing python-docx skips is lost ──
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


_MC_FALLBACK = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"


def _wtag(el) -> str:
    return el.tag[len(_W):] if isinstance(el.tag, str) and el.tag.startswith(_W) else ""


def _walk(el):
    """An element's descendants in order, leaving out the fallback copy Word keeps of a text box or drawing
    (mc:Fallback repeats mc:Choice), so its text is read once."""
    for child in el:
        if child.tag == _MC_FALLBACK:
            continue
        yield child
        yield from _walk(child)


def _para_text(p) -> str:
    """All the text of a paragraph, including hyperlinks, fields, content controls and tracked insertions."""
    parts = []
    for el in _walk(p):
        tag = _wtag(el)
        if tag == "t":
            parts.append(el.text or "")
        elif tag in ("tab", "noBreakHyphen"):
            parts.append(" " if tag == "tab" else "-")
        elif tag in ("br", "cr"):
            parts.append("\n")
    return "".join(parts)


def _outline(ppr) -> int:
    """The heading level an outline level in paragraph properties gives (0: none, or body text)."""
    outline = ppr.find(_W + "outlineLvl") if ppr is not None else None
    value = outline.get(_W + "val") if outline is not None else None
    return int(value) + 1 if value and value.isdigit() and int(value) < 6 else 0


def _heading_level(p, styles: dict[str, tuple[str, int]]) -> int:
    """A paragraph's heading level: from its style's name ("Heading 2", "Title"), its own outline level, or the
    outline level its style (or a style it is based on) sets."""
    ppr = p.find(_W + "pPr")
    if ppr is None:
        return 0
    style = ppr.find(_W + "pStyle")
    sid = style.get(_W + "val") if style is not None else None
    name, style_level = styles.get(sid, (sid or "", 0)) if sid else ("", 0)
    m = re.match(r"(?i)\s*heading\s*(\d)", name)
    if m:
        return int(m.group(1))
    if re.match(r"(?i)\s*title\b", name):
        return 1
    return _outline(ppr) or style_level


def _docx_blocks(parent, styles: dict[str, tuple[str, int]], out: list[str], in_cell: bool = False) -> None:
    """Paragraphs and tables in document order; content controls and custom XML are opened."""
    for child in parent.iterchildren():
        tag = _wtag(child)
        if tag == "p":
            text = _para_text(child)
            level = 0 if in_cell else _heading_level(child, styles)
            out.append("#" * min(level, 6) + " " + " ".join(text.split()) if level and text.strip() else text)
        elif tag == "tbl":
            out.extend(_table_lines(_docx_rows(child, styles)))
        elif tag == "sdt":
            content = child.find(_W + "sdtContent")
            if content is not None:
                _docx_blocks(content, styles, out, in_cell)
        elif tag in ("customXml", "smartTag", "ins"):
            _docx_blocks(child, styles, out, in_cell)


def _docx_cells(tr):
    for child in tr.iterchildren():
        tag = _wtag(child)
        if tag == "tc":
            yield child
        elif tag in ("sdt", "customXml"):
            inner = child.find(_W + "sdtContent") if tag == "sdt" else child
            if inner is not None:
                yield from _docx_cells(inner)


def _docx_rows(tbl, styles: dict[str, tuple[str, int]]) -> list[list[str | None]]:
    """A table's rows. A horizontally merged cell is followed by None for each column it covers; a
    vertically merged cell repeats the text of its first row (so each co-offender's row keeps the case name)."""
    rows, above, trs = [], {}, []             # above: grid column -> text of the vertical merge that started there
    for child in tbl.iterchildren():
        if _wtag(child) == "tr":
            trs.append(child)
        elif _wtag(child) == "sdt":                        # rows wrapped in a content control
            content = child.find(_W + "sdtContent")
            trs += [c for c in (content.iterchildren() if content is not None else []) if _wtag(c) == "tr"]
    for tr in trs:
        trpr = tr.find(_W + "trPr")
        before = trpr.find(_W + "gridBefore") if trpr is not None else None
        skip = int(before.get(_W + "val")) if before is not None and (before.get(_W + "val") or "").isdigit() else 0
        row, col = [""] * skip, skip                        # a row that starts some columns in keeps its labels
        for tc in _docx_cells(tr):
            tcpr = tc.find(_W + "tcPr")
            span = 1
            vmerge = None
            if tcpr is not None:
                gs = tcpr.find(_W + "gridSpan")
                if gs is not None and (gs.get(_W + "val") or "").isdigit():
                    span = max(1, int(gs.get(_W + "val")))
                vmerge = tcpr.find(_W + "vMerge")
            lines: list[str] = []
            _docx_blocks(tc, styles, lines, in_cell=True)
            text = "\n".join(lines)
            if vmerge is not None and vmerge.get(_W + "val") != "restart":
                text = above.get(col, text)                     # continuation of a vertical merge
            elif vmerge is not None:
                above[col] = text
            else:
                above.pop(col, None)
            row.append(text)
            row.extend([None] * (span - 1))
            col += span
        rows.append(row)
    return rows


def _docx_text(raw: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(raw))
    found: dict[str, tuple[str, int, str | None]] = {}       # style id -> (name, outline level, based on)
    for s in doc.styles:
        try:
            el = s.element
            based = el.find(_W + "basedOn")
            found[s.style_id] = (s.name or "", _outline(el.find(_W + "pPr")),
                                 based.get(_W + "val") if based is not None else None)
        except Exception:  # noqa: BLE001 — a damaged style entry is ignored
            continue
    styles: dict[str, tuple[str, int]] = {}
    for sid, (name, level, based) in found.items():
        for _ in range(5):                                   # an outline level set by the style it is based on
            if level or not based or based not in found:
                break
            level, based = found[based][1], found[based][2]
        styles[sid] = (name, level)
    out: list[str] = []
    _docx_blocks(doc.element.body, styles, out)
    return "\n".join(out)


# ── HTML: visible text, headings marked "#", tables (with spans) as _table_lines ──
class _HTMLText(HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "section", "article", "header", "footer", "dt", "dd", "blockquote", "pre",
              "ul", "ol", "dl", "hr", "caption"}
    HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
    HIDDEN = {"script", "style", "noscript", "template", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.buf: list[str] = []
        self.skip = 0
        self.heading = 0
        self.tables: list[dict] = []     # tables being read: {"rows": [[(text, colspan, rowspan)]], "row", "cell"}

    # text inside the innermost open cell, or the page
    def _flush(self):
        text = " ".join("".join(self.buf).split())
        self.buf = []
        if not text:
            return
        if self.heading and not (self.tables and self.tables[-1]["cell"] is not None):
            text = "#" * self.heading + " " + text
        if self.tables and self.tables[-1]["cell"] is not None:
            self.tables[-1]["cell"]["parts"].append(text)
        elif self.tables:
            self.tables[-1]["loose"].append(text)      # text in a table but outside any cell
        else:
            self.out.append(text)

    def _close_cell(self):
        t = self.tables[-1]
        if t["cell"] is not None:
            self._flush()
            cell = t["cell"]
            if t["row"] is None:
                t["row"] = []
            t["row"].append(("\n".join(cell["parts"]), cell["colspan"], cell["rowspan"]))
            t["cell"] = None

    def _close_row(self):
        t = self.tables[-1]
        self._close_cell()
        if t["row"] is not None:
            t["rows"].append(t["row"])
            t["row"] = None

    @staticmethod
    def _span(attrs, name) -> int:
        value = dict(attrs).get(name) or "1"
        return max(1, min(int(value), 100)) if value.strip().isdigit() else 1

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.skip += 1
            return
        if tag == "table":
            self._flush()
            self.tables.append({"rows": [], "row": None, "cell": None, "loose": []})
        elif tag == "tr" and self.tables:
            self._flush()                                       # text before the row (e.g. stray text) stays apart
            self._close_row()
            self.tables[-1]["row"] = []
        elif tag in ("td", "th") and self.tables:
            self._flush()
            self._close_cell()                                  # an open cell ends where the next one starts
            if self.tables[-1]["row"] is None:
                self.tables[-1]["row"] = []
            self.tables[-1]["cell"] = {"parts": [], "colspan": self._span(attrs, "colspan"),
                                       "rowspan": self._span(attrs, "rowspan")}
        elif tag in self.HEADINGS:
            self._flush()
            self.heading = self.HEADINGS[tag]
        elif tag in self.BLOCKS:
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.skip = max(0, self.skip - 1)
            return
        if tag in ("td", "th") and self.tables:
            self._close_cell()
        elif tag == "tr" and self.tables:
            self._close_row()
        elif tag == "table" and self.tables:
            self._close_row()
            t = self.tables.pop()
            lines = t["loose"] + _table_lines(_expand_spans(t["rows"]))
            if self.tables and self.tables[-1]["cell"] is not None:   # a table inside a cell: part of that cell
                self.tables[-1]["cell"]["parts"].extend(lines)
            elif self.tables:
                self.tables[-1]["loose"].extend(lines)
            else:
                self.out.extend(lines)
        elif tag in self.HEADINGS:
            self._flush()
            self.heading = 0
        elif tag in self.BLOCKS:
            self._flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def text(self) -> str:
        while self.tables:                                      # a table never closed
            self.handle_endtag("table")
        self._flush()
        return "\n".join(self.out)


def _expand_spans(raw_rows: list[list[tuple[str, int, int]]]) -> list[list[str | None]]:
    """Cells with colspan/rowspan laid out on the grid: a colspan adds None for each covered column; a rowspan
    repeats the cell's text in the rows below (like a vertically merged Word cell)."""
    out, pending = [], {}                 # pending: column -> [text or None, rows left]
    for raw in raw_rows:
        row, col, k, used = [], 0, 0, set()
        while k < len(raw) or any(c >= col for c in pending):
            if col in pending:
                text, left = pending[col]
                row.append(text)
                used.add(col)
                if left <= 1:
                    del pending[col]
                else:
                    pending[col] = [text, left - 1]
                col += 1
            elif k < len(raw):
                text, colspan, rowspan = raw[k]
                k += 1
                row.append(text)
                row.extend([None] * (colspan - 1))
                if rowspan > 1:
                    for j in range(colspan):
                        pending[col + j] = [text if j == 0 else None, rowspan - 1]
                        used.add(col + j)                   # made in this row: not counted down until the next
                col += colspan
            else:
                row.append("")
                col += 1
        for c in [c for c in pending if c not in used]:     # a span the row's own cells overlapped (bad markup)
            pending[c][1] -= 1
            if pending[c][1] <= 0:
                del pending[c]
        out.append(row)
    return out


def _html_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    parser.close()
    return parser.text()


LONG_LINE = 2000                          # characters: longer lines are wrapped (so a part can always be cut)


def to_lines(text: str) -> list[str]:
    """Non-empty lines, trimmed; a line longer than LONG_LINE is wrapped at sentence ends (the words are
    unchanged). Line numbers (L1, L2 …) count these lines."""
    out = []
    for ln in (text or "").splitlines():
        ln = ln.strip()
        while len(ln) > LONG_LINE:
            cut = max(ln.rfind(". ", 0, LONG_LINE), ln.rfind("; ", 0, LONG_LINE))
            cut = cut + 1 if cut > LONG_LINE // 2 else (ln.rfind(" ", 0, LONG_LINE) if ln.rfind(" ", 0, LONG_LINE) > 0
                                                         else LONG_LINE)
            out.append(ln[:cut].strip())
            ln = ln[cut:].strip()
        if ln:
            out.append(ln)
    return out


# ── Headings: "# …" (marked by read_document, or Markdown) and numbered headings such as
#    "4.4 – Recklessly causing serious injury", "4.4.2 – County Court" or "4 – Causing injury offences" ──
_NUMBERED = re.compile(r"^(?:(\d{1,2}(?:\.\d{1,2}){1,4})\.?(?:\s*[–—-]\s*|\s+)|(\d{1,2})\s*[–—-]\s*)"
                       r"([A-Z(][^\n]{1,140})$")
_NUMBER_AT_START = re.compile(r"^#*\s*(\d{1,2}(?:\.\d{1,2}){0,4})(?:\.?\s*[–—-]|\.?\s)")
_NUMBERED_WORDS = {"s", "ss", "section", "sections", "part", "division", "chapter", "schedule", "rule", "order",
                   "item", "pt", "div", "ch", "sch", "r", "cl", "clause", "table", "group", "appendix", "annexure",
                   "attachment", "figure", "level", "stage", "tier", "category", "step", "phase", "volume", "book",
                   "no", "number", "version", "case", "example"}      # "… s 17" or "… Table 1" is not a page number
# Words that make a numbered line (or a merged table row) read as a sentence rather than a heading.
_SENTENCE_WORDS = {"include", "includes", "included", "arrived", "began", "pleaded", "struck", "punched", "stabbed",
                   "kicked", "attacked", "said", "says", "told", "asked", "went", "came", "took", "received",
                   "suffered", "he", "she", "they", "we", "i", "his", "him", "her", "their", "them"}


def _sentence_like(text: str) -> bool:
    return bool(_SENTENCE_WORDS & set(re.findall(r"[a-z]+", text.lower())))


def heading_level(line: str) -> int:
    """0 if the line is not a heading; otherwise its level (1 = top). A numbered line is not a heading if it
    reads as a numbered sentence ("1 – O was 24 years old …": a single capital letter, then a word) or ends with
    a page number (a running header such as "4 – Causing Injury Offences 23"). See also HeadingIndex."""
    m = re.match(r"^(#{1,6})\s+\S", line)
    if m:
        return len(m.group(1))
    m = _NUMBERED.match(line)
    if m and not line.rstrip().endswith(".") and not _NEUTRAL.search(line):
        title = m.group(3)
        page = re.search(r"(\S+)\s\d{1,3}$", title)
        if re.match(r"(?!A\s)[A-Z]\s+[a-z]", title) or (page and page.group(1).lower() not in _NUMBERED_WORDS):
            return 0
        return (m.group(1) or m.group(2)).count(".") + 1
    return 0


def _heading_number(line: str) -> tuple[int, ...] | None:
    m = _NUMBER_AT_START.match(line)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _follows(last: tuple[int, ...], number: tuple[int, ...]) -> bool:
    """A heading number that can come after `last`: a sub-heading of it (4.4 → 4.4.1), or a later heading at the
    same or a higher level (4.4.2 → 4.4.3, 4.5 or 5)."""
    if len(number) == len(last) + 1 and number[:-1] == last:
        return True
    d = len(number)
    return d <= len(last) and number[:-1] == last[:d - 1] and number[-1] > last[d - 1]


class HeadingIndex:
    """The headings in force at any line (all levels), worked out once per document. A numbered line that is not
    marked "#", whose number does not follow on from the numbered heading before it and which reads as a sentence
    ("2.5 – Mitigating factors include …" inside a case), is not taken for a heading; a real heading out of order
    or restarting its numbering still is."""

    def __init__(self, lines: list[str]):
        self.at: list[int] = []
        self.paths: list[tuple[str, ...]] = []
        stack: list[tuple[int, str]] = []
        last: tuple[int, ...] | None = None
        for k, ln in enumerate(lines):
            level = heading_level(ln)
            if not level:
                continue
            number = _heading_number(ln)
            if number:
                if not ln.startswith("#") and last is not None and not _follows(last, number) and _sentence_like(ln):
                    continue                     # e.g. "2.5 – Mitigating factors include …" inside a case
                last = number
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, ln.lstrip("#").strip())]
            self.at.append(k)
            self.paths.append(tuple(t for _, t in stack))
        self._set = set(self.at)

    def is_heading(self, index: int) -> bool:
        return index in self._set

    def before(self, index: int) -> tuple[str, ...]:
        """The headings in force just before line `index` (0-based)."""
        j = bisect.bisect_left(self.at, index) - 1
        return self.paths[j] if j >= 0 else ()


# ─────────────────────────────────────────────────────────────────────────────
# 3. The figures: read from the text as written, and compared with the model's
# ─────────────────────────────────────────────────────────────────────────────
_DUR = (r"(?:(?P<y>\d+(?:\.\d+)?)\s*(?:years?|yrs?)\b(?:\s*(?:and|,)?\s*(?P<m1>\d+(?:\.\d+)?)\s*(?:months?|mths?)\b)?"
        r"|(?P<m2>\d+(?:\.\d+)?)\s*(?:months?|mths?)\b)")
_DURATION = re.compile(_DUR, re.I)
_PRISON = r"(?:imprisonment|jail|gaol|prison)"
_TERM_BEFORE = re.compile(_DUR + r"['’]?\s*(?:\([^)]{0,40}\)\s*)?(?:of\s+)?" + _PRISON + r"\b", re.I)
_TERM_AFTER = re.compile(r"\b" + _PRISON + r"\b\s*(?:of|for|:|–|—|-)?\s*(?:a\s+(?:term|period)\s+of\s+)?" + _DUR,
                         re.I)
_TOTAL = re.compile(r"\b(?:TES|total effective (?:sentence|term)|aggregate (?:sentence|term)|total sentence)\b"
                    r"\s*(?:of|:|–|—|-|=)?\s*" + _DUR, re.I)
_NPP_MARK = re.compile(r"\b(no\s+)?(?:NPP|non[- ]parole(?:\s+period)?)\b", re.I)
_NIL = re.compile(r"\s*(?:[:–—=-]|of|was|is)?\s*(?:nil\b|none\b|n/?a\b|not\s+(?:fixed|set)|no\b|–\s*$)", re.I)
# An appeal summary may give the original sentence first: the figures are read after "resentenced".
_RESENTENCED = re.compile(r"\b(?:re-?sentenced|resentencing|sentence substituted|substituted)\b", re.I)
# A bare duration is a term of imprisonment unless its part of the text names another order.
_OTHER_ORDER = re.compile(r"\bCCO\b|\bYJCO\b|\bYJC\b|\bYAO\b|community|\bfines?\b|undertaking|\bbond\b|discharg|"
                          r"dismiss|youth|probation|detention|"
                          r"\border\b|disqualif|licen[cs]e|\bhours\b", re.I)
# Orders that are not a term of imprisonment (a term recorded for one of them alone is reported).
_NOT_IMPRISONMENT = re.compile(
    r"\bCCO\b|community correction|\bfines?\b|undertaking|\bbond\b|discharg|dismiss|probation|"
    r"\bYJCO\b|\bYJC\b|\bYAO\b|\bYSO\b|youth (?:justice|attendance|supervision|residential|detention)|"
    r"\bMTMO\b|treatment and monitoring|hospital security|residential treatment|\bRTO\b|\bCSTO\b|"
    r"custodial supervision|\bDATO\b|drug and alcohol treatment|disqualif", re.I)
# A note in brackets about the original sentence on appeal is not read for the figures.
_ORIGINAL = re.compile(r"\((?:originally|original(?: sentence)?|previously|at first instance)\b[^)]*\)", re.I)
_BARE = re.compile(r"\s*(?:head\s+sentence\s*(?:of|:|–|—|-)?|to|of|:|–|—|-)?\s*" + _DUR, re.I)


def _months(m) -> float:
    if m.group("y"):
        return float(m.group("y")) * 12 + float(m.group("m1") or 0)
    return float(m.group("m2"))


def duration_months(text: str) -> float | None:
    """The first duration in a text ("6 years", "3 years 6 months", "90 months"), in months."""
    m = _DURATION.search(text or "")
    return _months(m) if m else None


@dataclass
class Figures:
    term: float | None          # total term of imprisonment stated (None: none stated, several, or life)
    npp: float | None           # non-parole period stated
    npp_nil: bool = False       # the text says there is no non-parole period


def _bare_term(head: str) -> float | None:
    """A term written as a bare duration, as the Manual's case tables do ("57 months (4 years 9 months) / NPP –
    42 months"): the first part of the text (split at "/", ";" and line ends) that starts with a duration, if
    that part names no other order (a CCO, a fine, a licence disqualification …)."""
    for part in re.split(r"[/;\n]", head):
        m = _BARE.match(part)
        if m:
            tail = re.sub(r"^\s*\([^)]*\)", "", part[m.end():])       # "(4 years 9 months)" restates it
            tail = re.split(r",", tail)[0]                               # "…, 200 days pre-sentence detention"
            return None if _OTHER_ORDER.search(tail) else _months(m)
    return None


def figures_from_text(head: str) -> Figures:
    """The total term and non-parole period a head sentence states, e.g. "90 months (7 years 6 months)
    imprisonment / NPP – 66 months". The term is the duration after "TES", else the one next to "imprisonment",
    else a bare duration at the start of the text; several different terms of imprisonment with no total, or a
    life sentence, give None. After "resentenced" (an appeal), only what follows is read."""
    head = _ORIGINAL.sub(" ", head or "")
    marks = list(_RESENTENCED.finditer(head))
    if marks and _DURATION.search(head[marks[-1].end():]):
        head = head[marks[-1].end():]
    term = None
    m = _TOTAL.search(head)
    if m:
        term = _months(m)
    elif not re.search(r"\blife\b", head, re.I):
        found = {_months(m) for m in _TERM_BEFORE.finditer(head)} | {_months(m) for m in _TERM_AFTER.finditer(head)}
        term = found.pop() if len(found) == 1 else (_bare_term(head) if not found else None)
    npp, nil = None, False
    for m in _NPP_MARK.finditer(head):
        if m.group(1):                                  # "no NPP"
            nil = True
            break
        after = head[m.end():m.end() + 40]
        if _NIL.match(after):
            nil = True
            break
        d = re.match(r"\s*(?:[:–—=-]|of|fixed at|set at|was|is)?\s*" + _DUR, after, re.I)
        if not d:                                       # "4 years NPP": the duration written before it
            d = re.search(_DUR + r"['’]?\s*(?:\([^)]{0,30}\)\s*)?$", head[max(0, m.start() - 40):m.start()], re.I)
        if d:
            npp = _months(d)
            break
    return Figures(term, npp, nil)


def _sane(m) -> float | None:
    try:
        m = float(m)
    except (TypeError, ValueError):
        return None
    return m if 0 < m <= 1200 else None


def _tokens_in(phrase: str, text_norm: str, share: float = 0.9) -> bool:
    """Most of a phrase's words are in the text (the model may drop a bracket or a word when copying)."""
    words = _norm(phrase).split()
    if not words:
        return True
    have = set(text_norm.split())
    return sum(1 for w in words if w in have) >= share * len(words)


_NAME_STOP = {"of", "the", "a", "an", "and", "in", "to", "causing", "with", "intent", "s"}


def _written_for(written: str, name: str) -> float | None:
    """The duration written for one offence. When the text lists several offences ("RCSI – 6 years / Affray –
    6 months", copied whole for each offence), the part that names this offence counts."""
    parts = re.split(r"[/;\n]", written)
    words = set(_norm(name).split()) - _NAME_STOP
    if len(parts) > 1 and words:
        for part in parts:
            if words & set(_norm(part).split()):
                d = duration_months(part)
                if d is not None:
                    return d
    return duration_months(written)


def check_figures(c: "CaseRecord") -> str:
    """What to check in a case's figures, comparing what the model gave with the case's own text. The figures
    are never changed here: anything that does not match is reported for the user to check."""
    notes = []
    body = _norm(c.text)
    if c.head_sentence_text and not _tokens_in(c.head_sentence_text, body):
        notes.append("the head sentence given is not in the case's text")
    head = c.head_sentence_text or ""
    f = figures_from_text(head)
    term, npp = c.head_sentence_months, c.npp_months
    if f.term is not None and term is None:
        notes.append(f"the head sentence states a term of {f.term:g} months, but none was recorded")
    elif f.term is not None and abs(f.term - term) > 0.5:
        notes.append(f"total term recorded as {term:g} months, but the head sentence reads {f.term:g}")
    elif f.term is None and term is not None and not re.search(r"\b" + _PRISON + r"\b|\bTES\b|aggregate|total effective",
                                                                head, re.I):
        stated = [_months(m) for m in _DURATION.finditer(head)]
        if not any(abs(d - term) <= 0.5 for d in stated):
            notes.append(f"a term of {term:g} months was recorded, but the head sentence states no such term")
        elif _NOT_IMPRISONMENT.search(head) and f.npp is None and not f.npp_nil:
            notes.append(f"a term of {term:g} months was recorded, but the head sentence names an order other than "
                         "imprisonment and no term of imprisonment")
    if f.npp_nil and npp is not None:
        notes.append(f"a non-parole period of {npp:g} months was recorded, but the head sentence says there is none")
    elif f.npp is not None and npp is None:
        notes.append(f"the head sentence states a non-parole period of {f.npp:g} months, but none was recorded")
    elif f.npp is not None and abs(f.npp - npp) > 0.5:
        notes.append(f"non-parole period recorded as {npp:g} months, but the head sentence reads {f.npp:g}")
    if term is not None and npp is not None and npp >= term:
        notes.append("the non-parole period recorded is not shorter than the term")
    for o in c.offences:
        written = o.get("sentence_text") or ""
        if written and not _tokens_in(written, body):
            notes.append(f"the sentence given for {o.get('name') or 'an offence'} is not in the case's text")
        stated = _written_for(written, o.get("name") or "")
        months = o.get("sentence_months")
        custodial = not re.search(r"CCO|community|fine|undertaking|order|bond|discharge|dismiss|penalty|disqualif|"
                                  r"licen[cs]e", written, re.I)
        if stated is not None and months is not None and abs(stated - months) > 0.5:
            notes.append(f"{o.get('name') or 'an offence'}: recorded as {months:g} months, but written “{written}”")
        elif stated is not None and months is None and custodial:
            notes.append(f"{o.get('name') or 'an offence'}: “{written}” was not recorded in months")
    return "; ".join(dict.fromkeys(notes))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Chunking with the AI (one call per part; parts overlap)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Segment:
    start: int          # first line shown (0-based)
    own_end: int        # cases starting before this line belong to this part (exclusive)
    shown_end: int      # lines shown, including the overlap (exclusive)
    depth: int = 0      # how many times it has been halved


def _extend(lines: list[str], start: int, chars: int) -> int:
    end, total = start, 0
    while end < len(lines) and total < chars:
        total += len(lines[end]) + 1
        end += 1
    return end


def segments(lines: list[str], size: int | None = None, overlap: int | None = None) -> list[Segment]:
    """Cut the lines into parts of about `size` characters (CHUNK_SEGMENT_CHARS). Each call also sees about
    `overlap` characters more (CHUNK_OVERLAP_CHARS), so a case starting near the end of its part is seen whole."""
    size, overlap = size or config.CHUNK_SEGMENT_CHARS, overlap or config.CHUNK_OVERLAP_CHARS
    out, start = [], 0
    while start < len(lines):
        own_end = max(start + 1, _extend(lines, start, size))
        out.append(Segment(start, own_end, _extend(lines, own_end, overlap)))
        start = own_end
    return out


def split_segment(lines: list[str], seg: Segment, overlap: int | None = None) -> list[Segment]:
    """Two halves of a part (used when an answer was cut short)."""
    overlap = overlap or config.CHUNK_OVERLAP_CHARS
    chars = sum(len(lines[k]) + 1 for k in range(seg.start, seg.own_end))
    mid = min(max(seg.start + 1, _extend(lines, seg.start, chars // 2)), seg.own_end - 1)
    if mid <= seg.start:
        return [seg]
    return [Segment(seg.start, mid, min(seg.shown_end, max(seg.own_end, _extend(lines, mid, overlap))), seg.depth + 1),
            Segment(mid, seg.own_end, seg.shown_end, seg.depth + 1)]


def segment_input(file_name: str, lines: list[str], seg: Segment, labels: str, headings: HeadingIndex) -> str:
    """One chunking call's input. labels: the offence codes it may use (kb.offence_code_list)."""
    shown = "\n".join(f"L{k + 1}: {lines[k]}" for k in range(seg.start, seg.shown_end))
    path = " › ".join(headings.before(seg.start)) or "none"
    return (f"## Document\nFile: {file_name}. Headings in force before these lines: {path}.\n\n"
            f"## Offence codes you may use (anything else: \"other\")\n{labels}\n\n"
            f"## Lines to cover\nList the cases that START on lines L{seg.start + 1}–L{seg.own_end}. "
            + (f"Lines after L{seg.own_end} are shown only so that you can see where a case ends.\n\n"
               if seg.shown_end > seg.own_end else "\n\n")
            + f"## Lines shown (L{seg.start + 1}–L{seg.shown_end})\n{shown}")


def _chunk_one(llm: LLM, file_name: str, lines: list[str], seg: Segment, labels, headings: HeadingIndex,
               step: str) -> tuple[list[tuple[Segment, object]], list[CallRecord], list[tuple[Segment, str]]]:
    """One part. If the answer was cut short (too many cases for one answer), the part is halved and each half
    asked again, up to CHUNK_MAX_SPLITS times. Returns (cases with their part, calls, parts that failed)."""
    try:
        result, record = llm.structured(step, config.CHUNK_ROLE, prompts.CHUNKING,
                                        segment_input(file_name, lines, seg, labels, headings), ChunkResult)
        return [(seg, c) for c in result.cases], [record], []
    except LLMError as exc:
        calls = [exc.record] if exc.record else []
        halves = split_segment(lines, seg)
        if "cut short" in str(exc) and seg.depth < config.CHUNK_MAX_SPLITS and len(halves) == 2:
            found, failed = [], []
            for k, half in enumerate(halves):
                f, c, e = _chunk_one(llm, file_name, lines, half, labels, headings, f"{step}, half {k + 1}")
                found += f
                calls += c
                failed += e
            return found, calls, failed
        return [], calls, [(seg, str(exc))]


@dataclass
class ChunkReport:
    """What one document produced."""
    file_name: str
    lines: int
    segments: int
    cases: list[CaseRecord] = field(default_factory=list)
    calls: list[CallRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)         # parts whose call failed (after one retry)
    warnings: list[str] = field(default_factory=list)

    @property
    def ai_calls(self) -> int:
        return sum(1 for c in self.calls if c.kind == "ai")


def chunk_document(llm: LLM, kb: KnowledgeBase, file_name: str, text: str, progress=None,
                   parallel: int | None = None, chapters=None) -> ChunkReport:
    """Split a document into cases: one AI call per part (several at a time), one retry of each part that
    failed, then each case's lines taken from the document and checked. chapters: the Manual's chapters the
    document covers (each call lists only their offences); default every chapter of Part E."""
    lines = to_lines(text)
    segs = segments(lines)
    headings = HeadingIndex(lines)
    doc_id = hashlib.sha1("\n".join(lines).encode()).hexdigest()[:12]
    report = ChunkReport(file_name=file_name, lines=len(lines), segments=len(segs))
    labels, codes = kb.offence_code_list(chapters or config.CASE_LABEL_CHAPTERS)     # short codes (guide step 20)
    allowed = {"codes": codes, "ids": set(codes.values())}
    by_name = unique_names(kb, codes.values())
    found: list[tuple[Segment, object]] = []
    failed: list[tuple[Segment, str]] = []

    def step(seg):
        k = next((j for j, s in enumerate(segs) if s.start <= seg.start < s.own_end), 0)
        return f"Case database — chunking {file_name} (part {k + 1} of {len(segs)})"

    # 1. every part, several at a time. Progress is reported from this thread only. If the page is
    #    interrupted (a click, another page), the parts not yet started are cancelled.
    with ThreadPoolExecutor(max_workers=max(1, min(parallel or config.CHUNK_PARALLEL, len(segs)))) as pool:
        futures = [pool.submit(_chunk_one, llm, file_name, lines, seg, labels, headings, step(seg)) for seg in segs]
        try:
            for done, fut in enumerate(as_completed(futures), start=1):
                f, calls, fails = fut.result()
                found += f
                report.calls += calls
                failed += fails
                if progress:
                    progress(done / max(1, len(segs)), f"Chunked part {done} of {len(segs)}")
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
    # 2. one retry of each part (or half) that failed, e.g. after a rate limit — only that part
    still_failed: list[tuple[Segment, str]] = []
    for seg, _ in failed:
        f, calls, fails = _chunk_one(llm, file_name, lines, seg, labels, headings, step(seg) + " — retry")
        found += f
        report.calls += calls
        still_failed += fails
    report.errors = [f"lines {s.start + 1}–{s.own_end}: {e}" for s, e in still_failed]
    failed_starts = sorted(s.start for s, _ in still_failed)

    # 3. keep each case once, in the part that owns its first line. A first line a line or two off is first
    #    moved onto the line with the citation (looking at every line the part was shown).
    kept: dict[tuple, tuple[int, object, Segment]] = {}
    for seg, c in found:
        first = c.start_line - 1
        if not (seg.start <= first < seg.shown_end and first < len(lines)):
            continue
        first = _citation_line(lines, first, c.citation, seg.start, seg.shown_end)
        if not (seg.start <= first < seg.own_end):
            continue
        key = (first, citation_key(c.citation), _norm(c.offender), _norm(c.head_sentence_text),
               c.head_sentence_months, c.npp_months)          # co-offenders on one line differ by their figures
        kept.setdefault(key, (first, c, seg))
    starts = sorted({first for first, _, _ in kept.values()})

    # 4. each case's text: from its first line to its last (never into the next case)
    ordinal: Counter = Counter()
    for first, c, seg in sorted(kept.values(), key=lambda t: t[0]):
        later = [s for s in starts if s > first]
        next_first = later[0] if later else len(lines)
        if c.continues_past_shown_lines:
            last = next_first - 1
            for j in range(max(first + 1, seg.shown_end), next_first):   # stop at a heading past the lines shown
                if headings.is_heading(j):
                    last = j - 1
                    break
            gap = [s for s in failed_starts if first < s <= last]
            if gap:                                          # never run into a part that could not be chunked
                last = gap[0] - 1
                report.warnings.append(f"Lines {first + 1}–{last + 1} ({c.citation}): the case may continue into "
                                       "a part that could not be chunked — check its text.")
        else:
            last = min(max(first, c.end_line - 1), next_first - 1)
        last = min(last, len(lines) - 1)
        # The key names the citation only when it is a neutral citation: an "unreported" citation may be worded
        # differently each time the document is chunked.
        cit = citation_key(c.citation) if _NEUTRAL.search(c.citation or "") else ""
        ordinal[(first, cit)] += 1
        record = _record(c, "\n".join(lines[first:last + 1]), file_name, first, last, headings, allowed, by_name)
        record.key = f"{doc_id}|{first + 1}|{cit}|{ordinal[(first, cit)]}"
        if not record.citation_found:
            report.warnings.append(f"Lines {first + 1}–{last + 1}: the citation “{c.citation}” is not in the "
                                   "case's text — check this case.")
        report.cases.append(record)
    if not report.cases and not report.errors:
        report.warnings.append("No cases were found in this document.")
    return report


def _citation_line(lines: list[str], first: int, citation: str, lo: int, hi: int) -> int:
    """Where a case starts: the line with its citation (the model's line, or the nearest within 3, among the lines
    shown) — or the line naming the case just above it, as when a narrow PDF column puts "DPP v Lees" on one line,
    perhaps the court on the next, and "[2019] VCC 1234" after. A line that belongs to another case is never taken:
    a name line must hold this case's party names and must not be a labelled line of a case's body."""
    target = citation_key(citation)
    if not target:
        return first
    names = party_names(citation)

    def named(k: int) -> bool:
        if not (lo <= k < len(lines)) or not names or _NEUTRAL.search(lines[k]):
            return False
        label = re.match(r"^([A-Z][A-Za-z /()&'’-]{1,40}):\s", lines[k])
        if label and not re.search(r"case|name", label.group(1), re.I):
            return False                         # e.g. "Offence: Lees punched V" — another case's body
        return any(_contains(_norm(lines[k]), n) for n in names)

    if _contains(_norm(lines[first]), target):
        j = first
    else:
        near = [k for k in sorted(range(max(lo, first - 3), min(hi, first + 4)), key=lambda k: abs(k - first))
                if _contains(_norm(lines[k]), target)]
        if not near:
            return first
        j = near[0]
        if j > first and named(first):
            return first                         # the model's line holds the case's name
    if named(j - 1):
        return j - 1                             # the name line directly above the citation
    if named(j - 2) and not _NEUTRAL.search(lines[j - 1]):
        return j - 2                             # name, then the court, then the citation
    return j


def _label(value: str, name: str, allowed: dict, by_name: dict[str, str]) -> str | None:
    """A register id from the model's answer: a listed code ("27-05"), or a listed id, or — if neither — the offence's
    name when exactly one listed offence has it; otherwise None ("other")."""
    v = (value or "").strip().strip("[]`'\"").strip()
    if v in allowed["codes"]:
        return allowed["codes"][v]
    if v.lower() in allowed["ids"]:
        return v.lower()
    return by_name.get(_norm(name))           # the model gave the offence's name instead of its code


def _record(c, body, file_name, first, last, headings, allowed, by_name) -> CaseRecord:
    offences = [{"name": o.name.strip(), "offence_id": _label(o.offence_id, o.name, allowed, by_name),
                 "sentence_text": o.sentence_text.strip(), "sentence_months": _sane(o.sentence_months)}
                for o in c.offences]
    record = CaseRecord(
        id="", citation=c.citation.strip(), court=c.court,
        year=c.year if c.year and 1900 < c.year < 2100 else None,
        section=" › ".join(headings.before(first)),
        principal_offence_id=_label(c.principal_offence_id, c.principal_offence, allowed, by_name),
        principal_offence=c.principal_offence.strip(), offences=offences,
        head_sentence_text=c.head_sentence_text.strip(), head_sentence_months=_sane(c.head_sentence_months),
        npp_months=_sane(c.npp_months), plea=c.plea, offender=c.offender.strip(), summary=c.summary.strip(),
        text=body, source=file_name, lines=[first + 1, last + 1],
        citation_found=_contains(_norm(body), citation_key(c.citation)))
    record.figures_note = check_figures(record)
    return record


# ─────────────────────────────────────────────────────────────────────────────
# 5. The database
# ─────────────────────────────────────────────────────────────────────────────
def embed_text(c: CaseRecord) -> str:
    """What is embedded (and keyword-ranked) for each case: its principal offence, summary and text."""
    return f"{c.principal_offence}\n{c.summary}\n{c.text}"[:8000]


def _vec_to_b64(v: np.ndarray) -> str:
    return base64.b64encode(np.asarray(v, dtype=np.float32).tobytes()).decode("ascii")


def _vec_from_b64(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32).copy()


def _record_key(c: CaseRecord) -> str:
    """The case's key, from its document (set when it was chunked). A case from an older file has none: its key
    is worked out from its text, place, citation, offender and figures (so co-offenders sharing a text stay apart)."""
    return c.key or (f"{hashlib.sha1(c.text.encode()).hexdigest()[:12]}|{c.lines[0] if c.lines else 0}|"
                     f"{citation_key(c.citation)}|{_norm(c.offender)}|{_norm(c.head_sentence_text)}|"
                     f"{c.head_sentence_months}|{c.npp_months}")


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _num(value, field_name: str, k: int, allow_none: bool = True):
    if value is None and allow_none:
        return None
    if not _finite(value):                       # NaN and Infinity (which JSON readers accept) are refused too
        raise ValueError(f"case {k + 1}: “{field_name}” should be a number")
    return float(value)


def _validated(d, k: int) -> dict:
    """One case from a saved file, checked; ValueError names what is wrong."""
    if not isinstance(d, dict):
        raise ValueError(f"case {k + 1} is not a JSON object")
    out = dict(d)
    for name in ("id", "citation", "text"):
        if not isinstance(d.get(name), str):
            raise ValueError(f"case {k + 1} has no “{name}”")
    if not re.fullmatch(r"C\d+", d["id"]):
        raise ValueError(f"case {k + 1}: its id “{d['id']}” is not like C1, C2 …")
    if not d["text"].strip():
        raise ValueError(f"case {k + 1} ({d['id']}) has no text")
    for name in ("court", "section", "principal_offence", "head_sentence_text", "plea", "offender", "summary",
                 "source", "figures_note", "key"):
        if d.get(name) is not None and not isinstance(d.get(name), str):
            raise ValueError(f"case {k + 1} ({d['id']}): “{name}” should be text")
        out[name] = d.get(name) or ("unknown" if name in ("court", "plea") else "")
    if d.get("principal_offence_id") is not None and not isinstance(d.get("principal_offence_id"), str):
        raise ValueError(f"case {k + 1} ({d['id']}): “principal_offence_id” should be text")
    out["principal_offence_id"] = d.get("principal_offence_id") or None
    out["head_sentence_months"] = _num(d.get("head_sentence_months"), "head_sentence_months", k)
    out["npp_months"] = _num(d.get("npp_months"), "npp_months", k)
    year = d.get("year")
    out["year"] = int(year) if _finite(year) else None
    offences = d.get("offences") or []
    if not isinstance(offences, list) or not all(isinstance(o, dict) for o in offences):
        raise ValueError(f"case {k + 1} ({d['id']}): “offences” should be a list of objects")
    out["offences"] = [{"name": str(o.get("name") or ""), "offence_id": o.get("offence_id") if isinstance(
                            o.get("offence_id"), str) else None,
                        "sentence_text": str(o.get("sentence_text") or ""),
                        "sentence_months": _num(o.get("sentence_months"), "sentence_months", k)} for o in offences]
    lines = d.get("lines")
    out["lines"] = [int(x) for x in lines[:2]] if isinstance(lines, list) and len(lines) >= 2 and all(
        _finite(x) for x in lines[:2]) else [0, 0]
    for name, default in (("use", True), ("edited", False), ("citation_found", True)):
        out[name] = d.get(name) if isinstance(d.get(name), bool) else default
    return out


@dataclass
class CaseDB:
    cases: list[CaseRecord] = field(default_factory=list)
    vectors: dict[str, np.ndarray] = field(default_factory=dict)    # case id -> unit vector
    origin: str = ""                                                 # where it came from, for the status line
    last_number: int = 0                                             # highest id number ever given (ids never reused)

    # ── adding ──
    def next_number(self) -> int:
        nums = [int(c.id[1:]) for c in self.cases if re.fullmatch(r"C\d+", c.id)]
        return max(nums + [self.last_number]) + 1

    def add(self, records: list[CaseRecord], vectors: dict[str, np.ndarray] | None = None) -> tuple[int, int]:
        """Add cases with new ids. A case already here (the same place in the same document) is skipped.
        `vectors`, keyed by the records' old ids, travel with them. Returns (added, skipped)."""
        seen = {_record_key(c) for c in self.cases}
        n, added, skipped = self.next_number(), 0, 0
        for c in records:
            key = _record_key(c)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            old, c.id, c.key = c.id, f"C{n}", key
            self.last_number = n
            n += 1
            if vectors is not None and old in vectors:
                self.vectors[c.id] = vectors[old]
            self.cases.append(c)
            added += 1
        return added, skipped

    def merge(self, other: "CaseDB") -> tuple[int, int]:
        copy = other.copy()
        return self.add(copy.cases, copy.vectors)

    def remove(self, ids: set[str]) -> None:
        self.last_number = max([self.last_number] + [int(c.id[1:]) for c in self.cases if re.fullmatch(r"C\d+", c.id)])
        self.cases = [c for c in self.cases if c.id not in ids]
        self.vectors = {k: v for k, v in self.vectors.items() if k not in ids}

    def get(self, case_id: str) -> CaseRecord | None:
        return next((c for c in self.cases if c.id == case_id), None)

    def usable(self) -> list[CaseRecord]:
        return [c for c in self.cases if c.use]

    def counts_by_offence(self, kb: KnowledgeBase) -> list[tuple[str, int]]:
        return Counter(kb.charge_label(c.principal_offence_id) if c.principal_offence_id in kb.offences
                       else "Other offences" for c in self.cases).most_common()

    def same_decision(self) -> dict[str, str]:
        """case id -> the id of an earlier case that is the same decision listed again (same citation and the
        same total term and non-parole period, e.g. a case in both a summary table and a court table)."""
        first: dict[tuple, str] = {}
        out = {}
        for c in self.cases:
            cit = citation_key(c.citation)
            if not cit:
                continue
            k = (cit, c.head_sentence_months, c.npp_months)
            if k in first:
                out[c.id] = first[k]
            else:
                first[k] = c.id
        return out

    def describe(self) -> str:
        if not self.cases:
            return "empty"
        vec = len([c for c in self.cases if c.id in self.vectors])
        return (f"{len(self.cases)} cases ({len(self.usable())} in use), {vec} with embeddings"
                + (f" — {self.origin}" if self.origin else ""))

    # ── vectors ──
    def has_vectors(self) -> bool:
        """Every case in use has a vector (cosine similarity can rank them all)."""
        usable = self.usable()
        return bool(usable) and all(c.id in self.vectors for c in usable)

    def missing_vectors(self) -> list[CaseRecord]:
        return [c for c in self.cases if c.id not in self.vectors]

    def embed_missing(self, llm: LLM, progress=None) -> list[CallRecord]:
        """Embed the cases that have no vector yet (with the user's key). Raises LLMError
        (the vectors made before the failure are kept)."""
        todo, records = self.missing_vectors(), []
        for k in range(0, len(todo), config.CASE_EMBED_BATCH):
            batch = todo[k:k + config.CASE_EMBED_BATCH]
            vecs, record = llm.embed("Case database — embeddings", [embed_text(c) for c in batch])
            records.append(record)
            for c, v in zip(batch, vecs):
                self.vectors[c.id] = (v / (float(np.linalg.norm(v)) or 1.0)).astype(np.float32)
            if progress:
                progress(min(1.0, (k + len(batch)) / len(todo)), f"Embedded {k + len(batch)} of {len(todo)} cases")
        return records

    # ── saving and loading ──
    def to_json(self) -> str:
        return json.dumps({"format": DB_FORMAT, "version": 1, "saved": time.strftime("%Y-%m-%d %H:%M"),
                           "embedding_model": config.EMBEDDING_MODEL, "dimensions": config.EMBEDDING_DIMENSIONS,
                           "last_number": self.last_number, "cases": [c.to_dict() for c in self.cases],
                           "vectors": {k: _vec_to_b64(v) for k, v in self.vectors.items()}},
                          ensure_ascii=False, indent=1)

    @classmethod
    def from_json(cls, text: str, origin: str = "") -> "CaseDB":
        """A saved database. Raises ValueError (with what is wrong) for anything else."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"This file is not valid JSON ({exc}).") from None
        if not isinstance(data, dict) or data.get("format") != DB_FORMAT:
            raise ValueError("This file is not a case database saved by this app.")
        cases = data.get("cases", [])
        if not isinstance(cases, list):
            raise ValueError("The file's “cases” should be a list.")
        records = [CaseRecord.from_dict(_validated(d, k)) for k, d in enumerate(cases)]
        ids = [c.id for c in records]
        if len(set(ids)) != len(ids):
            raise ValueError("The file gives two cases the same id.")
        for c in records:          # the checks are made again (a file from an older version may hold filled figures)
            c.figures_note = check_figures(c)
        last = data.get("last_number")
        db = cls(cases=records, origin=origin, last_number=int(last) if _finite(last) else 0)
        vectors = data.get("vectors") or {}
        if not isinstance(vectors, dict):
            raise ValueError("The file's “vectors” should be an object.")
        if (data.get("embedding_model") == config.EMBEDDING_MODEL
                and data.get("dimensions") == config.EMBEDDING_DIMENSIONS):
            for k, v in vectors.items():       # vectors from another model cannot be compared: they are made again
                try:
                    vec = _vec_from_b64(v)
                except (ValueError, TypeError):
                    continue
                if k in ids and vec.shape == (config.EMBEDDING_DIMENSIONS,) and np.isfinite(vec).all():
                    db.vectors[k] = vec
        return db

    def copy(self) -> "CaseDB":
        return CaseDB(cases=[CaseRecord.from_dict(c.to_dict()) for c in self.cases],
                      vectors={k: v.copy() for k, v in self.vectors.items()}, origin=self.origin,
                      last_number=self.last_number)


def load_committed(path=None) -> CaseDB | None:
    """The database committed to the repository (cases/database.json), if there is one."""
    path = path or (config.CASES_DIR / "database.json")
    if not path.exists():
        return None
    return CaseDB.from_json(path.read_text(encoding="utf-8"), origin=f"{path.parent.name}/{path.name}")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Choosing the most similar cases for a run
# ─────────────────────────────────────────────────────────────────────────────
MATCHES = ["same offence", "offence also charged in that case", "related offence (same section of the register)",
           "other offence"]
POLICY = ("app policy: cases for the same offence first, then cases in which that offence was also charged, then cases "
          "for an offence in the same section of the Manual's offence register (e.g. robbery, burglary and home invasion "
          "in 27.1.1.2; the causing injury offences in 25.1.1); cases for other offences are not used; a case that "
          "looks like the case being estimated, or that you left out, is not used, nor is any other listing of the "
          "same decision; the same decision listed twice is used once")


def case_query_text(kb: KnowledgeBase, facts) -> str:
    """The text compared with the cases: the offence and the confirmed facts."""
    lines = " ".join(f["text"] for f in facts.facts)[:6000]
    return f"{kb.offence_label(facts.case.offence_id)}\n{facts.summary}\n{lines}"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z]{3,}", (text or "").lower())


def keyword_scores(cases: list[CaseRecord], query: str) -> dict[str, float]:
    """BM25 scores, scaled so that the best match scores 1 (used when the cases or the query have no vectors)."""
    docs = {c.id: _words(embed_text(c)) for c in cases}
    n = len(docs) or 1
    avg = sum(len(t) for t in docs.values()) / n or 1.0
    df = Counter(w for t in docs.values() for w in set(t))
    q = set(_words(query))
    raw = {}
    for cid, toks in docs.items():
        tf = Counter(toks)
        raw[cid] = sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) * tf[w] * 2.5
                       / (tf[w] + 1.5 * (0.25 + 0.75 * len(toks) / avg)) for w in q if tf[w])
    top = max(raw.values(), default=0.0) or 1.0
    return {k: v / top for k, v in raw.items()}


_LABEL_PREFIX = re.compile(r"(?m)^[A-Z][A-Za-z /()&'’-]{1,40}:\s*")


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9]+", _LABEL_PREFIX.sub("", text or "").lower())
    return {tuple(words[k:k + 3]) for k in range(len(words) - 2)}


def text_overlap(case_text: str, narrative: str) -> float:
    """Share of the case's three-word sequences (column headings removed) that also appear in the narrative:
    near 1 when the case's own text was pasted in, low for a different case even on similar facts. 0 for a case
    too short to tell (fewer than 20 sequences)."""
    a = _shingles(case_text)
    if len(a) < 20:
        return 0.0
    return len(a & _shingles(narrative)) / len(a)


_PARTY_STOP = {"r", "v", "vs", "the", "king", "queen", "dpp", "director", "of", "public", "prosecutions",
               "prosecution", "and", "anor", "ors", "another", "others", "re", "in", "matter", "pseudonym", "appeal",
               "application", "for", "leave", "to", "state", "victoria", "cth", "commonwealth", "test", "case",
               "police", "attorney", "general"}


def party_names(name: str) -> set[str]:
    """The distinctive party names in a case name ("DPP v Moretti (test case 1)" -> {"moretti"})."""
    s = re.sub(r"\([^)]*\)", " ", name or "")
    s = re.split(r"\[(?:19|20)\d\d\]", s)[0]
    return {w for w in re.findall(r"[a-z][a-z'’-]+", s.lower()) if w not in _PARTY_STOP and len(w) >= 3}


@dataclass
class Comparables:
    """The comparable cases one run was given (a snapshot: later changes to the database do not affect it)."""
    selected: list[dict] = field(default_factory=list)    # {id, similarity, match, case, offence_sentence, same_as}
    left_out: list[dict] = field(default_factory=list)    # the same, plus "reason"
    method: str = ""
    score_label: str = "similarity"                       # "similarity" (cosine) or "keyword score" (BM25)
    database: str = ""                                     # the database's status line at the time
    available: int = 0                                     # cases in use in the database at the time
    note: str = ""                                         # e.g. why none was selected

    @property
    def ids(self) -> set[str]:
        return {e["id"] for e in self.selected}

    def case(self, case_id: str) -> CaseRecord | None:
        e = next((e for e in self.selected if e["id"] == case_id), None)
        return CaseRecord.from_dict(e["case"]) if e else None

    def block(self) -> str:
        """The section given to the comparables stage, the synthesis and the audit ("" if the database is empty)."""
        if not self.available:
            return ""
        head = ("## Comparable cases from your case database (cite by id in case_ids; yardsticks only, never a "
                f"source of law)\nChosen by {self.method} ({POLICY}).")
        if not self.selected:
            return f"{head}\nNone: {self.note}"
        return f"{head}\n\n{comparables_block(self.selected, score_label=self.score_label)}"


def _id_number(case_id: str) -> int:
    return int(case_id[1:]) if re.fullmatch(r"C\d+", case_id or "") else 10 ** 9


def select_comparables(db: CaseDB | None, offence_id: str, query_vector=None, query_text: str = "",
                       k: int | None = None, case_name: str = "", narrative: str = "",
                       exclude_ids=(), related: set[str] | None = None) -> Comparables:
    """The k cases most similar to the confirmed facts, the same offence first (see POLICY).
    Cosine similarity of query_vector with the case vectors if every case in use has one; otherwise keyword
    ranking of query_text. A case is left out if the user left it out, or if it looks like the case being
    estimated: its party names or citation match the case name, a cosine similarity of SAME_CASE_SIMILARITY or
    more, or SAME_CASE_TEXT_OVERLAP of its text is in the narrative."""
    out = Comparables(database=db.describe() if db else "no case database")
    cases = db.usable() if db else []
    out.available = len(cases)
    if not cases:
        out.note = "The case database is empty (add cases on the Case database page)."
        return out
    if query_vector is not None and db.has_vectors():
        q = np.asarray(query_vector, dtype=np.float32)
        q = q / (float(np.linalg.norm(q)) or 1.0)
        scores = {c.id: float(np.dot(db.vectors[c.id], q)) for c in cases}
        out.method = "cosine similarity of the embeddings"
    else:
        scores = keyword_scores(cases, query_text)
        out.method = "keyword ranking (BM25), because " + (
            "some cases in use have no embeddings" if not db.has_vectors() else "the facts were not embedded")
        out.score_label = "keyword score"
    # The "related offences" tier: the other offences in the charge's section of the register (related_offences;
    # app policy) — a theft is never given s 16 or s 17 cases as yardsticks.
    related = set(related or ()) - {offence_id}

    def tier(c: CaseRecord) -> int:
        if c.principal_offence_id == offence_id:
            return 0
        if offence_id in {o.get("offence_id") for o in c.offences}:
            return 1
        return 2 if c.principal_offence_id in related else 3

    name_norm = _norm(re.sub(r"\([^)]*\)", " ", case_name or ""))
    names = party_names(case_name)
    excluded = set(exclude_ids or ())

    def reason_for(c: CaseRecord, similarity: float) -> str:
        theirs = party_names(c.citation)
        cit = citation_key(c.citation)
        if c.id in excluded:
            return "you left it out"
        if names and names <= theirs:
            return "its party names match the case being estimated"
        if cit and _NEUTRAL.search(c.citation or "") and _contains(name_norm, cit):
            return "its citation is in the case name"
        if out.method.startswith("cosine") and similarity >= config.SAME_CASE_SIMILARITY:
            return f"similarity {similarity} — probably the case being estimated"
        if narrative and text_overlap(c.text, narrative) >= config.SAME_CASE_TEXT_OVERLAP:
            return "most of its text is in the case narrative — probably the case being estimated"
        return ""

    direct = {c.id: r for c in cases if (r := reason_for(c, round(scores.get(c.id, 0.0), 3)))}
    # A decision listed more than once (the same citation) is left out with any listing that is left out.
    by_citation: dict[str, list[str]] = {}
    for c in cases:                     # only a neutral citation identifies a decision ("Smith (CoA)" may be several)
        if _NEUTRAL.search(c.citation or ""):
            by_citation.setdefault(citation_key(c.citation), []).append(c.id)
    reasons = dict(direct)
    for c in cases:
        if c.id not in direct:
            twin = next((i for i in by_citation.get(citation_key(c.citation), []) if i in direct), None)
            if twin:
                reasons[c.id] = f"the same decision as {twin}, which is left out ({direct[twin]})"
    pool = []
    for c in cases:
        t = tier(c)
        entry = {"id": c.id, "similarity": round(scores.get(c.id, 0.0), 3), "match": MATCHES[t], "case": c.to_dict(),
                 "offence_sentence": c.sentence_for(offence_id), "same_as": []}
        if c.id in reasons:
            out.left_out.append(dict(entry, reason=reasons[c.id]))
        elif t == 3 and not config.CASES_INCLUDE_OTHER_OFFENCES:
            continue                                        # not an injury offence: never a yardstick here
        else:
            pool.append((t, -scores.get(c.id, 0.0), _id_number(c.id), entry))
    pool.sort(key=lambda p: p[:3])
    chosen, seen = [], {}
    for *_, entry in pool:                                  # the same decision listed twice is used once
        c = entry["case"]
        dup = (citation_key(c["citation"]), c["head_sentence_months"], c["npp_months"])
        if dup[0] and dup in seen:
            seen[dup]["same_as"].append(entry["id"])
            continue
        seen[dup] = entry
        chosen.append(entry)
    out.selected = chosen[:k or config.CASES_FOR_COMPARISON]
    if not out.selected:
        out.note = (f"none of the {len(cases)} cases in use is for this offence"
                    + (" or an offence in the same section of the register" if related else "")
                    + (f" ({len(out.left_out)} left out)" if out.left_out else "") + ".")
    return out


def months_label(m) -> str:
    """Months as "7y 6m (90 months)"; "—" if none."""
    if m is None:
        return "—"
    m = float(m)
    y, mo = divmod(int(round(m)), 12)
    return (f"{y}y {mo}m" if y and mo else f"{y}y" if y else f"{mo}m") + f" ({m:g} months)"


def comparables_block(selected: list[dict], chars: int | None = None, score_label: str = "similarity") -> str:
    """The selected cases written out for the AI calls (cited by id)."""
    chars, blocks = chars or config.CASE_TEXT_CHARS, []
    for e in selected:
        c = CaseRecord.from_dict(e["case"])
        offences = "; ".join(o["name"] + (f" — {o['sentence_text']}" if o.get("sentence_text") else "")
                             for o in c.offences) or "—"
        text = c.text if len(c.text) <= chars else c.text[:chars] + " …"
        blocks.append(
            f"[{c.id}] {c.citation} — {c.court}{', ' + str(c.year) if c.year else ''}; {e['match']}; "
            f"{score_label} {e['similarity']}"
            + (f" (the same decision is also listed as {', '.join(e['same_as'])})" if e.get("same_as") else "") + "\n"
            f"Principal offence: {c.principal_offence or '—'}; its individual sentence: "
            f"{months_label(c.principal_sentence_months)}\n"
            f"Individual sentence for the offence being estimated (if charged): {months_label(e.get('offence_sentence'))}\n"
            f"Offences and individual sentences: {offences}\n"
            f"Head sentence / orders: {c.head_sentence_text or '—'} (total term {months_label(c.head_sentence_months)}; "
            f"non-parole period {months_label(c.npp_months)}); plea: {c.plea.replace('_', ' ')}; offender: "
            f"{c.offender or '—'}"
            + (f"\nFigures to check: {c.figures_note}" if c.figures_note else "") + "\n"
            f"Text of the summary (word for word): {text}")
    return "\n\n".join(blocks)
