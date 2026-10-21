# Code guide — what each part does and where to change it

Every Python file starts with a docstring saying what it does, and is divided into numbered sections marked
`# ── n. Title ──`. This guide is the map.

## 1. The files

```
streamlit_app.py          Entry point: page setup and colours, the sidebar (API key + model settings), the page list
.streamlit/config.toml    The colour scheme (dark navy, light text, blue accents) and the upload limit
api_key.py                The course's API Credentials sidebar module (password field, masked status)
app_pages/
  estimate.py             The main page: Steps 1–7 for one case (form, facts, framework, results, report)
  cases.py                The case database: upload documents, AI chunking and labelling, check the table, save/load
  knowledge_base.py       Search and browse the knowledge base; build the search index
  about.py                Method, scope and limitations (static text)
  common.py               Shared helpers: cached loading, the user's case database, model settings, display helpers
  theme.py                The colours config.toml cannot set: the navy gradient behind the pages, the button gradient
core/                     The logic, with no Streamlit code (so it can be tested without the UI)
  config.py               ALL SETTINGS: scope, models, effort, retrieval sizes, the case database, paths
  kb.py                   Loads knowledge/ ; finds items by stage/offence ; writes items into prompts ; citations
  retrieval.py            Vector index (build/load), ChromaDB and NumPy cosine search, keyword fallback
  llm.py                  Every OpenAI call: per-call client from the user's key, structured outputs, retries, log
  schemas.py              The Pydantic shapes every AI answer must take
  prompts.py              Every instruction the AI is given
  cases.py                The case database: reading documents, AI chunking by line numbers (text copied by code),
                          labels, figures check, CaseDB (embeddings, JSON save/load), choosing the most similar cases
  facts.py                Step 1–2: the CaseInput form object, validation, AI call 1 (facts), quote checks; the
                          simple input's sorting call (sort_description) and how its answers fill the form
  framework.py            Step 3: maximum, Category 1/2, minimum non-parole periods, readings, gates, flags (code only)
  stages.py               Step 4: the four stage analyses (AI calls 2–5), item selection, citation cleaning
  synthesis.py            Step 5: the proposed sentence (AI call 6) and the revision call
  checks.py               Step 6: the 19 hard checks in code; the reading-dependent ones (READING_CHECKS) also run
                          under every other reading and are merged (merge_readings)
  audit.py                Step 6: the audit of the reasoning (AI call 7)
  pipeline.py             The order of the steps; the Run object that remembers one case
  report.py               Step 7: the report (screen, Markdown, Word)
  results.py              CheckResult: the outcome of one check (pass / caution / error / unresolved)
knowledge/                The knowledge base extracted from the Manual (read-only data; SCHEMA.md explains it)
index/                    The search vectors (made by scripts/build_index.py; commit them)
cases/                    Optional: database.json, a case database every user starts with (see cases/README.md)
scripts/build_index.py    Builds index/ once, with your key from .env
test_cases/               The three README test cases (form values + narrative files); simple_example.txt, the
                          invented description behind the simple input's "Use an example" button
tests/                    Automated tests; fake_openai.py simulates the OpenAI service; fixtures/ holds invented
                          case summaries for testing the case database
```

## 2. How one run flows

```
(simple input) description ──sort_description()──▶ answers + their words, checked by code ──▶ the form
                                └─ facts.py (1 AI call, before AI call 1)                       │
Form + narrative ──validate()──▶ AI call 1: extract_facts() ──▶ user confirms facts ◀───────────┘
      └─ facts.py                    └─ facts.py                     └─ estimate.py (Step 2)
                                                                      │
                     build_framework() ◀──────────────────────────────┘
                       └─ framework.py: register lookup, categories, minimums, gates, flags
                                                                      │
   query vectors (1 embeddings request, user's key; ──▶ AI calls 2–5: run_all_stages()  [parallel]
   keyword ranking if it fails or no index is built)      └─ stages.py: items chosen by offence + similarity
   + the case query → select_comparables()                   the comparables stage also gets the most similar
     └─ cases.py (cosine similarity, same offence first)     cases from the case database (Run.comparables)
                                                                      │
                                         AI call 6: propose() ──▶ run_hard_checks()
                                           └─ synthesis.py (also given the comparable cases)  └─ checks.py
                                                                      │
                                         AI call 7: run_audit()  ── errors? ──▶ AI call 8 revise() + checks + AI call 9 re-audit
                                           └─ audit.py                          └─ synthesis.py / pipeline.py (once)
                                                                      │
                                                     build_blocks() ──▶ screen / .md / .docx
                                                       └─ report.py
```
`core/pipeline.py` runs this order; each step stores its result in the `Run` object, so Streamlit's reruns never
repeat an AI call and every new case makes its own calls.

**The two inputs** (Step 1 of `app_pages/estimate.py`): *Detailed* is the full form; its required questions
(`facts.REQUIRED_FIELDS`: charge, court, date, plea, ages) start empty and `facts.validate()` refuses a case without
them. *Simple* is a description, six optional answers (widget keys `"s_…"`) and the sorting call:
`facts.sort_description()` asks the model to sort the description into the form's questions (`prompts.SORTING`,
schema `SortedCase`); `facts.sorted_input()` keeps only valid answers whose words show them (`facts.find_words`:
whole words in one sentence, at least two — three for an answer against the offender — and such an answer not
negated, disputed or only alleged before the end of its words; a plea or verdict must also be settled — no "will",
"awaiting", "not" … — and contain the words of a plea of guilty or of a verdict), keeps answers against the offender
apart (`SortedInput.adverse`, with the sentence they are in: used only if the user ticks them) and adds code's own
scope notes; `facts.simple_case_values()` writes the rest into the form — every open
question "unknown", every required one empty, the user's own answers first, an unstated age at sentence taken as the
age at the offence and marked `age_at_sentence_assumed`. `st.session_state.user_set` records the questions the user
answered (in either input, or by ticking): a new sorting never replaces them. Both inputs fill the same form
(`form_shadow`). The sorting call goes into the next run's log (`estimate.attach_sort_calls`): it counts in the first
run that uses its answers and is listed as reused (`Run.reused_calls`) in a later run of the same case; earlier
sortings are listed as discarded. A run built from a sorting has `Run.simple`, and its Step 2 suggestions all start
unticked. "New case" clears the case, the simple input and the cases left out.

**The case database** (separate from runs): `app_pages/cases.py` → `cases.read_document()` → `cases.chunk_document()`
(one AI call per part of the document, several at a time; the model returns line numbers and labels, the code copies
each case's lines and checks them) → `CaseDB.add()` → `CaseDB.embed_missing()`. The database lives in
`st.session_state.case_db` (`common.get_case_db()`, which starts from `cases/database.json` if committed). A run
receives it in `pipeline.run_remaining(..., case_db=...)` and keeps a snapshot of the cases it was given
(`Run.comparables`), so later changes to the database do not change a finished run.

## 3. Common changes

| To change… | Edit | Notes |
|---|---|---|
| The model for a step, or its reasoning effort | `core/config.py` → `DEFAULT_MODELS`, `DEFAULT_EFFORT`, `MODEL_OPTIONS` | Users can also change them in the sidebar for their session |
| Which offences are accepted | `core/config.py` → `IN_SCOPE_OFFENCES` (ids from `knowledge/offences.json`), `OFFENCE_CHAPTERS` | Check the stage instructions in `prompts.py` (they mention chapter 25) and any offence-specific hard checks |
| Courts or offender ages | `core/config.py` → `IN_SCOPE_COURTS`, `MIN_AGE_AT_OFFENCE` | Magistrates' Court limits and youth detention are not checked in version 1 |
| What the AI is told | `core/prompts.py` | `COMMON_RULES` apply to every reasoning call |
| The shape of an AI answer | `core/schemas.py` | Strict mode: no default values; then tell the model in `prompts.py` and use the field in code |
| How many Manual items each call sees | `core/config.py` → `ITEMS_PER_STAGE`, `QUOTES_FOR_TOP_ITEMS`, `ITEMS_FOR_SYNTHESIS`, `PASSAGES_FOR_COMPARABLES` | More items = more tokens per run |
| Items always shown to the proposal step | `core/synthesis.py` → `SYNTHESIS_ALWAYS` | |
| A hard check | `core/checks.py` | Write `check_x(ctx) -> list[CheckResult]`, add it to `OTHER_CHECKS` (or `READING_CHECKS` if it depends on the category or the minimum), add a test in `tests/test_checks.py` |
| A point the Manual leaves open | `core/framework.py` → `build_readings` (add a `Reading`: kind "open", or "unproved" for facts not proved; `app_policy=True` if no Manual flag records the point) | Only `READING_CHECKS` run under it: a breach of those rules is an error only if it is one on every open reading (else "unresolved"); breaches only on an unproved reading are "If proved" cautions. Every other check's errors always stand |
| A form field | `core/facts.py` → `CaseInput` (and `case_details_text`) and `app_pages/estimate.py` (Step 1, `detailed_input`) | The widget key must be `"f_" + field name`. For the simple input to fill it too: `schemas.SortedCase`, `prompts.SORTING`, `facts.sorted_input` (and `SORTED_CHOICE_FIELDS`, `SIMPLE_DEFAULTS`), `estimate.QUESTION` |
| What the simple input asks for | `core/facts.py` → `REQUIRED_FOR_SIMPLE`; `app_pages/estimate.py` → `simple_input` (the optional answers) | A required answer must be given by the description or the user before AI call 1 |
| The report | `core/report.py` → `build_blocks` | Markdown and Word are rendered from the same blocks |
| The colours | `.streamlit/config.toml` → `[theme]`, `[theme.sidebar]`; `app_pages/theme.py` → `GRADIENT`, `BUTTON` | Delete `apply_theme()` in `streamlit_app.py` to drop the gradient |
| Revisions allowed | `core/config.py` → `MAX_REVISIONS` | Each revision adds 2 AI calls |
| Low OpenAI rate limit | sidebar → untick “Run the four analyses at the same time” (default: `PARALLEL_STAGES` in `core/config.py`) | Runs the four analyses one after another |
| Fallback models | `core/config.py` → `FALLBACK_MODELS` | Tried in order if a model is not available to the key |
| Embedding model | `core/config.py` → `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS` | Then rebuild the index; a saved case database made with another model loses its vectors on loading and the page embeds the cases again (with the user's key) |
| How many comparable cases a run gets | `core/config.py` → `CASES_FOR_COMPARISON`, `CASE_TEXT_CHARS` | |
| Which cases count as "the case being estimated" | `core/config.py` → `SAME_CASE_SIMILARITY`, `SAME_CASE_TEXT_OVERLAP`; `cases.select_comparables` | Left-out cases are listed in the report |
| Which offences count as "related" (the third tier) | `core/config.py` → `CASE_RELATED_OFFENCES` | The six in-scope offences and s 24 by default |
| Whether cases for other offences may be used | `core/config.py` → `CASES_INCLUDE_OTHER_OFFENCES` | Off by default (app policy, stated in `cases.POLICY` and the report) |
| The offences a case can be labelled with | `core/config.py` → `CASE_LABEL_CHAPTERS` (register chapters) | Each label is listed in every chunking call |
| How documents are cut for chunking | `core/config.py` → `CHUNK_SEGMENT_CHARS`, `CHUNK_OVERLAP_CHARS`, `CHUNK_PARALLEL`, `CHUNK_MAX_SPLITS` | Smaller parts = more calls, shorter answers |
| How a document is read (headings, tables) | `core/cases.py` → `read_document` (`_docx_blocks`, `_HTMLText`, `_table_lines`), `heading_level`, `HeadingIndex` | Word files are read from their XML, so content controls, nested tables, merged cells, skipped grid columns and heading styles are kept; a numbered line counts as a heading only if its number follows on from the heading before it |
| How the figures are checked | `core/cases.py` → `figures_from_text`, `check_figures` | Differences are only reported (⚠), never corrected. A bare duration (“57 months / NPP – 42 months”, as in the Manual's case tables) is read as the term; after “resentenced” only the new sentence is read |
| What the chunking model is told | `core/prompts.py` → `CHUNKING`; the answer's shape: `core/schemas.py` → `ChunkResult` | The model used is the sidebar's facts-extraction model (`CHUNK_ROLE`) |

## 4. Rules to keep when changing the code

* **The key:** only `st.session_state.openai_api_key`; never `os.environ`, a cache, a file, a log, or the report.
  The deployed app must never read `.env` (only `scripts/build_index.py` does).
* **Manual only:** the AI is shown Manual items and passages and must cite their ids; ids it was not shown are removed
  and reported (`stages.clean_citations`). Only support quotes are cited; `residual` quotes are context only.
* **Cases are yardsticks, never law:** cases from the case database go only to the current-practice analysis, the
  proposal and the audit, under a heading that says so, and are cited as `case_ids` (C1, C2 …), cleaned like the other
  ids. Never let the gravity, offender or purposes analyses, the framework or the hard checks rely on them. A case's
  text is always copied from the user's document by code, never written by the model, and the model's figures are
  checked against that text but never silently changed (the case table applies only the cells the user changed).
  Within a database, ids are not reused after cases are deleted (`CaseDB.last_number`); after Clear or Replace they
  start again, which is harmless because a finished run keeps a snapshot of its cases (`Run.comparables`).
* **Never resolve the Manual's ambiguities silently:** anything that depends on a `flag.*` item is a caution showing the
  flag, never an error; a point the Manual leaves open is a `Reading`, and a breach of the reading-dependent rules
  must hold on every reading to be an error. A check that does not depend on the category or a minimum must stay
  in `OTHER_CHECKS`, so that a reading never softens its errors.
* **Label app policy:** where the app decides something the Manual does not, set `app_policy=True` on the
  `CheckResult` or write "(app policy)"; it must never be presented as the Manual's law.
* **Five or more AI calls per run:** keep the seven unconditional calls in `pipeline.run_remaining`, and keep
  out-of-scope inputs in `facts.validate()` (before AI call 1). Calls discarded by a second confirmation (or an
  earlier sorting of a description) are kept in `Run.discarded_calls`, not counted; a second confirmation keeps the
  calls made before it (`pipeline.BEFORE_CONFIRMATION`).
* **Nothing guessed from a simple description:** an answer counts only if its words show it (`facts.check_words`);
  an answer against the offender (`facts.ADVERSE_VALUES`) is never applied without the user's tick; an open question
  is "unknown", never "no"; a required question is empty until answered; the user's own answers come first and survive
  a new sorting; an assumed value is marked as assumed in the AI calls and the report.
* **Suggestions:** `pipeline.step_confirm` rebuilds the case from `Run.entered` (the form as entered) plus the
  suggestions ticked now, so unticking one withdraws it.
* **Form state:** widget keys are `"f_" + field` (and `"s_…"` for the simple input's answers); `streamlit_app.py`
  re-assigns them (and the cases left out, `cdb_exclude`) at the top of every run so their values survive a visit to
  another page, and the form's lasting copy is `st.session_state.form_shadow`, from which the case is built. A field
  shown only conditionally needs nothing extra. Values written by code into a widget's key must be written before the
  widget is drawn (a callback, or `st.session_state.pending_form`, which `init_form` applies).

## 5. Testing

```bash
pip install pytest
pytest                    # about 10–20 seconds, no key or network needed
```
`tests/fake_openai.py` replaces only the HTTP transport of the real OpenAI library, so the tests cover the real request
format and Structured Outputs parsing. `tests/test_app.py` clicks through a whole run in Streamlit's test runner
(including a visit to another page, a run with a case database, and the simple input: sorting, answers still
needed, an edited description sorted again). `tests/test_simple_input.py` covers the sorting's checks. `tests/test_cases_db.py` covers the case
database: reading Word and HTML tables, chunking (every case copied word for word, retries, splitting, co-offenders,
label and figure checks), saving and loading, and choosing the comparable cases. `tests/test_readings.py` covers the readings: among other things, that an
unconfirmed scheme gives an error exactly when the sentence errs both with and without the scheme;
`tests/test_readings_cross_product.py` compares the merged results with every combination of the open points, each
built as a single reading.
