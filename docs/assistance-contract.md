# Assistance contract

The implemented contract lives in [`otsc/models.py`](../otsc/models.py). Pydantic validates model responses before they become artifacts. These are data structures within the Python/AppKit application, not a network service protocol.

## One task and its conversation

An `Assistance` contains `task`, `summary`, `conversation`, `artifacts`, `observed_files`, and `open_questions`. A conversation response cites its observation and records an action: answer, acknowledge, challenge, clarify, or defer. Its `artifact_effect` explains how the contribution affects the proposal. A response can answer a technology question without creating an artifact.

The quick model uses a smaller `QuickAssistance` schema: task, summary, at most one conversation response, and at most one open question. It is normalized into the common response object with empty artifact/file arrays. It receives recent observations from the same snapshot; deeper work also receives the full bounded workspace and artifact context. A quick reply leaves the current task's displayed artifact available.

Observations record kind, channel, configured speaker role, time, confidence, and a stable ID. Both inference lanes receive the same snapshot of these observations, previous work, unresolved questions, observed fragments, and any selected project files.

## Code, diffs, and diagrams

Each artifact has a stable ID, kind, title, canonical `content`, language, relative path, basis, source IDs, line annotations, and optional diagram nodes/edges. The basis is `example`, `observed_fragment`, `verified_file`, or `discussion`.

- Code requires one annotation for every line, including structural lines. Clean copy preserves the canonical content and its original source comments exactly.
- A model proposes a complete annotated replacement for a verified file. The host computes the unified diff from the immutable input snapshot, including correct handling of missing final newlines and deletion-only hunks. Patch annotations refer to added lines' new-file positions.
- A replacement for an observed excerpt can have a companion diff. Its basis remains `observed_fragment`; known starting lines are preserved, and unknown positions are labeled as excerpt-relative. It does not become a full-file or verified repository diff.
- Diagrams contain bounded nodes and edges referencing valid node IDs. The same data drives native drawing and SVG export. These are schematic drawings, not generated raster images or interactive diagram editors.
- Explanations and checklists use text content. The app does not execute generated code or apply a proposal to the user's project.

Every artifact cites observations from its request. Observed-file content must occur in its cited screen/file observations; spoken claims do not qualify. A verified-file proposal must target a file actually included in the selected snapshot.

## Lifecycle

1. One request starts independent quick and deep workers from one immutable snapshot.
2. Streaming APIs can expose a provisional summary. Only a validated complete response enters the artifact view.
3. Deep output can replace quick output; late quick output cannot replace deep output.
4. New automatic observations queue while that request finishes. Manual Help now refreshes the screen, flushes captured audio, waits for its transcripts, and supersedes prior work. Session/request/revision checks reject stale responses.
5. Pinning or selecting text holds replacements. Unpinning discards a pending result if its context has since become stale.
6. Views retain their text state. Resize, view selection, annotation toggles, export, and copy reuse existing artifacts. Recent accepted artifacts remain available in History.

[`otsc/demo.py`](../otsc/demo.py) supplies the current, explicitly synthetic code and design examples. The older [assistance-example.json](examples/assistance-example.json) is retained as the original product-design sketch; its envelope predates the implemented schema.
