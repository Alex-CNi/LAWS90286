# Covering every offence in the Manual — step by step

This guide takes the Victorian Sentencing Estimator from the six causing-injury charges of version 1.1 to every
offence in the *Victorian Sentencing Manual*'s offence register (chapters 21–34). It has three parts:

| Part | What it does | State |
|---|---|---|
| **A** — Steps 1–7 | Every register offence can be chosen and run, and nothing is guessed: any part of an offence's statutory scheme the hard checks do not cover yet is shown as **“Not fully checked”** | **Done in version 1.2** |
| **B** — Steps 8–19 | Each statutory scheme implemented in code, one family at a time; each step deletes one “Not fully checked” entry | **Steps 8–17 done in version 2.0** (no entry is left); steps 18–19 (optional) not done |
| **C** — Steps 20–23 | Case database for every chapter, test cases, docs, live check and deploy | **Steps 20–22 done in version 2.0**; step 23 (a live check with your own key, then deploy) is yours |

Run the tests after every step: `python -m pytest -q` (version 1.2: 275 passed; version 2.0: see “Version 2.0” below).

## Version 2.0 — what was built, and where it differs from the plan
* **Batches:** steps 9, 10, 15, 17 (minimum facts, category conditions, conditional maxima, contempt); 11, 12, 13
  (standard sentences, life imprisonment, maxima fixed by reference to another offence); 14, 16 (bodies corporate,
  licence orders, the alcohol exclusion order's relevant offence); 20–22 (the case database for every chapter, ten
  test cases, the docs). `core/coverage.NOT_YET_CHECKED` is empty: 335 of 335 offences have no “Not fully checked”
  caution.
* **Step 20:** the full list of offence codes is about 33,600 characters (not the 20,000 estimated above), so the
  Case database page lets the user choose the chapters a document covers; a **Relabel** action labels the offences a
  version 1.x database marked “other” by an exact, unique register name (no AI).
* **Step 21:** the test cases are `tc4`–`tc13`. The attempt case uses theft of a firearm, whose maximum is not given by
  a penalty level: s 321P(1)(b)'s 60% rule applies to it, while an offence whose maximum is a level falls under
  s 321P(1)(a), whose table the Manual does not reproduce (the maximum is then entered by the user).
* **Review (three independent reviewers, after step 22):** 7 major findings — the ss 317AC/317AD special-reason
  route; contempt cautions that reached checks not based on the Sentencing Act; answers to one charge's questions
  surviving a change of charge; a body corporate's person answers reaching the AI calls; the offence attempted not
  enterable on s 321P(1)(a) (so its licence order could not be checked); a company's historic OHS maximum taken from
  the current row — and about 30 minor ones (60% rounded up, the level/term band lost at 60%, the school condition's
  “no” and its fine band, “#” provisions past 30 June 2017, DPCSA s 79/80 and attempt rows taking any offence, the
  15-year cap on the wrong rows, a user-identified company maximum, conviction-only licence rules, labels). All
  fixed, each with a regression test (`tests/test_review_v2.py`; the form's in `tests/test_app.py`). The reviewers'
  check of the fixes found about 15 smaller problems, mostly in the fixes themselves (the licence form check hidden
  by a new branch, DPCSA sub-paragraph sections, an uncapped incitement fine, the 30 June 2017 reading one way only,
  the school band and a person's flags for a company, the sorting's answers to the old charge, a historic maximum
  kept after a change of table — by the form, by a new sorting, or through a period end left in the form): all fixed
  and re-checked. Tests: 488 passed.
* **New inputs from the review:** the historic table's fine (`historic_maximum_fine_units`: the OHSA 1985 table gives
  fines), a higher company maximum the user identifies (`company_maximum_units`, app policy), and the offence
  attempted on every row whose maximum depends on another offence.

---

## Why widening the list is not enough

The knowledge base already covers the whole Manual: 801 items, 888 passages and a register of 337 offence rows. What
was chapter-25-only is the code: the scope list, two retrieval filters, two prompt lines and — most of the work —
the legal framework (`core/framework.py`) and the hard checks (`core/checks.py`), which were written for chapter
25's statutory schemes. Widening the list alone gives answers that look checked but are wrong. Version 1.1's
framework, run on other charges:

| Charge | Version 1.1's framework | The register / knowledge base |
|---|---|---|
| Armed robbery (no firearm, no injury, alone) | Category 2 | Category 2 only if the offender has a firearm, a victim suffers injury as a direct result, or the offence was committed in company |
| Manslaughter (no gross violence, no single punch) | 10-year minimum non-parole period enforced | Only for gross violence or a single punch, with a prosecution notice (`mmnpp.manslaughter_10y`) |
| s 317AC (the official not injured) | 2-year minimum non-parole period | Only if the official was injured (`mmnpp.driving_protected_2y`) |
| Taking away a child / blackmail, offence in 1995 | Maximum 84 / 144 months | 7½ / 12½ years = 90 / 150 months |
| OHS s 21(1) | No maximum, so any fine passes | Fine of 1800 penalty units |
| Using cannabis (5 penalty units) | Imprisonment not stopped | Fine only |
| Murder | No maximum (“unresolved”, no reason given) | Life |
| Attempt (the 60% rule) | No maximum (“unresolved”, no reason given) | 60% of the primary offence's maximum |

The hard checks also applied chapter 25 rules to everything: an alcohol exclusion order demanded for any offence
(the Manual's “relevant offences” are categories), the chapter 25 licence rule applied to culpable driving, the
one-year limit on a combination sentence applied to arson (it does not apply to arson in the higher courts), and
“no fine on a Level 1 offence” applied against the fine the Manual prints for DPCSA ss 71 and 72.

## Three rules for every step

1. **Never present an unchecked scheme as checked.** `core/coverage.py` lists what is not implemented; delete an
   entry only when its step is implemented and tested.
2. **The knowledge base is the specification.** Every rule below names its item id. Open it on the Knowledge base
   page (search the id) or in `knowledge/*.json` and implement its `logic` / `test` text exactly. Where the Manual is
   inconsistent, the flag's “Handling:” sentence says what to do. Anything else the app decides is marked
   `app_policy=True` and never attributed to the Manual.
3. **One family at a time, tests green after each.** An error only where the Manual makes the result unlawful on
   every reading; a point depending on a Manual flag is a caution carrying the flag; an adverse fact not proved is
   not relied on (main reading) but shown as an “unproved” reading.

---

# Part A — every offence selectable, nothing guessed (done in version 1.2)

Each step says what changed and where, so you can review it or re-apply it to your own copy.

### Step 1 — Back up and branch
```bash
git checkout -b all-offences     # your Codespace, before replacing any code
python -m pytest -q              # version 1.1: 239 passed
```
Then replace the code with the version 1.2 package exactly as you did for version 1.1 (delete the old files, unzip,
commit).

### Step 2 — Keep the six version 1 charges under their own name
* `core/config.py`: `CAUSING_INJURY_OFFENCES` holds the six chapter 25 ids. The tests, the protected-official scheme
  and `CASE_RELATED_OFFENCES` use it; `IN_SCOPE_OFFENCES` becomes the list the charge box offers (Step 5).
* `core/facts.py`: `CaseInput.offence_id`'s default comes from `CAUSING_INJURY_OFFENCES`.
* Four test files unpack `S15A, S15B, S16, S17, S18I, S18R = config.CAUSING_INJURY_OFFENCES`.

### Step 3 — The coverage gate (`core/coverage.py`, new)
`unchecked_features(kb, offence_id)` reads the offence's register row and returns the features the hard checks do
not implement yet. `framework.gates()` turns them into one caution, **“Not fully checked”** (app policy). In
version 1.2, 260 of the 335 offences offered have no unchecked feature; the rest:

| Feature (`NOT_YET_CHECKED` key) | Offences | Removed by |
|---|---|---|
| `licence_order` — a mandatory licence order (VSM 16) | 23 (driving offences incl. RSA s 61(3); murder, manslaughter, kidnapping, carjacking … when arising from driving) | Step 16 |
| `relative_maximum` — a maximum fixed by reference to another offence | 16 (chapter 32 attempts, conspiracy, incitement; s 247B) | Step 13 |
| `contempt` — the Sentencing Act applies only in part (VSM 29.5.2) | 16 (the 29.1.3 rows) | Step 17 |
| `standard_sentence` — the scheme in VSM 9.2 | 13 (murder, homicide by firearm, culpable driving, rape, eight child sexual offences, trafficking a large commercial quantity) | Step 11 |
| `life` — life imprisonment | 8 | Step 12 |
| `conditional_maximum` | 5 (ss 71AA, 71AC, 71B, 195H, 195I) | Step 15 |
| `minimum_other_group` — the facts a minimum turns on are not asked | 3 (manslaughter, ss 317AC, 317AD) | Step 9 |
| `category_condition` — a category clause the code cannot test | 1 (armed robbery) | Step 10 |

Two gaps depend on the case rather than the row, so they have their own cautions: **“Offender-status schemes not
checked”** (serious offender and continuing criminal enterprise status, shown when prior convictions are not “none”
for chapters 21, 22 and 24–28; Step 19) and **“Historic offending — confirm the maximum”** (Step 4).

### Step 4 — The framework refuses what it cannot read
`core/framework.py`:
* `find_maximum`:
  * fractional years count (`round(years × 12)`: 7½ years is 90 months, never 84); `imprisonment_months`;
  * **life** (`Maximum.life`); **at large** (no ceiling; caution, `scale.at_large`); **fine-only** (`Maximum.fine_only`:
    no imprisonment, and a CCO only above five penalty units, `order.cco`);
  * a **printed fine** is used before the general 10-penalty-units-per-month rule (`fine_source`);
  * a **current-only** row (no start date) adds a caution (`audit.maximum_applicable` (5));
  * **conditional maxima** are noted, never applied (Step 15); a maximum **relative to another offence** is
    unresolved (Step 13);
  * **historic offending under a text-only table** — sexual offences 1 April 1959 – 30 June 2017 (VSM 24.1.6.1–7) and
    OHS Act 1985 offences before 1 July 2005 (31.1.2): the maximum is unresolved, with an error gate, until the user
    enters it from the table, which Step 1 shows (`kb.historic_tables`, `CaseInput.historic_maximum_months`;
    `audit.offence_date_and_historic_offending`, `flag.historic_tables_unstructured`). Sexual offending before 1 April
    1959 is unresolved. In the window 20 March – 30 June 2017 the category is an open reading until confirmed
    (`cat1.historic_sexual_provisions`). The form now accepts offence dates from 1950;
  * **level and printed term disagree** (e.g. Level 6 printed “6 years”): error above the higher figure, unresolved
    between the two (`Maximum.ceiling_months`; `audit.maximum_applicable` (8), `audit.level_term_mismatch`); for a
    period before 1 September 1997 the printed term is used and flagged (`flag.historic_level_scale`).
  * `Maximum.describe()` is what the screen, the report and the AI calls show.
* `find_category` — a clause it cannot test is never assumed: the main reading does not rely on it, an “unproved”
  reading shows the category “if … were proved” (also at the end of a period straddling 20 March 2017), and a gate
  says so (`category_unparsed`). Open readings carry the Manual's own category points for suicide pact manslaughter
  (`flag.suicide_pact_category2`) and ss 317AC/317AD (`flag.driving_protected_category1`) (`coverage.OPEN_CATEGORY`).
* `find_minimum` — the chapter 25 groups as before; **ss 77B and 79A: the 3-year minimum applies**
  (`mmnpp.aggravated_home_invasion_carjacking_3y`); **manslaughter** (from 1 November 2014) **and ss 317AC/317AD**
  turn on facts the app does not ask yet (`coverage.MINIMUM_FACTS`), so their minimum is never applied on the facts as
  entered but shown as an “if proved” reading, with a gate (Step 9 adds the questions). The framework text, the
  dispositions note, the check row and the report say “none on the facts as entered” and point to that reading.
* `protected_inputs()` — the protected-official scheme (VSM 9.1.2.3) applies only to ss 15A–18; for other charges
  its answers are ignored, not sent to the AI calls (`case_details_text`), and its gates are not shown.
* Flags confined by their Handling to particular charges (home invasion, drug and contempt flags) are listed only
  for those charges (`FLAG_WORDS_FOR`).

`core/checks.py`:
* `check_maximum` handles life, at large and fine-only maxima and the level/term band; `check_fine` names a printed
  maximum, and a fine alone on a life or 25-year maximum printed without a level is a caution
  (`flag.fine_unlabelled_life_25_maximum`).
* `check_mandatory_minimum` cites each group's own item; `Ctx.is_protected_injury` also covers ss 317AC/317AD (the
  same special-reason dispositions, VSM 9.1.2.4).
* `check_ancillary` — an alcohol exclusion order is required (error) for the charges VSM 20.1.1 names (murder,
  manslaughter, the causing-injury offences including s 24); for any other charge it is a caution asking whether the
  charge is a “relevant offence”, whether or not an order is proposed (`AEO_PLAINLY_RELEVANT`); the chapter 25
  licence rule runs only for ss 15A–18.
* `check_cco` — over the one-year combination limit on an arson or bushfire charge: a caution
  (`cco.combination_one_year`, `flag.cco_arson_combination`).
* `check_fine` — a Level 1 row that prints a fine (DPCSA ss 71, 72): a caution (`flag.level1_fine_dpcsa`).
* `Ctx.is_category1_with_minimum` — route (d) of `audit.category_1_2` (a Category 1 offence carrying a minimum, a
  special reason, another disposition: caution, `flag.category1_special_reason`) now reaches ss 77B and 79A.
* `run_hard_checks` — for a contempt charge (29.1.3 rows) every error from a Sentencing Act-based check becomes a
  caution (`audit.scope`; `flag.contempt_sentencing_act_and_orders`); a term or fine above the contempt's own printed
  maximum stays an error.

`core/cases.py` — the “related injury offences” tier of comparable cases is used only for a causing-injury charge.

### Step 5 — Offer every register offence
* `config.IN_SCOPE_OFFENCES = ALL_REGISTER_OFFENCES` — 335 offences by chapter (conspiracy to defraud and conspiracy
  to cheat and defraud, each listed in both chapters 27 and 32, appear once).
* The charge box labels put the name first, with the chapter (`kb.charge_label`: “27 · Armed robbery — Crimes Act
  1958 s 75A”). Streamlit's select box filters as you type.
* The protected-official questions appear only for the causing-injury charges (`detailed_input`, `injury = …`).
* `validate()` refuses the three “– body corporate” rows until Step 14 (the app sentences natural persons), and no
  longer refuses a date that a row's undated entry (“prior to 1 September 1997 …”) or a historic table covers.
* Step 1 (detailed input) shows the historic table and takes the maximum (`historic_offending()`).
* Knowledge base page: a chapter picker for the register table and the “linked items” filter.

### Step 6 — The simple input's sorting call uses short codes
The call is shown `27-06: Crimes Act 1958 s 75A — Armed robbery`, grouped under chapter headings
(`kb.offence_codes()`), and answers with the code; `sort_description` maps it back and treats anything that is not a
listed code as “unclear”. `SortedCase.offence_id` is free text. Why: 335 ids are 24,287 characters, which in a strict
enum would be costly on every call and risks the API's limits on enum size.

### Step 7 — Retrieval, flags and prompts follow the charge's chapter
* `kb.candidates()` leaves out items that cite only *other* Part E chapters than the charge's own
  (`_other_offence_chapter_only(meta, chapters)`); `kb.comparable_passages(chapters)`; `framework.relevant_flag`.
  An offence listed in two chapters (conspiracy to defraud: 27 and 32) uses both, and the flags linked to either row
  (`kb.offence_aliases`, `kb.offence_chapters`). `config.OFFENCE_CHAPTERS` is gone. (Setting it to chapters 21–34
  would have sent OHS guidance to an assault case.)
* `OUT_OF_SCOPE_FLAG_WORDS` no longer drops the drug, home invasion and contempt flags; `FLAG_WORDS_FOR` keeps them
  to their own charges.
* The synthesis call is always shown the framework's own rule items for the charge (e.g. its minimum's item).
* The gravity and purposes stage instructions (`core/prompts.py`) refer to “the Manual's chapter on this offence”.
* Tests: `tests/test_all_offences.py` — every offence at four dates; fractions; the armed robbery reading (and at the
  end of a straddling period); the minimums outside chapter 25; coverage; fine-only and relative maxima; body
  corporate rows; historic tables; the level/term band; pre-1997 manslaughter; retrieval by chapter and aliases;
  whole runs and reports for murder, trafficking, armed robbery, cannabis use and contempt; the ancillary, arson,
  route (d), contempt, open-category, unlabelled-fine, flag, offender-status and comparables rules. App tests run a
  murder charge and a 2010 sexual offence (with the historic maximum) through the form.
* Version 1.2 was reviewed independently (review R6): 3 major and 8 minor findings, all fixed above, with these
  regression tests.

---

# Part B — the statutory schemes, one family at a time

### Step 8 — The recipe (read once; every later step follows it)
1. **Read the items the step names.** Their `logic` / `test` text is the rule to code.
2. **Ask the facts the rule needs.** Add a field to `CaseInput` (`core/facts.py`), default `"unknown"` for anything
   that counts against the offender. Then:
   * detailed form — `app_pages/estimate.py → detailed_input()`: widget key `"f_" + field`, shown only for the charges
     it concerns (copy the `injury = …` pattern);
   * simple input — `schemas.SortedCase`, `prompts.SORTING`, `facts.sorted_input`, `SIMPLE_DEFAULTS`, and
     `ADVERSE_VALUES` if it counts against the offender (adverse answers stay opt-in, with the sentence shown);
   * facts extraction — `schemas.FactsExtraction` and its prompt, if the AI should look for it in the narrative;
   * `facts.case_details_text` and `report.LABELS`, so the AI calls and the report show it.
3. **Framework** (`core/framework.py`): turn the answer into the maximum, category or minimum. An adverse fact
   answered “unknown” follows the existing pattern in `build_readings`: main reading not proved (app policy,
   `findings.burden`), plus an `"unproved"` reading; a point the Manual leaves open is an `"open"` reading.
4. **Hard check** (`core/checks.py`): `check_x(ctx) -> list[CheckResult]`; add it to `READING_CHECKS` if it depends on
   the category or the minimum (it then runs under every reading), otherwise to `OTHER_CHECKS`.
5. **Tell the AI**: a bullet in `prompts.SYNTHESIS` (“The app checks the following in code …”); add the rule's id to
   `synthesis.SYNTHESIS_ALWAYS` if every proposal for the family must see it.
6. **Tests**: one per limb, with `setup(kb, offence_id, **answers)` and `proposal(...)` from `tests/test_checks.py`.
7. **Delete the feature** from `coverage.NOT_YET_CHECKED` (and its detector, or narrow it). Run the whole suite.

### Step 9 — Mandatory minimums outside chapter 25: ask the facts (VSM 9.1.2)
Items: `mmnpp.overview`, `mmnpp.manslaughter_10y`, `mmnpp.gross_violence_definition`, `mmnpp.driving_protected_2y`,
`mmnpp.aggravated_home_invasion_carjacking_3y`, `mm.special_reason`, `audit.mandatory_minimum`; flags
`flag.mtmo_combination_sentence`, `flag.ahi_minimum_provision_10ac_10ad`; `intake.prosecution_notices`.

Version 1.2 already applies the ss 77B/79A minimum and shows the other two groups as “if proved” readings. What is
left is asking the facts, so the minimum applies on the main reading when they are proved:

| Group | Charges | Applies when | New questions | Exclusions |
|---|---|---|---|---|
| `manslaughter` | Manslaughter | offence on or after 1 Nov 2014; the prosecution gave notice; gross violence or a single punch or strike proved beyond reasonable doubt | `manslaughter_circumstance` (gross_violence / single_punch / neither / unknown); `prosecution_notice` (yes / no / unknown) | under 18 at the offence; special reason; single punch only: an accessory (directing, assisting or encouraging) |
| `driving_protected` | ss 317AC, 317AD | the official was injured in the commission of the offence | `official_injured` (yes / no / unknown) | under 18; special reason → imprisonment without a CCO, or an MTMO, RTO or CSTO on the 9.1.2.3 conditions (already routed: `Ctx.is_protected_injury`) |
| `aggravated_hi_cj` | ss 77B, 79A | always, for an adult | — (done) | under 18; special reason (route (d) done) |

* `framework.find_minimum`: for `MINIMUM_FACTS` charges, “yes” to every fact → the minimum on the main reading;
  any “unknown” → keep the “if proved” reading; any “no” → none. Then delete the charge from
  `coverage.MINIMUM_FACTS` (keep the facts text for the gate) and the `minimum_other_group` feature.
* `accessory_exclusion()` — add the single-punch rule (gross-violence manslaughter has none in the item); the
  accessory gate already names it.
* Tests: manslaughter 2015, single punch, notice given, NPP 9 years → error; no notice → no minimum; offence in 2013 →
  none; circumstance unknown → “if proved” caution; s 317AC injured / not injured; the special-reason routes.

### Step 10 — Category conditions the code cannot test yet (`audit.category_1_2`)
* **Armed robbery (s 75A)**: Category 2 only if “the offender has a firearm, or a victim suffers injury as a direct
  result, or the offence was committed in company”. Add `category_condition_met` (yes / no / unknown), asked only when
  `fw.category_unparsed` is set (show the clause as the question). In `find_category`, “yes” satisfies the clause, “no”
  fails it, “unknown” keeps the Part A unproved reading.
* **ss 317AC, 317AD**: footnote 2005 calls them Category 1, the table gives no category
  (`flag.driving_protected_category1`). Version 1.2 adds the `"open"` Category 1 reading the handling asks for (a
  non-custodial disposition is unresolved, not an error); add a question so that, when the user confirms Category 1,
  it becomes the main reading. Suicide pact manslaughter's open Category 2 reading (`flag.suicide_pact_category2`)
  needs nothing more.
* **Home invasion (s 77A)**: the register's Category 2 governs (`flag.home_invasion_mandatory_scheme_fn4044`) — no
  change, add a test.
* **Historic child sexual offence provisions marked “#”** (`cat1.historic_sexual_provisions`) have no register rows
  (the 24.1.6 tables are held as text). Version 1.2 takes the maximum from the user and keeps the category open in the
  window 20 March – 30 June 2017; add a question confirming the provision (“#” row or not) to settle the category.

### Step 11 — The standard sentence scheme (VSM 9.2)
Items: `sso.scope`, `sso.meaning_and_use`, `sso.reasons`, `sso.list`, `npp.sso_minimum`, `audit.standard_sentence_use`
(audit standard); flags `flag.sso_effective_date`, `flag.sso_npp_20_year_overlap`, `flag.sso_npp_short_terms`,
`flag.sso_rape_by_compelling`, `flag.sso_table_lineal_descendant_typo`. Register fields: `standard_sentence_years`,
`standard_sentence_variants` (murder: 30 years if the victim was a custodial officer or emergency worker on duty),
`standard_sentence_exclusions` (s 71: not attempted trafficking).
* **Framework**: `Framework.sso` — applies if the row has a standard sentence, the offence was committed after 1
  February 2018, the offender was 18 or older at the offence, the matter is not heard summarily, and the charge is not
  a conspiracy, incitement or attempt. An offence on 1 February 2018 itself, or a period spanning it, is unresolved
  (`flag.sso_effective_date`). Murder's variant needs a question (`victim_custodial_or_emergency_on_duty`). Add the
  standard sentence to `Framework.text()` so every AI call sees it.
* **Proposal schema**: `standard_sentence_statement: str` (how the sentence relates to the standard sentence, naming
  the factors relied on — `sso.reasons`) and `npp_interests_of_justice_reason: Optional[str]`.
* **Hard checks**: `check_sso_npp` from `npp.sso_minimum` — life: NPP ≥ 30 years; term over 20 years: ≥ 70%; under 20
  years: ≥ 60%; exactly 20 years: the 60% floor, and a caution below 70%; a term under 15 months: unresolved, citing
  the three restrictions; below the floor without an interests-of-justice reason: error. `check_sso_reasons` —
  presence only (adequacy is the audit's: `audit.standard_sentence_use`).
* **Prompts**: a SYNTHESIS bullet (a guidepost, not a starting point; no two-stage reasoning; state the
  relationship); `SYNTHESIS_ALWAYS` += `sso.meaning_and_use`, `sso.reasons`, `npp.sso_minimum` when it applies.
* **Tests**: rape in 2019 (floor 60% of the term); rape in 2017 (no scheme); offence on 1 February 2018 (unresolved);
  attempted trafficking (excluded); murder of an emergency worker on duty (30 years); rape by compelling sexual
  penetration (outside the scheme, with the caution).

### Step 12 — Life imprisonment
Charges: murder, s 3A, incitement to murder, conspiracy or incitement to murder or treason, trafficking or
cultivating a large commercial quantity, and s 71AA at the direction of a criminal organisation (Step 15). Items:
`guide.murder.life_without_parole`, `npp.sso_minimum`, `cum.life_parole`, `scale.fine_alternative` (no general fine
for life).
* `schemas.DispositionType` += `"life_imprisonment"` (also `config.SUPPORTED_DISPOSITIONS`, `report` labels and
  `describe_sentence`, the screen); for life, `term_months` is null; the band shows life in `disposition_types`.
* Checks: `check_complete` (no term needed), `check_maximum` (life only where `maximum.life`), `check_non_parole`
  (life: an NPP, or a stated reason why none is fixed; under the standard sentence scheme ≥ 30 years), `check_cco`
  (no CCO with life), `check_fine` (no general fine with life; the printed DPCSA fine stays a caution), `check_band`.
* Prompt: life only where the maximum is life; state the NPP or why none is fixed.

### Step 13 — Maxima fixed by reference to another offence (chapter 32; s 247B)
Items: `audit.maximum_applicable` limb (4), `guide.inchoate.maxima_notes`; flags `flag.attempt_table_not_reproduced`,
`flag.inchoate_life_maximum_footnotes`. The register's `maxima[].relation`: `equal_to_primary`, `fraction_of_primary`
(`factor` 0.6), `greater_of_primary_and_stated` (`stated_years` 5), `equal_to_intended_offence` (s 247B),
`table_not_reproduced`, `lower_penalty_in_other_enactment`, `law_of_place_capped_at_life`.
* `CaseInput.primary_offence_id` — the offence attempted, conspired, incited or intended; asked (with the same charge
  box) when the row has `relative_to_primary`.
* `find_maximum`: compute from the primary offence's maximum at the offence date — equal (copy), 60% (of the term),
  the greater of the primary and 5 years; the three relations the Manual cannot resolve → ask the user for the
  maximum and mark it user-supplied (app policy). A Crimes Act conspiracy to commit an offence punishable by life
  (not murder or treason, not DPCSA s 79): 15 years, error above; incitement likewise, but above 15 years a caution.
* An attempt, conspiracy or incitement is not a standard sentence offence (`sso.scope`); the inchoate rows carry no
  category or minimum of their own.

### Step 14 — Bodies corporate and the remaining fine rules
Items: `intake.offender_type`, `audit.fine_rules`, `scale.body_corporate_fine`, `order.fine`,
`order.fine.directors_liability`, `guide.ohs.formulation_and_sanctions`; flags `flag.body_corporate_fine_multiplier`,
`flag.fine_unlabelled_life_25_maximum`.
* `CaseInput.offender_type` (natural_person / body_corporate). For a body corporate: ages not required
  (`REQUIRED_FIELDS`, `validate()`); no imprisonment or CCO (error); a fine may be an alternative to imprisonment for a
  Level 2 offence (`intake.offender_type`); `validate()` stops refusing the “– body corporate” rows and refuses a
  mismatch between the offender type and such a row.
* Fine maximum: the printed company maximum (`pu_company`, e.g. OHS s 21(1): 9000 penalty units), error above; none
  printed: five times the natural-person maximum, error above; outside the Crimes Act, between one and five times →
  caution (`flag.body_corporate_fine_multiplier`).
* Workplace manslaughter (25 years printed without a level): a fine alone on a natural person is already a caution
  (`flag.fine_unlabelled_life_25_maximum`); its body corporate fine is printed only in the row's `extra` text, so add
  it as the company maximum.

### Step 15 — Conditional maxima
Item: `audit.maximum_applicable` limb (3). Register `conditional_maxima`: s 71AA (at the direction of a criminal
organisation: life and 5000 penalty units), ss 71AC and 71B (within 500 metres of a school: 20 years — apply only when
the user confirms the statutory condition, `flag.school_500m_condition`), s 195H affray and s 195I violent disorder
(wearing a face covering: 7 and 15 years).
* One question per condition (yes / no / unknown), plus the school row's confirmation.
* Framework: “yes” → the conditional maximum; “unknown” or “no” → the base maximum, with the note “if … were proved,
  the maximum would be …” (the existing `Maximum.notes`). `check_maximum` stays on the main reading: above the base
  maximum is an error unless the condition is proved.

### Step 16 — Mandatory licence orders and the alcohol exclusion order's “relevant offences”
Items: `order.licence_disqualification` → `licence.serious_mv_offences` (its `map` lists offence ids by group;
`params`: dangerous driving causing death or serious injury 18 months, substance-influenced 12, every other serious
motor vehicle offence 2 years), `licence.other_mandatory` (s 319AA ≥ 12 months on conviction; theft or attempted theft
of a motor vehicle: an order, default 3 months), `licence.form_by_licence_status`, `audit.mandatory_ancillary_orders`
(RSA s 61(3): 4 years if a conviction is recorded, else 2; a subsequent offence 8 or 4; previous offence unknown → the
first-offence minimum and a caution), `flag.licence_guilty_or_convicted`; `anc.alcohol_exclusion_order`.
* Make the licence part of `check_ancillary` data-driven: find the charge's group in the map, test its condition
  (arising out of driving; under the influence of alcohol, or alcohol and a drug — not a drug alone), the minimum from
  `params`, the form by the licence held, and the conviction rules. Remove the `CAUSING_INJURY_OFFENCES` guard.
* New questions: `previous_s61_3_offence`; `motor_vehicle_stolen` (theft and attempted theft).
* Alcohol exclusion order: version 1.2 treats the charges VSM 20.1.1 names (murder, manslaughter, causing injury) as
  relevant and asks for confirmation (a caution) for every other charge. Add a question (`aeo_relevant_offence`:
  yes / no / unknown) so a confirmed relevant offence gets the error-level check.

### Step 17 — Contempt (the 29.1.3 rows)
Part A already turns Sentencing Act-based errors into cautions (the contempt's own printed maximum still binds).
Still to do:
* The Magistrates' Court and tribunal contempt rows are punished by those bodies: refuse them in `validate()` until
  Step 18, or ask which body is punishing.
* The dispositions the Manual mentions for contempt — a suspended committal, coercive committal of indeterminate
  length (`flag.contempt_sentencing_act_and_orders`) — cannot be proposed: add them, or keep the limitation stated.
* Gravity versus culpability for contempt by disobedience (`flag.contempt_gravity_culpability_29_2_2_2`): the
  stages see the flag through retrieval; check a test run cites it.

### Step 18 (optional) — The Magistrates' Court
Many register offences (fine-only offences, most OHS duties) are usually sentenced there. Items:
`audit.magistrates_court_limits`, `mc.summary_offence_two_years`, `mc.indictable_triable_summarily`,
`mc.two_year_max_summary`, `mc.fine_limits`, `mc.limit_is_jurisdictional_only`, `cco.maximum_term`; flags
`flag.summary_two_year_regardless`, `flag.mc_aggregate_over_two_years`; also `sso.scope` (no standard sentence scheme
when heard summarily) and `cco.combination_one_year` (arson in the Magistrates' Court).
* `config.IN_SCOPE_COURTS` += “Magistrates' Court”; drop `summary_` and `mc_` from `OUT_OF_SCOPE_FLAG_WORDS`.
* Ask whether the charge is a summary offence or an indictable offence heard summarily (the register does not say).
* `check_magistrates_court`: each term ≤ 24 months (a summary offence: ≤ the lesser of its maximum and 24 months); a
  natural person's fine for an indictable offence ≤ 500 penalty units; a body corporate's ≤ 2500; a CCO ≤ 24 months
  for one offence (`check_cco`'s five-year limit is for the higher courts).

### Step 19 (optional) — Offender-status schemes
Items: `serious_offender.regime`, `principle.totality_serious_offenders`, `cce.regime`; flags
`flag.cce_value_threshold`, `flag.cce_drug_offences_value`; `intake.criminal_history`.
* Version 1.2 shows “Offender-status schemes not checked” when prior convictions are not “none”. Add questions:
  serious offender status for the sexual, violent, drug and arson offences the regime lists (the offence chapters
  list the qualifying offences); CCE status and the value involved.
* Framework: a CCE offender sentenced for a CCE offence — the maximum becomes the lesser of twice the maximum term and
  25 years; a serious offender — community protection becomes the principal purpose (a stage and synthesis matter,
  plus the totality rules when several charges are added).

---

# Part C — finish

### Step 20 — The case database for every chapter
* `config.CASE_LABEL_CHAPTERS = PART_E_CHAPTERS`.
* `cases.segment_input` and the label check in the chunking step: every chunking call lists the labels, and all 335
  as `id: name` lines are about 43,000 characters (chapter 25's are 900). Send short codes (`kb.offence_codes()`; with
  their names, about 33,600 characters in all) and map them back as the sorting call does (Step 6), and let the user
  pick the chapters a document covers on the Case database page so each call lists only those.
* `CASE_RELATED_OFFENCES` → offences in the same register section (`row["section"]`, e.g. 27.1.1.2: robbery,
  burglary, home invasion …), as app policy stated in `cases.POLICY` (version 1.2 uses the related tier only for a
  causing-injury charge, and says so in the note).
* Databases built with version 1.1 labelled every non-chapter-25 case “other”: re-chunk them, or add a relabel
  action.

### Step 21 — Test cases and tests
One README test case per family (form and narrative in `test_cases/`, each with its “shows” line), run end to end in
`tests/test_pipeline.py`: manslaughter by a single punch (minimum and notice), rape after 2018 (standard sentence),
murder of an emergency worker (life and the 30-year variant), culpable driving (licence order), armed robbery (the
category condition), aggravated home invasion (3-year minimum), trafficking near a school (conditional maximum and its
flag), an OHS company (fine), an attempt (60% rule), contempt (cautions only). Keep the three version 1 cases.

### Step 22 — Docs
README (§1 the legal task, §2 test cases, §7 limitations), CODE_GUIDE (files; common changes), the About page (scope;
the number of hard checks), `APP_VERSION = "2.0"`, and the project's design document.

### Step 23 — Live check, review, deploy
* With your own key, run one real case per family: the fake OpenAI service in the tests cannot catch a schema the API
  rejects or a model that misreads a code.
* Have the diff reviewed by someone (or an AI) that has not seen it, against the items each step names.
* `git push`; Streamlit Community Cloud redeploys.

---

## What stays outside, by design
* **Commonwealth offences** (most of chapter 34's terrorism material; `scope.federal_offences`): the app is Victorian
  only.
* **Children** (VSM 10): `MIN_AGE_AT_OFFENCE = 18`.
* **Several charges** — totality, cumulation, aggregate sentences: a separate extension (one charge per run).
* **Historic sexual offence provisions and the repealed OHS 1985 offences** have no register rows: choose the current
  offence, and the app takes the maximum you read from the Manual's table (Step 4).
* Youth justice centre orders, CSTOs, RTOs, DATOs and indefinite sentences.
