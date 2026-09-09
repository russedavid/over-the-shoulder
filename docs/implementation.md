# Implementation status — September 8, 2026

The overhaul is implemented as one Python/PyObjC/AppKit application. The pre-overhaul source remains in Git history. The old mode-specific entry points, prompts, static personal/interview material, and fixed-column renderers were retired; `test.py` and `lib/main.py` are import-safe compatibility launchers.

## Modules

| Responsibility | Module |
|---|---|
| Window, task controls, retained output, automatic cadence | `otsc/app.py`, `otsc/native.py` |
| Startup configuration and Keychain credentials | `otsc/preferences.py`, `otsc/settings.py` |
| One response schema and task instructions | `otsc/models.py`, `otsc/prompts.py` |
| Observations, partial filesystem, remembered artifacts/questions | `otsc/context.py` |
| Two bounded response workers and revision checks | `otsc/scheduler.py` |
| Streaming HTTP providers | `otsc/providers.py` |
| Existing Codex CLI integration | `otsc/codex.py` |
| Read-only project selection and host-calculated diffs | `otsc/workspace.py` |
| Screen OCR, vision frames, microphone/system audio, ASR | `otsc/capture.py` |
| Native diagrams and SVG | `otsc/diagram.py` |
| Original MIDI bindings and controller reconnection | `otsc/midi.py` |
| Synthetic interaction replay | `otsc/demo.py` |
| Explicit task details and private, opt-in checkpoints | `otsc/task_details.py`, `otsc/sessions.py` |
| Optional bounded model-directed snapshot inspection | `otsc/inspection.py` |
| Operational metadata, recovery checks, source review export | `otsc/telemetry.py`, `otsc/operations.py`, `otsc/release.py` |
| Synthetic task corpus, behavioral checks, provisional semantic review | `evals/` |

Codex runs `exec` against an ephemeral, filtered source snapshot, with read-only sandboxing, no approvals, no user config/rules, and no web search. It reuses CLI authentication. Responses API, Gemini `streamGenerateContent`, Anthropic Messages with a forced structured tool response, and Groq/compatible Chat Completions share the application contract. HTTP failures are surfaced without automatic provider fallback. No extra engine process or app-server migration was introduced.

The Codex adapter disables model-visible shell/executor, browser, image-read, app/connector, memory, and multi-agent capabilities, ignores user configuration/rules, and aborts unexpected tool activity. Inherited permission/session environment variables and environment API keys are removed from the child process. Optional app-level inspection tools read only the immutable supplied snapshot. The trusted CLI process retains its own authentication/runtime access; this is not complete OS isolation of a malicious CLI binary. See the [boundary review](threat-model.md).

## Capture and bounded work

Screen capture reuses PyAutoGUI and Tesseract. The window hides briefly for capture and returns while OCR runs off the main thread. Text and a coarse image fingerprint gate repeated requests. Recognized credential rows are redacted in OCR and optional vision frames. The most recent 12 frames are retained during the session and removed on normal exit; abrupt process termination may leave temporary files.

Microphone capture uses sounddevice; system audio uses ScreenCaptureKit and excludes the current process's audio. Channels are transcribed separately in bounded worker queues. Transcription can use a local MLX recognizer or configured Groq/OpenAI ASR. Temporary audio is removed after local transcription; cloud ASR uses an in-memory WAV. The system does not claim biometric identification, individual diarization of every remote participant, or echo cancellation.

Context retains up to 100 observations, a bounded set of observed fragments, prior artifacts, unresolved questions, and at most 30 filtered source files / 180 KB in the selected project snapshot. Snapshot limits are visible as an omitted-file count. Two fixed daemon workers replace unbounded inference threads. New incoming context queues during a response; Help now supersedes it explicitly. Long-running Codex work has a 240-second deadline.

## What has been verified

Automated tests exercise source provenance, code annotation coverage, same-screen gating, revision isolation, quick/deep ordering, pin staleness, HTTP streaming adapters and errors, credential-free settings, source filtering, exact diff application in temporary fixtures, observed excerpt offsets, WAV construction, separate transcription credentials, native audio-buffer decoding, and real Tesseract OCR on a synthetic image.

The native smoke test creates an AppKit window using synthetic responses, resizes it, checks selection and clean copy, toggles click-through, reads collaborator replies, selects an excerpt diff, checks queued audio, draws a diagram, constructs Settings, and saves/restores exact artifacts and constraints with capture paused. View renders and reports are written outside the repository. These tests use no model calls or live desktop/audio recording.

Version 0.2 adds explicit task constraints/decisions, saved task sessions, metadata-only diagnostics, an optional bounded inspection loop, and a task-specific evaluation harness. Automatic session memory and inspection are off by default. Proposals retain the base hashes from their generation snapshot; loading a session cannot grant access to a new local project. A controlled recovery exercise verifies that provider failure preserves existing work and stale responses cannot replace it.

## Live behavior verified

The Terminal launch has screen-capture and microphone permission. The actual `AudioCapture` class started microphone and system audio together and received frames from both. Ambient audio from that check was neither saved nor transmitted. Separately, a generated question played through system audio was captured by ScreenCaptureKit and correctly recognized by the local Parakeet model.

Local speech recognition is installed and uses the previously cached Parakeet weights. A generated question and reply were recognized correctly; the first transcription took 5.7 seconds including loading, and the next took 0.06 seconds. These are individual short examples, not general performance benchmarks.

`tests/live_workflow.py` runs the actual AppKit controller with a staged source window, local speech transcription through the normal per-channel queues, and real Codex requests. Two completed runs returned quick help in 4.72/5.41 seconds and the deeper annotated code in 14.01/13.71 seconds. Both answered the other participant's suggestion, kept missing data distinct from zero, supplied an observed-excerpt diff, and retained the artifact during a short follow-up. Raw captures, speech, model responses, and timings remain outside the repository.

A separate real design request returned a 16-node/21-edge webhook-delivery diagram and challenged immediate retries with backoff, jitter, throttling, and duplicate protection. A connected-project request returned a host-calculated patch that passed `git apply --check`, while the original selected file remained unchanged.

When malformed status values were explicitly included in that fixture's requirements, its generated retry predicate used the exact 500–599 bounds. The reviewed patch was applied only in a temporary fixture and passed ten behavioral cases. Help now also has a regression check ensuring newly flushed speech arrives before audio readiness is signaled; microphone/system buffers cover the longest configurable chunk.

The final live run, including Help now's fresh screen/audio collection, returned quick help at 4.9 seconds and the deeper artifact at 13.8 seconds. After MIDI restoration, the automated suite has 33 passing tests; the native smoke check also passes.

The current local preferences use Codex Spark / GPT-5.5, local transcription, and separate microphone/system channels. Capture remains paused at startup. No API keys were copied into the application or committed.

MIDI support has been restored from the baseline mappings. CoreMIDI delivery was verified with a virtual destination; no physical controller was connected during that check. Tests cover note filtering, debounce, disconnect/reconnect, and shutdown. Native window checks cover MIDI movement, resize, and hide/show without discarding content. Standalone voice completion stops hardware capture before waiting for transcription.

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
