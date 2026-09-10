# Implementation status — September 10, 2026

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
| Five-minute observations, partial filesystem, remembered artifacts/questions | `otsc/context.py` |
| Full-resolution Astra low/Fast screen reading | `otsc/perception.py` |
| Background source-linked memory and observed workspace updates | `otsc/context_builder.py` |
| Frozen output snapshots and Older/Newer/Latest navigation | `otsc/output_history.py` |
| Two bounded response workers and revision checks | `otsc/scheduler.py` |
| Streaming HTTP providers | `otsc/providers.py` |
| Existing Codex CLI integration | `otsc/codex.py` |
| Read-only project selection and host-calculated diffs | `otsc/workspace.py` |
| Screen OCR, vision frames, microphone/system audio, ASR | `otsc/capture.py`, `otsc/audio_tap.py` |
| Legacy native diagrams and SVG | `otsc/diagram.py` |
| Channel-1 keypad bindings and controller reconnection | `otsc/midi.py` |
| Synthetic interaction replay | `otsc/demo.py` |
| Explicit task details and private, opt-in checkpoints | `otsc/task_details.py`, `otsc/sessions.py` |
| Optional bounded model-directed snapshot inspection | `otsc/inspection.py` |
| Operational metadata, recovery checks, source review export | `otsc/telemetry.py`, `otsc/operations.py`, `otsc/release.py` |
| Synthetic task corpus, behavioral checks, provisional semantic review | `evals/` |

Codex runs `exec` against an ephemeral, filtered source snapshot, with read-only sandboxing, no approvals, no user config/rules, and no web search. It reuses CLI authentication. Responses API, Gemini `streamGenerateContent`, Anthropic Messages with a forced structured tool response, and Groq/compatible Chat Completions share the application contract. HTTP failures are surfaced without automatic provider fallback. No extra engine process or app-server migration was introduced.

The Codex adapter disables model-visible shell/executor, browser, image-read, app/connector, memory, and multi-agent capabilities, ignores user configuration/rules, and aborts unexpected tool activity. Inherited permission/session environment variables and environment API keys are removed from the child process. Optional app-level inspection tools read only the immutable supplied snapshot. The trusted CLI process retains its own authentication/runtime access; this is not complete OS isolation of a malicious CLI binary. See the [boundary review](threat-model.md).

## Capture and bounded work

Click-through now follows the prototype's background-only transparency: window alpha remains 1.0, the window background is clear, the root backdrop is 5% opaque, and text/scroll-view backgrounds and control bezels are removed temporarily. Text and diagram content retain their normal opacity. Toggling off or using the Dock icon restores the original appearance without replacing text or artifacts. Native tests inspect rendered background/text alpha and cover diagram transparency and Dock restoration. This corrects the earlier whole-window 5% fade, which incorrectly faded the output text too.

Screen capture uses PyAutoGUI, a local Tesseract redaction pass, and full-resolution Astra low/Fast visual reading. Tesseract text is not used as task OCR. Window sharing starts off (`NSWindowSharingNone`), with no capture hide/show or artificial wait. The Sharing button can enable sharing; only then does a visible window hide with a 10 ms preparation delay. The screenshot-completion callback dispatches restoration directly to AppKit before OCR, without waiting for the 100 ms event poll. Failure and cancellation also restore the window, while explicit user hiding is respected. A completed reading schedules the next fresh screenshot immediately during following. The most recent 12 redacted frames are retained during the session and removed on normal exit; abrupt termination may leave temporary files. Errors back off and Pause cancels in-flight work.

Microphone capture uses sounddevice; system audio now defaults to an audio-only Core Audio process tap with a private aggregate, native PCM decoding, and resampling to 16 kHz mono. It does not create a screen/display stream. ScreenCaptureKit remains an explicitly selected legacy backend. Channels are transcribed separately in bounded worker queues. Transcription can use a local MLX recognizer or configured Groq/OpenAI ASR. Temporary audio is removed after local transcription; cloud ASR uses an in-memory WAV. The system does not claim biometric identification, individual diarization of every remote participant, or echo cancellation.

Context retains five minutes of screen/audio observations (up to 1,000 entries), typed context, source evidence for accumulated memory, observed fragments, prior answers/artifacts, unresolved questions, and at most 30 filtered source files / 180 KB in the selected project snapshot. The source-text snapshot budget is 800 KB with explicit omitted IDs; the former 24 KB budget is gone. A separate bounded context worker maintains up to 80 source-linked notes while two fixed answer workers consume immutable snapshots. New passive evidence enters immediately without invalidating an in-flight answer. Explicit task/project edits and newer requests still supersede old work. Long-running Codex work has a 240-second deadline.

The [task-planning report](task-planning.md) records dynamic output contracts, ongoing plan-fit decisions, generated images, and the audio-only capture migration. Its live examples include plan reuse, a coding-to-design pivot, and an unfamiliar nested JSON deliverable. Native checks cover PNG/JSON presentation, frozen image completion, resize/scroll, the channel-1 keypad, Older/Newer/Latest controls, and checkpoint restoration without rolling live memory back. The earlier [continuous workflow report](continuous-context.md) records the capture/context changes. Later sections below preserve earlier checks and their original models and limits; they are not reruns of the new architecture.

The later [automatic refresh change](automatic-refresh.md) suppresses answer generation when new OCR/audio evidence adds nothing substantive. Memory-only revisions do not trigger a review; the existing planner can retain the prior answer, and a final content comparison prevents citation/Plan-metadata churn from creating duplicate history. Explicit Help now bypasses this gate. Seven live synthetic judgment checks covered both harmless repetition and consequential small changes.

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

The current local preferences use Codex Spark at low reasoning and GPT-6 Astra at medium reasoning with Fast processing, local transcription, and separate microphone/system channels. Capture remains paused at startup. No API keys were copied into the application or committed. The earlier GPT-5.5 results above retain their original model identity; the [Astra comparisons](astra-processing-comparison.md) record the later model update and controlled comparisons of processing and reasoning settings on one recorded case. The expanded suite has 76 passing tests; native checks cover saving the Fast preference and clearing it when switching away from Codex.

After the 0.2 additions, the explicitly rendered-fixture live test passed with quick/deep output at 5.7/15.3 seconds. It used real OCR, local ASR, vision and Codex calls, and native rendering; desktop capture was marked false because macOS reported no active display. The earlier hardware and active-display checks above remain distinct evidence.

MIDI was initially restored from the baseline mappings, then remapped to the owner's Keychron K0 Max layout. Channel-1 notes 36/37 turn backward/forward through output history without debounce, and Enter 60 returns to live output. The 8/4/5/6 arrow cluster moves the window, -/+ resize it, and M1–M5 select views. Tests exercise the note-to-handler path, rapid knob turns, channel filtering, bounds, reconnection and shutdown. Native smoke checks inject those MIDI messages through the receiver and AppKit controller without opening capture hardware or calling live models. The keypad's input port was detected; these software checks do not certify a physical press of every key. Standalone voice completion still stops hardware capture before waiting for transcription.

## Practical limits

Other HTTP providers have mocked protocol coverage; their accounts, model support, and quotas were not certified by these Codex runs. Echo, overlapping remote speakers, long sessions, and dense multi-window OCR still need broader field evaluation. The frame gate remains a heuristic. The local ASR installation was exercised on this Apple Silicon Mac; it is an optional dependency for other setups.

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
