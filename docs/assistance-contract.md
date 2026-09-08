# Assistance contract sketch

This is a proposed application contract, not an implemented API. The example is hand-authored product-design material, not a captured conversation or model evaluation result.

These are Python data objects and model-response structures used within the existing application. They do not imply a separate engine process or a frontend/backend IPC protocol.

## State used by task logic and rendering

| Record | Essential fields | Rule |
|---|---|---|
| Task session | Session/task IDs, primary user's goal, working artifact, constraints, unresolved conversation acts | The person's task remains the organizing context |
| Observation | Source kind, source ID, capture/utterance time, content reference/hash, confidence, optional speaker role | Channel and speaker identity are separate; retain uncertain attribution |
| Context snapshot | Immutable revision, included observation IDs, task hypothesis, workspace snapshot ID | Both response lanes use the same snapshot |
| Workspace snapshot | Verified files or partial observed ranges, completeness, conflicts, provenance, file hashes where known | Unknown ranges are not empty content; hypotheses are explicitly marked |
| Assistance response | Request/context IDs, stage, task interpretation, conversation responses, artifacts, limitations | Validate before display; stale revisions cannot replace current work |
| Artifact | Stable ID/revision, kind, scope, evidence links, payload, optional base snapshot | A patch, example, diagram, or answer states what it is and what it is based on |

## Conversation responses

Each relevant question, suggestion, or objection can produce a response linked to its originating event. The response records an action such as answer, acknowledge, challenge, clarify, or defer, plus its effect on the current artifact.

An answer may have no artifact effect. A proposed design change is not automatically an accepted decision. If two people disagree, preserve the alternatives and explain the basis for the recommendation. The primary user can correct speaker attribution, pin the task, or dismiss a suggestion.

## Code and patch representation

Keep `clean_code` or `patch_text` as the canonical content. Teaching annotations are separate objects addressed by artifact revision and line number/content hash. Every generated code line receives an explanation; formatting-only lines can be explicitly marked as structural. The UI shows annotations by default but copies canonical code through **Copy clean**.

An annotated explanation can be copied as Markdown. Language-aware commented exports can be added later; naïvely inserting comment markers can corrupt JSON, multiline strings, or indentation-sensitive code. Existing meaningful comments remain in canonical source.

A patch additionally records the target file and base hash. A partial screen observation can support a localized proposal or a complete example, but cannot justify claiming a full-file diff against unseen contents. Applying to a connected real folder verifies the base again.

## Response lifecycle

1. The scheduler allocates one request ID against a context revision and starts quick and deep work.
2. Quick output is validated and displayed as provisional. Partial structured data is never treated as a complete patch.
3. Deep output identifies which artifact revision it supersedes. The UI replaces or supplements only the matching artifact and keeps its history.
4. If context changes, results are canceled or retained as historical proposals. A late result cannot overwrite newer or pinned work.
5. Resize, scrolling, annotation toggles, and copy operations reuse the same artifact data without making another inference request.

## Example

[assistance-example.json](examples/assistance-example.json) illustrates one collaborative coding task. Another participant proposes returning zero for empty input. The assistant answers the question, explains the tradeoff, and supplies an example helper with four line annotations. The surrounding file is unobserved, so this is a code example rather than a verified repository patch.

Other artifact payloads will use the same envelope: a diagram has editable nodes/edges and a rendered preview; an image has a locally managed media reference and provenance; an explanation has concise and expanded text. The renderer selects a layout for the current viewport and exposes additional detail through expansion/scrolling.
