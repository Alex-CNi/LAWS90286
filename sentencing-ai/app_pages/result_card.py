"""
app_pages/result_card.py — THE RESULT CARD: THE ESTIMATE, LARGE AND CENTRED.

Shown on the estimate page as soon as a proposal exists, above the analysis. Top to bottom:
  * a red line if errors remain (in the gates, the hard checks or the audit);
  * the charge, then the formulated sentence in large type, its kind and its details (non-parole period, CCO …);
  * the indicative range (the proposal's band) — the estimate itself — with its main uncertainties and, folded, the
    assumptions and unresolved points; for the prosecution, the VSM 2.2.3.1 note;
  * two panels: where the estimate sits among the most similar cases from the user's case database (one months
    axis with the band, the estimate and each case), and the share of those cases sentenced within the range
    (core/agreement.py) — a comparison with these few cases, never a probability or a sentencing practice;
  * chips: the maximum, the category, the mandatory minimum, and the results of the gates, checks and audit;
  * the source and the disclaimer (audit.estimate_presentation).

The card is one HTML string (st.html: no scripts, no inline SVG). Every text that comes from the case database or
an AI call is escaped. The functions other than show_result_card() do not use Streamlit, so tests can call them.
"""

from __future__ import annotations

import html
import math
import re

from core import config
from core.agreement import CUSTODIAL, DISCLAIMER, LIFE, Agreement, band_agreement, band_text
from core.checks import months_text
from core.report import LABELS

e = html.escape


# ─────────────────────────────────────────────────────────────────────────────
# 1. The sentence in words: the large figure, its kind, the details
# ─────────────────────────────────────────────────────────────────────────────
def _condition_short(c) -> str:
    text = c.kind.replace("_", " ")
    if c.hours:
        text += f" ({c.hours} hours)"
    elif c.hours_per_day:
        text += f" ({c.hours_per_day:g} hours a day)"
    return text


def headline(p) -> tuple[str, str, list[str]]:
    """(the figure shown large, the kind of sentence, the details) for a proposal."""
    t = p.disposition_type
    details: list[str] = []
    conditions = ", ".join(_condition_short(c) for c in (p.cco.conditions if p.cco else []))
    if t == LIFE:
        big, kind = "Life imprisonment", ""
        details.append(f"Non-parole period {months_text(p.npp_months)}" if p.npp_months else "No non-parole period")
    elif t in ("imprisonment", "imprisonment_and_cco") or (t == "mtmo" and p.term_months):
        big = months_text(p.term_months) if p.term_months else "Imprisonment"
        kind = {"imprisonment": "Imprisonment", "imprisonment_and_cco": "Imprisonment, then a community correction order",
                "mtmo": "Imprisonment, then a mandatory treatment and monitoring order"}[t]
        details.append(f"Non-parole period {months_text(p.npp_months)}" if p.npp_months else "No non-parole period")
        if p.cco:
            details.append(f"{'MTMO' if t == 'mtmo' else 'CCO'} of {months_text(p.cco.months)}"
                           + (f": {conditions}" if conditions else ""))
    elif t in ("cco", "mtmo"):
        big = months_text(p.cco.months) if p.cco and p.cco.months else LABELS[t]
        kind = LABELS[t]
        if conditions:
            details.append(f"Conditions: {conditions}")
    elif t == "fine":
        big = f"{p.fine_penalty_units} penalty units" if p.fine_penalty_units else "Fine"
        kind = "Fine"
    elif t == "adjourned_undertaking":
        big = months_text(p.undertaking_months) if p.undertaking_months else "Adjourned undertaking"
        kind = "Adjourned undertaking" if p.undertaking_months else ""
    else:
        big, kind = LABELS.get(t, t), ""
    if p.fine_penalty_units and t != "fine":
        details.append(f"and a fine of {p.fine_penalty_units} penalty units")
    if t not in ("imprisonment", "imprisonment_and_cco", LIFE) and p.conviction_recorded is not None:
        details.append("Conviction recorded" if p.conviction_recorded else "No conviction recorded")
    if kind.lower() == big.lower():
        kind = ""                                       # "Fine" over "Fine": the figure already says it
    return big, kind, details


def formulated_within(p) -> bool:
    """The formulated sentence lies within its own band: a type the band includes and, for a term (or a CCO), a
    length within the band's range (the hard check check_band reports it otherwise)."""
    b, t = p.band, p.disposition_type
    types = set(b.disposition_types)
    if t not in types and not (t in CUSTODIAL and types & CUSTODIAL):
        return False
    term = p.term_months if (t in CUSTODIAL or t == "mtmo") else None
    if term is not None and ((b.term_low_months is not None and term < b.term_low_months)
                             or (b.term_high_months is not None and term > b.term_high_months)):
        return False
    cco = p.cco.months if p.cco and t in ("cco", "mtmo") else None
    if cco is not None and ((b.cco_low_months is not None and cco < b.cco_low_months)
                            or (b.cco_high_months is not None and cco > b.cco_high_months)):
        return False
    return True


def range_text(p) -> str:
    """The indicative band in a line, with the non-parole range if the band gives one."""
    text = band_text(p.band)
    b = p.band
    if b.npp_low_months is not None and b.npp_high_months is not None:
        text += f"; non-parole period {months_text(b.npp_low_months)} to {months_text(b.npp_high_months)}"
    return text


def _short_months(m: float) -> str:
    """Compact months for the axis and labels: "3y 6m", "8m", "4y"."""
    y, mo = divmod(int(round(m)), 12)
    return (f"{y}y {mo}m" if y and mo else f"{y}y" if y else f"{mo}m")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Chips: the framework and the results of the checks
# ─────────────────────────────────────────────────────────────────────────────
def _maximum_text(m, body_corporate: bool = False) -> str:
    if m.unresolved:
        return "unresolved"
    entered = " (entered by you, app policy)" if m.confirmed_by_user and m.relative_note else \
        " (entered by you from the Manual's table)" if m.confirmed_by_user else ""
    if body_corporate:
        if m.at_large:
            return "at large"
        how = (" (entered by you from the Manual's table)" if m.company_fine_source == "entered" else
               " (five times a person's maximum, which you entered: app policy)"
               if m.confirmed_by_user and m.relative_note else "")
        return f"a fine of {m.company_fine_units} penalty units" + how if m.company_fine_units else "no fine computed"
    if m.life:
        return "life imprisonment" + entered
    if m.at_large:
        return "at large"
    if m.fine_only:
        return f"a fine of {m.fine_penalty_units} penalty units" + entered
    return (months_text(m.months) + entered) if m.months else "—"


def _status_chip(label: str, errors: int, cautions: int) -> str:
    if errors:
        cls, ic = "bad", "✕"
        text = f"{errors} error{'s' if errors != 1 else ''}" + (f" · {cautions} caution{'s' if cautions != 1 else ''}"
                                                                if cautions else "")
    elif cautions:
        cls, ic, text = "warn", "!", f"no errors · {cautions} caution{'s' if cautions != 1 else ''}"
    else:
        cls, ic, text = "good", "✓", "no errors or cautions"
    return (f'<span class="vse-chip {cls}"><span class="ic" aria-hidden="true">{ic}</span>'
            f'<span class="k">{e(label)}</span> {e(text)}</span>')


def status_counts(run) -> dict[str, tuple[int, int]]:
    """Errors and cautions (with unresolved points) in the gates, the hard checks and the audit."""
    a, fw = run.current, run.framework
    gates = fw.gates if fw else []
    checks = a.checks if a else []
    findings = a.audit.result.findings if a and a.audit and a.audit.result else None
    out = {"Framework": (sum(g.outcome == "error" for g in gates),
                         sum(g.outcome in ("caution", "unresolved") for g in gates)),
           "Hard checks": (sum(c.outcome == "error" for c in checks),
                           sum(c.outcome in ("caution", "unresolved") for c in checks))}
    if findings is not None:
        out["Audit"] = (sum(f.outcome == "error" for f in findings), sum(f.outcome == "caution" for f in findings))
    return out


def chips_html(run) -> str:
    fw = run.framework
    chips = []
    if fw:
        chips.append(f'<span class="vse-chip"><span class="k">Maximum</span> '
                     f'{e(_maximum_text(fw.maximum, fw.body_corporate))}</span>')
        chips.append(f'<span class="vse-chip"><span class="k">Category</span> '
                     f'{e(str(fw.category)) if fw.category else "none on these facts"}</span>')
        if fw.mnpp_months and not fw.mnpp_excluded:
            minimum = months_text(fw.mnpp_months)
        else:
            minimum = "none on the facts as entered" if any(r.mnpp_months for r in fw.alternatives) else "none"
        chips.append(f'<span class="vse-chip"><span class="k">Minimum non-parole period</span> {e(minimum)}</span>')
        if fw.sso and fw.sso.applies != "no":
            chips.append(f'<span class="vse-chip"><span class="k">Standard sentence</span> {fw.sso.years} years'
                         + (" (unresolved)" if fw.sso.applies == "unresolved" else "") + "</span>")
    for label, (errors, cautions) in status_counts(run).items():
        chips.append(_status_chip(label, errors, cautions))
    return '<div class="vse-chips">' + "".join(chips) + "</div>"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Where the estimate sits: one months axis with the band, the estimate and each case
# ─────────────────────────────────────────────────────────────────────────────
STEPS = [3, 6, 12, 24, 36, 60, 120, 180, 240, 360]
DOT_GAP = 3.6           # % of the axis: closer dots are stacked
ROW_PX = 19


def _tick_label(v: int, step: int) -> str:
    if v == 0:
        return "0"
    return f"{v // 12}y" if step % 12 == 0 else f"{v}m"


def _tip(r, score_label: str) -> str:
    where = {"within": "within the range", "below": "below the range", "above": "above the range",
             "outside": "outside the range"}.get(r.position, r.position)
    return (f'<span class="tip" role="tooltip"><b>{e(r.outcome)}</b><br>{e(r.citation)}<br>'
            f'<span>{e(r.source)} · {e(score_label)} {r.similarity:.2f} · {e(where)}</span></span>')


def _dot(r, x: float, row: int, score_label: str = "similarity") -> str:
    cls = "within" if r.position == "within" else "out"
    cls += " tl" if x < 22 else " tr" if x > 78 else ""          # keep the tooltip inside the panel at the edges
    label = f"{r.id} {r.citation}: {r.outcome}, {r.position}"
    return (f'<span class="vse-dot {cls}" tabindex="0" aria-label="{e(label)}" '
            f'style="left:{x:.2f}%;bottom:{27 + 2 + row * ROW_PX}px">{_tip(r, score_label)}</span>')


def strip_html(ag: Agreement, p) -> str:
    """The band, the estimate and the comparable cases on one months axis ("" if there is nothing to plot)."""
    band = p.band
    lo, hi = band.term_low_months, band.term_high_months
    custodial_band = bool(set(band.disposition_types) & CUSTODIAL) or (
        "mtmo" in band.disposition_types and (lo is not None or hi is not None))
    t = p.disposition_type
    est_term = p.term_months if (t in CUSTODIAL or (t == "mtmo" and p.term_months)) else None
    est_life, band_life = t == LIFE, LIFE in band.disposition_types
    done = [r for r in ag.rows if r.compared]
    terms = [r for r in done if r.kind == "term"]
    lifes = [r for r in done if r.kind == "life"]
    orders = [r for r in done if r.kind not in ("term", "life")]
    if not (terms or lifes or est_term or est_life or band_life or (custodial_band and (lo or hi))):
        return ""
    values = [r.months for r in terms] + [v for v in (lo, hi, est_term) if v]
    top = max(values) if values else 12
    step = next((s for s in STEPS if top * 1.06 / s <= 6), STEPS[-1])
    domain = max(step, int(math.ceil(top * 1.06 / step)) * step)
    noncustodial_band = any(x not in CUSTODIAL and x != LIFE for x in band.disposition_types)
    lane_w = 14.0 if (orders or noncustodial_band or (est_term is None and not est_life)) else 0.0
    gap = 4.0 if lane_w else 0.0
    life_slot = bool(lifes or est_life or band_life)
    life_w = 8.0 if life_slot else 0.0
    x0, x1 = lane_w + gap, 100.0 - life_w - (5.0 if life_slot else 0.0)

    def x(m: float) -> float:
        return x0 + (min(max(m, 0), domain) / domain) * (x1 - x0)

    parts = []
    if lane_w:
        parts.append(f'<div class="lane" style="left:0;width:{lane_w:.2f}%"></div>')
        parts.append(f'<span class="tick lane-label" style="left:{lane_w / 2:.2f}%">other orders</span>')
    for v in range(0, domain + 1, step):
        parts.append(f'<span class="tickline" style="left:{x(v):.2f}%"></span>'
                     f'<span class="tick" style="left:{x(v):.2f}%">{_tick_label(v, step)}</span>')
    if life_w:
        parts.append(f'<span class="tick" style="left:{100 - life_w / 2:.2f}%">life</span>')
    parts.append(f'<div class="axis" style="left:{x0:.2f}%;right:{100 - x1:.2f}%"></div>')
    if custodial_band:
        left, right = x(lo or 0), x(hi if hi is not None else domain)
        parts.append(f'<div class="band" title="The indicative range" style="left:{left:.2f}%;width:{max(right - left, 0.6):.2f}%"></div>')
    if band_life:
        parts.append(f'<div class="band" title="The indicative range: life imprisonment" '
                     f'style="left:{100 - life_w:.2f}%;width:{life_w:.2f}%"></div>')
    # the cases: stacked where they would overlap
    placed: list[tuple[float, object]] = [(x(r.months), r) for r in terms]
    placed += [(lane_w / 2, r) for r in orders] + [(100 - life_w / 2, r) for r in lifes]
    rows_end: list[float] = []
    dots = []
    for px, r in sorted(placed, key=lambda item: item[0]):
        row = next((k for k, end in enumerate(rows_end) if px - end >= DOT_GAP), len(rows_end))
        if row == len(rows_end):
            rows_end.append(px)
        else:
            rows_end[row] = px
        dots.append(_dot(r, px, row, ag.score_label))
    # the estimate
    if est_term:
        ex, label = x(est_term), f"Estimate · {_short_months(est_term)}"
    elif est_life:
        ex, label = 100 - life_w / 2, "Estimate · life"
    else:
        ex, label = lane_w / 2, "Estimate"
    align = "left" if ex < 12 else "right" if ex > 88 else ""
    parts.append(f'<div class="est" style="left:{ex:.2f}%"></div>'
                 f'<span class="est-label {align}" style="left:{ex:.2f}%">{e(label)}</span>')
    height = 150 + max(0, len(rows_end) - 3) * ROW_PX
    legend = []
    if any(r.position == "within" for r in done):
        legend.append('<span><i class="sw-dot within"></i>a case within the range</span>')
    if any(r.position != "within" for r in done):
        legend.append('<span><i class="sw-dot out"></i>a case outside it</span>')
    if custodial_band or band_life:
        legend.append('<span><i class="sw-band"></i>the indicative range</span>')
    legend.append('<span><i class="sw-est"></i>the estimate</span>')
    return (f'<div class="vse-strip" style="height:{height}px" role="img" aria-label="The indicative range, the estimate '
            f'and the most similar cases on a scale of months">' + "".join(parts) + "".join(dots) + "</div>"
            '<div class="vse-legend">' + "".join(legend) + "</div>")


def cases_table_html(ag: Agreement) -> str:
    """Every case the run was given, as a table (the chart's table view)."""
    if not ag.rows:
        return ""
    score = ag.score_label.capitalize()
    head = (f"<thead><tr><th>Case</th><th>Sentence compared</th><th>{e(score)}</th><th>Against the range</th></tr>"
            "</thead>")
    body = []
    for r in ag.rows:
        key = "within" if r.position == "within" else ("none" if not r.compared else "out")
        sentence = (f"{e(r.outcome)}<br><span class='muted'>{e(r.source)}</span>" if r.compared
                    else f"<span class='muted'>Not compared: {e(r.reason)}</span>")
        body.append(f"<tr><td>{e(r.id)} · {e(r.citation)}</td><td>{sentence}</td>"
                    f"<td class='num'><span class='lbl'>{e(ag.score_label)} </span>{r.similarity:.2f}</td>"
                    f"<td class='pos'><span class='key {key}'></span>{e(r.position)}</td></tr>")
    return ('<details class="vr-more"><summary>The ' + str(len(ag.rows)) + " most similar cases, one by one</summary>"
            '<table class="vse-cases">' + head + "<tbody>" + "".join(body) + "</tbody></table></details>")


# ─────────────────────────────────────────────────────────────────────────────
# 4. The share of similar cases within the range (a stat tile with a meter)
# ─────────────────────────────────────────────────────────────────────────────
def agreement_html(ag: Agreement) -> str:
    parts = ['<div class="vr-panel"><div class="vr-h">Similar cases within this range</div>']
    if ag.percent is None:
        parts.append('<div class="vr-value none">—</div>')
        parts.append(f'<div class="vr-counts">{e(ag.note)}</div>')
    else:
        parts.append(f'<div class="vr-value">{ag.percent}<small>%</small></div>')
        parts.append(f'<div class="vse-meter" role="meter" aria-valuemin="0" aria-valuemax="100" '
                     f'aria-valuenow="{ag.percent}" aria-label="Similar cases within the range">'
                     f'<div style="width:{ag.percent}%"></div></div>')
        parts.append(f'<div class="vr-counts">By count: {ag.within} of {ag.compared} cases within · '
                     f'{e(ag.counts_text.split(" · ", 1)[1]) if " · " in ag.counts_text else "none outside"}</div>')
        parts.append('<div class="vr-sub">' + ("The percentage is weighted by cosine similarity, so closer cases count "
                                               "more and it can differ from the count." if ag.weighted else
                                               f"Equal weights, because {e(ag.weights_note)}.") + "</div>")
    parts.append(f'<div class="vr-note">{e(DISCLAIMER)}</div></div>')
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 5. The card
# ─────────────────────────────────────────────────────────────────────────────
def charge_text(kb, run) -> str:
    label = kb.charge_label(run.case.offence_id)
    label = re.sub(r"\s+", " ", label.split(" · ", 1)[-1])
    plea = "plea of guilty" if run.case.plea == "guilty" else "found guilty at trial"
    return f"{label} · {run.case.court} · {plea}"


def card_html(run, kb, ag: Agreement | None = None) -> str:
    """The whole result card for the run's current proposal ("" if there is none)."""
    a = run.current
    p = a.proposal.proposal if a and a.proposal else None
    if p is None:
        return ""
    ag = ag or band_agreement(run.comparables, p.band, run.case.offence_id)
    big, kind, details = headline(p)
    counts = status_counts(run)
    errors = sum(err for err, _ in counts.values())
    out = ['<div class="vse-result" role="region" aria-label="The estimate">']
    if errors:
        out.append('<div class="vr-alert"><span class="ic" aria-hidden="true">✕</span>Errors remain — do not rely on this '
                   "estimate until they are resolved (Step 6)</div>")
    out.append('<div class="vr-overline">Indicative estimate</div>')
    out.append(f'<div class="vr-charge">{e(charge_text(kb, run))}</div>')
    out.append(f'<div class="vr-sentence">{e(big)}</div>')
    if kind:
        out.append(f'<div class="vr-kind">{e(kind)}</div>')
    if details:
        out.append(f'<div class="vr-detail">{e(" · ".join(details))}</div>')
    out.append(f'<div class="vr-range">Indicative range: <b>{e(range_text(p))}</b></div>')
    explain = ("The estimate is the range. The large figure is the formulated sentence within it, on which the hard "
               "checks and the audit were run." if formulated_within(p) else
               "The estimate is the range. The large figure is the formulated sentence on which the hard checks and "
               "the audit were run; it lies <b>outside</b> the range (see Step 6).")
    if p.band.main_uncertainties:
        explain += " <b>Main uncertainties:</b> " + e("; ".join(p.band.main_uncertainties)) + "."
    out.append(f'<div class="vr-explain">{explain}</div>')
    if p.assumptions or p.unresolved:
        items = [f"<li>{e(x)}</li>" for x in p.assumptions] + [f"<li>Unresolved: {e(x)}</li>" for x in p.unresolved]
        out.append(f'<details class="vr-assume"><summary>Assumptions and unresolved points ({len(items)})</summary>'
                   f'<ul>{"".join(items)}</ul></details>')
    if run.case.user_role == "prosecution":
        out.append('<div class="vr-prosecution">For the prosecution: the prosecution may make submissions on the type '
                   "of disposition but not on the range of permissible durations (VSM 2.2.3.1).</div>")
    if len(run.attempts) > 1:
        out.append(f'<div class="vr-revised">Revised after the checks ({len(run.attempts) - 1} revision'
                   f'{"s" if len(run.attempts) > 2 else ""})' + (" — the revision failed, so the first proposal is shown"
                                                                if run.revision_error else "") + "</div>")
    strip = strip_html(ag, p)
    heading = "Where the estimate sits among similar cases" if ag.rows else "The indicative range and the estimate"
    left = [f'<div class="vr-panel"><div class="vr-h">{heading}</div>']
    left.append(strip)
    if strip and any(r.compared for r in ag.rows):
        left.append('<div class="vr-caption">Each dot is one of the most similar cases in your case database, placed '
                    "by its sentence for this offence; orders other than imprisonment sit at the left. Point at a dot "
                    "(or tab to it) for the case.</div>")
    elif strip and ag.rows:
        left.append(f'<div class="vr-caption">{e(ag.note)}</div>')
    elif not strip:
        left.append('<div class="vr-sub">The range has no term of imprisonment to draw on a scale of months'
                    + ("; the cases are listed below." if ag.rows else ".") + "</div>")
    left.append("</div>")
    out.append('<div class="vr-grid">' + "".join(left) + agreement_html(ag) + "</div>")
    out.append(cases_table_html(ag))
    out.append(chips_html(run))
    out.append(f'<div class="vr-disclaimer">Indicative estimate from the {e(config.MANUAL_NAME)} (version of '
               f"{e(config.MANUAL_VERSION_DATE)}). It is not a prediction of the sentence a court will impose and not "
               "legal advice: sentencing is discretionary and there is no single correct sentence (VSM 2.1). The "
               "reasons, the checks and the audit are below.</div>")
    out.append("</div>")
    return "".join(out)


def show_result_card(run, kb) -> None:
    """Draw the result card (estimate page)."""
    import streamlit as st
    card = card_html(run, kb)
    if card:
        st.html(card)
