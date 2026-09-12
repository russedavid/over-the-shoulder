# Background planning comparison — September 12, 2026

The proposed consolidation reduces model calls but does not improve time to a fresh answer in this test. Keep the current application path: an independent context builder and a separate plan review before deep generation. The prototype is isolated in `evals/planning_comparison.py`; it is not enabled in the desktop application.

## Results

Eight synthetic development scenarios ran twice through each variant, for 32 initial pipeline trials. These checkpoints included a previous answer/plan where appropriate, but began with empty accumulated working notes. Paired variants received identical frozen input and previous-answer state. Order alternated between variants and reversed on the second pass. Each pair ran sequentially; background work inside a variant remained concurrent. The additional warm-memory and busy-worker probes below bring the total to **48 pipeline trials**.

| Main comparison | Current | Combined context and plan review |
|---|---:|---:|
| Median time to validated deep answer, 8 required updates per variant | 23.13 s | 24.12 s |
| Median quick-guidance delivery, same scenarios | 14.66 s | 16.17 s |
| Median no-update decision, 8 quiet inputs per variant | 6.61 s | 8.36 s |
| Necessary answers delivered | 8/8 | 8/8 |
| Correct answer-needed decisions | 16/16 | 16/16 |
| Unnecessary quick/deep generations on quiet inputs | 0 | 0 |
| Unnecessary history publications on quiet inputs | 0 | 0 |
| Total model calls, including background and quick work | 48 | 32 |

The combined variant was faster in two of eight fresh-answer pairs. Its median paired difference was **1.10 seconds slower**. Call count fell by one third; that is not a measured billing or token-cost reduction.

| Fresh-answer scenario | Current, rounds 1 / 2 | Combined, rounds 1 / 2 |
|---|---:|---:|
| Small boundary change in code | 25.33 / 23.99 s | 26.17 / 29.96 s |
| New question with the existing plan | 22.26 / 19.83 s | 21.52 / 22.07 s |
| Switch from code to a novel JSON output contract | 19.92 / 19.02 s | 18.04 / 21.97 s |
| First answer without an existing plan | 26.59 / 28.82 s | 27.95 / 29.43 s |

The quiet scenarios were reworded OCR of unchanged code, an acknowledgment, an unrelated editor notice, and a repeated view after the requested correction had already been proposed. They exercise semantic suppression, not just exact-string deduplication. The control and prototype both suppressed all eight quiet inputs without discarding a required update.

## Existing working memory

The initial checkpoints had no accumulated notes, which could make memory initialization look like the cost of every update. A follow-up comparison seeded three source-backed working notes about the task, fee contract, and visible implementation before adding the new input. Their evidence watermark stops before that input, and neither variant receives future information. The notes are assistant-authored fixture state, not a claim of a model-generated long session.

The code boundary change, worked-example question, and repeated view after a completed answer were each run twice through both variants: 12 additional trials, with four needed answers and two quiet inputs per variant.

| With working notes already populated | Current | Combined |
|---|---:|---:|
| Median validated deep answer | 22.35 s | 22.87 s |
| Median quick guidance | 12.73 s | 14.06 s |
| Needed answers delivered | 4/4 | 4/4 |
| Unnecessary generations/publications | 0 | 0 |
| Model calls | 20 | 14 |

The combined variant was 0.32 and 1.19 seconds faster for the code change, but 4.64 and 1.25 seconds slower for the new question. Its median paired difference was 0.46 seconds slower. Existing memory removes a potential initialization confound but still does not show a consistent fresh-answer advantage. Across the initial and warm-memory tests, each variant suppressed all **10 quiet inputs** and delivered every required answer.

## A busy background worker changes the result

A separate run started a real review of the older evidence one second before a new requirement arrived. The single background worker then processed the new evidence. The clock starts at the new input, excluding the earlier second but including any remaining wait. Both variant orders were tested; this added four pipeline trials.

| Busy-worker probe | Current | Combined |
|---|---:|---:|
| Fresh answer, round 1 | 26.88 s | 35.87 s |
| Fresh answer, round 2 | 23.28 s | 36.98 s |
| Median fresh-answer time | 25.08 s | 36.42 s |
| Correct updated code | 2/2 | 2/2 |

The combined variant was **8.99 and 13.69 seconds slower**. Its new context review waited 8.79 and 7.77 seconds for the older review, then performed the combined review before deep generation. Current planning proceeded while its background worker was occupied.

This is the main architectural finding: context-building currently runs outside the answer dependency chain. Consolidating the answer-necessity decision and planning into that worker makes a fresh automatic answer depend on its availability. Asynchronous execution does not eliminate that dependency. Reusing the existing plan still requires judging whether new evidence warrants another answer; this prototype does not bypass that judgment or release an answer using an obsolete approval.

## What was compared

- **Current:** Astra low/Fast builds context in the background. Astra medium/Fast reviews the plan and answer necessity, then Astra medium/Fast generates the answer. Spark low/Standard supplies quick guidance according to the existing gate.
- **Combined:** one Astra low/Fast background call returns both the source-linked context update and the planning decision. A one-use, snapshot-bound adapter supplies that decision to the existing `TaskPlanningProvider`, avoiding a separate medium planning call. The same medium answer generator, Spark quick worker, validation, annotation repair, coordinator, and output browser remain in use.
- Both answer workers use the same immutable source snapshot and previously completed memory. Newly built memory is retained for subsequent work. The prototype changes where plan review occurs; it does not give the generator future evidence.
- Cached approvals are bound to the session, task/evidence revision, previous answer, plan, file snapshot, and refresh mode. A changed answer or task cannot consume an older approval. Offline checks cover cancellation/error handling and the occupied-worker dependency.

Timing begins when synthetic OCR/transcript evidence is available and ends when the validated response is accepted for publication. Provider startup, real Codex work, validation, and any repair are included. OCR inference, transcription, native painting, and image generation are excluded. The harness polls completion events every 20 ms; it does not benchmark the application's 100 ms UI timer.

The busy-worker probe is a controlled scheduling experiment over frozen inputs, not a continuous desktop session. An older context update is retained in the trace; its planning decision cannot authorize the new request. The two variants share the same new-input snapshot.

## Output review and remaining issues

All code outputs checked for the initial-function or changed-boundary requirement passed five restricted-AST behavior checks covering zero, a negative weight, positive weights, and a fractional weight. Generated code was not executed. Both variants changed the output contract for the JSON request and returned the exact region configuration. Assistant review confirmed all eight worked-example answers across the initial and warm-memory tests addressed the other participant with the correct 10-dollar calculation. The deterministic `needs_review` labels for those semantic cases remain in the original records; separate assistant reviews record the judgment.

The context provenance guard rejected some model-proposed memory claims that cited conversation/typed requirements as direct observations: 13 claims in the current variant and 9 in the combined variant in the main run, plus 3 and 2 in the busy-worker probe. No such rejections occurred in the warm-memory probe. Those notes and raw proposals remain visible. Answer delivery was unaffected because both variants retained the original evidence. These are context-quality findings, not hidden provider failures or claims that every intermediate step was correct.

The corpus, expectations, and prompts were fixed before the main calls. No held-out examples were used or tuned against. Results and reviews are assistant-authored and provisional, not human-calibrated accuracy measurements. Two passes over short synthetic contexts cannot establish general latency or duplicate rates for long, dense sessions. Provider load, caching, and stochastic output length remain sources of variation; alternated order reduces but does not eliminate them.

The test method follows the existing [Hamel Husain / Shreya Shankar evaluation protocol](../evals/protocol.md): evaluate concrete task outcomes, inspect traces, preserve failures and evaluator limitations, and assess duplicate publication separately from answer correctness.

## Reproduce and inspect

```sh
.venv/bin/python -m evals.planning_comparison --live --rounds 2 --output /tmp/otsc-planning-comparison
.venv/bin/python -m evals.planning_comparison --live --rounds 2 --cases small_constraint --background-busy --output /tmp/otsc-planning-busy
.venv/bin/python -m evals.planning_comparison --live --rounds 2 --cases small_constraint new_question repeat_after_answer --warm-memory --output /tmp/otsc-planning-warm
```

Use a new output directory for each run. These commands make live calls through saved provider settings. They do not capture the desktop, record/play audio, or change application settings.

Private run identities:

- `planning-comparison-20260912-075351`: original 32-trial comparison. `experiment-manifest.json` and the preserved experiment source identify the exact original harness.
- `planning-busy-comparison-20260912-080316`: four busy-worker trials. The manifest and `runner-source.py` preserve that experiment version.
- `planning-warm-comparison-20260912-081416`: 12 warm-memory trials. The manifest and `runner-source.py` preserve that experiment version.

Each run has an `index.html` review page, unchanged `comparison.json`, per-call prompts/results/timings, per-arm input and output, and separate `assistant-review.json` files. `comparison-reviewed.json` adds aggregate reporting and provisional reviews without replacing original outcomes. Raw results and rendered reports are stored outside Git.

Validation: **181 unit tests**, Ruff, and offline corpus validation passed. No desktop UI implementation changed, so a new native render test was not required. The next latency experiment should keep context-building independent and evaluate plan reuse without requiring a fresh combined memory/review call before every answer.
