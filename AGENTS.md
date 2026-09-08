# Over The Shoulder Coder

Read `docs/over-the-shoulder-coder-plan.md`, `docs/current-system-audit.md`, and `docs/assistance-contract.md` before the overhaul.

The primary user is trying to complete a task and create or change an artifact. Surrounding conversation is part of that work: acknowledge, answer, assess, challenge, or defer relevant questions and suggestions, and connect useful input to the artifact. Do not turn the product into a generic meeting summarizer or a collection of interview modes.

Use one task-centered interaction model. Coding, system design, diagrams, explanations, and suggested replies are output capabilities within it. A conversation-only response is valid when no artifact change is justified.

Every generated code artifact needs line-by-line teaching annotations and a separate clean representation. Explanatory annotations must not be silently inserted into the user's code or diffs. Preserve comments that belong to the actual source.

Observed screen/OCR fragments, spoken claims, inferred hypotheses, and verified local files are different evidence. Preserve provenance, completeness, and version. Never present a guessed complete filesystem as an observed one. Suggestions against a partial observation must say so.

Fast and deep results must be bound to the same task/context revision. Stale results must not overwrite newer work or pinned artifacts. Window resizing is a rendering operation, not a reason to regenerate an answer.

Do not start capture or model calls on import. Keep source control free of credentials, raw captures, session recordings, and generated runtime output. Use synthetic fixtures for replay tests. Keys belong in an appropriate credential store; logs must redact them.

Evaluation work follows Hamel Husain and Shreya Shankar: inspect traces, retain concrete critiques, derive failure categories, and build targeted checks. Assistant judgments may guide development but must remain explicitly assistant-authored; do not claim human calibration without human labels.

Commit at real work/verification checkpoints using actual timestamps, in the user's allowed America/Chicago windows: 07:00–09:00 or 17:00–02:00. Preserve local artifacts when removing them from tracking. Do not rewrite or publish historical capture-bearing commits as part of routine implementation.
