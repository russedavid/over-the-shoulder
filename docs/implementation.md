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
| Synthetic interaction replay | `otsc/demo.py` |

Codex runs `exec` against an ephemeral, filtered source snapshot, with read-only sandboxing, no approvals, no user config/rules, and no web search. It reuses CLI authentication. Responses API, Gemini `streamGenerateContent`, Anthropic Messages with a forced structured tool response, and Groq/compatible Chat Completions share the application contract. HTTP failures are surfaced without automatic provider fallback. No extra engine process or app-server migration was introduced.

The Codex read-only sandbox is not a claim of complete filesystem read isolation. The supplied prompt prohibits external reads and execution; the host provides only the filtered snapshot and never applies edits to the real project. Stronger OS-level read isolation and a production threat review would be separate hardening work.

## Capture and bounded work

Screen capture reuses PyAutoGUI and Tesseract. The window hides briefly for capture and returns while OCR runs off the main thread. Text and a coarse image fingerprint gate repeated requests. Recognized credential rows are redacted in OCR and optional vision frames. The most recent 12 frames are retained during the session and removed on normal exit; abrupt process termination may leave temporary files.

Microphone capture uses sounddevice; system audio uses ScreenCaptureKit and excludes the current process's audio. Channels are transcribed separately in bounded worker queues. Transcription can use a local MLX recognizer or configured Groq/OpenAI ASR. Temporary audio is removed after local transcription; cloud ASR uses an in-memory WAV. The system does not claim biometric identification, individual diarization of every remote participant, or echo cancellation.

Context retains up to 100 observations, a bounded set of observed fragments, prior artifacts, unresolved questions, and at most 30 filtered source files / 180 KB in the selected project snapshot. Snapshot limits are visible as an omitted-file count. Two fixed daemon workers replace unbounded inference threads. New incoming context queues during a response; Help now supersedes it explicitly. Long-running Codex work has a 240-second deadline.

## What has been verified

Automated tests exercise source provenance, code annotation coverage, same-screen gating, revision isolation, quick/deep ordering, pin staleness, HTTP streaming adapters and errors, credential-free settings, source filtering, exact diff application in temporary fixtures, observed excerpt offsets, WAV construction, separate transcription credentials, native audio-buffer decoding, and real Tesseract OCR on a synthetic image.

The native smoke test creates an AppKit window using synthetic responses, resizes it, checks selection and clean copy, toggles click-through, reads collaborator replies, selects an excerpt diff, checks queued audio, draws a diagram, and constructs Settings. View renders and reports are written outside the repository. These tests use no paid model calls or live desktop/audio recording.

## Still requires live acceptance

- First-run macOS permissions and actual microphone/system-audio routing on the user's setup, including echo and overlapping speech.
- Live API/Codex account authentication, model-specific structured-output support, latency, quotas, and quality. Adapters have mocked transport coverage; this is not a live provider certification.
- Optional local ASR model download and transcription on actual audio. Its large optional dependencies were not installed as part of the default setup.
- More realistic screen/OCR inputs, dense code views, screenshots containing diagrams without much text, and long collaborative tasks. The current frame gate is a conservative heuristic, not a learned task-change detector.

The owner has deferred `.app` packaging. The Python entry point and `.command` launchers are the current run paths; distribution work is outside the active scope.

No public portfolio release or push was performed. Clean publication of the old capture-bearing history remains a separate release task.

## API references used during implementation

- [OpenAI Responses and structured output](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Codex noninteractive execution](https://developers.openai.com/codex/noninteractive)
- [Gemini content generation](https://ai.google.dev/api/generate-content)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)
- [Groq API reference](https://console.groq.com/docs/api-reference)
- [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit/capturing-screen-content-in-macos)
