"""
core/kb.py — LOADS THE KNOWLEDGE BASE AND ANSWERS QUESTIONS ABOUT IT.

The knowledge base in knowledge/ was extracted from the Victorian Sentencing
Manual and nothing else (see knowledge/SCHEMA.md for every field). This module:

  1. loads the files once (Streamlit caches the result for all users — it is
     public data and holds no secrets);
  2. indexes every item by id, including nested entries (e.g. the rule
     "cco.maximum_term" lives inside the item "order.cco");
  3. picks the items relevant to one reasoning stage and one offence;
  4. writes items into the text an AI call is shown (id, title, statement,
     verbatim quotes);
  5. renders citations: "VSM 25.1.3.2 (pp 497–498)" plus the footnotes and the
     authorities they cite.

Rules carried over from the knowledge base (do not remove):
  * Only "support" quotes are cited as authority. Quotes marked residual=True
    are context only (SCHEMA.md, "residual").
  * Commonwealth items (jurisdiction "cth") are never used for reasoning.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from core import config

# Files that hold curated items (each a list of items with an "id").
ITEM_FILES = ["method", "principles", "factors", "rules", "orders",
              "offence_guides", "ancillary", "audit", "flags", "intake"]

# Nested lists inside items whose entries can carry their own ids and refs.
NESTED_KEYS = ["rules", "cautions", "examples", "factors", "limbs", "indicators",
               "counter_indicators", "principles", "exceptions", "not_late",
               "indicia", "forms", "mitigating", "aggravating", "serious_offences"]


# ─────────────────────────────────────────────────────────────────────────────
# Citation: one verbatim quote, ready to display
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Footnote:
    n: int
    text: str                      # the footnote's own text (usually the citation)
    authorities: list[str] = field(default_factory=list)   # names from authorities.json


@dataclass
class Citation:
    label: str                     # e.g. "VSM 25.1.3.2 (pp 497–498)"
    section: str
    quote: str
    footnotes: list[Footnote] = field(default_factory=list)
    residual: bool = False         # True = context only, never authority


def _pages_text(pages) -> str:
    """[497, 498] -> 'pp 497–498'; [497, 497] -> 'p 497'."""
    if not pages:
        return ""
    if isinstance(pages, int):
        return f"p {pages}"
    first, last = pages[0], pages[-1]
    return f"p {first}" if first == last else f"pp {first}–{last}"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# The penalty tables the Manual's text holds only as verbatim text (penalty_scale.json "historic_tables";
# flag.historic_tables_unstructured): (table, first day, last day, the register sections whose offences it
# covers). The user must confirm the maximum from the table (audit.offence_date_and_historic_offending).
SEXUAL_GROUPS = ("24.1.1", "24.1.2", "24.1.3", "24.1.4", "24.1.5")
HISTORIC_TABLES = [
    ("24.1.6.1", "1991-08-05", "2017-06-30", ("24.1.1",)),     # sexual offences against adults
    ("24.1.6.2", "1991-08-05", "2017-06-30", ("24.1.2",)),     # sexual offences against or involving children
    ("24.1.6.3", "1996-01-01", "2017-06-30", ("24.1.3",)),     # child abuse material offences
    ("24.1.6.4", "1991-08-05", "2017-06-30", ("24.1.4",)),     # sexual offences against the cognitively impaired
    ("24.1.6.5", "2004-05-19", "2017-06-30", ("24.1.5",)),     # sexual servitude offences
    ("24.1.6.6", "1981-03-01", "1991-08-04", SEXUAL_GROUPS),   # sexual offences
    ("24.1.6.7", "1959-04-01", "1981-02-28", SEXUAL_GROUPS),   # sexual offences
]
SEXUAL_TABLES_FROM, SEXUAL_TABLES_TO = "1959-04-01", "2017-06-30"
OHS_1985_TABLE, OHS_2004_FROM = "31.1.2", "2005-07-01"      # repealed Occupational Health and Safety Act 1985


# ─────────────────────────────────────────────────────────────────────────────
# The knowledge base
# ─────────────────────────────────────────────────────────────────────────────
class KnowledgeBase:
    """Everything the app knows about the law, loaded from knowledge/."""

    # ── 1. Loading ──────────────────────────────────────────────────────────
    def __init__(self, directory: Path = config.KNOWLEDGE_DIR):
        self.dir = Path(directory)

        # items: every id (top-level and nested) -> the item dict.
        # Each stored item gets two helper keys: "_file" and "_parent".
        self.items: dict[str, dict] = {}
        for name in ITEM_FILES:
            for item in self._load_json(f"{name}.json"):
                self._add_item(item, name, parent=None)

        # meta: one flat summary row per top-level item (used for retrieval).
        self.meta: dict[str, dict] = {}
        for line in (self.dir / "kb_items.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                self.meta[row["id"]] = row

        # The offence register, the penalty scale and the stage descriptions.
        self.offences: dict[str, dict] = {o["id"]: o for o in self._load_json("offences.json")}
        self.penalty_scale: dict = self._load_json("penalty_scale.json")
        self.stage_map: dict = self._load_json("stage_map.json")
        self.toc: list = self._load_json("vsm_toc.json")

        # Authorities (cases, legislation) and footnotes, for citations.
        self.authorities: dict[str, dict] = {a["id"]: a for a in self._load_json("authorities.json")}
        self.footnote_authorities: dict[str, dict] = self._load_json("footnote_authorities.json")
        self.footnotes: dict[int, dict] = {}
        for line in (self.dir / "vsm_footnotes.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                fn = json.loads(line)
                self.footnotes[int(fn["n"])] = fn

        # Retrieval chunks: the Manual's whole text in passages of ≤ 350 words.
        self.chunks: dict[str, dict] = {}
        for line in (self.dir / "vsm_chunks.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                ch = json.loads(line)
                self.chunks[ch["id"]] = ch

        # The penalty-scale rules (scale.*) are stored differently: give them
        # the same shape as other items so they can be cited.
        self._add_scale_rules()

    def _load_json(self, name: str):
        return json.loads((self.dir / name).read_text(encoding="utf-8"))

    def _add_item(self, item: dict, file: str, parent: str | None):
        item_id = item.get("id")
        if isinstance(item_id, str):
            item["_file"] = file
            item["_parent"] = parent
            self.items[item_id] = item
        for key in NESTED_KEYS:
            for entry in item.get(key) or []:
                if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                    self._add_item(entry, file, parent=item_id)

    def _add_scale_rules(self):
        """penalty_scale.json rules have {id, text, quote}; find the quote's
        section in the Manual text so the rule can be cited like any item."""
        for rule in self.penalty_scale.get("penalty_scale", {}).get("rules", []):
            quote = _norm(rule.get("quote") or "")
            section = ""
            if quote:
                probe = quote[:80]
                for ch in self.chunks.values():
                    if probe in _norm(ch.get("text", "")):
                        section = ch["section"]
                        break
            self.items[rule["id"]] = {
                "id": rule["id"], "title": rule["text"][:80], "statement": rule["text"],
                "check": "info", "stage": ["framework"], "_file": "penalty_scale", "_parent": None,
                "refs": [{"s": section, "q": rule.get("quote") or "", "pages": self._section_pages(section)}]
                if quote else [],
            }

    def _section_pages(self, section: str):
        for ch in self.chunks.values():
            if ch["section"] == section:
                return ch.get("pages")
        return None

    # ── 2. Looking things up ────────────────────────────────────────────────
    def get(self, item_id: str) -> dict | None:
        return self.items.get(item_id)

    def exists(self, item_id: str) -> bool:
        return item_id in self.items

    def title(self, item_id: str) -> str:
        item = self.items.get(item_id) or {}
        return item.get("title") or item.get("statement", "")[:80] or item_id

    def statement(self, item_id: str) -> str:
        item = self.items.get(item_id) or {}
        return item.get("statement") or item.get("text") or ""

    def offence(self, offence_id: str) -> dict:
        return self.offences[offence_id]

    def offence_chapter(self, offence_id: str) -> str:
        """The Part E chapter the offence's register row sits in, e.g. "27"."""
        return str(self.offences[offence_id].get("chapter") or "")

    def offence_aliases(self, offence_id: str) -> list[str]:
        """The offence's id and the ids of the same offence listed again in another table (also_listed_as:
        conspiracy to defraud is in chapters 27 and 32)."""
        return [offence_id] + [a for a in self.offences[offence_id].get("also_listed_as") or [] if a in self.offences]

    def offence_chapters(self, offence_id: str) -> set[str]:
        """Every Part E chapter the offence is listed in (its own and its aliases')."""
        return {self.offence_chapter(a) for a in self.offence_aliases(offence_id)}

    def chapter_title(self, chapter: str) -> str:
        """'27 — Property Offences' (from the Manual's table of contents)."""
        for entry in self.toc:
            if str(entry.get("id")) == str(chapter):
                return f"{chapter} — {entry.get('title')}"
        return str(chapter)

    def historic_tables(self, offence_id: str, start: str, end: str | None = None) -> tuple[str, list[str]] | None:
        """For offending in a period whose penalty table the Manual holds only as text: ("table", [sections]);
        sexual offending before 1 April 1959 (no table): ("none_before_1959", []); a sexual offence in the
        1959–2017 window that no table covers: ("no_table", []). None if no historic table applies."""
        row = self.offences[offence_id]
        dates = [start] + ([end] if end else [])
        chapter, group = str(row.get("chapter")), ".".join(str(row.get("section") or "").split(".")[:3])
        if chapter == "31":
            return ("table", [OHS_1985_TABLE]) if any(x < OHS_2004_FROM for x in dates) else None
        if chapter != "24":
            return None
        if any(x < SEXUAL_TABLES_FROM for x in dates):
            return "none_before_1959", []
        window = [x for x in dates if x <= SEXUAL_TABLES_TO]
        if not window:
            return None
        hits = sorted({t for t, first, last, groups in HISTORIC_TABLES
                       if group in groups and any(first <= x <= last for x in window)})
        return ("table", hits) if hits else ("no_table", [])

    def section_text(self, section: str) -> str:
        """The Manual's text of one section (its passages in order), e.g. a historic penalty table."""
        parts = sorted((cid for cid, ch in self.chunks.items() if str(ch.get("section")) == str(section)),
                       key=lambda c: int(c.split("#")[-1]) if c.split("#")[-1].isdigit() else 0)
        return "\n\n".join(self.chunks[c].get("text") or "" for c in parts)

    def charge_label(self, offence_id: str) -> str:
        """The charge box's label, name first so typing a word finds it: '27 · Home invasion — Crimes Act 1958 s 77A'."""
        o = self.offences[offence_id]
        act = o.get("act") or ""
        act = "Crimes Act 1958" if act.lower().startswith("crimes") else act
        prov = o.get("provision_norm") or o.get("provision") or ""
        where = " ".join(x for x in (act, prov) if x)
        return f"{o.get('chapter')} · {o['offence']}" + (f" — {where}" if where else "")

    def offence_codes(self) -> dict[str, str]:
        """Short codes for the simple input's sorting call ("27-05" -> offence id), so the call is not shown 335
        long ids. Built from config.IN_SCOPE_OFFENCES, numbered within each chapter in register order."""
        codes: dict[str, str] = {}
        count: dict[str, int] = {}
        for oid in config.IN_SCOPE_OFFENCES:
            ch = self.offence_chapter(oid)
            count[ch] = count.get(ch, 0) + 1
            codes[f"{ch}-{count[ch]:02d}"] = oid
        return codes

    def offence_code_list(self, chapters=None) -> tuple[str, dict[str, str]]:
        """The offence codes as an AI call is shown them, under chapter headings ("### Chapter 27 — Property
        Offences", then "27-06: Crimes Act 1958 s 75A — Armed robbery"), and code -> offence id. chapters: only the
        offences listed in those chapters (the case database's chunking); default every offence. Used by the simple
        input's sorting call and the chunking calls: all 335 offences as "id: name" lines would be about 43,000
        characters on every call."""
        wanted = {str(c) for c in chapters} if chapters else None
        codes = {code: oid for code, oid in self.offence_codes().items()
                 if wanted is None or self.offence_chapters(oid) & wanted}
        lines, chapter = [], None
        for code, oid in codes.items():
            if self.offence_chapter(oid) != chapter:
                chapter = self.offence_chapter(oid)
                lines.append(f"### Chapter {self.chapter_title(chapter)}")
            lines.append(f"{code}: {self.offence_label(oid)}")
        return "\n".join(lines), codes

    def offence_label(self, offence_id: str) -> str:
        """'Crimes Act 1958 s 17 — Recklessly causing serious injury'."""
        o = self.offences[offence_id]
        act = o.get("act") or ""
        act = "Crimes Act 1958" if act.lower().startswith("crimes") else act
        prov = o.get("provision_norm") or o.get("provision") or ""
        where = " ".join(x for x in (act, prov) if x)
        return f"{where} — {o['offence']}" if where else o["offence"]

    # ── 3. Citations ────────────────────────────────────────────────────────
    def support_refs(self, item_id: str) -> list[dict]:
        """The quotes that support the item's statement (never residual ones)."""
        item = self.items.get(item_id) or {}
        return [r for r in item.get("refs") or [] if not r.get("residual") and r.get("q")]

    def citation(self, ref: dict) -> Citation:
        """Turn one stored ref into a displayable citation."""
        if ref.get("s"):
            section = str(ref["s"])
            pages = ref.get("pages")
            label = f"VSM {section}" + (f" ({_pages_text(pages)})" if pages else "")
        else:  # a footnote quote: {"fn": n, "q": ..., "section": ..., "page": ...}
            n = ref.get("fn")
            fn = self.footnotes.get(int(n)) if n is not None else None
            section = str(ref.get("section") or (fn or {}).get("section") or "")
            page = ref.get("page") or (fn or {}).get("page")
            label = f"VSM fn {n}" + (f" (s {section}, p {page})" if section else "")
        footnotes = [self.footnote(int(n)) for n in ref.get("fns") or []
                     if int(n) in self.footnotes]
        return Citation(label=label, section=section, quote=_norm(ref.get("q", "")),
                        footnotes=footnotes, residual=bool(ref.get("residual")))

    def footnote(self, n: int) -> Footnote:
        """Footnote n with the names of the authorities it cites."""
        fn = self.footnotes.get(n) or {}
        ids = (self.footnote_authorities.get(str(n)) or {}).get("authorities") or []
        names = [self.authorities[a]["name"] for a in ids if a in self.authorities]
        return Footnote(n=n, text=_norm(fn.get("text", "")), authorities=names)

    def citations_for(self, item_id: str, limit: int = 3) -> list[Citation]:
        return [self.citation(r) for r in self.support_refs(item_id)[:limit]]

    def chunk_citation(self, chunk_id: str) -> Citation | None:
        ch = self.chunks.get(chunk_id)
        if not ch:
            return None
        label = f"VSM {ch['section']}" + (f" ({_pages_text(ch.get('pages'))})" if ch.get("pages") else "")
        return Citation(label=label, section=ch["section"], quote=_norm(ch.get("text", "")))

    # ── 4. Choosing items for a stage and an offence ────────────────────────
    def candidates(self, stages: list[str], offence_id: str,
                   include_audit: bool = False, include_flags: bool = True) -> list[str]:
        """Top-level items that may matter at these reasoning stages for this
        offence: same stage, Victorian (not Commonwealth), and either general
        (no offence links) or linked to this offence."""
        out = []
        for item_id, m in self.meta.items():
            if m.get("jurisdiction") == "cth":
                continue
            if m["file"] == "intake":
                continue
            if m["file"] == "audit" and not include_audit:
                continue
            if m["file"] == "flags" and not include_flags:
                continue
            if not set(m.get("stage") or []) & set(stages):
                continue
            linked = set(m.get("offence_ids") or [])
            if linked and not linked & set(self.offence_aliases(offence_id)):
                continue
            if not linked and self._other_offence_chapter_only(m, self.offence_chapters(offence_id)):
                continue
            out.append(item_id)
        return out

    @staticmethod
    def _other_offence_chapter_only(meta_row: dict, chapters_of_offence) -> bool:
        """True if every section the item cites is in a Part E (offence)
        chapter other than the offence's own chapter(s) (e.g. occupational
        health and safety guidance never reaches an assault case)."""
        own = {chapters_of_offence} if isinstance(chapters_of_offence, str) else set(chapters_of_offence)
        chapters = {str(s).split(".")[0] for s in meta_row.get("sections") or [] if s}
        if not chapters:
            return False
        return chapters <= set(config.PART_E_CHAPTERS) and not chapters & own

    def audit_items(self, check: str) -> list[str]:
        """Audit item ids by check type: 'hard', 'gate' or 'critic'."""
        return [i for i, m in self.meta.items() if m["file"] == "audit" and m["check"] == check]

    def flags_for_offence(self, offence_id: str) -> list[str]:
        ids = set(self.offence_aliases(offence_id)) if offence_id in self.offences else {offence_id}
        out = []
        for item_id, item in self.items.items():
            if item.get("_file") != "flags" or item.get("_parent"):
                continue
            if ids & (set(item.get("offence_ids") or []) | set(item.get("offence_ids_all") or [])):
                out.append(item_id)
        return out

    def flags_related_to(self, item_ids: list[str]) -> list[str]:
        """Flags named in the 'related' list of these items, or whose own
        'related' list names one of them (audit.manual_flags)."""
        wanted = set(item_ids)
        out = set()
        for item_id in item_ids:
            for rel in (self.items.get(item_id) or {}).get("related") or []:
                if rel.startswith("flag.") and rel in self.items:
                    out.add(rel)
        for item_id, item in self.items.items():
            if item.get("_file") == "flags" and wanted & set(item.get("related") or []):
                out.add(item_id)
        return sorted(out)

    # ── 5. Writing items into an AI call's input ────────────────────────────
    def prompt_block(self, item_ids: list[str], quotes_for: set[str] | None = None,
                     quotes_per_item: int = config.QUOTES_PER_ITEM) -> str:
        """One block of text per item:
            [id] Title (kind; direction; check)
            Statement: ...
            Quote (VSM 5.2.1, p 123): "..."
        Only items in `quotes_for` get their verbatim quotes (to save tokens)."""
        quotes_for = quotes_for or set()
        blocks = []
        for item_id in item_ids:
            item = self.items.get(item_id)
            if not item:
                continue
            tags = [item.get("kind") or item.get("_file")]
            if item.get("direction"):
                tags.append(item["direction"])
            if item.get("check"):
                tags.append(f"check={item['check']}")
            lines = [f"[{item_id}] {self.title(item_id)} ({'; '.join(t for t in tags if t)})",
                     f"Statement: {_norm(self.statement(item_id))}"]
            if item.get("affects"):
                lines.append("Affects: " + ", ".join(item["affects"]))
            if item_id in quotes_for:
                for ref in self.support_refs(item_id)[:quotes_per_item]:
                    c = self.citation(ref)
                    lines.append(f'Quote ({c.label}): "{c.quote}"')
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def passage_block(self, chunk_ids: list[str]) -> str:
        """Manual passages (raw text) for the comparables stage."""
        blocks = []
        for cid in chunk_ids:
            ch = self.chunks.get(cid)
            if ch:
                c = self.chunk_citation(cid)
                blocks.append(f"[{cid}] {c.label} — {ch.get('title', '')}\n{_norm(ch.get('text', ''))}")
        return "\n\n".join(blocks)

    # ── 6. Retrieval helpers ────────────────────────────────────────────────
    def embed_text(self, kind: str, item_id: str) -> str:
        if kind == "item":
            return (self.meta.get(item_id) or {}).get("embed_text") or self.statement(item_id)
        return (self.chunks.get(item_id) or {}).get("embed_text") or ""

    def fingerprint(self) -> str:
        """A short hash of everything that is embedded; the search index stores
        it so the app can tell when the index is out of date."""
        h = hashlib.sha256()
        for item_id in sorted(self.meta):
            h.update(item_id.encode())
            h.update(self.embed_text("item", item_id).encode())
        for cid in sorted(self.chunks):
            h.update(cid.encode())
            h.update(self.embed_text("chunk", cid).encode())
        return h.hexdigest()[:16]

    def comparable_passages(self, chapters) -> list[str]:
        """Manual passages searched for 'most similar' material: the offence's
        own chapter(s) plus the general current-sentencing-practice sections."""
        own = {str(chapters)} if isinstance(chapters, (str, int)) else {str(c) for c in chapters}
        out = []
        for cid, ch in self.chunks.items():
            if ch.get("jurisdiction") == "Cth":
                continue
            section = str(ch.get("section") or "")
            if str(ch.get("chapter")) in own or \
                    any(section == s or section.startswith(s + ".") for s in config.COMPARABLES_GENERAL_SECTIONS):
                out.append(cid)
        return out
