"""
core/config.py — THE SETTINGS FILE.

Every setting you are likely to want to change is in this one file:
  0. Identity    — the app's name and version, and the Manual's edition
  1. Scope       — which offences, courts and offenders the app accepts
  2. AI models   — which OpenAI model runs each step, and how hard it "thinks"
  3. Retrieval   — how much of the knowledge base each AI call is shown
  4. Cases       — the case database: comparable cases, chunking, which cases are used
  5. Paths       — where the knowledge base, search index, test cases and case database live

Nothing in this file talks to OpenAI or Streamlit; it is plain Python values.
"""

import json
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# 0. App identity and source
# ─────────────────────────────────────────────────────────────────────────────
APP_TITLE = "Victorian Sentencing Estimator"
APP_VERSION = "2.2"

# The only legal source. Shown on every report (the Manual asks for its edition
# and disclaimer to accompany anything built from it).
MANUAL_NAME = "Victorian Sentencing Manual (4th ed), Judicial College of Victoria"
MANUAL_VERSION_DATE = "21 September 2026"

# ─────────────────────────────────────────────────────────────────────────────
# 1. SCOPE — what the app accepts
#    The six chapter 25 (causing injury) charges version 1 was built for keep their
#    own name: the tests and the protected-official scheme use it. IN_SCOPE_OFFENCES
#    is what the charge box offers: every register offence (docs/ALL_OFFENCES_GUIDE.md).
# ─────────────────────────────────────────────────────────────────────────────
CAUSING_INJURY_OFFENCES = [
    "crimes-act-s-15a-1-intentionally-causing-serious-injury-in-circumstances-of-gross-violence",
    "crimes-act-s-15b-1-recklessly-causing-serious-injury-in-circumstances-of-gross-violence",
    "crimes-act-s-16-intentionally-causing-serious-injury",
    "crimes-act-s-17-recklessly-causing-serious-injury",
    "crimes-act-s-18-intentionally-causing-injury",
    "crimes-act-s-18-recklessly-causing-injury",
]


def _register_offences() -> list[str]:
    """Every offence in the Manual's register (knowledge/offences.json), by chapter. A row the register lists
    twice (also_listed_as: conspiracy to defraud, in chapters 27 and 32) is offered once."""
    path = Path(__file__).resolve().parent.parent / "knowledge" / "offences.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    order = sorted(range(len(rows)), key=lambda i: (int(rows[i]["chapter"]), i))
    seen: set[str] = set()
    out: list[str] = []
    for i in order:
        r = rows[i]
        if r["id"] not in seen:
            out.append(r["id"])
        seen |= {r["id"], *(r.get("also_listed_as") or [])}
    return out


ALL_REGISTER_OFFENCES = _register_offences()
IN_SCOPE_OFFENCES = ALL_REGISTER_OFFENCES      # the charge box offers every register offence

# Offences whose charge is itself a "gross violence" offence (mandatory minimum
# group: VSM 9.1.2.2). Used by core/framework.py.
GROSS_VIOLENCE_OFFENCES = {
    "crimes-act-s-15a-1-intentionally-causing-serious-injury-in-circumstances-of-gross-violence",
    "crimes-act-s-15b-1-recklessly-causing-serious-injury-in-circumstances-of-gross-violence",
}

# Courts: the higher courts only (no Magistrates' Court caps, no Drug Court, no
# Children's Court; guide step 18).
IN_SCOPE_COURTS = ["County Court", "Supreme Court"]

# Adults only: the offender must have been 18 or older at the offence.
MIN_AGE_AT_OFFENCE = 18

# The earliest offence date the form accepts. The Manual's historic sexual offence tables reach back to 1 April 1959
# (VSM 24.1.6.7); earlier sexual offending has no table and its maximum is unresolved.
EARLIEST_OFFENCE_DATE = "1950-01-01"

# Disposition types the app can propose and check. Anything else the facts
# point to (youth justice centre order, CSTO, RTO, DATO, indefinite sentence)
# is reported as outside the app's scope rather than proposed.
SUPPORTED_DISPOSITIONS = [
    "imprisonment",            # imprisonment (with or without a non-parole period)
    "imprisonment_and_cco",    # combination sentence: imprisonment + CCO
    "cco",                     # community correction order alone
    "mtmo",                    # mandatory treatment and monitoring order (a type of CCO)
    "fine",
    "adjourned_undertaking",
    "discharge",
    "dismissal",
    "life_imprisonment",       # only where the maximum is life (with a non-parole period, or reasons for none)
]

# ─────────────────────────────────────────────────────────────────────────────
# 2. AI MODELS
#    Each step of a run has a "role". The sidebar lets the user override these
#    defaults for their session (so a marker whose key lacks one model can pick
#    another without any change to the code).
#    GPT-4o is deliberately not a default: OpenAI notified its removal from the
#    API on 14 January 2027.
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_MODELS = {
    "extraction": "gpt-6-luna",   # step 2: structure the facts (fast, cheap)
    "analysis": "gpt-5.6-sol",    # step 4: the four stage analyses
    "synthesis": "gpt-5.6-sol",   # step 5: the proposed sentence (and any revision)
    "audit": "gpt-5.6-sol",       # step 7: the audit of the reasoning
}

# Shown in the sidebar picker. "Other…" lets the user type any model name.
MODEL_OPTIONS = [
    "gpt-5.6-sol",
    "gpt-6.1-sol",
    "gpt-6-sol",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
]

# Reasoning effort per role. "default" sends no setting (the model's own
# default applies). If a model does not accept the setting, the app retries the
# call without it (core/llm.py).
DEFAULT_EFFORT = {
    "extraction": "low",
    "analysis": "medium",
    "synthesis": "medium",
    "audit": "medium",
}
EFFORT_OPTIONS = ["default", "none", "low", "medium", "high"]

# If the chosen model is not available to the user's key (OpenAI answers "not
# found" or "permission denied"), the app tries these in order and says so in
# the run log. Set to [] to switch the fallback off.
FALLBACK_MODELS = ["gpt-5.6-sol", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-terra", "gpt-5.6-luna"]

# Upper limit on each call's output (reasoning tokens count towards it).
MAX_OUTPUT_TOKENS = {
    "extraction": 12000,
    "analysis": 24000,
    "synthesis": 32000,
    "audit": 24000,
}

# How long one call may take before it is abandoned (seconds), and how many
# times the OpenAI library retries a failed connection.
REQUEST_TIMEOUT_SECONDS = 420
SDK_MAX_RETRIES = 1

# Run the four stage analyses at the same time (faster). Set to False if your
# OpenAI account's rate limit is low and calls fail with "rate limit".
PARALLEL_STAGES = True

# Revision loop: if the hard checks or the audit find errors, the proposal is
# revised this many times at most (each revision = 2 more AI calls: revise +
# re-audit).
MAX_REVISIONS = 1

# The assignment requires at least 5 AI calls per run. The app always makes 7
# (1 extraction + 4 stage analyses + 1 synthesis + 1 audit); a revision adds 2.
MIN_AI_CALLS_PER_RUN = 5

# ─────────────────────────────────────────────────────────────────────────────
# 3. RETRIEVAL — how much of the knowledge base each AI call sees
# ─────────────────────────────────────────────────────────────────────────────
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1024          # shortened vectors: smaller index, same model
EMBEDDING_BATCH = 96                 # texts per embeddings request when building

# "auto" = ChromaDB if it can be imported, otherwise NumPy (the same ranking in
# practice; ChromaDB's search is approximate).
RETRIEVAL_BACKEND = "auto"           # "auto" | "chroma" | "numpy"

ITEMS_PER_STAGE = 30                 # knowledge items given to each stage analysis
QUOTES_FOR_TOP_ITEMS = 12            # of those, how many also get their verbatim quotes
QUOTES_PER_ITEM = 2                  # quotes shown per item
PASSAGES_FOR_COMPARABLES = 6         # most similar Manual passages for the comparables stage
ITEMS_FOR_SYNTHESIS = 60             # knowledge items given to the synthesis call (always-included ones count)

# The Manual's Part E (chapters 21–34) holds offence-specific guidance. Items
# whose citations all sit in Part E chapters other than the charge's own chapter
# are left out (e.g. occupational health and safety guidance never reaches an
# assault case): core/kb.py, KnowledgeBase.candidates.
PART_E_CHAPTERS = [str(n) for n in range(21, 35)]

# Sections of general current-sentencing-practice guidance searched, with the
# offence chapter, for the "most similar passages" in the comparables stage.
COMPARABLES_GENERAL_SECTIONS = ["5.2.9"]

# ─────────────────────────────────────────────────────────────────────────────
# 4. THE CASE DATABASE — comparable cases the user uploads (Case database page)
#    e.g. the Judicial College's Sentencing Manual case summaries. They are used
#    only as comparable cases (yardsticks of current sentencing practice), never
#    as a source of law (core/cases.py).
# ─────────────────────────────────────────────────────────────────────────────
CASES_FOR_COMPARISON = 8             # most similar cases shown to the comparables stage and the synthesis
CASE_TEXT_CHARS = 2500               # characters of each case's text shown to those calls
SAME_CASE_SIMILARITY = 0.95          # a case this similar to the facts is taken to be the case itself (left out)
SAME_CASE_TEXT_OVERLAP = 0.6         # …or if this share of its 3-word sequences is in the narrative (it was pasted in)
# After cases for the same offence (and cases in which it was also charged), cases for an offence in the same section
# of the Manual's register may fill the list (app policy; core/cases.py related_offences). Cases for any other offence
# are not used unless CASES_INCLUDE_OTHER_OFFENCES is True.
CASES_INCLUDE_OTHER_OFFENCES = False
# The result card's comparison with the most similar cases (core/agreement.py): the similarity-weighted share of the
# comparable cases sentenced within the estimate's band is shown only if at least this many cases can be compared
# (app policy; the Manual: a range should not be defined by a few cases, VSM 5.2.9.3.1).
AGREEMENT_MIN_CASES = 3
CASE_EMBED_BATCH = 48                # cases per embeddings request
CHUNK_SEGMENT_CHARS = 12000          # characters of the document each chunking call covers
CHUNK_OVERLAP_CHARS = 6000           # extra characters shown after each part (a case cut at the end is seen whole)
CHUNK_PARALLEL = 4                   # chunking calls made at the same time (lower it if calls fail with "rate limit")
CHUNK_MAX_SPLITS = 3                 # a part whose answer is cut short is halved, at most this many times
CHUNK_ROLE = "extraction"            # the model role used for chunking (the sidebar's facts extraction model)
# Offences a case can be labelled with: every register offence of the Manual's Part E (the Case database page lets
# the user narrow a document to its chapters, so each chunking call lists fewer); anything else is labelled
# "other" with its name as written.
CASE_LABEL_CHAPTERS = PART_E_CHAPTERS

# ─────────────────────────────────────────────────────────────────────────────
# 5. PATHS
# ─────────────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"
INDEX_DIR = ROOT / "index"
TEST_CASES_DIR = ROOT / "test_cases"
CASES_DIR = ROOT / "cases"           # the case database's files: case_database.sqlite3 (SQLite) and chroma/ (ChromaDB),
                                     # written by core/case_store.py; a committed database.json seeds them the first time
