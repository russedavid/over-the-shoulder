# Evaluation and preservation findings — September 8, 2026

The final standard workflow passed all 20 development cases. The frozen held-out run recorded seven passes and one disputed evaluator failure across eight cases. These are small, assistant-authored synthetic assessments with provisional model judgments, not field accuracy estimates.

The existing desktop workflow passed 52 automated checks and the native preservation check. Optional inspection passed the same four selected tasks as the standard workflow, with greater latency and reported token use. Inspection remains off by default.

## Run identity

- Application: 0.2.0; runtime source at `30e6000e6adb706bf1ba103442492ed98297afea`.
- Runtime code hash: `bb71467185582bbf796dc3ca153fb35fd7b067cbd6735c3957cba62cc3dd7c25`.
- Corpus: `2026-09-08.3`, hash `228a90fa03471e7c9fa5255a296e28f2651a53a579ba24575a39a4bad5ab9f90`.
- Generator: Codex `gpt-5.5`, low reasoning; reviewer: Codex `gpt-6-astra`, low reasoning.
- Reference check: 16/16 agreement with assistant-authored positive/negative examples, zero false-positive passes. This is a reference sanity check, not human calibration.
- Standard, held-out, and inspection runs confirmed unchanged runtime source during execution. Their complete manifests, response data, and critiques are retained locally outside Git.

## Preserve the development record

| Run | Recorded passes | Interpretation |
|---|---:|---|
| Original development generation, v1 | 17/20 | Two responses were rejected for optional file metadata; a sum response was also failed by an unrequested comparison in its rubric |
| Recorded-output replay, v2 | 19/20 | Corroborated optional metadata was normalized and the sum rule corrected; no new generations. A snippet still assumed an unseen enclosing function |
| Fresh targeted generation, v2 | 1/4 | One malformed-string error and two additional disputed review rules |
| Same targeted outputs replayed, v3 | 3/4 | Rules now accept a local zero-divisor guard and a clearly conditional caveat. The malformed-string issue remains a failure |
| Fresh full development generation, v3 | 20/20 | A new stochastic run with the frozen current rules; not a causal estimate of improvement over v1 |
| Held-out generation, v3 | 7/8 | One evaluator failure is disputed after inspecting the output; its original result is retained |

The malformed-string failure is concrete: the response claimed robust input validation but used `str.isdigit()` before `int()`. The string `²` satisfies the former and is rejected by the latter. The recorded output remains a failure. A later correct generation does not erase it.

The metadata correction is deliberately narrow. Unnamed file metadata can be omitted when real cited screen content corroborates it. Redundant metadata for a complete supplied file can be omitted when both path and content match exactly. Invented content, unknown citations, and unsupported named files still fail validation. The generation snapshot remains the source of every verified proposal base.

## Held-out disagreement

Case `holdout-01` asked for a conversion from milliseconds that preserves fractional seconds. The response supplied `return milliseconds / 1000`, explicitly rejected integer division, and included the stated example as an assertion. The reviewer failed a line annotation describing a blank line. Separately, the restricted behavior checker declined to process the module-level assertion.

The assistant audit disputes the semantic failure: the displayed function and explanation meet the stated conversion requirement. However, no automated behavior pass is claimed for this case, and the frozen result has not been changed. Its full response, original critique, and separate assistant audit are retained. No prompt, case, or checker was tuned using the held-out output.

This is a limitation of the current evaluation system. Reference examples do not guarantee that a model reviewer interprets every rubric correctly. A definitive product-defect count cannot be read directly from the aggregate score.

## Standard workflow versus optional inspection

Same generator, runtime, corpus, and four input cases; one generation per condition and case. Timing excludes semantic-review time. Token counts exclude the reviewer and include inspection planning calls.

| Measure | Standard | Optional inspection |
|---|---:|---:|
| Provisional task passes | 4/4 | 4/4 |
| Median generation time | 14.32 s | 21.59 s |
| Calls reporting usage | 4 | 9 |
| Reported input tokens | 36,558 | 71,848 |
| Reported output tokens | 2,115 | 2,495 |
| Reported cached input tokens, already included above | 26,624 | 31,232 |

The model chose one actual file read for the sum task and stopped without a tool call in the other three cases. The tools were available, but most of these small inputs already contained enough evidence. This sample supports keeping inspection opt-in. It does not establish that inspection is always slower or never useful. No dollar cost is inferred from subscription access or unconfigured token prices.

## Preservation, recovery, and review artifacts

The automated checks cover original task behavior plus session authority, proposal bases, metadata privacy and storage failure, bounded inspection, evaluator isolation, and reference/split handling. The native check verifies quick/deep output, copying, retained selection, click-through, diagrams, queued audio, MIDI window actions, and exact session restore with capture paused.

A controlled in-process failure exercise verifies that a failed provider preserves existing work and a stale response cannot replace it. This is an injected test, not a field incident or recovery-time service guarantee. The portfolio illustration was checked at desktop and phone widths, including its controls and layout, with external network requests blocked.

The final desktop-capture attempt could not run: macOS granted screen permission but reported zero active displays. This environmental result remains recorded. The explicit rendered-fixture run then passed with real OCR, local speech recognition, model inference, and native output: quick help arrived at 5.7 seconds and the deeper annotated artifact at 15.3 seconds. These are observations from one generated-speech, rendered-image example, not a latency benchmark or a fresh hardware-capture certification. Hardware microphone/system-audio checks and earlier active-display runs are documented separately in [implementation status](implementation.md).

Runtime captures, generated responses, recordings, reports, and traces remain outside Git. Only this reviewed aggregate account is committed. The source archive excludes Git history and local runtime material. No public release or remote CI run is claimed.

## Reproduce and inspect

Run `python ots.py eval` for offline corpus checks, or the explicit live commands in the [operating guide](operating-guide.md). Every live run writes its own manifest and report and refuses to overwrite a prior run. Recorded development outputs can be replayed under a corrected evaluator while preserving their earlier judgments. Holdout replay is rejected.

See the [evaluation protocol](../evals/protocol.md) for the methods drawn from Hamel Husain and Shreya Shankar, and the [case study](portfolio/case-study.md) for the application decisions these checks support.
