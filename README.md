# Over The Shoulder Coder

A Mac desktop collaborator that follows the task you are working on and helps create its code, design, or explanation. It uses the screen and surrounding conversation as context, including other people's questions and objections.

The working application uses **Python, PyObjC, and AppKit**. It has one task workflow, a quick answer and an independent deeper response, selectable text, annotated code, proposed diffs, native diagrams, and optional click-through presentation. There is no separate UI framework, engine service, or IPC layer.

## Run it

macOS 13 or later, Python 3.13, and [uv](https://docs.astral.sh/uv/). Install Tesseract (`brew install tesseract`) for the local credential-redaction pass. The screen text used for assistance is read by Astra, not that local pass.

```sh
uv sync --python 3.13 --extra mac
python ots.py
```

`ots.py` uses this checkout's `.venv` automatically, so there is no activation step. The former `test.py` and `lib/main.py` entry points launch the same application without starting anything when imported.

To try the interaction without credentials or capture:

```sh
python ots.py --demo
python ots.py --design-demo
```

These are explicitly synthetic examples. The app menu can load either example; they exercise the same response and rendering paths as live assistance. Quit and relaunch normally to leave the synthetic demo.

## Configure once, then work

Settings lets you choose separate quick and deep models: Codex CLI, OpenAI, Google Gemini, Anthropic, Groq, or an OpenAI-compatible endpoint. The defaults use GPT-5.3 Codex Spark at low reasoning for quick replies and GPT-6 Astra at medium reasoning with Fast processing for deeper work, through the existing Codex sign-in. The **Codex Fast** checkbox saves processing speed separately from reasoning effort. Model names remain editable. API keys are saved in macOS Keychain; a blank key field preserves an existing key. Standard provider environment variables are also supported.

Continuous screen reading uses **GPT-6 Astra, low reasoning, Fast processing**, at full screenshot resolution. A separate Astra low/Fast worker maintains accumulated context and the observed workspace. These workers use the existing Codex sign-in; the `ocr` and `context_builder` preferences are saved separately from the answer models.

1. Describe the task, or let the captured work establish it. You can add typed context as yourself, another person, an uncertain speaker, or a screen/code excerpt.
2. **Capture now** reads one screen with Astra. **Help now** (Cmd-Return) finishes the current speech chunk and asks from the latest available context while a fresh screen reading runs. If there is no context yet, it waits for that first reading. **Start following** continuously takes a fresh screenshot as soon as the previous OCR call finishes, alongside audio capture and transcription.
3. Choose an artifact, read the conversation response, or inspect the observed files. **Copy clean** omits teaching notes; **Copy explained** includes them. Diagrams export as SVG and code changes as diffs.
4. **Pin** protects work you are reading. Selecting output text also holds incoming replacements. Cmd-Shift-I toggles click-through at 5% opacity; clicking the app's Dock icon restores interaction and full opacity.
5. **Sharing: Off** is the default on every launch. The window stays visible while screenshots are taken. **Sharing: On** allows window sharing; for the app's own screenshots it hides the window, waits 10 ms, captures, and restores it before OCR. The 10 ms is the preparation delay, in addition to the screenshot's actual duration.

Capture starts only from an explicit control. Screen, microphone, and system audio can be enabled separately. macOS may request Screen & System Audio Recording and Microphone permissions for the launcher/Python application. After changing permissions, relaunch if capture still fails.

Microphone and system audio remain separate channels with configurable speaker roles. These are attribution hints, not voice identification; headphones reduce the chance of remote speech also entering your microphone. Choose Groq/OpenAI transcription with its own credential, or install the optional local recognizer:

```sh
uv sync --python 3.13 --extra mac --extra local-asr
```

Local transcription loads its model on first use and may download model weights. No model download or audio transcription starts during ordinary app launch.

## MIDI controls

The original note numbers are supported through CoreMIDI. The app connects to the first input and reconnects when a controller is plugged in. Only note-on presses with nonzero velocity trigger actions; the original 150 ms debounce is retained. Set `OTSC_MIDI_PORT` to an exact device name if you need a particular input.

| Note | Action |
|---|---|
| 38 / 39 | Start a voice question; press either again to finish and request help. Task context is retained. |
| 40 / 41 | Make the window smaller / bigger in 50-pixel steps. |
| 42 / 44 / 45 / 46 | Move left / down / up / right by 50 pixels. |
| 43 | Hide / show the window. Capture updates respect the hidden state. |
| 47 | Help now. |
| 48 | Cycle output views, replacing the former task-mode switch. |
| 49 | Advance a page; at the end, advance to the next artifact or wrap. |
| 50 | Capture the current screen. |
| 51 | Clear and start a new task. |

Voice controls use the configured audio inputs. When following continuously, finishing a voice question keeps that capture running; a standalone voice recording closes its inputs when the question ends. MIDI callbacks enqueue actions for the AppKit thread and do not touch the window from the MIDI thread.

## Context and proposed changes

Observed files are bounded, versioned **fragments**, with source observations and known line positions. A spoken description cannot become an observed file. When an example replaces a visible excerpt, the app shows an excerpt diff and preserves its partial status.

**Project…** explicitly connects a folder. The app reads a bounded selection of text source files, respects Git ignore rules, skips symlinks, binary files, common credential files, and detected secrets. The selected snapshot may be sent to the configured model. Verified diffs are calculated by the app against the exact file contents in that snapshot. The app does not apply edits to your actual project.

Quick and deep work begin from the same immutable snapshot: the last five minutes of screen readings and separately attributed transcripts, accumulated context, the observed/selected workspace, and the previous substantive answer. The background context builder adds, revises, or retires source-linked working notes and observed fragments. It cannot edit your project or change user-confirmed constraints and decisions.

Capture, context building, and answers progress independently. Incoming evidence is available immediately for the next snapshot and does not cancel a useful answer already in progress. A newer request or explicit task/project change still supersedes old work; a late quick response cannot replace deep output. Exact repeated readings refresh the timeline without requesting an identical answer. Help now uses available context without waiting behind an OCR or context-building call. Resizing and copying never invoke a model.

Diagrams remain first-class artifacts. A system-design request produces nodes and connections that the app draws and exports as SVG; follow-ups can revise the existing diagram using its stable identity. Code retains line-by-line teaching notes and clean copy. The five-minute stream and accumulated memory supply evidence for any of these outputs.

Quick replies answer the pressing question without generating a second code proposal. Existing artifacts stay visible during those replies; deeper work supplies the annotated code, diff, or drawing. Editor line numbers are separated from source code during OCR, and indentation changes count as meaningful changes.

Optional file-cache updates are validated separately from the answer. Unconfirmed entries are excluded without discarding useful assistance or creating a misleading diff. Code with missing or misaligned explanations receives at most one bounded annotation-only repair; the code itself is preserved. See the [validation fix and replay results](docs/validation-fix.md).

The hourly assistance limit counts answer requests, not OCR, context-building, transcription calls, or dollars. Continuous following makes ongoing OCR and context calls as well as answers. One successful OCR call starts the next immediately; failed OCR/context calls back off, and Pause cancels in-flight work. A request normally uses quick/deep models and can add one annotation-repair call when needed. Provider quotas still apply. Failed requests retain existing work; the app does not silently switch providers.

## Keep and inspect a task

The app menu adds **Task details…** for constraints and confirmed decisions, **Save task session**, and **Open task session…**. **Remember sessions locally** enables automatic checkpoints; it is off by default. Checkpoints preserve task text, proposals, and history without raw audio, images, credentials, or a running capture state. Restoring a task pauses capture and keeps saved proposals distinct from freshly verified source.

**Inspect context before deep responses** is optional and off by default. A single model can choose a few read-only operations on the already supplied snapshot before producing the normal deep response. The app enforces three inspection steps and a 30-second inspection budget. See the [operating guide](docs/operating-guide.md) for controls, storage, recovery, and deletion.

Local diagnostics record bounded operational metadata: versions, request timing, token usage when supplied, errors, stale responses, and explicit useful/needs-work feedback. Captured content and proposals are excluded from this diagnostic stream. It does not estimate dollar costs from missing pricing data.

```sh
python ots.py ops --output /tmp/otsc-operations.html
python ots.py ops --recovery-exercise --output /tmp/otsc-recovery
```

## Verification and implementation

```sh
uv run --extra mac python -m unittest discover -s tests -v
uvx ruff check ots.py otsc evals tests
python ots.py --smoke-test /tmp/otsc-smoke
python ots.py eval
```

Core tests can run outside macOS with `uv sync --extra test`. The native smoke test exercises synthetic quick/deep responses, annotations, clean copy, collaborator replies, excerpt diffs, resizing, selection, click-through, audio queuing, diagrams, and settings construction. It renders only the app's own views under `/tmp`; it does not capture the desktop. Test reports distinguish these checks from live provider, permission, and acoustic testing.

`python ots.py eval` validates the 28-case synthetic corpus without inference. Explicit live evaluations produce a local HTML report containing inputs, responses, checks, and case-specific critiques:

```sh
python ots.py eval --live --split development --output /tmp/otsc-development
python ots.py eval --live --split holdout --output /tmp/otsc-holdout
```

The [evaluation protocol](evals/protocol.md) follows the task-specific error-analysis approach of Hamel Husain and Shreya Shankar. Reference examples and model judgments are assistant-authored and provisional; they are not human calibration or an estimate of field accuracy. The portable CI workflow runs offline checks only.

The [recorded-input evaluation](evals/recordings-protocol.md) covers the archived audio and screenshots with new ASR, OCR, visual interpretation, and current-system quick/deep replays. Its local review UI links each output to its source media and keeps manual verdicts, reference approvals, and corrections separate. Launch it with `.venv/bin/python -m evals.recording_review --port 8767` after preparing the private dataset. Raw recordings and generated evaluation data remain excluded from Git.

An explicit live run is available with `.venv/bin/python tests/live_workflow.py`. It uses a staged code window, locally generated speech, the real local recognizer, and real Codex requests, then verifies the result in the AppKit window. It requires the local-ASR extra, cached speech weights, Codex sign-in, and screen-recording permission. It is not part of the ordinary offline test suite.

`.venv/bin/python tests/live_continuous.py` runs a finite design-and-revision example through full-resolution Astra OCR, actual local speech transcription, the background context builder, and quick/deep answers. Its screen and speech are generated fixtures; it does not open capture hardware. The private report includes both diagrams, exact generation inputs/outputs, memory deltas, audio, and timings. See [continuous context and visual reading](docs/continuous-context.md).

If macOS has no active display, use `.venv/bin/python tests/live_workflow.py --rendered-screen`. This explicitly renders the owned code fixture into an image before running the real OCR, vision, speech-recognition, and model paths. Its report marks desktop capture as untested; it is not an acoustic hardware test.

- [Current implementation and verification boundaries](docs/implementation.md)
- [Evaluation findings, including disputed judgments](docs/evaluation-results.md)
- [Recorded-input findings and review limits](docs/recorded-evaluation-findings.md)
- [Astra medium: Standard/Fast comparison and current models](docs/astra-processing-comparison.md)
- [Interactive walkthrough](docs/portfolio/index.html) and [technical case study](docs/portfolio/case-study.md)
- [Information and action boundaries](docs/threat-model.md)
- [Response contract](docs/assistance-contract.md)
- [Development review and follow-up cases](docs/development-review.md)
- [Product plan](docs/over-the-shoulder-coder-plan.md)
- [Pre-overhaul audit](docs/current-system-audit.md)

Preferences and temporary redacted vision frames use `~/Library/Application Support/Over The Shoulder Coder`; credentials use Keychain. Raw screenshot and cloud-transcription audio files are not persisted. Local ASR and Codex use temporary directories removed after each operation. OCR redaction is a best-effort filter, not a guarantee that every secret in an image is recognizable. Enable vision only for screen content you intend to send to that provider.

Legacy source was preserved in commit `c6f644a` before replacement. Local recordings and screenshots remain excluded from Git. Historical capture-bearing commits have not been rewritten or published as part of this work.

`python ots.py export-source --output /tmp/otsc-source.zip` prepares a filtered source archive for review without Git history. It does not publish or package the application.
