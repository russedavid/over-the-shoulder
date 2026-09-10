# Over The Shoulder Coder

Read `README.md`, `docs/implementation.md`, and `docs/assistance-contract.md` before changing the application. `docs/over-the-shoulder-coder-plan.md` retains the product intent; `docs/current-system-audit.md` is the historical baseline audit.

Run focused checks with `.venv/bin/python -m unittest discover -s tests -v`, `uvx ruff check ots.py otsc evals tests`, and `python ots.py eval`. For native view changes, run `.venv/bin/python -m otsc --smoke-test /tmp/otsc-smoke` and inspect its report/rendered views. Synthetic demo and smoke-test paths make no API calls and do not capture the user's screen or audio. Do not present those checks as live model-quality or acoustic validation.

The owner explicitly deferred `.app` packaging and requested a simple `python ots.py` entry point. Focus on task assistance, context, capture, and interaction; do not resume bundle/distribution work unless requested.

Keep the existing Python/PyObjC/AppKit application. Refactor into modules within that application and improve its existing window. The owner explicitly rejected a SwiftUI rewrite and separate Python engine process. Prioritize the requested task behavior and concrete interaction improvements; do not turn module separation into a new service or IPC architecture.

The primary user is trying to complete a task and create or change an artifact. Surrounding conversation is part of that work: acknowledge, answer, assess, challenge, or defer relevant questions and suggestions, and connect useful input to the artifact. Do not turn the product into a generic meeting summarizer or a collection of interview modes.

Use one task-centered interaction model. Coding, system design, diagrams, explanations, and suggested replies are output capabilities within it. A conversation-only response is valid when no artifact change is justified.

Deep assistance must review the existing task plan before generating its named outputs. Reuse it for minor changes and revise it when the task/deliverable warrants a new approach or output schema. Preserve the decision reason in the Plan artifact. Section names and nested JSON schemas are task-defined, not a fixed mode catalog. New drawings use the configured image API and host-verified PNG assets; retain legacy SVG support for historical artifacts. Image completion must not mutate pinned history or roll live context back.

Screen captures remain one-shot PyAutoGUI/Pillow operations. Default system audio uses an audio-only Core Audio tap, with ScreenCaptureKit available only as an explicitly selected legacy backend. Do not remove separate microphone/system channels to suppress a screen-sharing indicator. Announce any audible playback test before playing it; silent fixture transcription is preferred when testing the rest of the pipeline.

Preserve MIDI keypad support using the channel-1 layout in README. The owner replaced the prototype bindings with notes 36–63: knob counterclockwise 36 browses Older, clockwise 37 browses Newer, and Enter 60 returns to Latest. Knob turns must not lose steps to button debouncing; other buttons retain per-note 150 ms duplicate protection. Pressing the knob switches keyboard/MIDI mode in the hardware and is not an application action. M1–M5 select output views, not task modes. Keep hide/show, resize, voice controls, and model/capture actions usable without clicking the overlay.

Click-through makes pane backgrounds transparent (at most 5% opacity), while output text and diagram content stay opaque. Keep the window alpha at 1.0; do not fade the whole window. Restore normal backgrounds and controls when click-through is disabled or the Dock icon is used.

Every generated code artifact needs line-by-line teaching annotations and a separate clean representation. Explanatory annotations must not be silently inserted into the user's code or diffs. Preserve comments that belong to the actual source.

Observed screen/OCR fragments, spoken claims, inferred hypotheses, and verified local files are different evidence. Preserve provenance, completeness, and version. Never present a guessed complete filesystem as an observed one. Suggestions against a partial observation must say so.

Fast and deep results must be bound to the same task/context revision. Stale results must not overwrite newer work or pinned artifacts. Window resizing is a rendering operation, not a reason to regenerate an answer.

Do not start capture or model calls on import. Keep source control free of credentials, raw captures, session recordings, and generated runtime output. Use synthetic fixtures for replay tests. Keys belong in an appropriate credential store; logs must redact them.

Evaluation work follows Hamel Husain and Shreya Shankar: inspect traces, retain concrete critiques, derive failure categories, and build targeted checks. Assistant judgments may guide development but must remain explicitly assistant-authored; do not claim human calibration without human labels.

Read `evals/protocol.md` before changing evaluations. Preserve original run results when correcting an evaluator. Do not tune to held-out results. Keep runtime reports, traces, and generated responses outside Git; reviewed aggregate findings may be written into documentation with their run identity and limits.

The owner explicitly authorized historical recordings for evaluation. Read `evals/recordings-protocol.md` for that workflow. Run recorded inputs through the current components; saved historical responses are neither ground truth nor generator input. Keep private recordings and derived outputs out of Git. Unit and browser-write tests use isolated synthetic fixtures so they cannot create false human labels in the real review dataset. Reference drafts stay provisional until actual manual review; preserve earlier attempts and rejected drafts when investigating failures.

Task memory and model-directed inspection are additive, opt-in features. Restoring a checkpoint cannot authorize capture or a new project folder. Inspection tools use only immutable supplied evidence. Keep original proposal bases, user-confirmed decisions, and inferred suggestions distinct. Operational logging failures must not interrupt task assistance.

Commit at real work/verification checkpoints using actual timestamps, in the user's allowed America/Chicago windows: 07:00–09:00 or 17:00–02:00. Preserve local artifacts when removing them from tracking. Do not rewrite or publish historical capture-bearing commits as part of routine implementation.
