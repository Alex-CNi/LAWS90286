"""
core/report.py — STEP 7: THE REPORT (on screen, and as Markdown or Word downloads).

The report is first built as a list of simple "blocks" (heading, paragraph,
bullets, table, quote); render_markdown() and render_docx() turn the same blocks
into the two download formats, so they always say the same thing.

Every report states: the indicative band (not a prediction), the formulated
sentence, the reasons with citations to the Manual, the assumptions, the
cautions and Manual flags, the Manual's edition and its disclaimer
(source.manual_edition_and_disclaimer; audit.estimate_presentation). If the
user has a case database, the report lists the comparable cases the run was
given, which of them the reasoning relied on, and the cases left out.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass

from core import config
from core.agreement import DISCLAIMER as AGREEMENT_DISCLAIMER
from core.agreement import band_agreement
from core.cases import POLICY, CaseRecord, months_label
from core.checks import months_text
from core.facts import asked_charge_questions, charge_answer_text, charge_question
from core.framework import RELATED_FLAGS_GATE, flag_applies
from core.kb import KnowledgeBase
from core.pipeline import Run
from core.results import sort_results
from core.schemas import Proposal
from core.stages import STAGE_ORDER


@dataclass
class Block:
    kind: str            # h1 | h2 | h3 | p | bullets | table | quote | small
    text: str = ""
    items: list | None = None      # bullets, or table rows
    headers: list | None = None    # table headers


LABELS = {"imprisonment": "Imprisonment", "imprisonment_and_cco": "Imprisonment combined with a CCO",
          "cco": "Community correction order (CCO)", "mtmo": "Mandatory treatment and monitoring order",
          "fine": "Fine", "adjourned_undertaking": "Adjourned undertaking", "discharge": "Discharge",
          "dismissal": "Dismissal", "life_imprisonment": "Life imprisonment"}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Describing a sentence in words
# ─────────────────────────────────────────────────────────────────────────────
def describe_sentence(p: Proposal) -> str:
    t = p.disposition_type
    parts = []
    if t == "life_imprisonment":
        parts.append("Life imprisonment" + (f", with a non-parole period of {months_text(p.npp_months)}" if p.npp_months
                                            else ", with no non-parole period"))
    elif t in ("imprisonment", "imprisonment_and_cco") or (t == "mtmo" and p.term_months):
        s = f"Imprisonment for {months_text(p.term_months)}"
        s += f", with a non-parole period of {months_text(p.npp_months)}" if p.npp_months else ", with no non-parole period"
        parts.append(s)
    if p.cco:
        kind = "mandatory treatment and monitoring order (a CCO)" if t == "mtmo" else "community correction order"
        conds = "; ".join(_condition_text(c) for c in p.cco.conditions)
        parts.append(f"{'followed by a' if parts else 'A'} {kind} of {months_text(p.cco.months)}"
                     + (f" with conditions: {conds}" if conds else ""))
    if p.fine_penalty_units:
        parts.append(f"{'and a' if parts else 'A'} fine of {p.fine_penalty_units} penalty units")
    if t == "adjourned_undertaking":
        parts.append(f"Adjourned undertaking for {months_text(p.undertaking_months)}")
    if t in ("discharge", "dismissal"):
        parts.append(LABELS[t])
    conv = {True: "Conviction recorded.", False: "No conviction recorded.", None: ""}[p.conviction_recorded]
    return (", ".join(parts) + ". " + conv).strip()


def _condition_text(c) -> str:
    bits = [c.kind.replace("_", " ")]
    if c.hours:
        bits.append(f"{c.hours} hours" + (f" within {c.completion_months} months" if c.completion_months else ""))
    if c.hours_per_day:
        bits.append(f"{c.hours_per_day:g} hours a day" + (f" for {c.duration_months} months" if c.duration_months else ""))
    if c.details and c.details.lower() not in bits[0]:
        bits.append(c.details)
    return " — ".join(bits)


def protected_text(fw) -> str:
    """The protected-official status as the framework applies it (screen and report)."""
    if fw.offence_id not in config.CAUSING_INJURY_OFFENCES:
        return "Not applicable to this charge (ss 15A–18 only)"
    text = fw.protected_official
    if fw.protected_assumed_no:
        text += " (treated as not proved, app policy)"
    elif fw.protected_official == "yes" and fw.applied_protected == "no":
        text += " (not applied: you said the protected-official scheme did not apply at the offence date)"
    return text


def minimum_text(fw) -> str:
    if not fw.mnpp_months:
        return "None on the facts as entered" + (" — one applies only if further facts are proved (see the other "
                                                  "readings)" if any(a.mnpp_months for a in fw.alternatives) else "")
    return months_text(fw.mnpp_months) + (f" — EXCLUDED: {fw.mnpp_excluded}" if fw.mnpp_excluded else "")


def reading_heading(a) -> str:
    """How another reading is labelled (the same on screen and in the report)."""
    if a.kind == "open":
        return "Other reading (unresolved in the Manual" + (", app policy)" if a.app_policy else ")")
    return "Other reading (only if proved" + (", app policy)" if a.app_policy else ")")


def describe_band(p: Proposal) -> str:
    b = p.band
    types = ", ".join(LABELS.get(t, t) for t in b.disposition_types) or "—"
    lines = [f"Dispositions within the band: {types}."]
    if b.term_low_months is not None or b.term_high_months is not None:
        lines.append(f"Imprisonment: {months_text(b.term_low_months)} to {months_text(b.term_high_months)}.")
    if b.npp_low_months is not None or b.npp_high_months is not None:
        lines.append(f"Non-parole period: {months_text(b.npp_low_months)} to {months_text(b.npp_high_months)}.")
    if b.cco_low_months is not None or b.cco_high_months is not None:
        lines.append(f"CCO: {months_text(b.cco_low_months)} to {months_text(b.cco_high_months)}.")
    return " ".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2. The report as blocks
# ─────────────────────────────────────────────────────────────────────────────
def cited_items(run: Run) -> tuple[list[str], list[str]]:
    """Every knowledge item and passage the run relies on, in order of first use."""
    items, passages = [], []

    def add(ids, into):
        for i in ids:
            if i not in into:
                into.append(i)
    fw = run.framework
    if fw:
        add(fw.rule_items, items)
        for g in fw.gates:
            add(g.cite + g.flags, items)
    for key in STAGE_ORDER:
        st = run.stages.get(key)
        if st and st.analysis:
            for pt in st.analysis.points:
                add(pt.item_ids, items)
                add(pt.passage_ids, passages)
            add(st.analysis.flags_engaged, items)
    a = run.current
    if a and a.proposal.proposal:
        p = a.proposal.proposal
        for r in p.reasons:
            add(r.item_ids, items)
            add(r.passage_ids, passages)
        for o in p.ancillary_orders:
            add(o.item_ids, items)
        add(p.flags_shown, items)
        for c in a.checks:
            add(c.cite + c.flags, items)
    return items, passages


def cited_cases(run: Run) -> dict[str, list[str]]:
    """Comparable cases the reasoning relied on: case id -> where ("Current practice …", "Reasons")."""
    out: dict[str, list[str]] = {}
    for key in STAGE_ORDER:
        st = run.stages.get(key)
        for pt in (st.analysis.points if st and st.analysis else []):
            for cid in pt.case_ids:
                out.setdefault(cid, [])
                if st.title not in out[cid]:
                    out[cid].append(st.title)
    a = run.current
    for r in (a.proposal.proposal.reasons if a and a.proposal.proposal else []):
        for cid in r.case_ids:
            out.setdefault(cid, [])
            if "Reasons" not in out[cid]:
                out[cid].append("Reasons")
    return out


def _cites(item_ids, passage_ids, case_ids) -> str:
    ids = list(item_ids) + list(passage_ids) + [f"case {c}" for c in case_ids]
    return f" [{'; '.join(ids)}]" if ids else ""


def comparables_blocks(run: Run) -> list[Block]:
    """The comparable cases from the user's case database (none if there is no database)."""
    comp = run.comparables
    if not comp or not comp.available:
        return []
    B = [Block("h2", "9. Comparable cases from your case database"),
         Block("small", "These cases are yardsticks of current sentencing practice only. They are not a source of "
                        "law: the Manual is. A few cases do not set a range. The AI was told not to average or "
                        "match their sentences, and the audit checks the reasoning against the Manual's standard on "
                        "comparable cases.")]
    B.append(Block("p", f"Database: {comp.database}. Chosen by {comp.method} ({POLICY}). "
                        f"{len(comp.selected)} case(s) were given to the current-practice analysis, the proposal and "
                        "the audit."))
    if not comp.selected:
        B.append(Block("p", f"No comparable case was given: {comp.note}"))
    used = cited_cases(run)
    rows = []
    for e in comp.selected:
        c = CaseRecord.from_dict(e["case"])
        rows.append([c.id + (f" (also {', '.join(e['same_as'])})" if e.get("same_as") else ""),
                     f"{c.citation}" + (f" ({c.court})" if c.court and c.court != "unknown" else ""),
                     f"{e['match']} ({c.principal_offence or '—'}); {comp.score_label} {e['similarity']}",
                     months_label(e.get("offence_sentence")) if e.get("offence_sentence") is not None else "not given",
                     (c.head_sentence_text or "—")
                     + (f" (figures to check: {c.figures_note})" if c.figures_note else ""),
                     ", ".join(used.get(c.id, [])) or "not relied on"])
    if rows:
        B.append(Block("table", headers=["Id", "Case", "Match (principal offence); score",
                                         "Its sentence for this offence", "Head sentence / orders", "Relied on in"],
                       items=rows))
    a = run.current
    if comp.selected and a and a.proposal.proposal:
        ag = band_agreement(comp, a.proposal.proposal.band, run.case.offence_id)
        B.append(Block("h3", "These cases against the estimate's range (a check, not a probability)"))
        B.append(Block("p", f"{ag.note} {AGREEMENT_DISCLAIMER} Range compared: {ag.band_text}. Only a sentence for "
                            "this offence is compared (app policy, core/agreement.py)."))
        B.append(Block("table", headers=["Id", "Sentence compared", "From", "Against the range", "Why"],
                       items=[[r.id, r.outcome or "—", r.source or "—", r.position, r.reason or "—"]
                              for r in ag.rows]))
    if comp.left_out:
        B.append(Block("h3", "Left out"))
        B.append(Block("bullets", items=[f"{e['id']} {e['case'].get('citation', '')}: {e['reason']}"
                                         for e in comp.left_out]))
    return B


def engaged_flags(run: Run) -> list[str]:
    """The flags this case engages directly, in order of first use."""
    out = []
    fw = run.framework
    if fw:
        for g in fw.gates:
            if g.title != RELATED_FLAGS_GATE:
                out += g.flags + [c for c in g.cite if c.startswith("flag.")]
        out += [f for a in fw.alternatives for f in a.flags]
    for key in STAGE_ORDER:
        st = run.stages.get(key)
        if st and st.analysis:
            out += st.analysis.flags_engaged + [i for pt in st.analysis.points for i in pt.item_ids if i.startswith("flag.")]
    a = run.current
    if a and a.proposal.proposal:
        p = a.proposal.proposal
        out += p.flags_shown + [i for r in p.reasons for i in r.item_ids if i.startswith("flag.")]
        out += [f for c in a.checks for f in c.flags + [x for x in c.cite if x.startswith("flag.")]]
    return list(dict.fromkeys(out))


def build_blocks(run: Run, kb: KnowledgeBase) -> list[Block]:
    B: list[Block] = []
    case, fw, facts = run.case, run.framework, run.facts
    a = run.current
    p = a.proposal.proposal if a else None
    title = case.case_name or kb.offence_label(case.offence_id)

    B.append(Block("h1", f"Sentencing estimate — {title}"))
    B.append(Block("small", f"Indicative estimate from the {config.MANUAL_NAME}, version published "
                            f"{config.MANUAL_VERSION_DATE}. It is not a prediction of the sentence a court will impose "
                            f"and it is not legal advice. Run {run.run_id}, {run.started}."))

    # ── 1. The estimate ──
    B.append(Block("h2", "1. The estimate"))
    if p:
        B.append(Block("p", f"**Indicative band.** {describe_band(p)}"))
        if p.band.main_uncertainties:
            B.append(Block("bullets", items=[f"Uncertainty: {u}" for u in p.band.main_uncertainties]))
        B.append(Block("p", f"**Formulated sentence used for the checks.** {describe_sentence(p)}"))
        if p.s6aaa.stated:
            n = p.s6aaa
            notional = LABELS.get(n.type or "", n.type or "")
            figs = ", ".join(x for x in [
                f"term {months_text(n.term_months)}" if n.term_months else "",
                f"non-parole period {months_text(n.npp_months)}" if n.npp_months else "",
                f"CCO {months_text(n.cco_months)}" if n.cco_months else "",
                f"{n.fine_penalty_units} penalty units" if n.fine_penalty_units else ""] if x)
            B.append(Block("p", f"**Section 6AAA.** But for the guilty plea the sentence would have been: {notional}"
                                + (f" ({figs})" if figs else "") + "."))
        scheme = bool(fw and fw.sso and fw.sso.applies != "no")          # only where the scheme applies
        if scheme and (p.standard_sentence_statement or "").strip():
            B.append(Block("p", f"**Standard sentence.** {p.standard_sentence_statement}"))
        if scheme and (p.npp_interests_of_justice_reason or "").strip():
            B.append(Block("p", f"**Non-parole period below the s 11A(4) floor — interests of justice.** "
                                f"{p.npp_interests_of_justice_reason}"))
        if p.special_reason.found:
            B.append(Block("p", f"**Special reason** ({(p.special_reason.limb or '').replace('_', ' ')}): "
                                f"{p.special_reason.evidential_basis}"))
        if p.category2_exception.relied_on:
            B.append(Block("p", f"**Category 2 exception** ({(p.category2_exception.exception or '').replace('_', ' ')}): "
                                f"{p.category2_exception.evidential_basis}"))
        if p.ancillary_orders:
            B.append(Block("bullets", items=[f"{o.type.replace('_', ' ').capitalize()} ({o.status})"
                                             + (f", {months_text(o.months)}" if o.months else "")
                                             + (f": {o.details.strip()}" if o.details.strip() else "")
                                             for o in p.ancillary_orders]))
        if case.presentence_detention_days and (p.term_months or p.disposition_type == "life_imprisonment"):
            B.append(Block("p", f"**Time served.** Declare {case.presentence_detention_days} days of pre-sentence "
                                "detention."))
        hard_err = sum(1 for c in a.checks if c.outcome == "error")
        hard_cau = sum(1 for c in a.checks if c.outcome in ("caution", "unresolved"))
        gate_err = sum(1 for g in (fw.gates if fw else []) if g.outcome == "error")
        aud = a.audit.result.findings if a.audit and a.audit.result else []
        aud_err = sum(1 for f in aud if f.outcome == "error")
        B.append(Block("p", f"**Checks.** Gates before the reasoning: {gate_err} error(s). Hard checks in code: "
                            f"{hard_err} error(s), {hard_cau} caution(s) or unresolved point(s). Audit: {aud_err} "
                            f"error(s), {sum(1 for f in aud if f.outcome == 'caution')} caution(s)."
                            + (f" The proposal was revised {len(run.attempts) - 1} time(s)." if len(run.attempts) > 1 else "")
                            + (f" The revision failed ({run.revision_error}), so the first proposal is shown."
                               if run.revision_error else "")
                            + (" Errors remain: do not rely on this estimate without resolving them."
                               if hard_err or aud_err or gate_err else "")))
        comp = run.comparables
        if comp and comp.available:
            ag = band_agreement(comp, p.band, case.offence_id)
            B.append(Block("p", f"**Similar cases.** {ag.note} {AGREEMENT_DISCLAIMER} (See section 9.)"))
    else:
        B.append(Block("p", "No proposal yet."))
    if case.user_role == "prosecution":
        B.append(Block("p", "Note for the prosecution: the prosecution may make submissions on the type of disposition "
                            "but not on the range of permissible durations (VSM 2.2.3.1)."))

    # ── 2. Case and facts ──
    B.append(Block("h2", "2. The case and the facts relied on"))
    B.append(Block("p", f"{kb.offence_label(case.offence_id)}; offence date {case.offence_date.isoformat()}"
                        + (f" to {case.offence_date_end.isoformat()}" if case.offence_date_end else "")
                        + f"; {case.court}; plea: {'guilty' if case.plea == 'guilty' else 'found guilty at trial'}; "
                        + ("the offender is a body corporate." if case.body_corporate else
                           f"offender aged {case.age_at_offence} at the offence and {case.age_at_sentence} at sentence"
                           + (" (assumed: not stated, taken as the age at the offence)."
                              if getattr(case, "age_at_sentence_assumed", False) else "."))))
    asked = asked_charge_questions(kb, case)                # the answers this charge's statutory scheme turns on
    if asked:
        B.append(Block("bullets", items=[f"{charge_question(kb, f, case.offence_id)[0]} "
                                         f"**{charge_answer_text(f, getattr(case, f)).capitalize()}**" for f in asked]))
    if facts:
        B.append(Block("p", facts.summary))
        B.append(Block("table", headers=["Fact", "Bearing", "Basis"],
                       items=[[f"{f['id']}: {f['text']}", f["bearing"], f["basis"].replace("_", " ")] for f in facts.facts]))
        if facts.missing:
            B.append(Block("h3", "Facts not supplied (assumed)"))
            B.append(Block("bullets", items=[f"{m['question']} — assumed: {m['assumption']}" for m in facts.missing]))

    # ── 3. Framework ──
    if fw:
        B.append(Block("h2", "3. The legal framework (worked out in code)"))
        rows = [["Maximum", fw.maximum.describe()],
                ["Maximum fine" + ((" (entered for the body corporate)" if fw.maximum.fine_source == "entered"
                                    else " (a natural person's)") if fw.body_corporate else ""),
                 f"{fw.maximum.fine_penalty_units} penalty units ("
                 + {"printed": "as printed", "entered": "as you entered it from the Manual's table"}.get(
                     fw.maximum.fine_source, "10 per month of the maximum term") + ")"
                 if fw.maximum.fine_penalty_units else "—"],
                ["Category", f"Category {fw.category}" if fw.category else "None on these facts"],
                ["Protected official", protected_text(fw)],
                ["Mandatory minimum NPP", minimum_text(fw)],
                ["Dispositions", fw.dispositions_note]]
        if fw.sso:
            rows.append(["Standard sentence (VSM 9.2)", fw.sso.line()])
        if fw.licence:
            rows.append(["Mandatory licence order (VSM 16)", fw.licence.line()])
        if fw.body_corporate:
            rows.append(["Body corporate fine maximum", fw.maximum.company_fine_text()])
        if fw.pu_value:
            rows.append(["Penalty unit value", fw.pu_value])
        rows += [[reading_heading(a), f"{a.title}: {a.summary()}."] for a in fw.alternatives]
        B.append(Block("table", headers=["", ""], items=rows))

    # ── 4. Stage analyses ──
    if run.stages:
        B.append(Block("h2", "4. The analysis"))
        for key in STAGE_ORDER:
            st = run.stages.get(key)
            if not st or not st.analysis:
                continue
            B.append(Block("h3", st.title))
            B.append(Block("bullets", items=[
                f"**{pt.heading}** ({pt.effect}, {pt.weight}) — {pt.analysis}"
                + _cites(pt.item_ids, pt.passage_ids, pt.case_ids)
                + (f" (facts {', '.join(pt.fact_ids)})" if pt.fact_ids else "")
                + ("" if pt.proof in ("established", "not_applicable") else f" — {pt.proof.replace('_', ' ')}")
                for pt in st.analysis.points]))
            B.append(Block("p", f"*Conclusion:* {st.analysis.conclusion}"))

    # ── 5. Reasons ──
    if p:
        B.append(Block("h2", "5. Reasons for the proposed sentence"))
        for r in p.reasons:
            B.append(Block("p", f"**{r.heading}.** {r.text}" + _cites(r.item_ids, r.passage_ids, r.case_ids)))
        if p.revision_notes:
            B.append(Block("h3", "Changes made on revision"))
            B.append(Block("bullets", items=p.revision_notes))

    # ── 6. Checks ──
    if a:
        B.append(Block("h2", "6. Checks"))
        B.append(Block("h3", "Hard checks (code)"))
        B.append(Block("table", headers=["Outcome", "Check", "Detail"],
                       items=[[c.outcome.upper(), f"{c.title} ({c.check_id})",
                               c.message + (" (app policy)" if c.app_policy else "")]
                              for c in sort_results(a.checks) if c.outcome != "info"]))
        if a.audit and a.audit.result:
            B.append(Block("h3", "Audit of the reasoning (AI)"))
            B.append(Block("p", a.audit.result.overall))
            B.append(Block("table", headers=["Outcome", "Standard", "Finding"],
                           items=[[f.outcome.upper(), kb.title(f.check_id), f.explanation + (f" Fix: {f.fix}" if f.fix else "")]
                                  for f in sorted(a.audit.result.findings,
                                                  key=lambda f: {"error": 0, "caution": 1, "pass": 2}.get(f.outcome, 3))
                                  if f.outcome != "not_applicable"]))
            if a.audit.not_assessed:
                B.append(Block("small", "Standards the audit did not report on: " + ", ".join(a.audit.not_assessed)))

    # ── 7. Gates, cautions and flags ──
    if fw:
        B.append(Block("h2", "7. Cautions and problems in the Manual"))
        proof = [w for st in run.stages.values() for w in st.proof_warnings]
        B.append(Block("bullets", items=[f"[{g.outcome.upper()}] {g.title}: {g.message}" + (" (app policy)" if g.app_policy else "")
                                         for g in sort_results(fw.gates) if g.outcome != "pass"]
                       + [f"[CAUTION] Analysis: {w}" for w in proof] or ["None."]))
        items_used = cited_items(run)[0]
        # Flags the case engages (in full): carried by the checks, the readings, the analyses, the proposal or a
        # gate other than the list of flags related to the rules used.
        engaged = list(dict.fromkeys(f for f in engaged_flags(run) if kb.exists(f)))
        if engaged:
            B.append(Block("h3", "Manual flags engaged by this case"))
            B.append(Block("bullets", items=[f"**{kb.title(f)}** ({f}): {kb.statement(f)}" for f in engaged]))
        # audit.manual_flags also lists flags whose related list names a rule the reasoning used. Most
        # will not change this estimate, so they are listed by title only, after a scope and date filter.
        gate_related = {f for g in fw.gates if g.title == RELATED_FLAGS_GATE for f in g.flags}
        related = sorted((gate_related | {f for f in kb.flags_related_to(
                              [i for i in items_used if not i.startswith(("flag.", "audit.", "scale."))])
                          if flag_applies(kb, f, case)}) - set(engaged))
        if related:
            B.append(Block("h3", "Other Manual flags on rules the reasoning used"))
            B.append(Block("small", "Listed because the Manual is inconsistent or incomplete on a related point; open "
                                    "the Knowledge base page for the text."))
            B.append(Block("bullets", items=[f"{kb.title(f)} ({f}"
                                             + (f"; VSM {', '.join((kb.meta.get(f) or {}).get('sections') or [])}"
                                                if (kb.meta.get(f) or {}).get("sections") else "") + ")"
                                             for f in related]))

    # ── 8. Assumptions and unresolved points ──
    if p and (p.assumptions or p.unresolved):
        B.append(Block("h2", "8. Assumptions and unresolved points"))
        B.append(Block("bullets", items=[f"Assumption: {x}" for x in p.assumptions] + [f"Unresolved: {x}" for x in p.unresolved]))

    # ── 9. Comparable cases (the user's case database) ──
    comp_blocks = comparables_blocks(run)
    B += comp_blocks
    n = 10 if comp_blocks else 9

    # ── 10. Sources ──
    items, passages = cited_items(run)
    B.append(Block("h2", f"{n}. Sources (Victorian Sentencing Manual)"))
    src = []
    for i in items:
        cits = kb.citations_for(i, 2)
        if not cits:
            continue
        quotes = " ".join(f"{c.label}: “{c.quote}”" + (" — " + "; ".join(
            f"fn {fn.n}: {fn.text[:160]}" for fn in c.footnotes[:2]) if c.footnotes else "") for c in cits)
        src.append(f"**[{i}] {kb.title(i)}** — {quotes}")
    for cid in passages:
        c = kb.chunk_citation(cid)
        if c:
            src.append(f"**[{cid}]** {c.label}: “{c.quote[:400]}…”")
    B.append(Block("bullets", items=src or ["No sources cited."]))

    # ── 11. Run log ──
    B.append(Block("h2", f"{n + 1}. Run log"))
    reasons = Counter((c.step, c.note if c.ok else f"failed ({c.note})") for c in run.discarded_calls
                      if c.kind == "ai")
    reused = [c for c in getattr(run, "reused_calls", []) if c.kind == "ai" and c.ok]
    B.append(Block("p", f"AI calls in this run: {run.ai_calls} (the assignment requires at least "
                        f"{config.MIN_AI_CALLS_PER_RUN}). Retrieval: {run.retrieval_mode or '—'}."
                        + (" Not counted: " + "; ".join(f"{step}{f' ×{n}' if n > 1 else ''} — {why or 'not used'}"
                                                        for (step, why), n in reasons.items()) + "."
                           if reasons else "")
                        + (" Made for an earlier run of this case and reused here (counted there): "
                           + "; ".join(dict.fromkeys(c.step for c in reused)) + "." if reused else "")))
    B.append(Block("table", headers=["#", "Step", "Model", "Tokens in/out", "Seconds", "Status"],
                   items=[[str(k + 1), c.step, c.model + (f" ({c.effort})" if c.effort else ""),
                           f"{c.input_tokens}/{c.output_tokens}", f"{c.seconds}", "ok" if c.ok else f"failed: {c.note}"]
                          for k, c in enumerate(run.calls)]))

    # ── Disclaimer ──
    B.append(Block("h2", "About this estimate"))
    ack = kb.citations_for("source.manual_edition_and_disclaimer", 5)
    disclaimer = next((c.quote for c in ack if "warranty" in c.quote), "")
    B.append(Block("p", f"Source: {config.MANUAL_NAME}, version published {config.MANUAL_VERSION_DATE}. "
                        "The Manual is the only source of law used; where it is silent or inconsistent the report says "
                        "so. Text marked “(app policy)” is this app's handling of such a point and is not the Manual's "
                        "law." + (" Cases from your case database were used only as comparable cases (yardsticks), "
                                  "never as law." if comp_blocks else "")))
    if disclaimer:
        B.append(Block("quote", f"{disclaimer} (Judicial College of Victoria)"))
    return B


# ─────────────────────────────────────────────────────────────────────────────
# 3. Rendering
# ─────────────────────────────────────────────────────────────────────────────
def render_markdown(blocks: list[Block]) -> str:
    out = []
    for b in blocks:
        if b.kind == "h1":
            out.append(f"# {b.text}")
        elif b.kind == "h2":
            out.append(f"## {b.text}")
        elif b.kind == "h3":
            out.append(f"### {b.text}")
        elif b.kind in ("p", "small"):
            out.append(b.text if b.kind == "p" else f"*{b.text}*")
        elif b.kind == "quote":
            out.append(f"> {b.text}")
        elif b.kind == "bullets":
            out.append("\n".join(f"- {x}" for x in b.items or []))
        elif b.kind == "table":
            rows = b.items or []
            if not rows:
                continue
            headers = b.headers or [""] * len(rows[0])
            out.append("| " + " | ".join(headers) + " |\n|" + "---|" * len(headers) + "\n" +
                       "\n".join("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |"
                                 for r in rows))
    return "\n\n".join(out) + "\n"


_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def clean_text(text) -> str:
    """Remove characters Word files cannot hold (e.g. control characters from a PDF)."""
    return _XML_INVALID.sub("", str(text))


def _add_runs(paragraph, text: str):
    """Very small markdown: **bold** and *italic*."""
    text = clean_text(text)
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        elif part:
            paragraph.add_run(part)


def render_docx(blocks: list[Block]) -> bytes:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    for b in blocks:
        if b.kind == "h1":
            doc.add_heading(clean_text(b.text), level=0)
        elif b.kind == "h2":
            doc.add_heading(clean_text(b.text), level=1)
        elif b.kind == "h3":
            doc.add_heading(clean_text(b.text), level=2)
        elif b.kind in ("p", "small", "quote"):
            para = doc.add_paragraph(style="Quote" if b.kind == "quote" else None)
            _add_runs(para, b.text)
            if b.kind == "small":
                for r in para.runs:
                    r.italic = True
        elif b.kind == "bullets":
            for x in b.items or []:
                _add_runs(doc.add_paragraph(style="List Bullet"), x)
        elif b.kind == "table" and b.items:
            cols = len(b.items[0])
            table = doc.add_table(rows=1, cols=cols)
            table.style = "Table Grid"
            for i, h in enumerate(b.headers or [""] * cols):
                table.rows[0].cells[i].text = clean_text(h)
            for row in b.items:
                cells = table.add_row().cells
                for i, value in enumerate(row):
                    cells[i].text = clean_text(value)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
