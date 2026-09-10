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

Before each deep answer, a separate planning pass chooses the task's approach, instructions, and named output sections. It reviews the existing plan against the latest evidence: minor clarifications keep the contract, while a different deliverable can change it. Select **Plan** in the artifact menu to read the decision and reason. Plans can define unfamiliar JSON structures as well as text, annotated code, and generated images. Planning and image models are editable in the lower part of Settings.

1. Describe the task, or let the captured work establish it. You can add typed context as yourself, another person, an uncertain speaker, or a screen/code excerpt.
2. **Capture now** reads one screen with Astra. **Help now** (Cmd-Return) finishes the current speech chunk and asks from the latest available context while a fresh screen reading runs. If there is no context yet, it waits for that first reading. **Start following** continuously takes a fresh screenshot as soon as the previous OCR call finishes, alongside audio capture and transcription.
3. Choose an artifact, read the conversation response, or inspect the observed files. **Copy clean** omits teaching notes; **Copy explained** includes them. Generated images copy/export as PNG, structured sections as JSON, and code changes as diffs. Images render in the background after the text arrives; unchanged image briefs reuse their pending or completed image. Old saved diagrams and the synthetic design demo retain SVG support.
4. Selecting an artifact or **History** freezes the displayed output. **Older** and **Newer** browse saved outputs and keep the pane frozen—even at the newest entry. **Latest** jumps to the newest result and resumes live updates. **Pin** and selecting output text also hold the pane. Cmd-Shift-I toggles click-through with transparent backgrounds and fully opaque text. Clicking the Dock icon restores interaction and the normal backgrounds.
5. **Sharing: Off** is the default on every launch. The window stays visible while screenshots are taken. **Sharing: On** allows window sharing; for the app's own screenshots it hides the window, waits 10 ms, captures, and restores it before OCR. The 10 ms is the preparation delay, in addition to the screenshot's actual duration.

Capture starts only from an explicit control. Screen, microphone, and system audio can be enabled separately. macOS may request Screen & System Audio Recording and Microphone permissions for the launcher/Python application. After changing permissions, relaunch if capture still fails.

System audio now defaults to an **audio-only Core Audio process tap** on macOS 14.2+. It does not create a display or ScreenCaptureKit stream. Screenshots retain the original one-shot PyAutoGUI/Pillow path. macOS can still show its normal recording/privacy indicators. The `screencapturekit` option in Settings preserves the legacy system-audio backend for older Macs; it can activate a screen-sharing indicator.

Image generation uses the configured OpenAI image API account, separately from Codex subscription access. It is called only when the task plan requests a new or revised image. Provider failures stay visible without discarding usable text. See [task planning and image checks](docs/task-planning.md) for measured examples and current quality limits.

Microphone and system audio remain separate channels with configurable speaker roles. These are attribution hints, not voice identification; headphones reduce the chance of remote speech also entering your microphone. Choose Groq/OpenAI transcription with its own credential, or install the optional local recognizer:

```sh
uv sync --python 3.13 --extra mac --extra local-asr
```

Local transcription loads its model on first use and may download model weights. No model download or audio transcription starts during ordinary app launch.

## MIDI controls

The Keychron K0 Max keypad uses **MIDI channel 1** (channel `0` in mido). Only note-on messages with positive velocity trigger actions. Knob detents are never debounced; other buttons retain per-note 150 ms duplicate protection. Pressing the knob changes keyboard/MIDI mode in the hardware and is not assigned an app action. The app connects to the first input and reconnects when it returns; set `OTSC_MIDI_PORT` to choose a specific input.

| Key | MIDI note | Action |
|---|---:|---|
| Knob counterclockwise / clockwise | 36 / 37 | Older / Newer output; remain frozen. |
| Circle | 38 | Help now. |
| Triangle | 39 | Start / pause following. |
| Square | 40 | Freeze / unfreeze output. |
| X | 41 | Show / hide window. |
| M1 / M2 / M3 / M4 / M5 | 42 / 47 / 52 / 56 / 61 | Artifact / Conversation / Context / Observed files / History. |
| Num/Clear | 43 | Clear and start a new task. |
| / and * | 44 / 45 | Previous / next artifact; freeze the output. |
| - and + | 46 / 51 | Shrink / enlarge in 50-pixel steps, respecting minimum size. |
| 7 and 9 | 48 / 50 | Page up / down in the current pane; freeze it and stop at its edges. |
| 8 / 4 / 5 / 6 | 49 / 53 / 54 / 55 | Move up / left / down / right in the traditional arrow arrangement. |
| 1 | 57 | Copy clean output. |
| 2 | 58 | Center window. |
| 3 | 59 | Capture now. |
| Enter | 60 | Latest output; resume live updates. |
| 0 | 62 | Start / finish a voice question and request help. |
| . | 63 | Toggle click-through: at most 5% background opacity, fully opaque output text. |

See the [printable keypad layout](docs/midi-keypad.txt). M1–M5 select output views within the one task workflow.

Voice controls use the configured audio inputs. When following continuously, finishing a voice question keeps that capture running; a standalone voice recording closes its inputs when the question ends. MIDI callbacks enqueue actions for the AppKit thread and do not touch the window from the MIDI thread.

## Context and proposed changes

Observed files are bounded, versioned **fragments**, with source observations and known line positions. A spoken description cannot become an observed file. When an example replaces a visible excerpt, the app shows an excerpt diff and preserves its partial status.

**Project…** explicitly connects a folder. The app reads a bounded selection of text source files, respects Git ignore rules, skips symlinks, binary files, common credential files, and detected secrets. The selected snapshot may be sent to the configured model. Verified diffs are calculated by the app against the exact file contents in that snapshot. The app does not apply edits to your actual project.

Quick and deep work begin from the same immutable snapshot: the last five minutes of screen readings and separately attributed transcripts, accumulated context, the observed/selected workspace, and the previous substantive answer. The background context builder adds, revises, or retires source-linked working notes and observed fragments. It cannot edit your project or change user-confirmed constraints and decisions.

Capture, context building, and answers progress independently. Incoming evidence is available immediately for the next snapshot and does not cancel a useful answer already in progress. A newer request or explicit task/project change still supersedes old work; a late quick response cannot replace deep output. Exact repeated readings refresh the timeline without requesting an identical answer. Help now uses available context without waiting behind an OCR or context-building call. Resizing and copying never invoke a model.

Diagrams remain first-class artifacts. A system-design request produces nodes and connections that the app draws and exports as SVG; follow-ups can revise the existing diagram using its stable identity. Code retains line-by-line teaching notes and clean copy. The five-minute stream and accumulated memory supply evidence for any of these outputs.

Quick replies answer the pressing question without generating a second code proposal. Existing artifacts stay visible during those replies; deeper work supplies the annotated code, diff, or drawing. Editor line numbers are separated from source code during OCR, and indentation changes count as meaningful changes.

**History** provides a dropdown of individual outputs. Pick one to inspect its artifacts, or use Older/Newer. The app retains the most recent 24 outputs plus an older output you are holding. New results continue arriving while you browse; they do not replace your selection or become confused with the older response in the model's context. Saved sessions preserve the browsing position separately from the latest task context.

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
