# Implementation status

## October 12, 2026 latency changes

Quick replies now make an answer-or-no-change decision independently of deep work. Each lane has one bounded job and its own evidence watermark; incoming evidence is coalesced while that lane is busy. A newer quick question can finish before an older deep job, and that older job cannot replace the newer reply. Manual Help now supersedes both jobs. Pinned views, source checks, code annotations, and per-type history remain in place.

Codex uses persistent local stdio app-server connections, with new ephemeral threads for each request and streamed quick text. There is no listening network port, copied authentication token, or shared conversation history between tasks. Final validation still precedes history publication. Per-model `codex_transport: "exec"` retains the former transport for compatibility; it is not a silent fallback. The Settings checkbox for combined deep output is on by default. Turn it off to use the separate planner.

The combined deep call reviews the existing plan and generates named output values in one response. Arbitrary JSON schemas, code, image briefs and text are decoded and checked by the host. An invalid section is isolated without discarding valid siblings. Exact screenshot deduplication skips local OCR/VLM work only after the matching frame has an accepted reading. A one-pixel or region change still gets read. Identical frames refresh the existing observation's recency without advancing the evidence revision; failed/cancelled reads retry, and explicit capture bypasses the cache.

Audio is checked every 100 ms and flushed after a short pause, with a bounded maximum of five seconds by default. Microphone and system audio retain separate buffers and attribution. `Flush at pauses` can be disabled to retain fixed batching, and `Max audio (s)` remains editable. Rigid two-second cuts were rejected after they hallucinated part of a synthetic sentence. The pause-aware test delivered a 3.37-second question at 3.6 seconds with exact text; a longer utterance retained its meaning but repeated one boundary word. This is not an acoustic accuracy benchmark.

### Measured comparison

Seconds from available synthetic evidence to accepted output; preview is provisional text, not a committed artifact. The same eight scenarios and model choices were used. The first comparison alternated baseline/new order. A discovered task-change error was corrected and the final new path reran all eight cases. Both run identities and original failures are retained outside Git.

| Scenario | Old quick | New preview | New quick | Old deep | New deep |
|---|---:|---:|---:|---:|---:|
| first answer | 6.66 | 3.96 | 4.84 | 31.81 | 20.39 |
| new question | 20.25 | 2.52 | 4.18 | 30.25 | 11.94 |
| small constraint | 21.59 | 3.18 | 4.29 | 32.94 | 20.33 |
| new contract | 21.54 | 2.27 | 3.08 | 24.92 | 15.43 |

Across the four required-answer scenarios, median completed quick latency fell from 20.89 to 4.23 seconds; median deep latency fell from 31.03 to 17.88 seconds. All four quiet scenarios produced zero additional history entries. Both code scenarios passed the existing five restricted-AST cases; the task-change case returned the exact requested JSON. The worked example was reviewed by the assistant and correctly answered the other participant with $10.

A separate busy-deep probe delivered a newly arriving question in 3.94 seconds while the older deep job continued. The newer quick guidance remained intact after the old job completed. A canary placed outside the supplied snapshot was not disclosed. Native checks verify provisional updates, frozen output, error recovery, settings, transparency, capture-frame geometry and MIDI navigation.

Two paired OCR trials per transport on one generated screenshot preserved the boundary operator, logs and line-number mapping. Exec took 15.41/14.70 seconds; app-server took 15.12/12.13 seconds. An earlier concurrent probe took 20.15 seconds, illustrating variability. The principal capture improvement is avoiding identical-frame calls; do not treat these few timings as a general OCR speed guarantee.

Private run identities: `latency-implementation-20261012` (original paired comparison) and `latency-implementation-fixed-20261012` (final answer-path rerun, preserved source snapshot, separate OCR/audio/busy-worker checks and review page). The answer-path code remained unchanged during its final run; subsequent audio endpointing and JSON-only HTTP contract support were tested separately. Provider load, cache hits and output lengths vary. These are assistant-authored synthetic development checks, not human-calibrated field accuracy.

```sh
.venv/bin/python -m evals.latency --live --output /tmp/otsc-latency-new-run
```

This requires existing configured providers, makes live calls on synthetic inputs, and refuses to overwrite a previous output directory. Ordinary unit tests and `python ots.py eval` remain offline. Final verification: 230 unit tests, Ruff, offline corpus checks, and native UI smoke checks passed.


## October 11, 2026 updates

Quick replies use GPT-6 Luna with `none` reasoning and Fast processing. Saved Codex Spark quick settings migrate automatically; the Astra workers retain their separate settings. See the [replacement checks](quick-model-replacement.md).

The Capture window control edits a saved global screen rectangle using a transparent native panel. Visible edges and handles stay outside the captured interior. Closing the frame retains the area; the app menu can reset to the primary display. Geometry uses the primary display’s top edge, never the active display’s height, and keeps signed coordinates for screens above or left of the primary. Screenshots retain the one-shot PyAutoGUI/Pillow path. A default capture now specifies the primary rectangle explicitly, preventing multi-display screenshots from leaving extra raw temporary files.

Native checks exercised frame movement and resizing on both attached displays, transparent interior pixels, persistence, screenshot exclusion, and the narrow-window toolbar. Separate live checks captured only owned color fixtures on each display and across the shared display boundary; their pixels matched the intended regions. There are no new capture streams, screenshot-hide delays, or model calls from moving the frame.

## Earlier implementation and verification

The overhaul is implemented as one Python/PyObjC/AppKit application. The pre-overhaul source remains in Git history. The old mode-specific entry points, prompts, static personal/interview material, and fixed-column renderers were retired; `test.py` and `lib/main.py` are import-safe compatibility launchers.

## Modules

| Responsibility | Module |
|---|---|
| Window, task controls, retained output, automatic cadence | `otsc/app.py`, `otsc/native.py` |
| Startup configuration and Keychain credentials | `otsc/preferences.py`, `otsc/settings.py` |
| Task-specific planning, ongoing plan review, dynamic output contracts | `otsc/planning.py` |
| Shared storage envelope and quick-response instructions | `otsc/models.py`, `otsc/prompts.py` |
| Generated PNGs, image revisions, bounded rendering worker | `otsc/images.py` |
| Independent component validation and bounded annotation repair | `otsc/delivery.py` |
| Conventional code comments, numbered diff tool and native code view | `otsc/code_rendering.py`, `otsc/diff_rendering.py`, `otsc/code_view.py` |
| Five-minute observations, partial filesystem, remembered artifacts/questions | `otsc/context.py` |
| Full-resolution Astra low/Fast screen reading | `otsc/perception.py` |
| Background source-linked memory and observed workspace updates | `otsc/context_builder.py` |
| Per-type history, cursors, badges, and raw output log | `otsc/output_browser.py`, `otsc/output_history.py` |
| Two bounded response workers and revision checks | `otsc/scheduler.py` |
| Streaming HTTP providers | `otsc/providers.py` |
| Existing Codex CLI integration | `otsc/codex.py` |
| Read-only project selection and host-calculated diffs | `otsc/workspace.py` |
| Screen OCR, vision frames, microphone/system audio, ASR | `otsc/capture.py`, `otsc/audio_tap.py` |
| Legacy native diagrams and SVG | `otsc/diagram.py` |
| Channel-1 keypad bindings and controller reconnection | `otsc/midi.py` |
| Synthetic interaction replay and native UI checks | `otsc/demo.py`, `otsc/native_smoke.py` |
| Explicit task details and private, opt-in checkpoints | `otsc/task_details.py`, `otsc/sessions.py` |
| Optional bounded model-directed snapshot inspection | `otsc/inspection.py` |
| Operational metadata, recovery checks, source review export | `otsc/telemetry.py`, `otsc/operations.py`, `otsc/release.py` |
| Synthetic task corpus, behavioral checks, provisional semantic review | `evals/` |

Codex runs `exec` against an ephemeral, filtered source snapshot, with read-only sandboxing, no approvals, no user config/rules, and no web search. It reuses CLI authentication. Responses API, Gemini `streamGenerateContent`, Anthropic Messages with a forced structured tool response, and Groq/compatible Chat Completions share the application contract. HTTP failures are surfaced without automatic provider fallback. No extra engine process or app-server migration was introduced.

The retained exec adapter disables model-visible shell/executor, browser, image-read, app/connector, memory, and multi-agent capabilities, ignores user configuration/rules, and aborts unexpected tool activity. The default streaming adapter pins those boundaries and clears inherited integrations; see the October 12 update and boundary review. Inherited permission/session environment variables and environment API keys are removed from the child process. Optional app-level inspection tools read only the immutable supplied snapshot. The trusted CLI process retains its own authentication/runtime access; this is not complete OS isolation of a malicious CLI binary. See the [boundary review](threat-model.md).

## Capture and bounded work

Click-through now follows the prototype's background-only transparency: window alpha remains 1.0, the window background is clear, the root backdrop is 5% opaque, and text/scroll-view backgrounds and control bezels are removed temporarily. Text and diagram content retain their normal opacity. Toggling off or using the Dock icon restores the original appearance without replacing text or artifacts. Native tests inspect rendered background/text alpha and cover diagram transparency and Dock restoration. This corrects the earlier whole-window 5% fade, which incorrectly faded the output text too.

Screen capture uses PyAutoGUI, a local Tesseract redaction pass, and full-resolution Astra low/Fast visual reading. Tesseract text is not used as task OCR. Window sharing starts off (`NSWindowSharingNone`), with no capture hide/show or artificial wait. The Sharing button can enable sharing; only then does a visible window hide with a 10 ms preparation delay. The screenshot-completion callback dispatches restoration directly to AppKit before OCR, without waiting for the 100 ms event poll. Failure and cancellation also restore the window, while explicit user hiding is respected. A completed reading schedules the next fresh screenshot immediately during following. The most recent 12 redacted frames are retained during the session and removed on normal exit; abrupt termination may leave temporary files. Errors back off and Pause cancels in-flight work.

Microphone capture uses sounddevice; system audio now defaults to an audio-only Core Audio process tap with a private aggregate, native PCM decoding, and resampling to 16 kHz mono. It does not create a screen/display stream. ScreenCaptureKit remains an explicitly selected legacy backend. Channels are transcribed separately in bounded worker queues. Transcription can use a local MLX recognizer or configured Groq/OpenAI ASR. Temporary audio is removed after local transcription; cloud ASR uses an in-memory WAV. The system does not claim biometric identification, individual diarization of every remote participant, or echo cancellation.

Context retains five minutes of screen/audio observations (up to 1,000 entries), typed context, source evidence for accumulated memory, observed fragments, prior answers/artifacts, unresolved questions, and at most 30 filtered source files / 180 KB in the selected project snapshot. The source-text snapshot budget is 800 KB with explicit omitted IDs; the former 24 KB budget is gone. A separate bounded context worker maintains up to 80 source-linked notes while two fixed answer workers consume immutable snapshots. New passive evidence enters immediately without invalidating an in-flight answer. Explicit task/project edits and newer requests still supersede old work. Long-running Codex work has a 240-second deadline.

The [task-planning report](task-planning.md) records dynamic output contracts, ongoing plan-fit decisions, generated images, and the audio-only capture migration. Its live examples include plan reuse, a coding-to-design pivot, and an unfamiliar nested JSON deliverable. Native checks cover PNG/JSON presentation, frozen image completion, resize/scroll, the channel-1 keypad, Older/Newer/Latest controls, and checkpoint restoration without rolling live memory back. The earlier [continuous workflow report](continuous-context.md) records the capture/context changes. Later sections below preserve earlier checks and their original models and limits; they are not reruns of the new architecture.

The later [automatic refresh change](automatic-refresh.md) suppresses answer generation when new OCR/audio evidence adds nothing substantive. Memory-only revisions do not trigger a review; the existing planner can retain the prior answer, and a final content comparison prevents citation/Plan-metadata churn from creating duplicate history. Explicit Help now bypasses this gate. Seven live synthetic judgment checks covered both harmless repetition and consequential small changes.

The September 11 [output-navigation change](output-navigation.md) makes actionable output the default, replaces dropdowns with a persistent highlighted type list, and adds per-type histories and red newer-version badges. Debug reveals internal planning/context/events. A changed explanation or completed image no longer advances code history. The 155-test suite and native preservation checks pass.

The [code/diff renderer](code-and-diff-rendering.md) replaces bare annotation prose with ordinary language comments. Changes use colored Old/New rows and retain exact Current/Proposed text. The 170-test suite and native checks pass; a live cached-excerpt edit preserved its line-40 origin and passed seven restricted-AST behavior checks.

## What has been verified

Automated tests exercise source provenance, code annotation coverage, same-screen gating, revision isolation, quick/deep ordering, pin staleness, HTTP streaming adapters and errors, credential-free settings, source filtering, exact diff application in temporary fixtures, observed excerpt offsets, WAV construction, separate transcription credentials, native audio-buffer decoding, and real Tesseract OCR on a synthetic image.

The native smoke test creates an AppKit window using synthetic responses, resizes it, checks selection and clean copy, toggles click-through, reads collaborator replies, selects an excerpt diff, checks queued audio, draws a diagram, constructs Settings, and saves/restores exact artifacts and constraints with capture paused. View renders and reports are written outside the repository. These tests use no model calls or live desktop/audio recording.

Version 0.2 adds explicit task constraints/decisions, saved task sessions, metadata-only diagnostics, an optional bounded inspection loop, and a task-specific evaluation harness. Automatic session memory and inspection are off by default. Proposals retain the base hashes from their generation snapshot; loading a session cannot grant access to a new local project. A controlled recovery exercise verifies that provider failure preserves existing work and stale responses cannot replace it.

The expanded behavior suite has 52 passing tests. The native preservation check passes. The [evaluation findings](evaluation-results.md) report the completed development, held-out, and inspection assessments, including evaluator mistakes and the boundaries of their evidence. The CI workflow is configured for portable offline checks; it has not been run remotely.

The later [validation fix](validation-fix.md) recovered all 18 saved deep drafts without changing their code or dropping their artifacts, then delivered all 18 deep responses in a fresh recorded-input replay. Optional file-cache entries are isolated from answer delivery, and code explanations can receive one bounded metadata-only repair. A subsequent boundary audit added malformed-entry isolation and mutation coverage; the expanded suite now has 72 passing tests. The recorded replays use the same source sessions and do not establish performance on unseen tasks. [OCR/VLM tradeoffs](ocr-vlm-tradeoffs.md) remain a separate perception decision.

## Live behavior verified

The Terminal launch has screen-capture and microphone permission. The actual `AudioCapture` class started microphone and system audio together and received frames from both. Ambient audio from that check was neither saved nor transmitted. Separately, a generated question played through system audio was captured by ScreenCaptureKit and correctly recognized by the local Parakeet model.

Local speech recognition is installed and uses the previously cached Parakeet weights. A generated question and reply were recognized correctly; the first transcription took 5.7 seconds including loading, and the next took 0.06 seconds. These are individual short examples, not general performance benchmarks.

`tests/live_workflow.py` runs the actual AppKit controller with a staged source window, local speech transcription through the normal per-channel queues, and real Codex requests. Two completed runs returned quick help in 4.72/5.41 seconds and the deeper annotated code in 14.01/13.71 seconds. Both answered the other participant's suggestion, kept missing data distinct from zero, supplied an observed-excerpt diff, and retained the artifact during a short follow-up. Raw captures, speech, model responses, and timings remain outside the repository.

A separate real design request returned a 16-node/21-edge webhook-delivery diagram and challenged immediate retries with backoff, jitter, throttling, and duplicate protection. A connected-project request returned a host-calculated patch that passed `git apply --check`, while the original selected file remained unchanged.

When malformed status values were explicitly included in that fixture's requirements, its generated retry predicate used the exact 500–599 bounds. The reviewed patch was applied only in a temporary fixture and passed ten behavioral cases. Help now also has a regression check ensuring newly flushed speech arrives before audio readiness is signaled; microphone/system buffers cover the longest configurable chunk.

The final live run, including Help now's fresh screen/audio collection, returned quick help at 4.9 seconds and the deeper artifact at 13.8 seconds. After MIDI restoration, the automated suite has 33 passing tests; the native smoke check also passes.

At the September checkpoint, the local preferences used Codex Spark at low reasoning and GPT-6 Astra at medium reasoning with Fast processing, local transcription, and separate microphone/system channels. Capture remains paused at startup. No API keys were copied into the application or committed. The earlier GPT-5.5 results above retain their original model identity; the [Astra comparisons](astra-processing-comparison.md) record the later model update and controlled comparisons of processing and reasoning settings on one recorded case. The expanded suite has 76 passing tests; native checks cover saving the Fast preference and clearing it when switching away from Codex.

After the 0.2 additions, the explicitly rendered-fixture live test passed with quick/deep output at 5.7/15.3 seconds. It used real OCR, local ASR, vision and Codex calls, and native rendering; desktop capture was marked false because macOS reported no active display. The earlier hardware and active-display checks above remain distinct evidence.

MIDI was initially restored from the baseline mappings, then remapped to the owner's Keychron K0 Max layout. Channel-1 notes 36/37 turn backward/forward through output history without debounce, and Enter 60 returns to live output. The 8/4/5/6 arrow cluster moves the window, -/+ resize it, and M1–M5 select views. Tests exercise the note-to-handler path, rapid knob turns, channel filtering, bounds, reconnection and shutdown. Native smoke checks inject those MIDI messages through the receiver and AppKit controller without opening capture hardware or calling live models. The keypad's input port was detected; these software checks do not certify a physical press of every key. Standalone voice completion still stops hardware capture before waiting for transcription.

## Practical limits

Other HTTP providers have mocked protocol coverage; their accounts, model support, and quotas were not certified by these Codex runs. Echo, overlapping remote speakers, long sessions, and dense multi-window OCR still need broader field evaluation. Exact-frame deduplication is conservative; semantic no-change decisions still depend on the model. The local ASR installation was exercised on this Apple Silicon Mac; it is an optional dependency for other setups.

The owner has deferred `.app` packaging. Run `python ots.py`; it uses the project's environment automatically. Distribution work is outside the active scope.

No public portfolio release or push was performed. Clean publication of the old capture-bearing history remains a separate release task.

## API references used during implementation

- [OpenAI Responses and structured output](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Codex noninteractive execution](https://developers.openai.com/codex/noninteractive)
- [Gemini content generation](https://ai.google.dev/api/generate-content)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)
- [Groq API reference](https://console.groq.com/docs/api-reference)
- [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)
- [Apple Core Audio process taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)
- [OpenAI image generation and editing](https://developers.openai.com/api/docs/guides/image-generation)
