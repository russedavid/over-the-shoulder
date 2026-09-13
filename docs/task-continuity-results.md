# Native task continuity: findings and open failures

The current pipeline handled changed requirements, another participant's question, a new deliverable, and explicit task supersession. It also exposed a concrete gap: line-numbered OCR prevented a valid observed code fragment from entering the workspace. Useful code reached the pane, but the source-backed companion diff was unavailable. No application logic, model defaults or source-validation rules were changed for this study.

## Run and boundaries

Local run `otsc-continuity-20260912-v1`, generated from commit `4938abd` on September 12, 2026, America/Chicago. Application code hash: `38b1822d8016e769f17f03aa4eca53aeff4ab3446ad70ec8e28c6bb04fe3f4c6`. The six runtime stages completed in about 306 seconds. The original manifest records a subsequent **harness reporting error**, preserved and explained below.

The actual AppKit Controller ran continuous capture scheduling, local redaction, Astra OCR, local Parakeet transcription on separate queues, context building, planning, quick/deep assistance, validation, output histories and native rendering. A fixture image replaced the external desktop screenshot; synthetic speech files entered the audio queues silently. The app did not open microphone/system audio hardware, play audio, read a real project, or modify the owner's preferences.

Models were the existing configuration: Spark low for quick guidance, Astra medium/Fast for planning and deep answers, Astra low/Fast for OCR and context building, and local Parakeet TDT 0.6B v3 for transcription. The run recorded 102 completed provider calls: 31 OCR, 37 context updates, 18 planning calls, eight quick responses and eight deep responses. No provider error was recorded. This is one controlled synthetic sequence, not a representative success rate, acoustic benchmark, or load study.

The [frozen scenarios and protocol](../evals/continuity-protocol.md) distinguish generator inputs from evaluator-only criteria. No historical saved answers or reference drafts were supplied to generation. Application runtime files remained unchanged.

## What worked

- The primary user's initial 500–599 requirement overrode the other speaker's suggestion to include 429. The first correction passed five restricted-interpreter boundary examples. After the primary user added 429, the new proposal passed its five examples too. No generated Python was executed.
- Six synthetic utterances retained the task-relevant numbers, negation and configured channel roles through actual transcription. This verifies the supplied clips, not speaker identification or noisy conversation.
- Three automatic reviews whose snapshots included “Right, thanks” kept the existing answer. A fourth review had begun before that acknowledgment. No new output versions were published during the unchanged interval. The final equality-based publication guard was not exercised by a duplicate: suppression happened at planning.
- The participant's question about 600 and duplicate payment charges received a source-linked answer. The deeper response distinguished retry eligibility from idempotency and kept the existence of a payment integration uncertain.
- A passive pivot from implementing code to a release checklist changed the plan/output contract without pressing New task. Later OCR supplied the exact three-section requirement and the next response incorporated it. Tests and deployment remained explicitly unestablished.
- A real completed deep answer was deliberately delayed, then released after New task. It was rejected; the new session retained no old output types. Fresh assistance then addressed the bounded-queue task.

## The source-mapping failure

All 17 accepted readings of the two code screens preserved the visible function and requirements, including the line gutter:

```text
1  def should_retry(status):
2      return status >= 500
```

The context builder proposed the correct unnumbered two-line fragment with `first_line=1`. The validator requires literal corroboration; unnumbered code is not a substring of numbered OCR text. It rejected **21 file deltas** across repeated context updates. The deep answer's optional cache entries encountered the same boundary. The app preserved usable annotated proposals but never materialized the observed file or its companion diff.

This is an OCR-to-workspace representation failure, not a failure to read the predicate. It calls for an explicit, source-backed distinction between editor gutters and source text, preserving indentation and line mapping. Loosening provenance checks or stripping arbitrary leading numbers would create a different defect. The current validator remains intact.

Two additional memory deltas incorrectly marked mixed screen/speech evidence as observed; the host rejected them and retained the earlier reported constraint. Quick-response wording issues also remain: an unsupported “sprint” frame, unnecessary mandatory organizational signoffs, and an imprecise throughput claim. Assistant critiques retain these separately from code correctness and workflow success.

## Timing: availability and visibility differ

These are seconds from the synthetic screen/complete speech clip becoming available, including actual queueing and planning. They are not utterance-start-to-physical-pixel measurements.

| Stimulus | First quick available | First deep available | Relevant selected-pane change |
|---|---:|---:|---:|
| Initial speech and screen | 9.9 | 25.5 (spoken rule); code at 54.9 | 9.9 |
| Spoken addition of 429 | 20.0 | 32.1 | 32.1 |
| Other-channel boundary/payment question | 27.6 | 34.8 | 34.8 |
| Spoken switch to checklist | 33.5 | 52.4 | Not observed for the checklist type |
| New bounded-queue task | 13.8 | 26.9 | 13.8 |

The automatic quick calls themselves took roughly 4–5 seconds. Their 20–33-second availability delay includes waiting for an existing review and the next planner decision. This identifies a tradeoff to measure before changing scheduling; it does not establish that removing planning would preserve quality or suppression.

The initial code and later checklist were available in the type list while an older explanation remained selected. The checklist gained red newer-version badges but did not replace that selected type. Model completion therefore overstates automatic visibility of the new deliverable. The exact three-section checklist became available about 93.9 seconds after the pivot; the earlier 52.4-second checklist answered the spoken request without that new screen specification.

## Hold/history and evaluator corrections

The original hold probe required an already-selected code version and missed an available unopened type. It was an invalid probe, not a product failure. `otsc-continuity-20260912-presentation-v3` replayed the **same two generated code responses** through the current native event handler and renderer, with inference and capture disabled. It uses the original snapshot clock so source evidence does not expire merely because review happens later. Nine checks passed: held identity/content, one new code version and its badge, updated task memory, Latest, and Older/Newer staying within the type. Native views show both the held old predicate and the revised predicate after Latest. This replay does not certify simultaneous live perception latency while held.

The first presentation attempt failed decoding a snapshot field: literal OCR is stored once outside the nested reading. The corrected decoder restores that known serialization field; model outputs are unchanged. Both subsequent checks passed; all attempt directories remain local.

After the live stages completed, a null cancellation reason broke the original HTML renderer. Its failure event and `harness_error` manifest remain preserved. The corrected report uses the unchanged journal. Scoring also now checks an explicitly retained answer when no new answer was needed, without counting retention as publication. The original evaluator remains available at `4938abd` and was recomputed separately for comparison. No application prompt or runtime code was tuned to this run.

All 16 quick/deep responses received provisional assistant critiques; the reviewer authored the scenarios and saw model identities. There are no human labels or calibration claims. The 31 screen readings repeat four fixture screens, with 15 distinct text renderings, not 31 independent examples. This sequence does not exercise five-minute expiration within one continuing task, realistic capture noise, generated diagrams, or long-session congestion.

## Reproduce and inspect

```sh
# Silent fixtures and configuration, no inference:
.venv/bin/python -m evals.native_continuity --prepare --output /tmp/otsc-continuity-prepared

# Explicit real-model run through the current native application:
.venv/bin/python -m evals.native_continuity --output /tmp/otsc-continuity-new

# Read-only report and local server:
.venv/bin/python -m evals.continuity --run /tmp/otsc-continuity-new
.venv/bin/python -m http.server 5005 --bind 127.0.0.1 --directory /tmp/otsc-continuity-new

# Separate native presentation replay without inference:
.venv/bin/python -m evals.continuity_presentation --run /tmp/otsc-continuity-new \
  --output /tmp/otsc-continuity-presentation-new
```

The report has stage anchors, scripted inputs, actual perception, source-linked replies, code, plans, source-validation notes, native views and links to every provider request. Assistant critiques remain separate from objective measurements. Generated speech, images, responses, runtime configuration and journals stay outside Git; committed artifacts are synthetic source fixtures, evaluation code and aggregate findings.
