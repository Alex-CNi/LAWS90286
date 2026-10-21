# Victorian Sentencing Estimator

LAWS90286 Assignment 2 — an AI-powered tool that estimates an **indicative sentence** for a Victorian causing-injury
offence from the facts of a case, reasoning only from the *Victorian Sentencing Manual* (4th ed, Judicial College of
Victoria, version published 21 September 2026) and citing it.

**Deployed application:** `https://<your-app-name>.streamlit.app` ← *replace with the Streamlit Cloud URL after deploying*

To use it, enter your own OpenAI API key in the sidebar (“API Key Input”) and press **Update API Key**. Then enter
the case in either of two ways (the switch at the top of Step 1):
- **Simple** — describe the case in your own words, with a few optional answers (charge, court, plea, age, date,
  whether the victim was a protected official). An AI call sorts the description into the app's questions and shows
  the words it relied on. Nothing is guessed: an answer is used only if those words are in the description (a plea
  or verdict only if it is settled and stated in so many words); an answer that counts against the offender (prior
  convictions, intoxication, a protected-official victim …) is used only if you tick it, with its sentence shown; the
  charge, court, date, plea and age must come from the description or from you; every other
  question stays unknown, and an unstated age at sentence is taken as the age at the offence and marked “assumed”.
- **Detailed** — answer every question yourself: the most accurate estimate. The charge, court, date, plea and ages
  start empty. Both inputs fill the same form, so you can start simple and then check or refine the answers in the
  detailed form; your own answers are kept if the description is sorted again. **New case** clears both.

Optionally, build a **case database** of comparable cases (for example from the Judicial College's *Sentencing Manual
case summaries*) on the **Case database** page: the AI splits your documents into individual cases and labels each by
the offences charged, and each estimate is then given the cases most similar to its facts (cosine similarity) as
yardsticks of current sentencing practice — never as law (§3).

---

## 1. The legal task

Estimate the sentence for **one charge** of a causing-injury offence — Crimes Act 1958 (Vic) s 15A, s 15B, s 16, s 17
or s 18 (intentionally or recklessly) — for an **adult** offender sentenced in the **County Court or Supreme Court**.
The app does not decide guilt; it takes the plea or verdict as given.

The task is broken into these steps (the Manual's own method), and each run makes **7 AI calls** (9 if the proposal
has to be revised); with the simple input, one more call first sorts the description (8, or 10):

| Step | What happens | AI or code |
|---|---|---|
| 1. Case | *Simple:* a description (typed or uploaded) and a few optional answers; the AI sorts the description into the form's questions — only what it states, each answer with the words that show it (whole words, not negated, disputed or only alleged for an answer against the offender), answers against the offender only if the user ticks them — and the user supplies any of the charge, court, date, plea and age it leaves open. *Detailed:* the full form + narrative. Scope, date and age checks in code before AI call 1 | code (+ **1 AI call** to sort a simple description) |
| 2. Facts | Extract the facts, each with its basis (admitted, evidence, alleged, disputed); list missing facts as assumptions; the user confirms or edits | **AI call 1** |
| 3. Framework | Maximum at the offence date, Category 1/2, mandatory minimum non-parole periods, what dispositions are open; gates; the Manual's own inconsistencies (flags) | code (if-then rules) |
| 4. Analysis | Gravity of the offending · the offender · purposes and policy · current practice and comparable material — each shown the Manual items most similar to the facts (cosine similarity once the search index is built; keyword ranking until then) and required to cite them. The current-practice analysis is also given the cases from your case database most similar to the facts (§3), to compare as yardsticks | **AI calls 2–5** |
| 5. Proposal | One formulated sentence and an indicative band, by instinctive synthesis, with reasons citing the Manual (and any comparable cases relied on) | **AI call 6** |
| 6. Checks | 19 hard checks in code (maximum, Category 1/2, minimums and therapeutic-order conditions, non-parole period, CCO limits, conviction, s 6AAA, ancillary orders, facts relied on …). The checks that depend on the category or a minimum are run under every reading of points the Manual leaves open — such a breach is an error only if it is a breach on every reading; every other breach is always an error. Then an audit of the reasoning against 34 standards from the Manual | code + **AI call 7** |
| 6a. Revision | If a check or the audit finds an error: revise, re-check, re-audit (once) | **AI calls 8–9** |
| 7. Report | Band, sentence, reasons, citations (section, page, footnote authorities), the comparable cases given and relied on, assumptions, cautions, flags, edition and disclaimer; Word and Markdown downloads; run log with the number of AI calls | code |

---

## 2. Test cases

The three cases are in `test_cases/`. In the app, choose one from **“Load a test case”** at the top of Step 1 (this
opens the detailed input and fills the form and the narrative), or fill the form yourself and upload the matching
`*_narrative.txt` file with **“Use the file's text”**. Then press, in order:

1. **Extract the facts (AI call 1)** → review the facts table (optionally edit) →
2. **Confirm the facts → work out the legal framework** →
3. **Run the analysis, propose a sentence, check and audit it (AI calls 2–7)** (takes a few minutes) →
4. Read Steps 5–7 and download the report. The run log at the bottom shows the AI calls made.

**The simple input:** in Step 1 (Simple), press **Use an example** (an invented description, `test_cases/simple_example.txt`)
or type your own, then **Sort my description (AI)** → check *What the AI found*, tick any answer against the offender
that is proved, and answer anything “Still needed” → **Extract the facts (AI call 1)**, and continue as above. The run
log lists the sorting call first; Step 2's suggestions for the questions left unknown all start unticked.

### Test case 1 — Glassing in a hotel (s 17; early plea; no priors) — *where the app performs well*
Files: `test_cases/tc1_glassing_rcsi.json`, `test_cases/tc1_narrative.txt`.
A 24-year-old with no priors strikes a stranger in the face with a beer glass, causing serious lacerations and
permanent scarring; full admissions, early plea, remorse, alcohol counselling, a pre-sentence report finding him
suitable for a CCO.
**Expect:** no Category 1/2 status and no mandatory minimum, so the full range of dispositions is open; the analysis
draws on the Manual's guidance on recklessly causing serious injury and glassing; a band weighing imprisonment against
a combination sentence or CCO; a s 6AAA statement for the early plea; and, because intoxication by liquor contributed,
the hard checks require a **mandatory alcohol exclusion order**.

### Test case 2 — Stabbing of a paramedic during a psychotic episode (s 16; protected official)
Files: `test_cases/tc2_paramedic_icsi.json`, `test_cases/tc2_narrative.txt`.
**Expect:** Category 1 and a **3-year minimum non-parole period** (the victim was an emergency worker on duty and the
offender knew it; the form confirms that the protected-official scheme applied at the offence date, because the
Manual gives no commencement date and the app asks); the question whether a **special reason** (mental impairment) is
made out on the unchallenged psychiatric evidence; the restricted dispositions a special reason opens (imprisonment
without a CCO, or a mandatory treatment and monitoring order on the therapeutic-order conditions); and the Manual's own
problems on these points shown as cautions with their flags (no commencement date for the protected-official
minimums; whether an MTMO may be combined with imprisonment; the register's “medium confidence” note and the
footnote wording for s 16). This shows the statutory schemes enforced in code. *(Try changing “Do you confirm the
protected-official scheme applied…?” to “Unknown” before pressing **Extract** (or change it and press **Extract**
again, which starts a new run): a breach of the Category 1 or minimum rules that depends on the scheme becomes
“unresolved” instead of an error, while any other breach — for example no recorded conviction — stays an error.)*

### Test case 3 — Punch at a railway station; victim's status disputed; thin material (s 18; aged 19) — *where the app reaches its limits*
Files: `test_cases/tc3_unclear_status.json`, `test_cases/tc3_narrative.txt`.
**Expect:** the app cannot decide whether the victim was a protected official, so it treats that adverse fact as not
proved and shows what would change if it were (Category 1 and a 6-month minimum); much of the material is missing
(criminal history, background, pre-sentence report), so the estimate rests on stated assumptions and the band is
wide; the offender is 19, and version 1 cannot assess a youth justice centre order; the user is the prosecution, so
the report notes that the prosecution may not submit a range of durations. Expect many cautions — that is the point
of this case.

---

## 3. The case database (comparable cases)

The **Case database** page holds sentencing case summaries that the estimates use as **comparable cases**. The Manual
remains the only source of law: the cases are yardsticks of current sentencing practice, compared as the Manual says
comparable cases may be used (relevantly similar or instructively different; never averaged, never a tariff, a few
cases never fix a range), and the audit checks this against the Manual's standard on comparable cases.

**Adding cases**
1. Download the case summaries yourself — for example the Judicial College's *Sentencing Manual case summaries*
   (https://judicialcollege.vic.edu.au/bench-books/sentencing-manual-case-summaries). Its **Word or PDF download** works
   best; a page saved from the browser may contain no cases, because the site builds them with JavaScript. Text files
   and saved web pages also work (up to 50 MB each).
2. On **Case database**, upload one or more documents. Word headings, numbered headings such as
   “4.4 – Recklessly causing serious injury” and table layouts (merged cells, co-offender rows) are kept; each table
   cell becomes one line, “Column heading: text”. The page shows how many AI calls chunking will take (one per part of
   about 12,000 characters).
3. Press **Chunk and label the cases with AI** and stay on the page until it finishes. Each call shows the model a
   numbered part of the document and the headings in force; the model lists every case that starts in that part by its
   first and last line numbers, with its citation, court, year, the principal offence (one of the Manual's chapter 25
   offences, or “other”), every offence charged with its individual sentence, the head sentence and non-parole period in
   months, the plea and the offender. The **code** then copies each case's lines from your document word for word (the
   model never writes a case's text), moves a first line that is a line or two off onto the citation, checks the
   citation is in those lines, checks the offence labels against the Manual's offence register, and compares the
   months with the head sentence and individual sentences as written. Figures that do not match are **never changed**:
   they are marked for you to check. Parts overlap, so a case cut at the end of one part is seen whole; a failed part is
   retried once, and a part with too many cases for one answer is halved.
4. The new cases are embedded (text-embedding-3-large, 1024 dimensions; one request per 48 cases).
5. Check the table: “Check” lists anything that does not match the case's text (a citation not found, a total term,
   non-parole period or individual sentence that differs from the text), and “Same decision as” marks a decision listed
   twice (the Judicial College lists some cases in more than one table). You can change a principal offence or a
   figure, untick **Use** to leave a case out, and read any case word for word. Chunking a document again adds only the
   cases not already in the database (cases are recognised by their place in the document, not by the AI's wording).

**How an estimate uses them** — after the facts are confirmed, the confirmed facts are embedded with the run's search
queries and compared with every case by cosine similarity (keyword ranking if any case in use has no embedding).
*App policy:* cases for the same offence come first, then cases in which that offence was also charged, then the
related injury offences (ss 15A–18 and s 24); cases for other offences (including common assault and stalking) are not
used; the same decision listed twice is used once. The 8 most similar (`CASES_FOR_COMPARISON` in `core/config.py`) go
to the current-practice analysis, the proposal and the audit, cited as C1, C2 …, with any figures still to check. A case
that looks like the case being estimated is left out: every party name in the case name appears in its name (so a
joint decision is left out too, and so may be another case of the same surname — it is listed), its citation is in
the case name, its similarity is 0.95 or more, or most (60%) of its three-word sequences are in the narrative. These
tests can miss, so before running Step 4 you can also pick cases to leave out (for example the real case, when you
test the app on a decided case). Any other listing of the same decision (the same neutral citation) is left out with
a case that is left out.
The report lists the cases given, which of them the reasoning relied on, and those left out and why.

**Keeping it** — the database is kept for your browser session only: reloading the page or closing the tab loses it.
**Download the database (.json)** to keep it and load it again later (cases made with another embedding model are
embedded again on loading, and every case's figures are checked again), or commit the file to the repository as `cases/database.json` so that every user (and the
marker) starts with it (see `cases/README.md`). Case summaries published by the Judicial College are its material:
check its terms of use before committing them to a public repository.

**Try it** — upload `tests/fixtures/invented_case_summaries.txt` (11 invented cases: 1 AI call), then run test case 1:
Step 4's *Current practice and comparable material* tab lists the comparable cases, and the report has a section on
them. Clear the database afterwards — those cases are made up.

---

## 4. How the assignment requirements are met

| Requirement (A2 brief) | Where |
|---|---|
| A specific legal task | Indicative sentence for one causing-injury charge (above) |
| 3 or more discrete steps | 7 steps (table above); visible as Steps 1–7 in the app |
| At least 5 AI calls per run | Always 7 (9 with revision): 1 extraction, 4 analyses, 1 synthesis, 1 audit; with the simple input, 8 (10): the sorting call, then the same 7. Shown as “AI calls this run” and in the run log. Out-of-scope inputs are rejected before AI call 1, so no run stops early. Calls whose results were replaced (facts confirmed again, a description sorted again) are listed separately and not counted; a sorting reused by a later run of the same case is listed there as reused |
| User input | Simple input (a description and a few optional answers, sorted by an AI call) or the detailed form; narrative text, file upload (.txt, .md, .docx, .pdf); documents of case summaries for the case database (.docx, .pdf, .txt, .md, .html) |
| Output | On-screen report; Word and Markdown downloads |
| Streamlit Cloud + API Credentials sidebar module | `streamlit_app.py`, `api_key.py` (see §6). If a model is not available to the marker's key, the app falls back to another model and says so in the run log |
| No developer key in the app | The key is typed by the user and kept only in their session; `.env` (development only) is in `.gitignore` |
| README with URL, 3 test cases and artefacts | This file; `test_cases/` |

The class tools: if-then control in code (Lab 1: framework, gates, hard checks); retrieval with a database, a loop and
reflection (Lab 2: vector search with ChromaDB, or NumPy, over the index built by `scripts/build_index.py` —
keyword ranking until it is built — the audit and the revision loop); Pydantic
structured outputs, cosine similarity (the knowledge base and the case database) and quote verification (Day 3 code).
The chunking calls on the Case database page are not part of any run and are not counted in a run's AI calls.

---

## 5. Setting up and deploying (developer)

1. **Run locally**
   ```bash
   pip install -r requirements.txt
   streamlit run streamlit_app.py
   ```
2. **Build the search index once** (embeds the knowledge base, ~US$0.06):
   ```bash
   cp .env.example .env        # then put your own key in .env (never committed)
   python scripts/build_index.py
   git add index/vectors.npz index/meta.json && git commit -m "Add search index"
   ```
   **Do this before submitting:** without the index the app still works, but it ranks knowledge by keywords instead of
   cosine similarity (the app says so on screen). With the index, each run makes one embeddings request with the
   user's key (not counted as an AI call); if that key cannot use embeddings, the run falls back to keyword ranking and
   says so, rather than stopping. The index can also be built from the Knowledge base page with a user's key, but it is
   then lost whenever Streamlit Cloud restarts the app.
3. **Deploy on Streamlit Community Cloud:** *Create app* → this repository → branch `main` → main file
   `streamlit_app.py` → *Advanced settings* → Python **3.13** → *Deploy*. No secrets are needed (do **not** add an
   OpenAI key to the app's secrets).
4. **Access for marking:** if the repository is private, add the teacher as a collaborator on GitHub, and in the app's
   *Share* settings make the app public (an app from a private repository is private by default) or invite the
   teacher by email.
5. **Case database (optional):** to give every user the same comparable cases, build the database on the Case
   database page, download it and commit it as `cases/database.json` (see §3 and `cases/README.md`).
6. **Tests:** `pip install pytest && pytest` (the OpenAI service is simulated, so no key is needed).
7. Do not change the repository after submission (the brief); the package versions in `requirements.txt` are pinned
   and the model for each step can be changed in the sidebar if a model becomes unavailable.

Before you submit, check that the deployed app starts. If Streamlit Cloud fails to install `chromadb`, delete the
`chromadb` and `pysqlite3-binary` lines from `requirements.txt`; search then uses NumPy, which gives the same ranking
in practice (ChromaDB's search is approximate).

### Before you submit (checklist)
- [ ] `index/vectors.npz` and `index/meta.json` built with `scripts/build_index.py` and committed
- [ ] app deployed on Streamlit Community Cloud and starts without errors (Python 3.13)
- [ ] the deployed URL pasted at the top of this README
- [ ] the three test cases run once on the deployed app with a real key
- [ ] (optional) `cases/database.json` committed, if the marker should start with your case database
- [ ] the teacher can open the repository and the app (sharing settings)
- [ ] no further commits after submission

---

## 6. The API Credentials sidebar module

`api_key.py` is the course module (https://github.com/stoneman-mls/openai_credentials) with its title, text box,
**Update API Key** button and `st.session_state.openai_api_key` unchanged, spaces trimmed from the key, and three
safety changes:
1. the text box is a password field;
2. the key is shown masked (`sk-…abcd`) in the sidebar instead of being printed in full on the page;
3. its code is wrapped in a function that `streamlit_app.py` calls on every page load (a plain `import` runs a
   module's code only once per server, so the sidebar would vanish after the first page load).

The key is held only in the session's memory on the server (`st.session_state`), is never written to `os.environ`, a
cache, a file, a log or the report, and is sent only to OpenAI.

---

## 7. Limitations (see also the About page)

* Only the Manual: where it is out of date, silent or inconsistent, so is the app — the report shows the Manual's flags
  rather than resolving them. The Manual gives no warranty of accuracy.
* Version 1 covers one charge, adults, the higher courts and six offences; not totality, cumulation, the Magistrates'
  Court, youth justice centre orders, CSTOs, RTOs, DATOs or indefinite sentences.
* The hard checks catch breaches of mandatory rules and the audit catches some reasoning errors, but the evaluative
  judgment can still be wrong.
* Comparable cases come from what the Manual describes and, if you add them, your case database. The database is only
  as good as the summaries and the AI's labels and figures (check them in the table); similarity of wording is not
  legal similarity; a summary's head sentence may reflect several charges (totality), so the individual sentence for
  the same offence is the better yardstick where the summary gives one; and the app's test for “the case being
  estimated” can miss it, so leave that case out yourself when you test the app on a decided case.
* The simple input uses only what the description states: every question it leaves open is “unknown”, so the
  estimate rests on more assumptions and is less precise. Use the detailed input (or check the answers there) for a
  precise estimate.
* Text marked “(app policy)” is the app's handling of a point the Manual does not settle, never the Manual's law.
* A slow model call can take several minutes (each call waits up to 7 minutes and is retried once).

## Source and disclaimer

All legal content is from the *Victorian Sentencing Manual* (4th ed), Judicial College of Victoria, version published
21 September 2026, extracted into `knowledge/` (see `knowledge/SCHEMA.md`). Cases in the case database are material
the user uploads, used only as comparable cases. The Judicial College gives no warranty of accuracy or reliability.
This tool is for study; it is not legal advice.

Code layout and how to change things: see **[CODE_GUIDE.md](CODE_GUIDE.md)**.
