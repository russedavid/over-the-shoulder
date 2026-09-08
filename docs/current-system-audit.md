# Current implementation audit

Inspected September 8, 2026. Source baseline: `c6f644a`. Host: macOS 15.6.1. Installed Codex CLI: 0.153.2. This is a source inspection and syntax check; the application, capture streams, and model calls were not started.

## What exists

| Area | Existing implementation | Implication for the overhaul |
|---|---|---|
| Application core | `lib/main.py`, 2,384 lines, with module-level AppKit startup and shared mutable state | Extract the engine before changing UI behavior; make imports safe |
| Task modes | `lib/control.py` cycles system, coding, pair, behavioral pair, and project views | Replace mode selection with one task session and several artifact renderers |
| Screen capture | Fixed screen rectangle and screenshot files; overlay hidden around capture | Replace geometry assumptions with selected-window/display capture and explicit coordinate transforms |
| OCR regions | `gui_section_boxes.py` and `ocr_pair_screenshot_blocks` detect panels and OCR blocks | Preserve as a comparison baseline; measure native Vision OCR on code and UI text |
| Context extraction | `call_openai_pair_context`, around line 725, accepts an image path but passes `image_path=None` into the model request | The current context path is OCR-only, despite receiving the screenshot filename; vision must be an explicit capability |
| Audio | ScreenCaptureKit system audio plus a separate microphone stream; separate WAV/transcript fields | Useful foundation, but track origin is not guaranteed speaker identity |
| Transcription | Local Parakeet configuration; microphone and system chunks transcribed sequentially, default 20-second flush interval | Introduce short finalized speech segments, timestamps, bounded buffers, and measured latency |
| Observed files | `PairContextUpdate` contains paths and content inferred from captures; a model result replaces the current context object | Add partial-range provenance, confidence, revisioning, and deterministic merging |
| Codex path | Around lines 950–1175: resolve a real Git repo, copy files into a scratch repo, run `codex exec`, return its Git diff, delete scratch | Reuse the proposal workflow, but distinguish verified repo copies from observed fragments |
| Quick/deep answers | Around lines 1965–2053: quick voice answer first, then a deep-dive thread | This is sequential, not parallel; the deep callback can append to newer global conversation state |
| Window interaction | Around lines 2055–2076: F2 toggles `ignoresMouseEvents`; rendering uses fixed line/column budgets | There is a hidden interaction toggle, but it clears/replaces content and lacks a full artifact interaction model |
| Persistence | Session directories, screenshots, WAV files, response JSON, and logs under the source tree | Move runtime storage to Application Support with retention controls and explicit exports |
| Provider configuration | OpenAI calls and named model constants; older scripts contain Anthropic and Google integrations | Extract native provider adapters and capability-based model roles |

## Concrete problems to design around

1. **Two meanings of repository are mixed.** The screen context is reconstructed from OCR, while the Codex path copies the real repository selected by `PAIR_REPO_ROOT` or the process working directory. Those sources have different completeness and authority.
2. **Session guards are not enough.** The capture worker checks a session generation, but quick/deep responses need per-task, per-context, and per-request revision checks. A late answer must not attach to a later question.
3. **Audio labels overstate identity.** The voice path labels system audio as “Interviewer.” System sound can include several participants, the primary user's echo, notifications, or media. Preserve source channel and separately infer or configure speaker role.
4. **The UI is coupled to content production.** Fixed character counts, page lists, and global section arrays make resizing and interaction fragile. The new renderer should own layout independently of the model.
5. **The scratch boundary needs enforcement.** The copy routine preserves symlinks and uses a limited exclusion list. A disposable folder alone does not prove that tools cannot read or follow links outside it. Validate roots, symlinks, selected files, environment exposure, and effective sandbox policy.
6. **Most workflow choices are hidden.** Startup defaults to the project view; mode cycling and MIDI mappings are the primary navigation. The new app needs visible task focus, model configuration, capture state, and ordinary controls.

## Baseline repository handling

The commit includes the outstanding changes to `lib/constants.py`, `lib/control.py`, `lib/formatting.py`, `lib/main.py`, and `lib/prompts.py`; the new `gui_section_boxes.py`; the small `exqmple` task note; and the already-deleted `system2.py`.

Thirteen tracked runtime artifacts were removed from the index: ten PNG files, a Python bytecode file, `Thumbs.db`, and an audio-directory `.DS_Store`. Existing local copies were preserved; already-missing files stayed missing. Session captures, recordings, logs, and the unrelated local PDF are ignored.

The staged source tree passed parsing for 17 Python files and checks for recognized credential literals and suspicious credential assignments. No matched API-key literals were found. This is not a guarantee that historical screenshots or commits contain no sensitive information. Historical commits were not rewritten or pushed for this task; a future public release needs a clean-history/export decision.

Legacy project notes and prompt documents are historical reference inputs, not current product requirements.
