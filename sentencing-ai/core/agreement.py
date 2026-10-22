"""
core/agreement.py — HOW THE MOST SIMILAR CASES COMPARE WITH THE ESTIMATE (code; no AI call).

After the proposal is made, each comparable case the run was given (core/cases.py: the cases most similar to the
confirmed facts by cosine similarity, the same offence first) is placed against the proposal's indicative band:
was that case's sentence for this offence below, within or above the band? The share within the band, weighted
by each case's cosine similarity, is shown with the estimate (the result card, the report).

What the figure is: a comparison of the estimate with these few cases from the user's case database. The Manual
notes that "courts have used the material to assist in determining whether a particular sentence accords with
current sentencing practices" (VSM 5.2.9.3.2, on statistics), but also that "It will not be possible to discern a
‘sentencing practice’ for an offence when there are only a small number of sentences that have been imposed"
(VSM 5.2.9.3.1). With 3 to 8 cases, the figure is therefore described as a comparison with these cases, never as
current practice.

What it is not: the probability that a court will impose the sentence. Cosine similarity measures how alike two
texts are, not how likely an outcome is; there is no single correct sentence (VSM 2.1); "the sentencing range should
not be defined by only a few select cases" (VSM 5.2.9.3.1). The figure is computed after the proposal and never
shown to an AI call, so it cannot drive the estimate (audit.statistics_and_medians).

The rules (app policy):
  * only a sentence for the offence being estimated is compared: the case's individual sentence for that offence,
    or — when that offence is the case's only charge — its head sentence. A total sentence for several charges, a
    case for another offence (e.g. a related injury offence), a written sentence that cannot be tied to this offence,
    figures that disagree with the case's text, and a term of imprisonment not recorded in months are listed as not
    compared;
  * a term of imprisonment is within the band if the band includes imprisonment (alone or combined with a CCO) and
    the term is within the band's term range; any other order is within the band if the band includes that order
    (its length is not compared). Otherwise the case is below or above the band by the ordering the hard checks use
    (principle.sentence_type_ordering: imprisonment > CCO > fine > adjourned undertaking, discharge or dismissal),
    or "outside" when it falls between the band's orders or the ordering does not place it. A band that includes an
    order the ordering does not rank (an MTMO, a type of CCO) places no case and gives no share — placing only the
    cases within it would overstate the share. A written order the ordering does not rank (a youth order, an ICO, a
    suspended sentence) is not compared, whatever figure was recorded with it;
  * each case's weight is its cosine similarity (equal weights if the cases were ranked by keywords, or if fewer than
    AGREEMENT_MIN_CASES of them have a positive similarity);
  * there is no percentage unless at least config.AGREEMENT_MIN_CASES cases can be compared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import config
from core.cases import _DURATION, _ORIGINAL, _RESENTENCED, Comparables
from core.checks import SEVERITY, months_text
from core.schemas import Band

CUSTODIAL = {"imprisonment", "imprisonment_and_cco"}
ORDER_WORDS = {"imprisonment": "imprisonment", "imprisonment_and_cco": "imprisonment combined with a CCO",
               "cco": "community correction order", "mtmo": "mandatory treatment and monitoring order",
               "fine": "fine", "adjourned_undertaking": "adjourned undertaking", "discharge": "discharge",
               "dismissal": "dismissal", "life_imprisonment": "life imprisonment"}
LIFE = "life_imprisonment"
POSITIONS = ("below", "within", "above", "outside", "not compared")

# ── Reading an order from a written sentence ──
# The result of an appeal is not an order ("appeal dismissed", "leave to appeal refused", "dismissed the appeal"):
# its words are removed, but an order named between them stays ("Crown appeal against the CCO … dismissed").
_APPEAL_RESULT = re.compile(
    r"\b(?:appeals?|applications?|leave)\b[^;.\n]{0,60}?\b(?:dismissed|refused|allowed|granted|upheld)\b"
    r"|\b(?:dismissed|refused|allowed|granted|upheld)\b[^;.\n]{0,40}?\b(?:appeals?|applications?)\b", re.I)
_APPEAL_WORDS = re.compile(r"\b(?:appeals?|applications?|leave|dismissed|refused|allowed|granted|upheld)\b", re.I)
# A sentence that no longer stands: "… set aside" / "quashed", "X substituted for Y", a clause about an earlier sentence.
_SET_ASIDE = re.compile(r"\bset aside\b|\bquashed\b", re.I)
_SUBSTITUTED_FOR = re.compile(r"\bsubstituted\s+for\b", re.I)
_NEW_ORDER = re.compile(r"\b(?:imposed|substituted|ordered|made|resentenced|sentenced)\b", re.I)
_EARLIER = re.compile(r"\b(?:original(?:ly)?|previous(?:ly)?)\s+(?:sentence|sentenced|imposed|term|order)\b"
                      r"|\bat first instance\b", re.I)
_CLAUSES = re.compile(r"[;\n]|\.(?=\s+[A-Z])")
_LIFE = re.compile(r"\blife\s+(?:imprisonment|sentence|term)\b|\bimprison(?:ment|ed)\s+for\s+(?:the\s+term\s+of\s+"
                   r"(?:his|her|their)\s+natural\s+)?life\b|\bsentenced\s+to\s+life\b|\bsentence\s+of\s+life\b"
                   r"|^\W*life\b(?=\s*(?:$|[,;:(–—-]|with\b|and\b|NPP\b|non[- ]parole))", re.I)
_PRISON_WORD = re.compile(r"\bimprison|\bjail\b|\bgaol\b|\bprison\b|\bTES\b|non[- ]parole|\bNPP\b|time served|"
                          r"reckoned as (?:already )?served", re.I)
_ORDER_PATTERNS = [("cco", re.compile(r"\bCCO\b|community correction", re.I)),
                   ("fine", re.compile(r"\bfined?\b|\bfines\b", re.I)),
                   ("adjourned_undertaking", re.compile(r"undertaking|\bbond\b|adjourn", re.I)),
                   ("discharge", re.compile(r"\bdischarg", re.I)),
                   ("dismissal", re.compile(r"\bdismiss", re.I))]
# Orders the ordering of sentence types does not rank — named as orders, so that a CCO condition such as "drug and
# alcohol treatment" or a "residential treatment program", or a licence "suspended", is not taken for one.
_UNRANKED_ORDER = re.compile(
    r"youth (?:justice|residential) centre|youth (?:attendance|supervision|control|diversion) order|\bYJCO?\b|\bYRC\b|"
    r"\bYAO\b|\bYSO\b|\bMTMO\b|treatment and monitoring order|\bDATO\b|drug and alcohol treatment order|"
    r"drug treatment order|hospital security order|residential treatment order|\bRTO\b|\bCSTO\b|"
    r"custodial supervision order|suspended sentence|(?:wholly|partially) suspended|sentence (?:was |is )?suspended|"
    r"intensive correction order|\bICO\b|community[- ]based order|\bCBO\b|indefinite sentence", re.I)
_STOP = {"of", "the", "a", "an", "and", "in", "to", "with", "s", "or", "by", "for", "on", "at"}
# Every duration in a written sentence, hyphenated forms included ("a 2-year CCO").
_ANY_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*-?\s*(years?|yrs?|months?|mths?)\b", re.I)
MTMO_NOTE = "the mandatory treatment and monitoring order, which the ordering of sentence types does not rank"


@dataclass
class CaseOutcome:
    """One comparable case placed against the band."""
    id: str
    citation: str
    court: str
    year: int | None
    similarity: float
    match: str                    # how it was chosen: "same offence", "offence also charged in that case", …
    outcome: str                  # the sentence compared, in words ("3 years 6 months imprisonment"); "" if none
    source: str                   # where it comes from: "its sentence for this offence" | "its head sentence (…)" | ""
    kind: str | None              # "term" | "life" | an order (cco, fine, …) | None (not compared)
    months: float | None          # the term of imprisonment, if kind == "term"
    position: str                 # one of POSITIONS
    reason: str                   # how it was placed, or why it was not compared
    band_blocked: bool = False    # not compared because of the band (an order the ordering does not rank)

    @property
    def compared(self) -> bool:
        return self.position != "not compared"


@dataclass
class Agreement:
    """How the comparable cases compare with the proposal's band."""
    percent: int | None           # the similarity-weighted share within the band; None if too few cases
    within: int = 0
    below: int = 0
    above: int = 0
    outside: int = 0
    compared: int = 0             # cases whose sentence for this offence could be placed against the band
    given: int = 0                # cases the run was given
    available: int = 0            # cases in use in the database at the time of the run (0: no database)
    weighted: bool = True         # weights are cosine similarities (False: equal weights)
    weights_note: str = ""        # why equal weights were used, if they were
    score_label: str = "similarity"   # what the cases' scores are: "similarity" (cosine) or "keyword score"
    min_cases: int = 3
    rows: list[CaseOutcome] = field(default_factory=list)
    band_text: str = ""           # the band in words, as compared
    note: str = ""                # one sentence: what the figure says, or why there is none

    @property
    def counts_text(self) -> str:
        """"4 within · 1 below · 1 above" (only the positions that occur)."""
        parts = [f"{self.within} within"] + [f"{n} {p}" for n, p in ((self.below, "below"), (self.above, "above"),
                                                                    (self.outside, "outside")) if n]
        return " · ".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 1. The band in words, as compared
# ─────────────────────────────────────────────────────────────────────────────
def _span(lo, hi) -> str:
    if lo is not None and hi is not None:
        return f"{months_text(lo)} to {months_text(hi)}" if lo != hi else months_text(lo)
    if hi is not None:
        return f"up to {months_text(hi)}"
    if lo is not None:
        return f"at least {months_text(lo)}"
    return ""


def band_text(band: Band) -> str:
    """e.g. "imprisonment of 2 years 6 months to 4 years 6 months, or community correction order of 1 year to 2 years"."""
    types = list(dict.fromkeys(band.disposition_types))
    term = _span(band.term_low_months, band.term_high_months)
    cco = _span(band.cco_low_months, band.cco_high_months)
    custodial = [t for t in types if t in CUSTODIAL]
    parts = []
    if custodial:
        word = " or ".join(ORDER_WORDS[t] for t in custodial)
        parts.append(f"{word} of {term}" if term else word)
        if "imprisonment_and_cco" in custodial and cco and "cco" not in types:
            parts[-1] += f" (CCO of {cco})"
    for t in types:
        if t in CUSTODIAL:
            continue
        word = ORDER_WORDS.get(t, t.replace("_", " "))
        if t == "cco" and cco:
            word += f" of {cco}"
        elif t == "mtmo" and term and not custodial:
            word += f" with imprisonment of {term}"
        parts.append(word)
    return ", or ".join(parts) if parts else "no disposition named"


# ─────────────────────────────────────────────────────────────────────────────
# 2. One case's sentence for this offence
# ─────────────────────────────────────────────────────────────────────────────
def _after_set_aside(clause: str) -> str:
    """What stands after "set aside" or "quashed": "3 years set aside and a CCO substituted" → "and a CCO
    substituted"; "set aside the CCO and imposed a fine" → "a fine"; "set aside the CCO" → ""."""
    parts = _SET_ASIDE.split(clause)
    if len(parts) == 1:
        return clause
    after = parts[-1]
    if re.match(r"\s*(?:the|a|an|his|her|their|that|this)\b", after, re.I):      # its object: the order set aside
        new = _NEW_ORDER.search(after)
        return after[new.end():] if new else ""
    return after


def _final_orders(head: str) -> str:
    """The orders a written sentence ends with: after "resentenced" on appeal (or, for "… a CCO of 2 years
    substituted", the clause ending with that word; for "a fine substituted for the CCO", what comes before); what
    stands after "set aside" or "quashed"; clauses about an earlier sentence, notes on the original sentence and the
    words of the appeal's result removed."""
    head = _ORIGINAL.sub(" ", head or "")
    instead = _SUBSTITUTED_FOR.search(head)
    if instead:
        head = _CLAUSES.split(head[:instead.start()])[-1]
    marks = list(_RESENTENCED.finditer(head))
    if marks:
        after = head[marks[-1].end():]
        if _DURATION.search(after) or _LIFE.search(after) or _PRISON_WORD.search(after) or \
                any(rx.search(after) for _, rx in _ORDER_PATTERNS):
            head = after
        else:
            head = _CLAUSES.split(head[:marks[-1].end()])[-1]
    clauses = []
    for clause in _CLAUSES.split(head):
        clause = _after_set_aside(clause)
        if clause.strip() and not _EARLIER.search(clause):
            clauses.append(clause)
    return _APPEAL_RESULT.sub(lambda m: " " + _APPEAL_WORDS.sub(" ", m.group(0)) + " ", "; ".join(clauses))


def names_unranked_order(written: str) -> bool:
    """The written sentence names an order the ordering of sentence types does not rank (a youth order, an ICO, a
    suspended sentence …): a figure recorded with it is not a term of imprisonment to compare."""
    return bool(_UNRANKED_ORDER.search(_final_orders(written)))


def read_order(written: str) -> tuple[str | None, str]:
    """The order a written sentence records, when no term of imprisonment was recorded in months: ("life", …), an
    order type ("cco", "fine", …), or (None, why not). A written term of imprisonment is never outranked by a lesser
    order also named ("imprisonment of 200 days (time served) and a CCO" is not a CCO)."""
    text = _final_orders(written)
    if _LIFE.search(text):
        return "life", "life imprisonment"
    if _UNRANKED_ORDER.search(text):
        return None, f"an order the comparison does not rank (“{written.strip()[:80]}”)"
    if _PRISON_WORD.search(text):
        return None, "a term of imprisonment the case database does not record in months"
    found = [t for t, rx in _ORDER_PATTERNS if rx.search(text)]
    if not found:
        return None, "no sentence the comparison can read"
    return max(found, key=lambda t: SEVERITY[t]), ""      # e.g. a CCO and a fine: the CCO


def _offence_names(case: dict, offence_id: str) -> tuple[list[str], list[str]]:
    """The names of the case's offences: this offence's, and the others'."""
    this, others = [], []
    for o in case.get("offences") or []:
        name = (o.get("name") or "").strip()
        if name:
            (this if o.get("offence_id") == offence_id else others).append(name)
    return this, others


def _doubts(case: dict, offence_id: str, include_head: bool) -> str:
    """The parts of the case's figures note (core/cases.py check_figures) that bear on the sentence compared: notes
    about this offence's sentence, and — when the head sentence is this offence's sentence — notes about the head
    sentence. Notes about another offence or about the non-parole period do not."""
    this, others = _offence_names(case, offence_id)

    def about(note: str, names: list[str]) -> bool:
        return any(note.startswith(f"{n}:") or f"given for {n} " in note for n in names)
    out = []
    for note in [n for n in (case.get("figures_note") or "").split("; ") if n]:
        if about(note, this):
            out.append(note)
        elif about(note, others) or "non-parole" in note:
            continue
        elif include_head:
            out.append(note)
    return "; ".join(out)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def _fit(part: str, name: str) -> float:
    """How well a part of a written sentence names an offence: 2 for the name as a phrase, 1.5 for its initials
    ("RCSI" for recklessly causing serious injury), else the share of the name's words in it."""
    if name and " ".join(re.findall(r"[a-z]+", name.lower())) in " ".join(re.findall(r"[a-z]+", (part or "").lower())):
        return 2.0
    initials = "".join(w[0] for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in _STOP)
    if len(initials) >= 2 and initials in _words(part):
        return 1.5
    words = _words(name) - _STOP
    return len(words & _words(part)) / len(words) if words else 0.0


def _without_name(part: str, name: str) -> str:
    """A written sentence without the offence's own name or initials (so that "Discharging a firearm – CCO" is not
    read as a discharge)."""
    out = re.sub(re.escape(name), " ", part or "", flags=re.I) if name else (part or "")
    initials = "".join(w[0] for w in re.findall(r"[a-z]+", (name or "").lower()) if w not in _STOP)
    if len(initials) >= 2:
        out = re.sub(rf"\b{initials}\b", " ", out, flags=re.I)
    return out


def _written_part(written: str, name: str, others: list[str]) -> str | None:
    """The part of a written sentence that is about this offence. When the text lists several offences ("RCSI – fine
    / Affray – CCO", often copied whole for each offence), the part that names this offence better than any other
    offence of the case; None if no single part does. A text whose parts name no offence is all this offence's."""
    parts = [p for p in re.split(r"[/;\n]", written or "") if p.strip()]
    if len(parts) <= 1:
        return written or ""
    scored = [(part, _fit(part, name), max((_fit(part, o) for o in others), default=0.0)) for part in parts]
    if not any(mine or theirs for _, mine, theirs in scored):
        return written
    best = sorted([(mine, part) for part, mine, theirs in scored if mine > theirs], key=lambda x: -x[0])
    if not best or (len(best) > 1 and best[0][0] == best[1][0]):
        return None
    return best[0][1]


def case_outcome(entry: dict, offence_id: str) -> CaseOutcome:
    """The sentence a comparable case imposed for the offence being estimated, before it is placed against the band.
    entry is one of Comparables.selected: {id, similarity, match, case, offence_sentence, same_as}."""
    c = entry.get("case") or {}
    row = CaseOutcome(id=entry.get("id", c.get("id", "")), citation=c.get("citation", ""), court=c.get("court", ""),
                      year=c.get("year"), similarity=float(entry.get("similarity") or 0.0),
                      match=entry.get("match", ""), outcome="", source="", kind=None, months=None,
                      position="not compared", reason="")
    own = entry.get("offence_sentence")
    offences = c.get("offences") or []
    head_months = c.get("head_sentence_months")
    only_charge = c.get("principal_offence_id") == offence_id and len(offences) <= 1
    other_names = _offence_names(c, offence_id)[1]
    head_text = c.get("head_sentence_text") or ""
    if only_charge and names_unranked_order(head_text):  # the only charge's sentence is a youth order, an ICO …
        row.reason = f"its head sentence is an order the comparison does not rank (“{head_text.strip()[:80]}”)"
        return row
    mine = next((o for o in offences if o.get("offence_id") == offence_id and (o.get("sentence_text") or "").strip()),
                None)
    part = _written_part(mine["sentence_text"], mine.get("name") or "", other_names) if mine else ""
    own_kind, own_why = None, ""
    if mine and part is None:
        own_why = "its written sentence lists several offences and the part for this offence cannot be identified"
    elif part:
        plain = _without_name(part, mine.get("name") or "")
        if names_unranked_order(plain):                  # a youth order, an ICO, a suspended sentence …
            row.reason = f"its sentence for this offence is an order the comparison does not rank (“{part.strip()[:80]}”)"
            return row
        written_kind, written_why = read_order(plain)
        if own is not None and written_kind not in (None, "life"):
            durations = [float(n) * (12 if unit.lower().startswith("y") else 1)
                         for n, unit in _ANY_DURATION.findall(_final_orders(plain))]
            if len(durations) == 1 and abs(durations[0] - float(own)) <= 0.5:
                own = None                              # "CCO 18 months" recorded as 18: the CCO's own length
        if own is None:
            own_kind, own_why = written_kind, written_why
    if own is not None and own_why:
        own_why = ""
    source, basis = "its sentence for this offence", "own"
    if own is not None:
        row.kind, row.months = "term", float(own)
        if only_charge and head_months is not None and abs(float(head_months) - float(own)) > 0.5:
            row.kind, row.months = None, None
            row.reason = (f"its sentence for this offence ({months_text(round(float(own)))}) and its head sentence "
                          f"({months_text(round(float(head_months)))}) differ, although this offence was its only "
                          "charge — check them on the Case database page")
            return row
    elif own_kind is not None:                           # an order other than a term, written for this offence
        row.kind = own_kind
    elif own_why and not only_charge:
        row.reason = f"its sentence for this offence: {own_why}"
        return row
    elif only_charge and head_months is not None:
        row.kind, row.months, basis = "term", float(head_months), "head"
        source = "its head sentence (this offence was the only charge)"
    elif only_charge:
        kind, why = read_order(head_text)
        if kind is None:
            row.reason = own_why or why
            return row
        row.kind, basis = kind, "head"
        source = "its head sentence (this offence was the only charge)"
    elif c.get("principal_offence_id") != offence_id and not any(o.get("offence_id") == offence_id for o in offences):
        row.reason = f"a different offence ({c.get('principal_offence') or 'not labelled'})"
        return row
    else:
        row.reason = ("its sentence covers several charges and the summary gives no separate sentence for this "
                      "offence")
        return row
    doubt = _doubts(c, offence_id, include_head=only_charge or basis == "head")
    if doubt:
        row.kind, row.months = None, None
        row.reason = f"its figures need checking on the Case database page ({doubt})"
        return row
    row.source = source
    if row.kind == "term":
        row.outcome = f"{months_text(round(row.months))} imprisonment"
    elif row.kind == "life":
        row.outcome = "life imprisonment"
    else:
        row.outcome = ORDER_WORDS[row.kind]
    return row


# ─────────────────────────────────────────────────────────────────────────────
# 3. Placing it against the band
# ─────────────────────────────────────────────────────────────────────────────
def _blocked(row: CaseOutcome, why: str) -> CaseOutcome:
    row.position, row.reason, row.band_blocked = "not compared", why, True
    return row


def place(row: CaseOutcome, band: Band) -> CaseOutcome:
    """Set row.position and row.reason: below, within, above or outside the band (or not compared)."""
    types = set(band.disposition_types)
    lo, hi = band.term_low_months, band.term_high_months
    ranks = [SEVERITY[t] for t in types if t in SEVERITY]
    unranked = any(t not in SEVERITY for t in types)      # an MTMO: the ordering does not rank it
    if row.kind is None:
        row.position = "not compared"
        return row
    if not types:
        row.position, row.reason = "not compared", "the range names no disposition"
        return row
    if unranked:                    # placing only the cases within it would overstate the share (review R7b)
        return _blocked(row, f"the range includes {MTMO_NOTE}")
    if row.kind == "term":
        if types & CUSTODIAL:
            if (lo is None or row.months >= lo - 1e-6) and (hi is None or row.months <= hi + 1e-6):
                row.position, row.reason = "within", ("within the range's term" if lo is not None or hi is not None
                                                      else "the range includes imprisonment and gives no term")
            elif unranked:
                return _blocked(row, f"outside the range's term, and the range also includes {MTMO_NOTE}")
            elif hi is not None and row.months > hi + 1e-6 and LIFE in types:
                row.position, row.reason = "outside", (f"longer than the range's highest term ({months_text(hi)}), but "
                                                       "less severe than life imprisonment, which the range includes")
            elif hi is not None and row.months > hi + 1e-6:
                row.position, row.reason = "above", f"longer than the range's highest term ({months_text(hi)})"
            elif any(r < SEVERITY["imprisonment"] for r in ranks):
                row.position, row.reason = "outside", (f"shorter than the range's lowest term ({months_text(lo)}), "
                                                       "but more severe than the range's other orders")
            else:
                row.position, row.reason = "below", f"shorter than the range's lowest term ({months_text(lo)})"
        elif unranked:
            return _blocked(row, f"a term of imprisonment cannot be placed against {MTMO_NOTE}")
        elif LIFE in types and any(r < SEVERITY["imprisonment"] for r in ranks):
            row.position, row.reason = "outside", ("a term of imprisonment: the range includes life imprisonment and "
                                                   "less severe orders, but no term")
        elif LIFE in types:
            row.position, row.reason = "below", "a term of imprisonment, less severe than life imprisonment"
        else:
            row.position, row.reason = "above", "imprisonment, but the range has no term of imprisonment"
        return row
    if row.kind == "life":
        if unranked:
            return _blocked(row, f"life imprisonment cannot be placed against {MTMO_NOTE}")
        if LIFE in types:
            row.position, row.reason = "within", "the range includes life imprisonment"
            return row
        if types & CUSTODIAL and hi is None:
            row.position, row.reason = "not compared", "the range gives no highest term to compare life imprisonment with"
            return row
        row.position, row.reason = "above", "life imprisonment, more severe than anything in the range"
        return row
    word = ORDER_WORDS[row.kind]
    if row.kind in types:
        row.position, row.reason = "within", f"the range includes a {word} (its length is not compared)"
        return row
    if unranked:
        return _blocked(row, f"a {word} cannot be placed against {MTMO_NOTE}")
    rank = SEVERITY.get(row.kind)
    if rank < min(ranks):
        row.position, row.reason = "below", f"a {word}, less severe than any order in the range"
    elif rank > max(ranks):
        row.position, row.reason = "above", f"a {word}, more severe than any order in the range"
    else:
        row.position, row.reason = "outside", (f"a {word}, which the range does not include (the ordering of sentence "
                                               "types does not place it below or above the range)")
    return row


# ─────────────────────────────────────────────────────────────────────────────
# 4. The share within the band
# ─────────────────────────────────────────────────────────────────────────────
def band_agreement(comparables: Comparables | None, band: Band, offence_id: str,
                   min_cases: int | None = None) -> Agreement:
    """Place each comparable case against the band and work out the similarity-weighted share within it."""
    min_cases = config.AGREEMENT_MIN_CASES if min_cases is None else min_cases
    out = Agreement(percent=None, min_cases=min_cases, band_text=band_text(band))
    if comparables is None or not comparables.available:
        out.note = "No case database: add cases on the Case database page to compare the estimate with similar cases."
        return out
    out.available, out.given = comparables.available, len(comparables.selected)
    out.score_label = comparables.score_label
    out.weighted = comparables.score_label == "similarity"
    out.rows = [place(case_outcome(e, offence_id), band) for e in comparables.selected]
    if comparables.selected and not band.disposition_types:
        out.note = "The range names no disposition, so the cases cannot be placed against it."
        return out
    done = [r for r in out.rows if r.compared]
    out.compared = len(done)
    for pos in ("within", "below", "above", "outside"):
        setattr(out, pos, sum(1 for r in done if r.position == pos))
    if not comparables.selected:
        out.note = f"No comparable case was given: {comparables.note}"
        return out
    if any(r.band_blocked for r in out.rows) or any(t not in SEVERITY for t in band.disposition_types):
        out.note = (f"The range includes {MTMO_NOTE} (principle.sentence_type_ordering), so the cases are listed but "
                    "not placed against it, and no share is given.")
        return out
    if out.compared < min_cases:
        some = f"Only {out.compared}" if out.compared else "None"
        out.note = (f"{some} of the {out.given} cases given {'has' if out.compared == 1 else 'have'} a sentence for "
                    f"this offence that can be compared with the estimate; a percentage needs at least {min_cases}.")
        return out
    weights = [max(r.similarity, 0.0) for r in done] if out.weighted else [1.0] * len(done)
    if out.weighted and sum(1 for w in weights if w > 0) < min_cases:
        weights, out.weighted = [1.0] * len(done), False
        out.weights_note = f"fewer than {min_cases} of the cases have a positive similarity"
    elif not out.weighted:
        out.weights_note = "the cases were ranked by keywords, not cosine similarity"
    share = sum(w for w, r in zip(weights, done) if r.position == "within") / sum(weights)
    out.percent = int(round(100 * share))
    if out.weighted:
        out.note = (f"The cases within the estimate's range carry {out.percent}% of the similarity weight of the "
                    f"{out.compared} most similar cases with a sentence for this offence (by count, {out.within} of "
                    f"the {out.compared}; closer cases count more).")
    else:
        out.note = (f"{out.percent}% of the {out.compared} most similar cases with a sentence for this offence were "
                    f"sentenced within the estimate's range ({out.within} of {out.compared}; equal weights, because "
                    f"{out.weights_note}).")
    return out


DISCLAIMER = ("A comparison with these few cases from your case database — not the probability that a court will "
              "impose this sentence, and not a sentencing practice: similarity measures how alike the cases' texts "
              "are, there is no single correct sentence (VSM 2.1), and a small number of cases cannot show a "
              "sentencing practice or define a range (VSM 5.2.9.3.1).")
