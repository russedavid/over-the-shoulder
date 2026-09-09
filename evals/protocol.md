# Evaluation protocol, version 2026-09-08.3

The purpose is to determine whether the assistant completes the user's stated task while respecting evidence, conversation, and authority. There is no generic 1–5 quality score.

The initial cases come from development failures recorded in `docs/development-review.md`: missing-data substitutions, loose numeric boundaries, incomplete annotations, stale context, invented file observations, and lost interaction behavior. Additional security cases are explicitly synthetic probes of declared boundaries. Coverage categories are not claimed to be a taxonomy discovered from field traffic.

## Data and separation

`cases.py` contains 28 assistant-authored synthetic cases: 20 development cases and 8 holdout cases. Scenario families are disjoint across those splits. Each case includes the task, observations and speaker roles, any complete fixture files, and an evaluator-only acceptance rule. The generator never receives the acceptance rule or expected test outputs separately from the user's stated requirements.

The corpus hash, selected IDs, application/code hash, prompt/schema hashes, model configuration, and evaluator/rubric hashes accompany each run. Holdout results are an assessment, not material for iterative prompt tuning. If a holdout issue becomes a development example, retire that assessment and create a new holdout version.

## Scoring

1. Validate the common output contract, evidence references, requested artifact, and explicit no-rewrite/no-observation requirements.
2. For small Python tasks, check specified behavioral examples with a restricted AST interpreter. It does not execute generated Python, imports, arbitrary calls, or I/O. Unsupported constructs are `needs review`, not automatically wrong.
3. Use a case-specific binary model judgment for semantic requirements such as handling a participant's suggestion or distinguishing uncertainty from observed facts. Preserve the critique and response evidence.
4. A failed deterministic check cannot be overridden by the judge. Missing or ineligible semantic review remains `needs review`.

The semantic reviewer is checked against 16 assistant-authored positive/negative examples from development families. Its eligibility rule is predefined: zero false-positive passes, all but at most one positive example recognized, and agreement on all but at most one reference. A successful check establishes only a narrow reference sanity check. It is **not human calibration**, and no inter-rater agreement or human-level reliability is claimed.

The original development runs are retained. Version 2 corrected a rubric that demanded an unrequested comparison between sum and mean. Version 3 accepts a local zero-divisor guard when the task asks for a guard without prescribing missing-value behavior, and distinguishes a conditional caveat from a false claim that working code is broken. Added reference examples exercise these distinctions. These are evaluator corrections, not improvements in the recorded generations. Replays use a new directory, retain previous critiques/outcomes, reject holdout inputs, and validate the judge/reference identity.

An evaluator outage is `needs review`, not a product failure. A genuinely unsupported deterministic check also remains `needs review` even when a model reviewer approves the response. A concrete deterministic or semantic violation is a failure.

All judgments remain provisional until qualified human review is available. The owner previously authorized assistant review; that authorization does not transform assistant-authored labels into human labels.

## Error analysis

Inspect the input, proposal, deterministic checks, and critique together. Record the first substantive failure, distinguish specification gaps from implementation/evaluator errors, and add a targeted regression after investigating the cause. Do not hide failures or tune to a single aggregate score. A model-assisted label is an aid to review, not a substitute for understanding the example.

For optional inspection, first assess end-to-end task success, then examine tool choices, denied paths, repeated calls, budgets, and stopping behavior. Compare the standard workflow with the inspection path on the same inputs and model. A result favoring the standard workflow is a valid outcome; inspection stays opt-in.

## Limits

This corpus does not estimate field accuracy, product adoption, long-session behavior, or performance across all hardware and providers. Generated speech and staged windows are labeled as such. Runtime failure exercises and policy tests are separate from semantic quality results. Live model calls use explicit CLI commands; ordinary tests and `python ots.py eval` make no inference calls.

## Method sources

- Hamel Husain and Shreya Shankar, [ready-to-use metrics](https://hamel.dev/blog/posts/evals-faq/should-i-use-ready-to-use-evaluation-metrics.html): derive concrete failure checks from observed problems.
- Husain and Shankar, [agentic workflow evaluation](https://hamel.dev/blog/posts/evals-faq/how-do-i-evaluate-agentic-workflows.html): task success first, then trajectory diagnostics.
- Shankar et al., [Who Validates the Validators?](https://people.eecs.berkeley.edu/~bjoern/papers/shankar-validators-uist2024.pdf): evaluator alignment requires scrutiny; model judgments are not inherently authoritative.
