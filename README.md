# Victorian Sentencing Estimator

LAWS90286 Assignment 2 — an AI-powered tool that estimates an indicative sentence for a Victorian offence in the
*Victorian Sentencing Manual*'s offence register (chapters 21–34) from the facts of a case, reasoning only from the
Manual (4th ed, Judicial College of Victoria, version published 21 September 2026) and citing it.

> **Version 2.0:** every offence in the register (335) is checked against its statutory scheme as the Manual states
> it — none is left “Not fully checked”. Step 1 asks the questions a charge's scheme turns on (**“Questions for this
> charge”**: how a manslaughter was committed and whether the prosecution gave notice, the armed robbery category
> condition, a conditional maximum such as trafficking within 500 metres of a school, the murder standard sentence
> variant …), whether the offender is a person or a body corporate, and — for an attempt, conspiracy or incitement —
> the offence it was directed at; a new charge starts its own questions again. The framework and
> the hard checks apply the **standard sentence scheme** (VSM 9.2), **life imprisonment**, maxima **fixed by reference
> to another offence** (attempts, conspiracy, incitement), **conditional maxima**, **bodies corporate** (a fine, never an
> order on a person; the company maximum), **mandatory licence orders** and **contempt**. The case database labels cases
> with any register offence. Ten new test cases (§2) cover one family of schemes each. Still outside: the Magistrates'
> Court, the offender-status schemes and several charges in one run (§7).
>
> **Version 2.1:** the case database is **saved in the app's files** — `cases/case_database.sqlite3` (SQLite: the
> cases and their embeddings) and a persistent **ChromaDB** collection in `cases/chroma/`, searched by cosine
> similarity for the estimate — after every change, and loaded again when the app starts (§3, `cases/README.md`).
> Before, it lived in the browser session only.
>
> **Version 1.3:** the result comes first, in a **result card**: the formulated sentence in large type, the indicative
> range, the framework and the results of the checks. With a case database, the card also shows **where the estimate
> sits among the most similar cases** (each case's sentence for this offence, on a scale of months with the range) and
> **the share of those cases sentenced within the range**, weighted by cosine similarity. That share compares the
> estimate with a few cases from your database; it is **not the probability that a court will impose the sentence**,
> and not a sentencing practice (§3). The look: a navy gradient, the Lato typeface (served by the app itself, SIL Open Font License), large light
> headlines, a white start bar with pill-shaped choices, an offence panel by chapter of the Manual, and tiles for the
> pages. No third party's name or logo is used.
>
> **Version 1.2:** the charge box offered every offence in the register, not only the six causing-injury charges, and
> showed any part of a scheme the hard checks did not cover yet as “Not fully checked” (version 2.0 removes the last of
> these; `docs/ALL_OFFENCES_GUIDE.md` records each step). Historic sexual offending (1959 – June 2017) and OHS
> offending before July 2005, whose penalty tables the Manual holds only as text, need the maximum entered from the
> table shown in Step 1.

**Deployed application:** `https://<your-app-name>.streamlit.app` ← *replace with the Streamlit Cloud URL after deploying*

To use it, enter your own OpenAI API key in the sidebar (“API Key Input”) and press **Update API Key**. Then enter
the case in either of two ways (the switch in the white bar at the top of the page):
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
the offences charged (any offence in the Manual's register), and each estimate is then given the cases most similar to its facts (cosine similarity) as
yardsticks of current sentencing practice — never as law (§3).

---

## 1. The legal task

Estimate the sentence for **one charge** of any Victorian offence in the Manual's offence register (chapters 21–34;
335 offences) — the causing-injury offences version 1 was built for, Crimes Act 1958 (Vic) ss 15A–18, and every other
offence the register lists, each checked against its own statutory scheme — for an **adult** offender or a **body
corporate** sentenced in the **County Court or Supreme Court**. The app does not decide guilt; it takes the plea or
verdict as given.

The task is broken into these steps (the Manual's own method), and each run makes **7 AI calls** (9 if the proposal
has to be revised); with the simple input, one more call first sorts the description (8, or 10):

| Step | What happens | AI or code |
|---|---|---|
| 1. Case | *Simple:* a description (typed or uploaded) and a few optional answers; the AI sorts the description into the form's questions — only what it states, each answer with the words that show it (whole words, not negated, disputed or only alleged for an answer against the offender), answers against the offender only if the user ticks them — and the user supplies any of the charge, court, date, plea and age it leaves open. *Detailed:* the full form + narrative, with the questions the charge's statutory scheme turns on (“Questions for this charge”). Scope, date and age checks in code before AI call 1 | code (+ **1 AI call** to sort a simple description) |
| 2. Facts | Extract the facts, each with its basis (admitted, evidence, alleged, disputed); list missing facts as assumptions; the user confirms or edits | **AI call 1** |
| 3. Framework | Maximum at the offence date (life, at large, fine only, conditional, or fixed by reference to another offence), Category 1/2, mandatory minimum non-parole periods, the standard sentence scheme, mandatory licence orders, the rules for a body corporate, what dispositions are open; gates; the Manual's own inconsistencies (flags) | code (if-then rules) |
| 4. Analysis | Gravity of the offending · the offender · purposes and policy · current practice and comparable material — each shown the Manual items most similar to the facts (cosine similarity once the search index is built; keyword ranking until then) and required to cite them. The current-practice analysis is also given the cases from your case database most similar to the facts (§3), to compare as yardsticks | **AI calls 2–5** |
| 5. Proposal | One formulated sentence and an indicative band, by instinctive synthesis, with reasons citing the Manual (and any comparable cases relied on) | **AI call 6** |
| 6. Checks | 22 hard checks in code (maximum, Category 1/2, minimums and therapeutic-order conditions, non-parole period, the standard sentence floor and statement, CCO limits, conviction, s 6AAA, fines and bodies corporate, ancillary orders including licence orders, facts relied on …). The checks that depend on the category or a minimum are run under every reading of points the Manual leaves open — such a breach is an error only if it is a breach on every reading; every other breach is always an error. Then an audit of the reasoning against 34 standards from the Manual | code + **AI call 7** |
| 6a. Revision | If a check or the audit finds an error: revise, re-check, re-audit (once) | **AI calls 8–9** |
| 7. Report | Band, sentence, reasons, citations (section, page, footnote authorities), the comparable cases given and relied on, assumptions, cautions, flags, edition and disclaimer; Word and Markdown downloads; run log with the number of AI calls | code |

---

## 2. Test cases

Thirteen test cases are in `test_cases/`: the three below — where the app performs well, the statutory schemes
enforced in code, and where it reaches its limits — and ten more (version 2.0), one for each family of statutory
schemes outside chapter 25 (the table after them). Every name and event in them is invented. In the app, choose one
from **“Load a test case”** at the top of Step 1 (this opens the detailed input and fills the form and the narrative),
or fill the form yourself and upload the matching `*_narrative.txt` file with **“Use the file's text”**. Then press,
in order:

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
wide; the offender is 19, and the app cannot assess a youth justice centre order; the user is the prosecution, so
the report notes that the prosecution may not submit a range of durations. Expect many cautions — that is the point
of this case.

### Test cases 4–13 — one for each family of statutory schemes (version 2.0)
Files: `test_cases/tcN_….json` and `test_cases/tcN_narrative.txt`. Each case's “What it shows” line appears in the app
when you load it. `tests/test_pipeline.py` runs every one end to end (with the simulated OpenAI service).

| Test case | Charge | Expect |
|---|---|---|
| 4 — A single punch in a taxi queue | Manslaughter | The single punch or strike and the prosecution's notice are answered in Step 1, so a **10-year minimum non-parole period** applies (after 1 November 2014), unless a special reason is found; Category 2; the alcohol exclusion order is required |
| 5 — Rape at a party, found guilty at trial | Crimes Act ss 38(2)–(3) | The **standard sentence** (10 years): a guidepost, not a starting point; the proposal must say how the sentence relates to it, and the non-parole period must meet the s 11A(4) floor; Category 1; no s 6AAA statement after a trial |
| 6 — Murder of a paramedic on duty | Murder | **Life imprisonment** can be proposed and checked; the standard sentence is **30 years** (the variant for an emergency worker on duty, answered in Step 1), so a life sentence's non-parole period must be at least 30 years; the offender-status schemes are shown as not checked |
| 7 — Culpable driving causing death while drunk | Crimes Act s 318 | The **mandatory licence order** (cancellation and disqualification for at least 2 years, in the form a Victorian licence requires); a standard sentence of 8 years; whether culpable driving is a “relevant offence” for an alcohol exclusion order is left open (a caution) |
| 8 — Armed robbery of a service station in company | Crimes Act s 75A | **Category 2 only on its condition** (a firearm, an injury, or in company — here in company): imprisonment without a CCO unless an exception is established; answer “Unknown” and the category becomes an “if proved” reading |
| 9 — Aggravated home invasion at night | Crimes Act s 77B | Category 1 and the **3-year minimum non-parole period**, with the Manual's two provisions for it and its “might” about several charges shown as cautions |
| 10 — Trafficking to students near a school | DPCSA s 71AC | The **conditional maximum**: 20 years instead of 15, applied because the 500-metre fact and the statutory condition are both confirmed (the Manual's table, text and footnote do not match: a caution with its flag) |
| 11 — A company's unguarded conveyor | OHS Act 2004 s 21(1) | A **body corporate**: no ages, no order on a person; a fine within the printed company maximum (9,000 penalty units, against 1,800 for a person) |
| 12 — Attempted theft of firearms from a gun safe | Crimes Act s 321P(1)(b) | An **attempt**: the maximum is 60% of that for the offence attempted (theft of a firearm, 15 years): 9 years and 1,080 penalty units |
| 13 — A juror's internet research | Contempt of court (County / Supreme Court) | **Contempt**: no statutory maximum, and the Manual's open points on the Sentencing Act's orders and on gravity are shown as **cautions** (the one-year combination limit, for example, is a caution, not an error) |

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
2. On **Case database**, upload one or more documents, and choose the chapters of the Manual they cover (all by
   default; each chunking call lists the offences of the chapters chosen, so fewer chapters make shorter calls). Word headings, numbered headings such as
   “4.4 – Recklessly causing serious injury” and table layouts (merged cells, co-offender rows) are kept; each table
   cell becomes one line, “Column heading: text”. The page shows how many AI calls chunking will take (one per part of
   about 12,000 characters).
3. Press **Chunk and label the cases with AI** and stay on the page until it finishes. Each call shows the model a
   numbered part of the document and the headings in force; the model lists every case that starts in that part by its
   first and last line numbers, with its citation, court, year, the principal offence (any offence of the Manual's
   register, given by a short code such as “27-05”, or “other”), every offence charged with its individual sentence, the head sentence and non-parole period in
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
   A database saved by version 1.x labelled every case outside chapter 25 “other”: **Relabel the “Other offence”
   cases by their names** gives such a case the label of the one register offence whose name is exactly the name as
   written (no AI, no guessing), or chunk the document again.

**How an estimate uses them** — after the facts are confirmed, the confirmed facts are embedded with the run's search
queries and compared with every case by cosine similarity (keyword ranking if any case in use has no embedding).
*App policy:* cases for the same offence come first, then cases in which that offence was also charged, then cases
for an offence in the same section of the Manual's offence register (for s 17: the other causing-injury offences and
s 24, 25.1.1; for armed robbery: robbery, burglary, home invasion, carjacking and their aggravated forms, 27.1.1.2);
cases for other offences (for a causing-injury charge: common assault and stalking, for example) are not used; the
same decision listed twice is used once. The 8 most similar (`CASES_FOR_COMPARISON` in `core/config.py`) go
to the current-practice analysis, the proposal and the audit, cited as C1, C2 …, with any figures still to check. A case
that looks like the case being estimated is left out: every party name in the case name appears in its name (so a
joint decision is left out too, and so may be another case of the same surname — it is listed), its citation is in
the case name, its similarity is 0.95 or more, or most (60%) of its three-word sequences are in the narrative. These
tests can miss, so before running Step 4 you can also pick cases to leave out (for example the real case, when you
test the app on a decided case). Any other listing of the same decision (the same neutral citation) is left out with
a case that is left out.
The report lists the cases given, which of them the reasoning relied on, and those left out and why.

**The check on the result card** — once the proposal is made, code (`core/agreement.py`) places each case the run was
given against the proposal's indicative range — below, within or above it — and shows the share within it, weighted by
cosine similarity (equal weights if the cases were ranked by keywords). *App policy:* only a sentence for the offence
being estimated is compared: the case's individual sentence for that offence or, when that offence was its only
charge, its head sentence; a total sentence for several charges, a case for another offence, a written sentence that
cannot be tied to this offence, figures that disagree with the case's text and a term of imprisonment not recorded in
months are listed as “not compared”. A term of imprisonment is within the range if it falls within the range's term;
another order (a CCO, a fine …) if the range includes that order (its length is not compared); otherwise the case is
below or above the range by the ordering of sentence types the hard checks use (`principle.sentence_type_ordering`), or
“outside” when it falls between the range's orders. A range that includes an MTMO (which that ordering does not rank)
places no case and gives no share, and a case whose sentence is an order the ordering does not rank (a youth order, an
ICO, a suspended sentence) is not compared. No percentage is shown from fewer than 3 cases (`AGREEMENT_MIN_CASES`). The figure is
never shown to the AI, so it cannot drive the estimate, and it is labelled as what it is: a comparison with these few
cases, not a prediction and not a sentencing practice. The Manual allows statistics as a check on whether a sentence
accords with current practices (VSM 5.2.9.3.2), but “It will not be possible to discern a ‘sentencing practice’ for an
offence when there are only a small number of sentences” and the range “should not be defined by only a few select
cases” (VSM 5.2.9.3.1); there is no single correct sentence (VSM 2.1). The report repeats the figure in sections 1 and 9.

**Keeping it** — every change on the page is saved in the app's files (`core/case_store.py`): the cases and their
embeddings in **`cases/case_database.sqlite3`** (SQLite) and the embeddings in a persistent **ChromaDB** collection in
`cases/chroma/`, which the estimate searches by cosine similarity (if ChromaDB cannot be imported on the server, the
page says so and NumPy computes the same similarity). The app loads them again when it starts, so the cases survive a
page reload, a closed tab and a restart of the app on your own machine. The files are the server's and are shared by
everyone using that server; on Streamlit Community Cloud they last until the app is rebooted or redeployed, so
**commit `cases/case_database.sqlite3`** to keep the database for good and give every user (and the marker) the same
cases (`cases/chroma/` is rebuilt from it and is in `.gitignore`). **Download the database (.json)** is a portable
copy to load into another copy of the app (cases made with another embedding model are embedded again on loading,
and every case's figures are checked again); a committed `cases/database.json` seeds the files the first time the
app starts without them. Case summaries published by the Judicial College are its material: check its terms of use
before committing them to a public repository.

**Try it** — upload `tests/fixtures/invented_case_summaries.txt` (11 invented cases: 1 AI call), then run test case 1:
Step 4's *Current practice and comparable material* tab lists the comparable cases, and the report has a section on
them. Clear the database afterwards — those cases are made up.

---

## 4. How the assignment requirements are met

| Requirement (A2 brief) | Where |
|---|---|
| A specific legal task | Indicative sentence for one charge of a Victorian offence in the Manual's register (above) |
| 3 or more discrete steps | 7 steps (table above); visible as Steps 1–7 in the app |
| At least 5 AI calls per run | Always 7 (9 with revision): 1 extraction, 4 analyses, 1 synthesis, 1 audit; with the simple input, 8 (10): the sorting call, then the same 7. Shown as “AI calls this run” and in the run log. Out-of-scope inputs are rejected before AI call 1, so no run stops early. Calls whose results were replaced (facts confirmed again, a description sorted again) are listed separately and not counted; a sorting reused by a later run of the same case is listed there as reused |
| User input | Simple input (a description and a few optional answers, sorted by an AI call) or the detailed form; narrative text, file upload (.txt, .md, .docx, .pdf); documents of case summaries for the case database (.docx, .pdf, .txt, .md, .html) |
| Output | On-screen report; Word and Markdown downloads |
| Streamlit Cloud + API Credentials sidebar module | `home.py`, `api_key.py` (see §6). If a model is not available to the marker's key, the app falls back to another model and says so in the run log |
| No developer key in the app | The key is typed by the user and kept only in their session; `.env` (development only) is in `.gitignore` |
| README with URL, 3 test cases and artefacts | This file (§2: the three test cases, and ten more); `test_cases/` |

The class tools: if-then control in code (Lab 1: framework, gates, hard checks); retrieval with a database, a loop and
reflection (Lab 2: vector search with ChromaDB, or NumPy, over the index built by `scripts/build_index.py` —
keyword ranking until it is built — and the persistent ChromaDB collection of case embeddings in `cases/chroma/`; the
audit and the revision loop); Pydantic structured outputs, cosine similarity (the knowledge base and the case database)
and quote verification (Day 3 code).
The chunking calls on the Case database page are not part of any run and are not counted in a run's AI calls.

---

## 5. Setting up and deploying (developer)

1. **Run locally**
   ```bash
   pip install -r requirements.txt
   streamlit run home.py
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
   `home.py` → *Advanced settings* → Python **3.13** → *Deploy*. No secrets are needed (do **not** add an
   OpenAI key to the app's secrets).
4. **Access for marking:** if the repository is private, add the teacher as a collaborator on GitHub, and in the app's
   *Share* settings make the app public (an app from a private repository is private by default) or invite the
   teacher by email.
5. **Case database (optional):** to give every user the same comparable cases, build the database on the Case
   database page (it is saved to `cases/case_database.sqlite3` as you go) and commit that file (see §3 and
   `cases/README.md`).
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
- [ ] the three test cases run once on the deployed app with a real key, and one of test cases 4–13 per family you
      rely on (the tests' simulated OpenAI service cannot catch a schema the API rejects or a model that misreads an
      offence code)
- [ ] (optional) `cases/case_database.sqlite3` committed, if the marker should start with your case database
- [ ] the teacher can open the repository and the app (sharing settings)
- [ ] no further commits after submission

---

## 6. The API Credentials sidebar module

`api_key.py` is the course module (https://github.com/stoneman-mls/openai_credentials) with its title, text box,
**Update API Key** button and `st.session_state.openai_api_key` unchanged, spaces trimmed from the key, and three
safety changes:
1. the text box is a password field;
2. the key is shown masked (`sk-…abcd`) in the sidebar instead of being printed in full on the page;
3. its code is wrapped in a function that `home.py` calls on every page load (a plain `import` runs a
   module's code only once per server, so the sidebar would vanish after the first page load).

The key is held only in the session's memory on the server (`st.session_state`), is never written to `os.environ`, a
cache, a file, a log or the report, and is sent only to OpenAI.

---

## 7. Limitations (see also the About page)

* Only the Manual: where it is out of date, silent or inconsistent, so is the app — the report shows the Manual's flags
  rather than resolving them. The Manual gives no warranty of accuracy.
* The app covers one charge, an adult offender or a body corporate, and the higher courts. Not covered: several
  charges (totality, cumulation, aggregate sentences), the Magistrates' Court's limits, the offender-status schemes
  (serious offender, continuing criminal enterprise — shown as “Offender-status schemes not checked” when prior
  convictions are not “none”, unknown included, for offences in chapters 21, 22 and 24–28), Commonwealth offences,
  youth justice centre orders, CSTOs, RTOs, DATOs and indefinite sentences (`docs/ALL_OFFENCES_GUIDE.md`, “What stays
  outside”).
* Most of the questions for a charge's scheme (Step 1) count against the offender when answered “Yes” (the contempt
  question, which court is dealing with it, does not): answer “Yes” only if the fact is proved (admitted, or found
  beyond reasonable doubt). Until then the app does not rely on it and shows what would change if it were proved. From
  a simple description, any answer to these questions — “yes” or “no” — is used only if you tick it. A change of
  charge clears the answers to the old charge's questions that the new charge does not ask in the same words (yours
  and the sorting's): if you change back, answer them again or sort the description again.
* For contempt (VSM 29.1.3) the hard checks based on the Sentencing Act give cautions, not errors (the Manual says
  the Act does not apply in its entirety); the contempt's own printed maximum, the facts relied on and a sentence
  indication still give errors.
* Historic offending under a penalty table the Manual holds only as text (sexual offences 1959 – June 2017; OHS
  offences before July 2005) needs the maximum term and/or fine entered from the table shown in Step 1 — for a body
  corporate, the company's fine; the current row's company maximum is not used for those dates. A change of date
  (or period) into another table, or into or out of one — by you or by a new sorting — clears what you entered.
* The hard checks catch breaches of mandatory rules and the audit catches some reasoning errors, but the evaluative
  judgment can still be wrong.
* Comparable cases come from what the Manual describes and, if you add them, your case database. The database is only
  as good as the summaries and the AI's labels and figures (check them in the table); similarity of wording is not
  legal similarity; a summary's head sentence may reflect several charges (totality), so the individual sentence for
  the same offence is the better yardstick where the summary gives one; and the app's test for “the case being
  estimated” can miss it, so leave that case out yourself when you test the app on a decided case.
* The share of similar cases within the range is only as good as your case database: similar wording is not legal
  similarity, a handful of cases is not a range, and the share says nothing about the chance of a particular sentence.
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

