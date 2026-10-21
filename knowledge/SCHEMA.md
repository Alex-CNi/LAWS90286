# Schema reference

## Common item fields (method, principles, rules, orders, factors, offence_guides, ancillary, audit, flags, intake)

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | unique, dotted (`factor.verdins_evidence`) |
| `kind` | yes | `method`, `principle`, `purpose`, `rule`, `order`, `order_rule`, `factor`, `csp_uplift`, `offence_guide`, `ancillary`, `audit_check`, `manual_flag`, `intake_field`, `source`, `procedure`, `info` |
| `check` | yes | `hard`: code enforces it on the structured proposal; `gate`: decides availability or scope from the intake facts; `critic`: a standard the reasoning model must apply and the auditor checks; `info`: background |
| `stage` | yes | one or more of `facts`, `framework`, `gravity`, `offender`, `policy`, `purposes`, `disposition`, `comparables`, `synthesis`, `ancillary`, `procedure` |
| `title`, `statement` | yes | heading and short paraphrase (the quotes govern) |
| `refs` | yes | verbatim support (below) |
| `direction` | factors | `aggravating`, `mitigating`, `either`, `neutral`, `not_mitigating`, `not_aggravating` |
| `affects` | factors | what the factor bears on (e.g. `head_sentence`, `general_deterrence`, `burden_of_imprisonment`) |
| `proof` | some factors | `BRD` (beyond reasonable doubt), `balance`, `evidence` |
| `params` | optional | numbers used by code; each value is printed in the item's own quotes |
| `use`, `logic`, `note` | optional | how the app should use the item; pseudo-code for hard checks; curator's note |
| `jurisdiction` | optional | `cth` or `mixed` (absent = Victorian) |
| `offence_ids` / `offence_sections` / `offence_ids_all` | guides, flags | links to `offences.json` (`offence_ids_all` = explicit ids + all offences in the listed table sections) |
| `offence_terms`, `link_basis`, `chapter` | guides | matching hints and the chapter |
| `link_conditions[]` | guides | conditions on some of the item's offence links: each `{offence_ids, applies_if, otherwise, refs}`. The item applies to those offences only when `applies_if` holds on the intake facts (e.g. a s 24 charge `arising_from_driving`); otherwise follow `otherwise` (usually: do not apply, or apply with a caution). Offences not named in any condition are linked unconditionally |
| `related` | any | ids of the knowledge-base items the item relies on or should be read with (validated by the build) |
| `order` | order_rule | id of the order the rule belongs to |
| `severity` | audit, flags | `error` (mandatory rule or error of law: correct it or mark it unresolved) or `caution` (show it to the user) |
| `test`, `message` | audit | operational test applied to the output; user-facing caution (placeholders in `{}`) |
| `field`, `type`, `needed_for` | intake | machine field name, expected type, items that need the fact (validated by the build) |
| nested lists | optional | `rules`, `cautions`, `examples`, `factors`, `limbs`, `indicators`, `counter_indicators`, `principles`, `exceptions`, `not_late`, `indicia`, `forms`, `mitigating`, `aggravating`, `serious_offences`: each entry `{text or id/statement, refs}` with its own verified refs (and optional `check`, `params`, `offence_ids`, `jurisdiction`). A nested entry's `offence_ids` confines that entry (and its `params`) to those offences — e.g. a maximum history or a standard sentence printed for only one of the item's offences |
| `source_file` | added | curation file it came from |

### Severity and wording conventions
* An audit item's `severity` is that of its most serious limb. Each limb of a `test` ends with its outcome (`→ error`,
  `→ caution`, `→ unresolved`). A limb that depends on a `manual_flag` (a point the Manual leaves open or states
  inconsistently) is never an error: it is a caution that cites the flag.
* Text marked “(app policy)” is a handling choice made by the app where the Manual is silent or ambiguous. It must never be
  attributed to the Manual in reasons or citations.
* Statements paraphrase; hedges in the Manual (“may”, “generally”, “it appears”, “likely”, “arguably”) are kept. Where a
  statement and its quotes differ, the quotes govern.
* `params` are descriptive unless a hard item's `logic` uses them. Practice figures (ranges, percentages, “not uncommon”
  cumulation) are never defaults, tariffs or range checks (see each item's `note`).

### A reference (`refs[]`)
```json
{"s": "6.2.2.11", "q": "<verbatim quote>",
 "path": "Part B – … > 6 – Circumstances of the offender > … > 6.2.2.11 – Evidence",
 "pages": [160, 162], "fns": [1234], "authorities": ["C0456"],
 "via": "factor.verdins_evidence"}
```
* `s` (section) or `fn` (footnote; output adds `section`, `page`) plus `q` (the quote) are curated; the rest is added by
  the build. `fns` = footnote markers inside the quoted words, or the first marker after them before the sentence or
  bullet ends. `authorities` = ids in `authorities.json` cited by those footnotes. `via` = the item a re-used quote came
  from.
* Citation rendering: `VSM {s} (p {pages[0]}{–pages[1]})` + for each `fns`: `fn {n}: {footnote text or authority names}`.
* `residual: true` (curated, optional) marks a sentence appended mechanically so that every sentence of the Manual is
  quoted by some item: a verbatim sentence from a section the item already quotes, attached to the item that quotes most
  from that section (appended after the curated refs). It gives retrieval and citation context; it is not support for the
  item's statement, and reasoning should not cite it as authority for that statement. Commonwealth sentences are never
  appended to a Victorian item this way (they go to a `jurisdiction: cth` item or entry, or are left to retrieval).

## L0 files
* `vsm_sections.jsonl` — `id`, `title`, `level`, `parent`, `children`, `chapter`, `chapter_title`, `part`, `pages` [first,last],
  `source_lines`, `text` (verbatim, `[^n]` markers), `footnotes`, `title_footnotes`, `path` [headings], `kind`, `stages`,
  `jurisdiction` (`Vic`/`Cth`/`mixed`), `mentions_cth`, `xrefs`, `authorities`, `has_table`.
* `vsm_footnotes.jsonl` — `n`, `page`, `section`, `text`, `marker_context`, `marker_mode`.
* `vsm_chunks.jsonl` — `id` (`<section>#<k>`), `section`, `title`, `path`, `pages`, `chapter`, `part`, `kind`, `stages`,
  `jurisdiction`, `has_table`, `text_marked` (with markers), `text` (without), `fns`, `authorities`, `embed_text`.
* `kb_items.jsonl` — `id`, `file`, `kind`, `check`, `severity`, `stage`, `direction`, `jurisdiction`, `title`, `statement`,
  `sections` (cited), `offence_ids`, `embed_text`.

## L1 files
* `authorities.json` — `id` (`C…` case, `L…` legislation, `O…` other), `type`, `name`, `name_variants`, `citations`, `short_forms`,
  `first_footnote`, `footnotes`, `sections`, `provisions`.
* `footnote_authorities.json` — `{ "<n>": {"authorities": [...], "provisions": {"<Act id>": ["s 5(2)", …]}} }`.

## L2 files
### `offences.json` (offence register, one row per offence and provision)
| Field | Meaning |
|---|---|
| `id`, `offence`, `act`, `provision`, `provision_norm`, `section_ref` | identity (`provision_norm` = provision without the Act prefix, e.g. `s 71AA`; common-law rows keep their text) |
| `chapter`, `section` | Manual chapter and the penalty-table section the row was transcribed from |
| `maxima[]` | dated maximum penalties (below) |
| `conditional_maxima[]` | a higher maximum that applies only if a statutory condition is established: `condition` (normalised), `condition_text` (printed), `text`, `level`/`life`/`imprisonment_years`/`fine_penalty_units`, `from`, `to` |
| `standard_sentence_years`, `standard_sentence_variants[]`, `standard_sentence_exclusions[]` | standard sentence (and variants such as the murder of emergency workers; exclusions such as attempted trafficking) |
| `minimum_non_parole[]` | `value`, `unit`, `condition_text`, `from_date` (if printed), `text`, `fn`, optional `variant` |
| `category[]` | `category` (1/2), `condition`, `symbol`, `fn` or `s`, `quote`, `from` (commencement), `from_basis` / `from_quote` (the verified words giving the date) |
| `extra[]` | other printed penalty-cell text (e.g. licence disqualification, corporation fines) |
| `history[]`, `footnote_quotes[]`, `section_quotes[]` | verified footnote / section words about earlier maxima, notes or the table |
| `aliases[]` | other provision numbers the Manual uses for the same offence `{provision, s, quote}` (e.g. s 274H / s 247H) |
| `also_listed_as[]` | ids of rows for the same offence printed in another table |
| `notes[]`, `confidence` | curator notes (always surfaced) and `high` / `medium` / `low` confidence (see flags) |
| `jurisdiction` | `Vic` (Commonwealth tables are held verbatim only) |

A `maxima[]` entry: `text` (verbatim cell), `when_text` (verbatim date cell), `from` / `to` (ISO dates; `null` = open), optional
`to_before` (printed "prior to" date; `to` is the day before), `level`, `life`, `imprisonment_years` or `imprisonment_months`,
`at_large`, `fine_level`, `fine_penalty_units`, `pu_individual` / `pu_company` (natural person / corporation fines where
printed), `row_quote` (the verified row), `relation` (+ `relative_to_primary`, `factor`, `stated_years`, `stated_level`,
`multiple_offences`) for a maximum stated relative to another offence, `current_only` (the table prints no dates; the
maximum is the current one only), `date_basis` {`s` or `fn`, `quote`} (the verified words that fix a date not printed in the
table), `source` (`fn N` where an earlier maximum stated in a footnote was turned into a dated entry), `from_rule` (a
printed "after DATE", read as from the next day), `unparsed` (date text that could not be parsed: treat as unresolved).

`relation` values: `equal_to_primary` (same as the primary offence; "accumulated maxima" where `multiple_offences`),
`fraction_of_primary` (`factor` 0.6 of the relevant offence's maximum), `greater_of_primary_and_stated` (the greater of the
primary offence's maximum and `stated_years`), `equal_to_intended_offence`, and three that the Manual does not let the app
compute: `table_not_reproduced`, `lower_penalty_in_other_enactment`, `law_of_place_capped_at_life`.

**Selecting a maximum (audit.maximum_applicable):** use the `maxima[]` entry whose [`from`, `to`] contains the offence date;
if none does, the Manual is silent for that date — mark it unresolved and ask the user (never interpolate); a date range that
straddles a change is unresolved. Apply a later reduction, never a later increase (5.1.3). For a closed historic period use
the printed term (`flag.historic_level_scale`). Apply `conditional_maxima` only when the condition is established. Relative
maxima need the primary offence. `current_only` rows carry a caution for older offending. Ceiling adjustments:
`max.absolute_ceiling` (summary offences, CCE offenders, indefinite sentences).

**Term and fine together.** The structured fields hold a term and a fine separately, and the audit checks each against its
own maximum; they do not record whether the printed cell joins them with "or", "and/or" or "or both" (e.g. 29.1.3 "1 year or
120 p.u." beside "5 years, 1000 p.u., or both"). The connective is kept in the verbatim `text`, which the reasoning must read
before proposing a term and a fine together on one charge. A company column printed "N/A" (31.1.1.4) leaves `pu_company`
empty and carries a row note: the table gives no company maximum for that offence.

### `penalty_scale.json`
`penalty_scale` {`imprisonment` levels, `fines` levels, `rules` with quotes}, `penalty_unit_values`, `historic_tables`
(sections held as text only).

## The structured proposal checked by `check: hard` audit items
The reasoning layer returns, besides its text:
```json
{"court": "SC|CC|MC|DrugCourt-MC|DrugCourt-CC",
 "offender": {"natural_person": true, "date_of_birth": null, "age_at_offence": 24, "age_at_sentence": 25,
              "serious_offender": false, "serious_offender_type": null, "cce_offender": false,
              "consents_to": {"cco": null, "adjourned_undertaking": null, "dato": null},
              "prior_serious_youth_offence": null},
 "status": {"on_parole_from_life": false, "serving_or_sentenced_to_life": false, "parole_cancelled_before_sentence": null,
            "serving_npp_of_prior_sentence": false, "prior_sentence_straight": null,
            "serving_cth_sentence": false, "cth_limit_date": null, "psd_declared_by_first_court_days": null,
            "yd_current_whole_term": null, "yd_current_unexpired": null,
            "orders_in_force_at_sentence": [{"type": "cco|youth_detention|dato|parole|higher_court_sentence|aeo|licence_suspension|intervention_order",
                                              "details": "…"}]},
 "indication": {"given": false, "type": null, "max_tes_months": null, "plea_entered_on_it": null, "same_officer": null},
 "charges": [{"no": 1, "offence_id": "…", "offence_dates": ["2024-03-01", "2024-03-01"], "plea": "guilty", "same_time_group": null,
              "historic_provision": null, "arising_from_driving": null, "committed_on_parole": false,
              "classes": {"schedule_1_sexual": null, "covered_by_ss_10_10AE": null, "arson": null, "excluded_offence": null,
                          "aeo_relevant": null, "lcra_specified": null, "confiscation_schedule": null,
                          "rto_eligible": null, "forensic_sample": null, "serious_mv": null},
              "serious_youth_offence": null, "previous_s61_3_offence": null,
              "summary": false, "heard_summarily": false, "inchoate": null, "primary_offence_id": null,
              "category": 2, "sso_in_scope": false, "mm_group": null, "cat2_exception": null,
              "protected_official": false, "gross_violence": false, "special_reason": null,
              "conditional_maximum_condition": null, "maximum_applied": {"months": 300, "fine_pu": 3000, "basis": "…"},
              "disposition": {"type": "imprisonment|cco|fine|yjc|yrc|adjourned_undertaking|discharge|dismissal|dato|csto|rto|mtmo|indefinite",
                              "months": 30, "fine_pu": null, "conviction_recorded": true, "cco_combination": false,
                              "cco": {"months": null, "conditions": [], "unpaid_hours": null, "curfew": null, "commencement": null}},
              "in_aggregate": false,
              "cumulation": [{"onto_charge": 2, "months": 6}], "base": false,
              "cumulation_default": null, "concurrent_with_previous": false, "departure_reason": null,
              "youth_centre_escape_or_damage": false, "yd_concurrent": false}],
 "aggregate": null,
 "total_effective_months": 42, "npp_months": 28, "npp_reason": null,
 "npp_commencement": null, "parole_sentence_allowance": null, "new_term_commencement": null,
 "commencement_date": null, "cumulated_on_life_sentence": false, "cumulated_on_life_parole": false,
 "cumulated_on_life_imposed_now": false,
 "special_reason": {"limb": null, "basis": null, "in_writing": false},
 "s6aaa": {"notional_type": "imprisonment", "notional_tes_months": 54, "notional_npp_months": 36,
           "notional_cco_months": null, "notional_unpaid_hours": null, "notional_fine_pu": null, "conditions_reduced": null},
 "cooperation": {"less_severe": false, "basis": "none|past|undertaking", "announced": false, "undertaking_recorded": false},
 "pre_sentence_detention_days": 112, "psd_basis": "from charge|continuous custody from first arrest",
 "ancillary_orders": [{"type": "licence_disqualification", "months": 24,
                       "form": "cancellation_and_disqualification|disqualification_from_driving|disqualification_from_obtaining",
                       "cancellation_and_disqualification": true}],
 "flags_shown": ["flag.…"], "unresolved": ["…"],
 "assumptions": ["…"], "citations": ["factor.…", "…"]}
```
`aggregate` (when used): `{"charges": [1, 2], "months": 36, "announced": true, "reasons": "…", "effect": "…"}`.
`same_time_group`: charges committed at the same time share a group label (the Magistrates’ Court five-year cap, 8.4.1); `null` when unknown. `cumulated_on_life_imposed_now`: a term cumulated on a life sentence imposed on another charge in the same sentencing (a caution, cum.life_parole). The `s6aaa` measures used depend on the notional type (imprisonment: term and npp; CCO: months and unpaid-work hours; fine: penalty units); sentence types are compared with `principle.sentence_type_ordering`. `conditions_reduced`: the CCO conditions (other than months and hours) the plea reduced, as text. `yd_current_whole_term` / `yd_current_unexpired`: months of youth detention already being served (the whole term imposed and its unexpired part), for the youth detention cap across occasions (flag.youth_detention_cap_across_occasions). In `cumulation`, `onto_charge: "yd_current"` records new youth detention cumulated on youth detention already being served, `months` being the part served after that detention ends (yjc.maximum, audit.youth_detention_limits); detention wholly concurrent with it has no such entry. A combination sentence is recorded on the charge with `cco_combination: true`, `months` = the term of imprisonment and the CCO in `cco`: `type: "imprisonment"` for an ordinary CCO, `type: "mtmo"` where the CCO is a mandatory treatment and monitoring order (audit.category_1_2, audit.mandatory_minimum, audit.cco_limits). `cco.commencement`: the commencement the reasoning states — months after the order (a number) for a CCO not combined with imprisonment, `"on_release"` for a combination CCO; `null` when not stated (audit.cco_limits (9) then does not run).
Unknown values are `null` and must appear in `assumptions`; anything the Manual does not settle goes in `unresolved` with the
flag id in `flags_shown`. Intake facts that the hard checks read (e.g. `parole_cancelled_before_sentence`,
`previous_s61_3_offence`, `arising_from_driving`, the indication) are copied into the proposal from the intake, not
inferred by the model.
