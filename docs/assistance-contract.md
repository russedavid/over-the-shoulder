# Assistance contract

The implemented contract lives in [`otsc/models.py`](../otsc/models.py). Pydantic validates model responses before they become artifacts. These are data structures within the Python/AppKit application, not a network service protocol.

## One task and its conversation

An `Assistance` contains `task`, `summary`, `conversation`, `artifacts`, `observed_files`, and `open_questions`. A conversation response cites its observation and records an action: answer, acknowledge, challenge, clarify, or defer. Its `artifact_effect` explains how the contribution affects the proposal. A response can answer a technology question without creating an artifact.

The quick model uses a smaller `QuickAssistance` schema: task, summary, at most one conversation response, and at most one open question. It is normalized into the common response object with empty artifact/file arrays. It receives recent observations from the same snapshot; deeper work also receives the full bounded workspace and artifact context. A quick reply leaves the current task's displayed artifact available.

Observations record kind, channel, configured speaker role, capture time, confidence, and a stable ID. Astra screen readings keep literal visible text separate from visual facts, inferred task context and uncertainties. Only literal text can corroborate observed source fragments. Both answer lanes start from the same snapshot; deep work receives the five-minute OCR/audio stream, accumulated working context, source evidence, previous substantive answer, unresolved questions, observed fragments, and any selected project files.

The background context builder returns `ContextUpdate`: source-linked `upsert` and `remove` entries, literal `observed_files`, and `retire_files` updates. These are working interpretations, not user-confirmed decisions. It cannot mutate typed constraints or selected source files. New passive observations can arrive during a memory update; newer task or memory revisions prevent an obsolete delta from committing.

## Code, diffs, and diagrams

Each artifact has a stable ID, kind, title, canonical `content`, language, relative path, basis, source IDs, line annotations, and optional diagram nodes/edges. The basis is `example`, `observed_fragment`, `verified_file`, or `discussion`.

- Code requires an annotation for every nonblank line, including structural lines and source comments. Blank lines need no filler; explanation numbers must refer to actual lines. Clean copy preserves the canonical content and its original source comments exactly.
- A model proposes a complete annotated replacement for a verified file. The host computes the unified diff from the immutable input snapshot, including correct handling of missing final newlines and deletion-only hunks. Patch annotations refer to added lines' new-file positions.
- A replacement for an observed excerpt can have a companion diff. Its basis remains `observed_fragment`; known starting lines are preserved, and unknown positions are labeled as excerpt-relative. It does not become a full-file or verified repository diff.
- Diagrams contain bounded nodes and edges referencing valid node IDs. The same data drives native drawing and SVG export. These are schematic drawings, not generated raster images or interactive diagram editors.
- Explanations and checklists use text content. The app does not execute generated code or apply a proposal to the user's project.

Every artifact cites observations from its request. Observed-file content must occur in its cited screen/file observations; spoken claims do not qualify. A verified-file proposal must target a file actually included in the selected snapshot.

Delivery validates the answer, artifacts, and optional file-cache updates independently. An uncorroborated cache entry is excluded from observed state and cannot create an excerpt diff; it does not discard an otherwise usable answer. A partial code proposal can retain its content while an unconfirmed file path is omitted. Unverified patches and unsupported verified-file claims remain withheld.

Missing or misaligned code explanations trigger at most one bounded, annotation-only repair call, with a 30-second deadline and no screenshot or project files. The original code, answer, paths, and citations are not editable by that call. If repair fails, only the affected code artifact is withheld and the answer explains that limitation. Other valid assistance remains available. Host delivery notes accompany replay traces; they are not model-provided evidence.

## Lifecycle

1. One request starts independent quick and deep workers from one immutable snapshot.
2. Streaming APIs can expose a provisional summary. Only a validated complete response enters the artifact view.
3. Deep output can replace quick output; late quick output cannot replace deep output.
4. New automatic observations enter context immediately while a request uses its immutable snapshot. They do not starve that request; the next answer catches up. Manual Help now flushes audio and supersedes prior work using available context, while a fresh screen read runs independently. Session/request/task-revision checks reject stale responses.
5. Presentation has its own `OutputHistory`, independent of the coordinator's latest accepted answer. Pinning, selecting output text, choosing an artifact, or entering History freezes the visible snapshot. Accepted results continue updating task context and the recent timeline in the background.
6. Older/Newer browse complete output snapshots, including inherited artifacts and the original conversation sources. Reaching the newest entry through Newer remains frozen. Latest (or Unpin) clears selection and resumes live display. History offers individually selectable outputs. The most recent 24 entries plus an older held entry are retained.
7. Resize, annotation toggles, export, and copy reuse the selected artifacts. Checkpoints preserve the selected output/artifact separately from the latest model context; restoring a browsing position cannot roll task memory back or authorize new capture.

[`otsc/demo.py`](../otsc/demo.py) supplies the current, explicitly synthetic code and design examples. The older [assistance-example.json](examples/assistance-example.json) is retained as the original product-design sketch; its envelope predates the implemented schema.
