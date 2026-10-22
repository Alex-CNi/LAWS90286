"""
tests/fake_openai.py — A STAND-IN FOR THE OPENAI SERVICE, FOR TESTS ONLY.

The real OpenAI library is used unchanged; only its HTTP transport is replaced
(httpx.MockTransport), so the tests exercise the same request building and
Structured Outputs parsing as the deployed app — without a key or network.

The fake answers each request according to the schema it asks for:
  FactsExtraction  -> facts drawn from the narrative (quotes copied verbatim)
  StageAnalysis    -> points citing ids actually shown in the input (+ one invented id,
                      to test that invented ids are removed)
  Proposal         -> first answer deliberately breaks a hard rule (to test the
                      revision loop) when `bad_first_proposal` is set; the revision fixes it
  AuditResult      -> one finding per standard listed in the input
  ChunkResult      -> the case database's chunking: a case starts at each line with a citation
                      ("[2021] VSCA 12"); optional failures and cut-short answers test the retries
  SortedCase       -> the simple input's sorting: answers found by patterns in the description, each
                      with its words; plus one invented answer whose words are not in the description
                      (to test that such an answer is not used)
  embeddings       -> hashed bag-of-words vectors (deterministic)
"""

from __future__ import annotations

import json
import re
import threading

import httpx
import numpy as np

from core import config, schemas


def fake_vector(text: str) -> list[float]:
    v = np.zeros(config.EMBEDDING_DIMENSIONS, dtype=np.float32)
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        v[hash(w) % config.EMBEDDING_DIMENSIONS] += 1.0
    n = np.linalg.norm(v) or 1.0
    return (v / n).tolist()


def fake_embed_batch(texts):
    return np.array([fake_vector(t) for t in texts], dtype=np.float32)


class FakeOpenAI:
    def __init__(self, bad_first_proposal: bool = True, proposal_kind: str = "auto", chunk_fail_first: int = 0,
                 chunk_max_cases: int = 0, wrong_months_for: str = "", extract_fail_first: int = 0,
                 first_kind: str = "auto"):
        self.bad_first_proposal = bad_first_proposal
        self.proposal_kind = proposal_kind
        self.first_kind = first_kind                # "combination": the first (bad) answer is a combination sentence
        self.requests: list[dict] = []
        self.proposals = 0
        self.chunk_fail_first = chunk_fail_first    # the first N chunking calls fail (to test the retry)
        self.chunk_max_cases = chunk_max_cases      # more cases than this in one part: the answer is cut short
        self.wrong_months_for = wrong_months_for    # a citation whose total term is given wrongly (figures check)
        self.chunk_calls = 0
        self.extract_fail_first = extract_fail_first  # the first N facts extractions fail (to test the call log)
        self.extract_calls = 0
        self._lock = threading.Lock()

    # ── transport ──
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport())

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        self.requests.append({"path": request.url.path, "body": body,
                              "auth": request.headers.get("authorization", "")})
        if request.url.path.endswith("/embeddings"):
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return httpx.Response(200, json={
                "object": "list", "model": body["model"],
                "data": [{"object": "embedding", "index": i, "embedding": fake_vector(t)} for i, t in enumerate(inputs)],
                "usage": {"prompt_tokens": 10 * len(inputs), "total_tokens": 10 * len(inputs)}})
        name = body["text"]["format"]["name"]
        text = body.get("input") if isinstance(body.get("input"), str) else json.dumps(body.get("input"))
        if name == "FactsExtraction":
            with self._lock:
                self.extract_calls += 1
                fail = self.extract_calls <= self.extract_fail_first
            if fail:
                return httpx.Response(400, json={"error": {"message": "Simulated failure of the facts extraction.",
                                                           "type": "invalid_request_error", "code": None}})
        if name == "ChunkResult":
            with self._lock:
                self.chunk_calls += 1
                fail = self.chunk_calls <= self.chunk_fail_first
            if fail:
                return httpx.Response(400, json={"error": {"message": "Simulated failure of a chunking call.",
                                                           "type": "invalid_request_error", "code": None}})
        answer = getattr(self, f"answer_{name}")(text, body)
        if name == "ChunkResult" and self.chunk_max_cases and len(answer.cases) > self.chunk_max_cases:
            data = self.response(body["model"], "")
            data["status"], data["incomplete_details"] = "incomplete", {"reason": "max_output_tokens"}
            data["output"] = []
            return httpx.Response(200, json=data)
        return httpx.Response(200, json=self.response(body["model"], answer.model_dump_json()))

    @staticmethod
    def response(model: str, output_text: str) -> dict:
        return {"id": "resp_test", "object": "response", "created_at": 1760000000, "status": "completed",
                "model": model, "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
                "output": [{"type": "message", "id": "msg_test", "status": "completed", "role": "assistant",
                            "content": [{"type": "output_text", "text": output_text, "annotations": []}]}],
                "usage": {"input_tokens": max(1, len(output_text) // 4), "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens": 300, "output_tokens_details": {"reasoning_tokens": 120},
                          "total_tokens": 500}}

    # ── helpers ──
    @staticmethod
    def section(text: str, heading: str) -> str:
        m = re.search(rf"## {re.escape(heading)}[^\n]*\n(.*?)(?=\n## |\Z)", text, flags=re.S)
        return m.group(1) if m else ""

    @staticmethod
    def ids_in(block: str) -> list[str]:
        return list(dict.fromkeys(re.findall(r"^\[([^\]\s]+)\]", block, flags=re.M)))

    # ── answers ──
    def answer_FactsExtraction(self, text, body):
        narrative = self.section(text, "Case narrative")
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", narrative) if len(s.strip()) > 30][:8]
        facts = []
        for k, s in enumerate(sentences):
            quote = " ".join(s.split()[:20])
            facts.append(schemas.ExtractedFact(
                id=f"F{k + 1}", text=s, topic="offence_circumstances" if k < 3 else "offender_background",
                bearing="aggravating" if k < 2 else ("mitigating" if k > 4 else "neutral"),
                basis="agreed_or_admitted" if k != 3 else "alleged", source_quote=quote))
        facts.append(schemas.ExtractedFact(id=f"F{len(facts) + 1}", text="An invented fact with a quote not in the narrative",
                                           topic="other", bearing="neutral", basis="assumption",
                                           source_quote="words that do not appear anywhere"))
        return schemas.FactsExtraction(
            summary="A test summary of the case.", facts=facts,
            missing=[schemas.MissingFact(question="Criminal history?", why_it_matters="bears on the purposes",
                                         assumption="no prior convictions")],
            conflicts_with_form=[], scope_warnings=[], charge_fit="The facts fit the charge.",
            suggested=schemas.SuggestedValues(victim_protected_worker="unknown", offender_knew_or_reckless_as_to_status="unknown",
                                              intoxicated_by_liquor="unknown", intoxication_contributed="unknown",
                                              mental_impairment_evidence="unknown", cco_consent="unknown",
                                              presentence_report="unknown", cooperation="unknown",
                                              prior_convictions="none", arising_from_driving="no",
                                              manslaughter_circumstance=(
                                                  "single_punch" if "single punch" in narrative.lower() else "unknown"),
                                              prosecution_notice="unknown", official_injured="unknown",
                                              category_condition_met="unknown", conditional_condition_met="unknown"))

    def answer_StageAnalysis(self, text, body):
        items = self.ids_in(self.section(text, "Knowledge items"))
        passages = self.ids_in(self.section(text, "Most similar Manual passages"))
        facts = re.findall(r"^(F\d+) ", self.section(text, "Confirmed facts"), flags=re.M)
        cases = self.ids_in(self.section(text, "Comparable cases from your case database"))
        points = [schemas.AnalysisPoint(heading=f"Point {k + 1}", analysis="Test analysis.", effect="increases",
                                        weight="moderate", item_ids=items[k:k + 2] + (["invented.item_id"] if k == 0 else []),
                                        passage_ids=passages[:1], fact_ids=facts[:2], proof="established",
                                        case_ids=(cases[:2] + ["C999"]) if (k == 0 and cases) else [])
                  for k in range(3)]
        return schemas.StageAnalysis(points=points, conclusion="Test conclusion.", gaps=[], flags_engaged=[])

    def _proposal(self, text, compliant: bool) -> schemas.Proposal:
        mnpp = re.search(r"Mandatory minimum non-parole period: (\d+) months", text)
        category = re.search(r"Category: Category (\d)", text)
        guilty = "Plea: guilty" in text
        sso = re.search(r"Standard sentence scheme \(VSM 9\.2\): (?:applies|UNRESOLVED)", text)
        statement = ("The sentence is below the standard sentence: the offending was a single, unplanned episode and "
                     "the offender pleaded guilty early (F1, F2)." if sso and compliant else "")
        if self.proposal_kind == "life":
            return self._life_proposal(text, compliant, guilty, statement)
        if "Offender: a body corporate" in text:
            return self._fine_proposal(text, compliant, guilty)
        items = self.ids_in(self.section(text, "Knowledge items"))
        cases = self.ids_in(self.section(text, "Comparable cases from your case database"))
        if (mnpp or category) and not (self.first_kind == "combination" and not compliant):
            mn = int(mnpp.group(1)) if mnpp else 0
            term = max(48, mn * 2)
            npp = max(mn, int(term * 0.65)) if compliant else max(0, mn - 6)
            kind, cco = "imprisonment", None
        else:
            kind = "imprisonment_and_cco"
            term, npp = (9, None) if compliant else (18, None)
            cco = schemas.CCOTerms(months=24, commences="on_release_from_prison", months_after_order=None, conditions=[
                schemas.CCOCondition(kind="unpaid_community_work", details="", hours=150, completion_months=12,
                                     hours_per_day=None, duration_months=None, monitors=None),
                schemas.CCOCondition(kind="treatment_and_rehabilitation", details="alcohol counselling", hours=None,
                                     completion_months=None, hours_per_day=None, duration_months=None, monitors=None)])
        notional = schemas.NotionalSentence(stated=guilty, type="imprisonment" if guilty else None,
                                            term_months=(term + 12) if guilty else None,
                                            npp_months=(npp + 6) if guilty and npp else None,
                                            cco_months=None, unpaid_hours=None, fine_penalty_units=None,
                                            conditions_reduced=None)
        aeo = "Intoxicated by liquor at the offence: yes; intoxication significantly contributed: yes" in text
        orders = ([schemas.AncillaryOrder(type="alcohol_exclusion_order", status="mandatory", months=24,
                                          details="two years", item_ids=["anc.alcohol_exclusion_order"])]
                  if aeo and compliant else [])
        licence = re.search(r"Mandatory licence order \(VSM 16\): mandatory: licence cancellation or disqualification "
                            r"of at least (\d+) months", text)
        held = re.search(r"licence: (victorian|other_jurisdiction|none)\b", text)
        if licence and compliant:                  # the form the licence held requires (licence.form_by_licence_status)
            form = {"victorian": "licence_cancellation_and_disqualification",
                    "other_jurisdiction": "licence_disqualification_from_driving",
                    "none": "licence_disqualification_from_obtaining"}.get(held.group(1) if held else "victorian")
            orders.append(schemas.AncillaryOrder(type=form, status="mandatory", months=int(licence.group(1)) + 12,
                                                 details="", item_ids=["order.licence_disqualification"]))
        return schemas.Proposal(
            disposition_type=kind, term_months=term, npp_months=npp, npp_reason=None,
            standard_sentence_statement=statement, npp_interests_of_justice_reason=None, cco=cco,
            fine_penalty_units=None, undertaking_months=None, conviction_recorded=True,
            special_reason=schemas.SpecialReason(found=False, limb=None, evidential_basis="", stated_in_writing=False),
            category2_exception=schemas.Category2Exception(relied_on=False, exception=None, evidential_basis=""),
            plea_reduced_sentence=guilty, s6aaa=notional,
            cooperation=schemas.Cooperation(less_severe_sentence=False, basis="none", announced=False, undertaking_recorded=False),
            ancillary_orders=orders,
            band=schemas.Band(disposition_types=[kind], term_low_months=max(1, term - 6), term_high_months=term + 6,
                              npp_low_months=npp, npp_high_months=(npp + 6) if npp else None,
                              cco_low_months=None, cco_high_months=None, main_uncertainties=["test"]),
            reasons=[schemas.ReasonParagraph(heading="Synthesis", text="Weighed together.", item_ids=items[:3] + ["made.up"],
                                             passage_ids=[], fact_ids=["F1"],
                                             case_ids=(cases[:1] + ["C998"]) if cases else [])],
            assumptions=["test assumption"], unresolved=[], flags_shown=[],
            revision_notes=["fixed the errors"] if compliant and self.proposals > 1 else [])

    def _fine_proposal(self, text, compliant: bool, guilty: bool) -> schemas.Proposal:
        """A body corporate: a fine (within the company maximum when compliant; 20,000 penalty units, above any
        company maximum in the register's rows used by the tests, in the first answer)."""
        units = 1500 if compliant else 20000
        return schemas.Proposal(
            disposition_type="fine", term_months=None, npp_months=None, npp_reason=None,
            standard_sentence_statement="", npp_interests_of_justice_reason=None, cco=None,
            fine_penalty_units=units, undertaking_months=None, conviction_recorded=True,
            special_reason=schemas.SpecialReason(found=False, limb=None, evidential_basis="", stated_in_writing=False),
            category2_exception=schemas.Category2Exception(relied_on=False, exception=None, evidential_basis=""),
            plea_reduced_sentence=guilty,
            s6aaa=schemas.NotionalSentence(stated=guilty, type="fine" if guilty else None, term_months=None,
                                           npp_months=None, cco_months=None, unpaid_hours=None,
                                           fine_penalty_units=(units + 500) if guilty else None,
                                           conditions_reduced=None),
            cooperation=schemas.Cooperation(less_severe_sentence=False, basis="none", announced=False,
                                            undertaking_recorded=False),
            ancillary_orders=[],
            band=schemas.Band(disposition_types=["fine"], term_low_months=None, term_high_months=None,
                              npp_low_months=None, npp_high_months=None, cco_low_months=None, cco_high_months=None,
                              main_uncertainties=["test"]),
            reasons=[schemas.ReasonParagraph(heading="Synthesis", text="Weighed together.", item_ids=[], passage_ids=[],
                                             fact_ids=["F1"], case_ids=[])],
            assumptions=["test assumption"], unresolved=[], flags_shown=[],
            revision_notes=["fixed the errors"] if compliant and self.proposals > 1 else [])

    def _life_proposal(self, text, compliant: bool, guilty: bool, statement: str) -> schemas.Proposal:
        """Life imprisonment with a non-parole period (30 years when compliant; 20 years in the first answer)."""
        npp = 360 if compliant else 240
        return schemas.Proposal(
            disposition_type="life_imprisonment", term_months=None, npp_months=npp, npp_reason=None,
            standard_sentence_statement=statement, npp_interests_of_justice_reason=None, cco=None,
            fine_penalty_units=None, undertaking_months=None, conviction_recorded=True,
            special_reason=schemas.SpecialReason(found=False, limb=None, evidential_basis="", stated_in_writing=False),
            category2_exception=schemas.Category2Exception(relied_on=False, exception=None, evidential_basis=""),
            plea_reduced_sentence=guilty,
            s6aaa=schemas.NotionalSentence(stated=guilty, type="life_imprisonment" if guilty else None,
                                           term_months=None, npp_months=(npp + 24) if guilty else None, cco_months=None,
                                           unpaid_hours=None, fine_penalty_units=None, conditions_reduced=None),
            cooperation=schemas.Cooperation(less_severe_sentence=False, basis="none", announced=False,
                                            undertaking_recorded=False),
            ancillary_orders=[],
            band=schemas.Band(disposition_types=["imprisonment", "life_imprisonment"], term_low_months=300,
                              term_high_months=420, npp_low_months=240, npp_high_months=360, cco_low_months=None,
                              cco_high_months=None, main_uncertainties=["test"]),
            reasons=[schemas.ReasonParagraph(heading="Synthesis", text="Weighed together.", item_ids=[], passage_ids=[],
                                             fact_ids=["F1"], case_ids=[])],
            assumptions=["test assumption"], unresolved=[], flags_shown=[],
            revision_notes=["fixed the errors"] if compliant and self.proposals > 1 else [])

    def answer_Proposal(self, text, body):
        self.proposals += 1
        revising = "## Your previous proposal" in text
        compliant = revising or not self.bad_first_proposal
        return self._proposal(text, compliant)

    def answer_AuditResult(self, text, body):
        standards = self.ids_in(self.section(text, "Audit standards to apply"))
        findings = [schemas.AuditFinding(check_id=s, outcome="pass", explanation="ok", fix="") for s in standards]
        if findings:
            findings[0] = schemas.AuditFinding(check_id=findings[0].check_id, outcome="caution",
                                               explanation="test caution", fix="explain more")
        return schemas.AuditResult(findings=findings, overall="Test audit.")

    # ── the case database's chunking ──
    CITATION = re.compile(r"\[(?:19|20)\d\d\]\s*(?:VSCA|VCC|VSC)\s*\d+")
    HEADING = re.compile(r"^(?:#{1,6}\s+|\d{1,2}(?:\.\d{1,2})*\s*[–-]\s+)(.+)$")

    @staticmethod
    def offence_of(path: list[str], by_name: dict) -> str:
        """The deepest heading that names a listed offence ("4.4 – Recklessly causing serious injury")."""
        for h in reversed(path):
            name = re.sub(r"^[#\s]*(?:\d{1,2}(?:\.\d{1,2})*\s*[–-]?\s*)?", "", h).strip().lower()
            if name in by_name:
                return by_name[name]
        return "other"

    @staticmethod
    def duration(t):
        m = re.search(r"(\d+) years?(?: (\d+) months?)?|(\d+) months?", t or "")
        if not m:
            return None
        return float(int(m.group(1)) * 12 + int(m.group(2) or 0)) if m.group(1) else float(m.group(3))

    def answer_ChunkResult(self, text, body):
        cover = re.search(r"START on lines L(\d+)–L(\d+)", text)
        lo, hi = int(cover.group(1)), int(cover.group(2))
        shown = [(int(n), line) for n, line in re.findall(r"^L(\d+): (.*)$", self.section(text, "Lines shown"), flags=re.M)]
        by_name: dict = {}                       # offence name -> its code ("27-06: Crimes Act 1958 s 75A — Armed robbery")
        for code, name in re.findall(r"^(\d{2}-\d{2}): .*? — (.+)$", self.section(text, "Offence codes you may use"),
                                     flags=re.M):
            by_name.setdefault(name.strip().lower(), code)
        before = re.search(r"Headings in force before these lines: (.*?)\.\n", text).group(1)
        path0 = [] if before == "none" else before.split(" › ")
        starts = [n for n, line in shown if self.CITATION.search(line)]
        heads = {n: line for n, line in shown if self.HEADING.match(line) and not self.CITATION.search(line)}
        cases = []
        def level(h):
            m = re.match(r"^(#+)", h)
            if m:
                return len(m.group(1))
            m = re.match(r"^(\d{1,2}(?:\.\d{1,2})*)", h)
            return m.group(1).count(".") + 1 if m else 2     # a heading given without its marker: level 2

        for k, n in enumerate(starts):
            stack = [(level(p), p) for p in path0]          # the headings in force: a later heading of the same
            for h in sorted(heads):                         # or a higher level replaces the earlier ones
                if h < n:
                    lv = level(heads[h])
                    stack = [(lv0, t) for lv0, t in stack if lv0 < lv] + [(lv, heads[h])]
            path = [t for _, t in stack]
            if not (lo <= n <= hi):
                continue
            nxt = [m for m in starts[k + 1:]] + [h for h in heads if h > n]
            end = (min(nxt) - 1) if nxt else shown[-1][0]
            lines = [line for m, line in shown if n <= m <= end]
            cit_line = lines[0]
            citation = cit_line.split(": ", 1)[1] if cit_line.startswith("Case name: ") else cit_line
            head_line = next((b for b in lines if b.lower().startswith("head sentence")), "")
            head_text = head_line.split(": ", 1)[1] if ": " in head_line else ""
            term = re.search(r"(\d+) years? imprisonment", head_text)
            npp = re.search(r"NPP (\d+) years?", head_text)
            months = float(int(term.group(1)) * 12) if term else None
            if self.wrong_months_for and self.wrong_months_for in citation and months:
                months += 7
            court = "Court of Appeal" if "VSCA" in citation else "County Court" if "VCC" in citation else "Supreme Court"
            year = int(re.search(r"\[(\d{4})\]", citation).group(1))
            label = self.offence_of(path, by_name)
            name = re.sub(r"^[#\s]*(?:\d{1,2}(?:\.\d{1,2})*\s*[–-]?\s*)?", "", path[-1]).strip() if path else "unknown"
            off_line = next((b for b in lines if b.lower().startswith("offences")), "")
            cases.append(schemas.ChunkedCase(
                start_line=n, end_line=end, continues_past_shown_lines=(not nxt) and shown[-1][0] > hi,
                citation=citation, court=court, year=year, principal_offence_id=label,
                principal_offence=name,
                offences=[schemas.ChunkOffence(name=name, offence_id=label,
                                               sentence_text=off_line.split(": ", 1)[-1],
                                               sentence_months=self.duration(off_line))],
                head_sentence_text=head_text, head_sentence_months=months,
                npp_months=float(int(npp.group(1)) * 12) if npp else None,
                plea="guilty" if "Plea: G" in "\n".join(lines) else "unknown",
                offender=next((b.split(": ", 1)[1] for b in lines if b.startswith("Offender: ")), ""),
                summary=f"A test summary of {citation}."))
        return schemas.ChunkResult(cases=cases)


    # ── the simple input's sorting ──
    MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
              "november", "december"]

    def answer_SortedCase(self, text, body):
        d = self.section(text, "The description")
        listed = re.findall(r"^(\d{2}-\d{2}): .*? — (.+)$", self.section(text, "Offence codes you may use"), flags=re.M)
        ids = [code for code, _ in listed]
        quotes: list = []

        def find(field, pattern):
            m = re.search(pattern, d, flags=re.I)
            if m:
                quotes.append(schemas.FieldQuote(field=field, quote=m.group(0)))
            return m

        phrase = {code: name.strip().lower() for code, name in listed}
        offence_id = next((c for c in sorted(ids, key=lambda c: -len(phrase[c]))
                           if find("offence_id", r"\b" + re.escape(phrase[c]) + r"\b")), "unclear")
        m = find("court", r"(County|Supreme|Magistrates'|Children's) Court")
        court = f"{m.group(1)} Court" if m else "unclear"
        day = r"\b(\d{1,2}) (" + "|".join(self.MONTHS) + r") (\d{4})\b"
        iso = lambda g: f"{g[2]}-{self.MONTHS.index(g[1].lower()) + 1:02d}-{int(g[0]):02d}"   # noqa: E731
        span = re.search(r"between " + day + r" and " + day, d, flags=re.I)
        offence_date_end = ""
        if span:                                   # an offence period
            quotes.append(schemas.FieldQuote(field="offence_date", quote=span.group(0)))
            quotes.append(schemas.FieldQuote(field="offence_date_end", quote=span.group(0)))
            offence_date, offence_date_end = iso(span.groups()[:3]), iso(span.groups()[3:])
        else:
            m = find("offence_date", day)
            offence_date = iso(m.groups()) if m else ""
        year = re.search(r"\bin (\d{4})\b", d)
        plea = ("guilty" if find("plea", r"pleaded guilty") else
                "found_guilty_at_trial" if find("plea", r"found guilty (?:after|at) (?:a )?trial") else "unclear")
        timing = ("earliest_opportunity" if find("plea_timing", r"at the (?:first|earliest)(?: available| reasonable)? opportunity")
                  else "late" if find("plea_timing", r"late plea|on the (?:first )?day of (?:the )?trial") else
                  "not_applicable" if plea == "found_guilty_at_trial" else "unknown")
        m = find("age_at_offence", r"(?:then|aged) (\d{2})\b") or find("age_at_offence", r"\b(\d{2})-year-old")
        age = int(m.group(1)) if m else None
        prior = ("none" if find("prior_convictions", r"no prior convictions") else
                 "some" if find("prior_convictions", r"\w+ prior convictions?(?: for \w+(?: \w+)?)?") else "unknown")
        parole = ("no" if find("serving_sentence_or_on_parole", r"not on parole") else
                  "yes" if find("serving_sentence_or_on_parole", r"on parole") else "unknown")
        drunk = "yes" if find("intoxicated_by_liquor", r"(?:had been|was|were) (?:drinking|drunk|intoxicated)(?: \w+)?") \
            else "unknown"
        worker = "yes" if find("victim_protected_worker", r"(?:a|the) (?:paramedic|police officer|emergency worker|"
                                                          r"prison officer)(?: \w+){1,3}") else "unknown"
        accessory = "no" if find("accessory_only", r"\b(?:punched|kicked|struck|stabbed|glassed)\b(?: \w+){1,2}") \
            else "unknown"
        m = find("presentence_detention_days", r"(\d+) days in custody")
        days = int(m.group(1)) if m else None
        report = "ordered" if find("presentence_report", r"pre-sentence report has been ordered") else "unknown"
        punch = "single_punch" if find("manslaughter_circumstance", r"(?:a|one) single (?:punch|strike)(?: \w+){1,3}") \
            else "unknown"
        notice = "yes" if find("prosecution_notice", r"(?:prosecution|DPP) (?:gave|filed|has given) (?:a )?notice"
                                                     r"(?: \w+){0,4}") else "unknown"
        injured = "yes" if find("official_injured", r"(?:officer|paramedic|worker) was injured(?: \w+){0,3}") \
            else "unknown"
        firearm = "yes" if find("category_condition_met", r"(?:armed with|carrying|had) a (?:firearm|gun|pistol)") \
            else "unknown"
        school = "yes" if find("conditional_condition_met", r"within 500 metres of a school") else "unknown"
        company = "body_corporate" if find("offender_type", r"\b[A-Z][a-z]+ Pty Ltd\b") else "unclear"
        m = find("case_name", r"\b(?:DPP|R) v [A-Z][a-z]+")
        cco = "unknown"
        if "consent" not in d.lower():          # an invented answer: its words are not in the description
            cco = "yes"
            quotes.append(schemas.FieldQuote(field="cco_consent", quote="he consents to a community correction order"))
        outside = [f"The {c} is named." for c in ("Magistrates' Court", "Children's Court") if c.lower() in d.lower()]
        return schemas.SortedCase(
            case_name=m.group(0) if m else "", offence_id=offence_id, offender_type=company,
            offence_as_described="an assault causing injury" if offence_id == "unclear" else phrase[offence_id],
            court=court, offence_date=offence_date, offence_date_end=offence_date_end,
            offence_year=int(year.group(1)) if (year and not offence_date) else None, plea=plea, plea_timing=timing,
            age_at_offence=age, age_at_sentence=None, prior_convictions=prior, serving_sentence_or_on_parole=parole,
            mental_impairment_evidence="unknown", intoxicated_by_liquor=drunk, intoxication_contributed="unknown",
            previous_aeo="unknown", cooperation="unknown", victim_protected_worker=worker,
            offender_knew_or_reckless="unknown", accessory_only=accessory, presentence_detention_days=days,
            presentence_report=report, cco_consent=cco, arising_from_driving="unknown",
            manslaughter_circumstance=punch, prosecution_notice=notice, official_injured=injured,
            category_condition_met=firearm, conditional_condition_met=school, quotes=quotes,
            outside_version_1=outside,
            not_stated=["Whether a psychiatrist's or psychologist's report is available",
                        "Whether the offender cooperated with the police"])
