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

    def offence_label(self, offence_id: str) -> str:
        """'Crimes Act 1958 s 17 — Recklessly causing serious injury'."""
        o = self.offences[offence_id]
        act = o.get("act") or ""
        act = "Crimes Act 1958" if act.lower().startswith("crimes") else act
        prov = o.get("provision_norm") or o.get("provision") or ""
        return f"{act} {prov} — {o['offence']}".strip()

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
            if linked and offence_id not in linked:
                continue
            if not linked and self._other_offence_chapter_only(m):
                continue
            out.append(item_id)
        return out

    @staticmethod
    def _other_offence_chapter_only(meta_row: dict) -> bool:
        """True if every section the item cites is in a Part E (offence)
        chapter other than the in-scope chapter(s)."""
        chapters = {str(s).split(".")[0] for s in meta_row.get("sections") or [] if s}
        if not chapters:
            return False
        return chapters <= set(config.PART_E_CHAPTERS) and not chapters & set(config.OFFENCE_CHAPTERS)

    def audit_items(self, check: str) -> list[str]:
        """Audit item ids by check type: 'hard', 'gate' or 'critic'."""
        return [i for i, m in self.meta.items() if m["file"] == "audit" and m["check"] == check]

    def flags_for_offence(self, offence_id: str) -> list[str]:
        out = []
        for item_id, item in self.items.items():
            if item.get("_file") != "flags" or item.get("_parent"):
                continue
            if offence_id in (item.get("offence_ids") or []) or offence_id in (item.get("offence_ids_all") or []):
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

    def comparable_passages(self) -> list[str]:
        """Manual passages searched for 'most similar' material: the offence
        chapter(s) plus the general current-sentencing-practice sections."""
        out = []
        for cid, ch in self.chunks.items():
            if ch.get("jurisdiction") == "Cth":
                continue
            section = str(ch.get("section") or "")
            if str(ch.get("chapter")) in config.OFFENCE_CHAPTERS or \
                    any(section == s or section.startswith(s + ".") for s in config.COMPARABLES_GENERAL_SECTIONS):
                out.append(cid)
        return out
