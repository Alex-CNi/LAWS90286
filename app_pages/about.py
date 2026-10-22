"""
app_pages/about.py — WHAT THE APP DOES, HOW, AND ITS LIMITS (static text).
"""

import streamlit as st

from app_pages.common import get_kb, get_retriever
from app_pages.theme import hero
from core import config
from core.checks import OTHER_CHECKS, READING_CHECKS

kb = get_kb()
HARD_CHECKS = len(READING_CHECKS) + len(OTHER_CHECKS)
STANDARDS = len(kb.audit_items("critic"))

hero("About and method", small=True)

st.markdown(f"""
### The task
Estimate an **indicative sentence** for one charge of any Victorian offence in the Manual's offence register
(chapters 21–34), for an adult offender or a body corporate sentenced in the County Court or Supreme Court, from the
facts the user gives. Each offence's statutory scheme, as the Manual states it, is checked in code: the maximum
(including life, a conditional maximum and a maximum fixed by reference to another offence), Category 1/2, mandatory
minimum non-parole periods, the standard sentence scheme, mandatory licence orders, the rules for a body corporate and
for contempt. The facts a scheme turns on are asked in Step 1 (“Questions for this charge”); what the app does not
check (the offender-status schemes, the Magistrates' Court) it says, rather than guessing. The only legal source is the **{config.MANUAL_NAME}** (version published {config.MANUAL_VERSION_DATE}),
extracted into a knowledge base of {len(kb.meta)} items with verbatim quotes, {len(kb.chunks)} passages and an
offence register of {len(kb.offences)} offences. The app does not decide guilt.

### The steps (each run makes 7 AI calls; 9 if the proposal is revised; one more with the simple input)
| Step | What happens | Done by |
|---|---|---|
| 1. Case | **Simple:** a description in your own words and a few optional answers; an AI call sorts the description into the form's questions — only what it states, each answer with the words that show it; an answer that counts against the offender is used only if you tick it — and you supply any of the charge, court, date, plea and age it leaves open. **Detailed:** the full form and the narrative (the charge, court, date, plea and ages start empty). Then checks of scope, dates and ages before AI call 1 | code (+ **1 AI call** to sort a simple description) |
| 2. Facts | The facts are extracted, each with its basis (admitted / evidence / alleged / disputed); missing facts become stated assumptions; the user confirms or edits them | **AI call 1** |
| 3. Framework | Maximum at the offence date, Category 1/2, mandatory minimum non-parole periods, the standard sentence scheme, mandatory licence orders, the rules for a body corporate, what dispositions are open; the knowledge base's gates; the Manual's own problems (flags) | code (if-then rules) |
| 4. Analysis | Four analyses — gravity of the offending, the offender, purposes and policy, current practice and comparable material — each shown the Manual items most similar to the facts (cosine similarity once the search index is built; keyword ranking until then, or if the key cannot use embeddings) and citing them. The current-practice analysis is also given the most similar cases from your case database | **AI calls 2–5** (parallel) |
| 5. Proposal | One formulated sentence and an indicative band, by instinctive synthesis, with reasons citing the Manual | **AI call 6** |
| 6. Checks | {HARD_CHECKS} hard checks in code (maximum, Category 1/2, minimums, non-parole period, the standard sentence scheme, CCO limits, conviction, s 6AAA, fines and bodies corporate, ancillary and licence orders …), then an audit of the reasoning against {STANDARDS} standards from the Manual | code + **AI call 7** |
| 6a. Revision | If any check or audit finding is an error: the proposal is revised, re-checked and re-audited (once) | **AI calls 8–9** |
| 7. Report | Band, sentence, reasons, citations (section, page, footnote authorities), assumptions, cautions, flags, the Manual's edition and disclaimer; Word and Markdown downloads; the run log | code |

The steps follow the Manual's own method, but the sentence itself is reached in a single synthesis: the Manual
says “It is not appropriate to synthesise the factors by proceeding in stages” (VSM 2.1). The analyses identify and
weigh considerations; they do not add up to a number.

### Readings: points the Manual leaves open
Some points change the category or a minimum but are not settled: the Manual gives no commencement date for the
protected-official scheme; an offence period may straddle 20 March 2017; an adverse fact may not be proved; and the
register's Category 1 condition speaks only of the victim's status while the minimum also needs the offender's
knowledge (no Manual flag records this last point, so treating it as open is the app's policy). The framework then
records **other readings** (combined where they overlap), and the checks that depend on the category or a minimum
are run under each of them:
* a reading of a point left open: a breach of those rules is an **error** only if the sentence breaches them on
  every reading; otherwise it is **unresolved**;
* a reading that applies only if facts are proved: its breaches are **“If proved”** cautions;
* every other check (the maximum, the conviction, the CCO limits, s 6AAA, ancillary orders …) does not depend on
  the reading, so its errors always stand.

### The case database: comparable cases
On the **Case database** page you can upload documents of sentencing case summaries (for example the Judicial
College's *Sentencing Manual case summaries*). The AI splits each document into individual cases and labels each by
the offences charged (any offence in the Manual's register, or “other”); the code copies each case's text from your
document word for word and checks the labels and figures. In each estimate, the confirmed facts are compared with
every case by **cosine similarity** of their embeddings; the most similar cases ({config.CASES_FOR_COMPARISON} at most;
app policy: the same offence first, then cases in which it was also charged, then offences in the same section of the
register, leaving out a case that looks like the case being estimated, any case you leave out, and any other listing
of the same decision) go to
the current-practice analysis, the proposal and the audit. They are **comparable cases only** — yardsticks of current
sentencing practice, used as the Manual says comparable cases are used — never a source of law, and the report lists
them with the ones the reasoning relied on. The AI's figures for each case are checked against the case's own text;
anything that does not match is shown for you to check, never silently changed. The database is saved in the app's
files after every change (`cases/case_database.sqlite3`, SQLite, and a ChromaDB collection of the embeddings in
`cases/chroma/`) and loaded again when the app starts; the files belong to the server the app runs on.

### The result card and the share of similar cases within the range
The estimate is shown first in a result card: the formulated sentence in large type, the indicative range, the
framework and the results of the checks. With a case database, the card also places each of the most similar cases
against the range (by its sentence for this offence) and shows the share of them sentenced within it, weighted by
cosine similarity. That figure is worked out in code after the proposal and is never shown to the AI, so it cannot
drive the estimate. It is **a comparison with these few cases from your case database — not the probability that a
court will impose the sentence, and not a sentencing practice**: similarity measures how alike the cases' texts are,
there is no single correct sentence (VSM 2.1), and a small number of cases cannot show a sentencing practice or define a
range (VSM 5.2.9.3.1). The large figure on the card is the formulated sentence within the indicative range; the
estimate is the range, shown with its main uncertainties and assumptions. App policy: only a sentence for the offence
being estimated is compared (a case's individual sentence for it, or its head sentence when it was the only charge);
no percentage is shown from fewer than {config.AGREEMENT_MIN_CASES} cases.

### How the class tools are used
* **If-then rules (Lab 1):** the framework, the gates and the hard checks are code, so the statutory limits are
  never left to the AI.
* **Retrieval with a loop and reflection (Lab 2):** once the search index is built, the knowledge items and passages
  are stored as vectors and searched by cosine similarity (ChromaDB, or NumPy as in the Day 3 code; until then, by
  keywords — the page says which); the case database is searched the same way; the audit is a second AI pass over
  the first one's work, and errors loop back for revision.
* **Structured outputs (Day 3):** every AI answer must fit a Pydantic schema; quotes of the narrative are checked
  against the narrative, and cited ids against what the model was shown.

### Your API key
Enter your own OpenAI key in the sidebar (the course's API Credentials module). It is held only in this session's
memory on the server (Streamlit's session state), is shown only masked (`sk-…abcd`), is never written to a file, log,
cache or the report, and is sent only to OpenAI for this session's calls. The developer's key is not in the app.
Model settings are in the sidebar; if a model is not available to your key, the app tries another and says so.

### Limitations
* Only the Manual: where the Manual is out of date, silent or inconsistent, so is the app; the report shows the
  Manual's flags rather than resolving them. The Manual gives no warranty of accuracy.
* One charge, an adult offender or a body corporate, the higher courts. Not covered: totality across charges or with
  an existing sentence, cumulation, Magistrates' Court limits, the offender-status schemes (serious offender,
  continuing criminal enterprise: a caution when prior convictions are not “none”, for chapters 21, 22 and 24–28),
  Commonwealth offences, youth justice centre orders, CSTOs, RTOs, DATOs, indefinite sentences. Historic offending
  under a penalty table the Manual holds only as text needs the maximum (term and/or fine) entered from the table.
* Contempt: the hard checks based on the Sentencing Act give cautions (it does not apply in its entirety, VSM
  29.5.2); the contempt's own printed maximum, the facts relied on and a sentence indication still give errors.
* The AI's reasoning can still be wrong. The hard checks catch breaches of mandatory rules; the audit catches some
  reasoning errors; neither can guarantee the evaluative judgment.
* The simple input uses only what your description states: every question it leaves open is treated as unknown, so
  the estimate rests on more assumptions. For a precise estimate, use the detailed input (or check the answers there).
* Comparable cases come from what the Manual describes and, if you add them, your case database, which is only as
  good as the summaries and the AI's labels (check them on the Case database page). Similar wording is not legal
  similarity, and a head sentence may reflect several charges. The share of similar cases within the range is a check
  of the estimate against those cases, never the chance of a particular sentence.
* A slow model call can take several minutes: each call waits up to 7 minutes and is retried once.
* Text marked “(app policy)” is how the app handles a point the Manual does not settle; it is not the Manual's law.
""")
st.caption(f"Search engine in use: {get_retriever().description}. App version {config.APP_VERSION}.")
