# Victorian Sentencing Estimator

LAWS90286 — Assignment 2

**Application:** deployment URL to be added.

## Overview

The Victorian Sentencing Estimator estimates an indicative sentence for a Victorian offence from the facts of a case. It uses the *Victorian Sentencing Manual* (4th ed, Judicial College of Victoria, version published 21 September 2026) as its legal source and provides reasons for its estimate.

I have designed it with someone who has received a summons in mind. Their summons and accompanying charge sheet provide a starting point for identifying the charge and entering the case into the application. The interface asks detailed questions because the charge alone cannot tell the application what sentence might follow. It also needs information about what happened, the person's circumstances and which facts are accepted or disputed. The simple input lets the user describe the case first, then review how the AI has organised that information into the form.

More ambitiously, this is an experiment in whether AI can accurately work out a sentence for a Victorian offence. That raises a further question: **is an AI sentence just if it is accurate?** Matching a sentence imposed by a court would be one way to assess the result, but it would leave questions about the reasons, the facts relied on and the opportunity to challenge them. I want to explore how far an AI can carry out the reasoning involved in sentencing, and what an accurate result would actually demonstrate.

## Using the application

The current prototype estimates a sentence for one charge involving an adult or a body corporate in the County Court or Supreme Court. It does not model sentencing in the Magistrates' Court. Its estimate assumes a guilty plea or a conviction; receiving a summons does not establish guilt.

Enter your own OpenAI API key in the sidebar and press **Update API Key**. The key is masked and held in your session; it is not saved to the case database or included in the report.

Choose **Simple** to type or upload a description, then press **Sort my description** and review the proposed answers. Choose **Detailed** to enter the answers yourself. Review the extracted facts before confirming them and running the analysis.

The result includes an indicative range, a formulated sentence, reasons, sources and any errors or unresolved issues. Reports can be downloaded in Word or Markdown. A run usually takes a few minutes and uses the API account associated with the key entered.

## How the harness works

The harness controls what each API call is asked to do, the material it receives and how its answer passes to the next step.

| Step | What happens |
|---|---|
| Facts - call 1 | The model extracts material facts, gives supporting words from the narrative and identifies missing information. The user can correct or remove facts before confirming them. |
| Legal framework - code | The application works out the applicable maximum, offence category, minimums and available sentencing options from the selected charge, dates and answers. |
| Analysis - calls 2–5 | Four parallel calls consider the gravity of the offending, the offender's circumstances, sentencing purposes, and current practice and comparable cases. Each returns its reasoning separately. |
| Synthesis - call 6 | The model brings those analyses together and proposes a sentence and indicative range, with reasons and references. |
| Checks and audit - code and call 7 | Code checks rules such as maximum penalties and non-parole requirements. A further AI call reviews the reasoning against standards drawn from the Manual. |
| Revision - calls 8-9, if needed | If the checks or audit report an error, the proposal is sent back with the problems identified. It is revised, checked again and re-audited once. Remaining problems are shown with the result. |

A normal completed run makes **seven AI calls**. Simple input adds one sorting call; revision adds two. Search embeddings are separate. The run log records the calls, and completed steps are retained when the page refreshes.

Each analysis receives relevant material retrieved from the Manual's knowledge base, using vector similarity where an index is available and keyword ranking otherwise. The current-practice analysis also receives cases from the case-summary database. These are passed to the synthesis and audit as comparisons, rather than treated as a formula for the sentence.

The models return structured answers so the code can inspect their fields and references. Citation IDs are checked against the material supplied, and quotations are checked against the narrative. These controls do not establish that the reasoning or sentence is correct. The audit is another AI assessment and can also miss a problem.

## Test cases

These three cases demonstrate ordinary operation, a more demanding statutory framework and the limits of incomplete information. All names and events are invented. The forms and narratives are included in `test_cases/`.

To run each case:

1. Enter your API key and select the case from **Load a test case**. This fills the detailed form and narrative.
2. Press **Extract the facts**, review the table, then **Confirm the facts**.
3. Run the analysis, proposal and audit. Inspect the result, cautions and call log, then download the report if required.

### 1 Glassing in a hotel

A 24-year-old with no prior convictions recklessly causes serious injury with a beer glass. The material includes an early plea, remorse and counselling.

Look for reasons weighing the injury against the offender's circumstances, the effect of the plea and the alcohol exclusion order identified by the application's rules.

Files: `tc1_glassing_rcsi.json` and `tc1_narrative.txt`.

### 2 Injury to a paramedic during a psychotic episode

The offender intentionally causes serious injury to a paramedic on duty. The material includes psychiatric evidence and confirmation that the protected-official scheme applies.

This tests the interaction between the offence category, a mandatory minimum and the possible special-reason pathway. The application should explain the restrictions and uncertainties. Change the scheme confirmation to **Unknown** and start a new run to compare how the framework handles uncertainty.

Files: `tc2_paramedic_icsi.json` and `tc2_narrative.txt`.

### 3 A punch at a railway station

The offender is 19 at the offence date. The victim's protected status is disputed, and important information about the offender is missing.

The application should distinguish disputed facts from established facts, identify missing information and explain the uncertainty. It should identify that a youth justice centre order is outside its scope. A precise-looking answer without those qualifications would be a weakness, even if the proposed term looked plausible.

Files: `tc3_unclear_status.json` and `tc3_narrative.txt`.

Ten further examples cover other offence schemes. Automated tests are in `tests/`; their simulated API responses test the software's behaviour, rather than the accuracy of live sentencing predictions.

## Limits

The application does not calculate a combined sentence for several charges, determine guilt or cover every sentencing order and offender-status scheme. Its legal knowledge is limited to the supplied edition of the Manual. Missing facts, errors in the source material or an unsuitable comparable case can affect the result.

The displayed comparison with similar cases is a comparison within the database, not the probability of receiving a particular sentence. The estimate is experimental and is not legal advice.


## Sources

Judicial College of Victoria, *Victorian Sentencing Manual* (4th ed, version published 21 September 2026). The knowledge base contains material extracted from this edition. Uploaded case summaries provide the comparable cases. The Judicial College gives no warranty of accuracy or reliability.

