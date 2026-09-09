# Continuous visual context and assistance

The application remains one Python/PyObjC/AppKit process launched with `python ots.py`.

Window sharing defaults off each launch. The original `NSWindowSharingNone` setting was verified with the current PyAutoGUI/Pillow screenshot path on this macOS 15.6.1 machine: a visible test window appeared in both sharing-enabled captures and was excluded from both sharing-disabled captures. Sharing off uses no window hiding or artificial capture delay. The Sharing button enables a fallback that hides the visible window, waits 10 ms, captures, and schedules restoration directly on AppKit before OCR; it no longer uses the old 200 ms timer or waits for the 100 ms event poll. The setting is legacy and must not be treated as a guarantee about other capture tools. [Apple's sharing-type documentation](https://developer.apple.com/documentation/appkit/nswindow/sharingtype-swift.enum/none).

1. Following captures a fresh, full-resolution screenshot, locally redacts recognizable credential rows, and passes the image to Astra low/Fast. The local Tesseract text is not supplied as OCR evidence. Each completed screen reading starts the next capture immediately; there is no extra 29/30-second sleep and only one screen-reading call can be active.
2. Microphone and system audio continue on separate queues. Their transcripts retain configured speaker hints and capture times. No screen or answer call holds those transcripts back.
3. `ContextStore` retains the last five minutes of raw OCR/audio, plus typed task context and source evidence needed by accumulated memory and observed files. A snapshot admits up to 800,000 source-text characters and reports omitted IDs if that limit is reached; dense logs are no longer squeezed into the former 24 KB budget. Screen readings can contain up to 45,000 characters. These bounds do not guarantee exhaustive screen understanding.
4. One background Astra low/Fast context worker compares the latest evidence with working notes and the observed workspace. It emits source-linked additions, revisions, removals, literal file fragments, and explicit path retirements. Invalid file entries do not discard valid memory. Old fragment versions remain available; working notes cannot alter a real project or user-maintained decisions. Scrolling away and window expiration are not evidence that a file or requirement disappeared.
5. Quick and deep answers use an immutable snapshot of the latest available context, five-minute stream, workspace, previous substantive answer, and previous artifacts. They do not wait for the context worker. Incoming passive evidence is for the next request; task/project edits or an explicitly newer request still invalidate old answers. There is one worker per answer lane and one bounded context worker, with no unbounded inference queue.

The answer capability is independent of screen reading. For a system-design task it produces a structured diagram, addressed questions/objections, and an explanation. The previous answer retains diagram nodes and edges so follow-ups can revise it coherently. Code continues through source validation, separate line annotations, and host-calculated diffs. A short conversation response can keep the existing artifact. Pinning, text selection, MIDI controls, transparency, resizing, copy, and SVG export are retained.

Help now flushes available audio and requests help from current evidence without waiting for the next OCR/context result. If there is no evidence at all, the first capture supplies it. Following automatically catches up after the current answer finishes. No-op context updates and exact repeated screen readings do not themselves form an answer/context feedback loop.

Pause cancels OCR, context building, and answers. OCR and context errors retain useful state and use a bounded retry delay rather than a rapid retry loop. Capture generations, request IDs, session IDs, task revisions, and memory revisions isolate late results. Background updates never grant filesystem or image-read authority. Session restoration preserves working notes and provenance but strips image paths and requires a fresh read of an explicitly authorized project.

## Checks and limits

The 92-test automated suite passes, including 16 new checks for full five-minute retention across both audio channels, long debug logs, source-backed memory after raw-window expiration, memory addition/revision/removal, observed file retirement, previous diagram retention, independently progressing workers, cancelled context updates, stale answers, image preflight, Help now during OCR/audio, completion-driven screen capture, sharing-dependent waits, restoration before OCR, and failure/cancellation/late-callback handling. Native smoke checks cover code, conversation, diagrams, resize, selection, copy, click-through, MIDI, sharing controls, settings, and session restoration. These are functional checks, not model-quality measurements.

`tests/live_continuous.py` runs an explicit finite live check using a rendered requirements image and generated speech. Run `continuous-live-20260909-161355` used real Astra OCR, local Parakeet transcription, source-bound memory updates, and two rounds of quick/deep design assistance. Runtime code was unchanged during the run. The private report preserves inputs, raw/validated generations, memory deltas, diagrams, and timings.

| Stage | Measured duration |
|---|---:|
| Full-resolution synthetic screen, redaction and Astra OCR | 10.7 s |
| Initial quick answer after perception | 3.9 s |
| Initial diagram after perception | 31.7 s |
| Follow-up quick answer | 4.5 s |
| Revised diagram | 29.4 s |

Assistant review: the first answer produced a ten-node webhook system and challenged immediate retries. The spoken follow-up requested stopping after five failed attempts and dead-lettering the event. The next answer retained the artifact ID and all ten node IDs, implemented an explicit five-attempt boundary, and preserved per-tenant ordering by pausing the affected tenant. Memory retained the other speaker's suggestion as a question rather than a user decision. Neither answer had withheld artifacts or delivery repairs. Parakeet rendered “queue” as “Q” in the follow-up; the intended requirement remained understandable.

These are single examples using generated screen/audio, not physical-microphone or desktop-permission tests, long-session/quota tests, or evidence of unseen-task accuracy. Judgments are assistant-authored and await human review. Sparse synthetic-screen latency does not replace the earlier dense IDE timings.

Visual review found overlapping long edge labels in the existing complex-diagram renderer. The owner chose to review the SVGs before further diagram-format changes; the renderer remains unchanged. The saved diagrams are available for that review, and structural validity should not be mistaken for polished layout.

The additional native live code check (`otsc-live-workflow-upg2nen_`, generated screen/speech) passed with quick help observed at 4.4 seconds and the deeper code state at 32.9 seconds after Help now. It exercised actual Astra OCR, local ASR, the AppKit controller, annotated code, clean copy, and retaining the artifact through a short follow-up. These are observed UI timings; a fresh OCR can trigger another answer before the harness samples the displayed state.

The previous attempt (`otsc-live-workflow-8ewtnl0q`) remains preserved as failed. Its assertion examined only the latest response and demanded that an already-addressed participant question be answered again. The corrected check saves the delivered history and requires an answer/challenge citing the actual other-speaker observation somewhere in that delivered conversation. This changes the evaluator, not the generated code or conversation behavior.

Tesseract remains a best-effort local redaction step and may miss sensitive text. Astra can omit or misread text; structured output validation does not establish visual accuracy. Continuous remote OCR and memory building use additional Codex allowance independently of the hourly answer-request limit.
