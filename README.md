# Over The Shoulder Coder

A Mac desktop collaborator that follows the task you are working on and helps create its code, design, or explanation. It uses the screen and surrounding conversation as context, including other people's questions and objections.

The working application uses **Python, PyObjC, and AppKit**. It has one task workflow, a quick answer and an independent deeper response, selectable text, annotated code, proposed diffs, native diagrams, and optional click-through presentation. There is no separate UI framework, engine service, or IPC layer.

## Run it

macOS 13 or later, Python 3.13, and [uv](https://docs.astral.sh/uv/). Screen OCR also requires Tesseract (`brew install tesseract`).

```sh
uv sync --python 3.13 --extra mac
uv run --extra mac otsc
```

On this checkout you can also double-click **Over The Shoulder Coder.command**. The former `test.py` and `lib/main.py` entry points launch this same application without starting anything when imported.

To try the complete interaction without credentials or capture, double-click **Demo.command**, or run:

```sh
uv run --extra mac otsc --demo
uv run --extra mac otsc --design-demo
```

These are explicitly synthetic examples. The app menu can load either example; they exercise the same response and rendering paths as live assistance. Quit and relaunch normally to leave the synthetic demo.

## Configure once, then work

On first launch, Settings lets you choose separate quick and deep models: Codex CLI, OpenAI, Google Gemini, Anthropic, Groq, or an OpenAI-compatible endpoint. API model names are editable. Codex uses your existing CLI sign-in; API keys are saved in macOS Keychain. A blank key field preserves an existing key. Standard provider environment variables are also supported.

1. Describe the task, or let the captured work establish it. You can add typed context as yourself, another person, an uncertain speaker, or a screen/code excerpt.
2. **Capture now** reads the screen. **Help now** (Cmd-Return) asks for assistance from the current context. **Start following** captures and checks for changes every 30 seconds by default.
3. Choose an artifact, read the conversation response, or inspect the observed files. **Copy clean** omits teaching notes; **Copy explained** includes them. Diagrams export as SVG and code changes as diffs.
4. **Pin** protects work you are reading. Selecting output text also holds incoming replacements. Cmd-Shift-I toggles click-through; clicking the app's Dock icon restores interaction.

Capture starts only from an explicit control. Screen, microphone, and system audio can be enabled separately. macOS may request Screen & System Audio Recording and Microphone permissions for the launcher/Python application. After changing permissions, relaunch if capture still fails.

Microphone and system audio remain separate channels with configurable speaker roles. These are attribution hints, not voice identification; headphones reduce the chance of remote speech also entering your microphone. Choose Groq/OpenAI transcription with its own credential, or install the optional local recognizer:

```sh
uv sync --python 3.13 --extra mac --extra local-asr
```

Local transcription loads its model on first use and may download model weights. No model download or audio transcription starts during ordinary app launch.

## Context and proposed changes

Observed files are bounded, versioned **fragments**, with source observations and known line positions. A spoken description cannot become an observed file. When an example replaces a visible excerpt, the app shows an excerpt diff and preserves its partial status.

**Project…** explicitly connects a folder. The app reads a bounded selection of text source files, respects Git ignore rules, skips symlinks, binary files, common credential files, and detected secrets. The selected snapshot may be sent to the configured model. Verified diffs are calculated by the app against the exact file contents in that snapshot. The app does not apply edits to your actual project.

Quick and deep work begin from the same snapshot. A newer request supersedes older work; a late quick response cannot overwrite deep output. New capture/audio arriving during an active response is queued for the next snapshot, avoiding endless cancellation during conversation. Help now promotes the latest queued context immediately. Repeated screen text and minor visual changes do not automatically regenerate the answer. Resizing and copying never invoke a model.

The hourly assistance limit counts requests (each can use two models), not dollars or transcription calls. Provider quotas still apply. Failed requests show their error and retain existing work; the app does not silently retry or switch providers.

## Local app bundle

```sh
uv run --extra mac python scripts/build_macos_app.py
open 'dist/Over The Shoulder Coder.app'
```

This is a **development bundle tied to this checkout and its `.venv`**. It is not a signed, notarized, portable distribution. Rebuild after moving the checkout or changing the Codex installation path.

## Verification and implementation

```sh
uv run --extra mac python -m unittest discover -s tests -v
uvx ruff check otsc tests scripts
uv run --extra mac otsc --smoke-test /tmp/otsc-smoke
```

Core tests can run outside macOS with `uv sync --extra test`. The native smoke test exercises synthetic quick/deep responses, annotations, clean copy, collaborator replies, excerpt diffs, resizing, selection, click-through, audio queuing, diagrams, and settings construction. It renders only the app's own views under `/tmp`; it does not capture the desktop. Test reports distinguish these checks from live provider, permission, and acoustic testing.

- [Current implementation and verification boundaries](docs/implementation.md)
- [Response contract](docs/assistance-contract.md)
- [Development review and follow-up cases](docs/development-review.md)
- [Product plan](docs/over-the-shoulder-coder-plan.md)
- [Pre-overhaul audit](docs/current-system-audit.md)

Preferences and temporary redacted vision frames use `~/Library/Application Support/Over The Shoulder Coder`; credentials use Keychain. Raw screenshot and cloud-transcription audio files are not persisted. Local ASR and Codex use temporary directories removed after each operation. OCR redaction is a best-effort filter, not a guarantee that every secret in an image is recognizable. Enable vision only for screen content you intend to send to that provider.

Legacy source was preserved in commit `c6f644a` before replacement. Local recordings and screenshots remain excluded from Git. Historical capture-bearing commits have not been rewritten or published as part of this work.
