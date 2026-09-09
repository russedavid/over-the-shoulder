# Delivering valid answers despite optional metadata failures

The recorded evaluation exposed an all-or-nothing delivery defect. An unconfirmed file-cache update could discard an otherwise usable answer. Misaligned annotation metadata could also block the entire response, including unrelated explanations and diagrams.

Delivery now validates the primary envelope, conversation references, proposed artifacts, and optional observed-file updates separately. Unconfirmed file entries are excluded from the observed workspace and cannot supply a diff base. Their failure does not discard the answer. A visual code proposal remains partial; an unconfirmed path is omitted. Unsupported verified-file claims and ungrounded patches remain withheld.

Code still needs a useful explanation for every nonblank line, including closing delimiters and source comments. Blank lines no longer require filler. Missing or out-of-range explanations receive one bounded annotation-only repair call. That call cannot change the code, answer, paths, or citations, and receives no screenshot or project files. If it fails, only the affected code proposal is withheld and the response explains the limitation.

Quick summaries also survive an invalid optional conversation citation. An unknown citation is removed, not relabeled as evidence from another observation. Duplicate generated diff IDs cannot discard an otherwise valid response.

## Evidence

| Check | Before | After / outcome |
|---|---:|---|
| Same 18 saved deep drafts | 9 delivered | 18 delivered |
| Original artifacts in that validation replay | — | All retained |
| Original code text in that validation replay | — | Unchanged |
| Annotation-only repair in that replay | — | Two code artifacts repaired; shifted notes and missing closing-brace notes corrected |
| Fresh current-system replay | — | 18/18 deep responses delivered, with no code artifacts withheld |
| Quick responses in the fresh replay | 17/18 delivered | The one citation failure led to the follow-up quick fix; all 18 saved quick outputs now deliver with summaries unchanged |
| Automated behavior / regression checks | — | 66 passed for the initial fix; 72 after the boundary audit below |
| Native preservation check | — | Passed |

The same-draft comparison isolates the validation behavior: the answers and code were not regenerated. The fresh replay is a separate stochastic model run. Its one recorded quick failure remains preserved; the later quick-specific validation replay documents the correction without rewriting that run.

The native check covers copying, annotations, observed diffs, diagrams, pinning/selection behavior, queued audio, MIDI window controls, click-through, and saved-task restoration. The source checks still reject invented observed-file contents, unseen verified-file targets, unsafe paths, and unknown evidence references. These checks establish delivery and boundary behavior, not universal semantic correctness.

## Generalization and boundary audit

The implementation contains no exceptions keyed to recording IDs, filenames, or particular code examples. Its policies address failure categories: optional cache updates are isolated, artifact content and citations are validated before repair, and repair can supply only line explanations. An invalid component is withheld; it is not declared valid to improve a delivery score.

A subsequent robustness audit reproduced two gaps in that isolation. A non-object entry in an optional list still failed validation of the entire envelope. A malformed artifact ID could also raise an exception during sorting after the artifact had already been rejected. Optional entries now reach their individual validators regardless of JSON type, and ordering uses only validated candidate IDs. A rejected source claim cannot reserve an otherwise valid artifact's identity. Quick replies use the same component checks while retaining their smaller contract.

Six additional test methods cover 240 deterministic combinations of optional entry types, mutations of every artifact field, and entry ordering, plus quick-reply failures, duplicate identities after rejected sources, strict envelope limits, and exhausted repair budgets. These checks require valid neighboring content and the input snapshot to remain unchanged. The full 72-test suite, lint, and offline corpus validation pass. No native view code changed in this audit; the native check above belongs to the initial fix.

These are synthetic development checks. Both recorded replays used the same eight sessions and 18 checkpoints; fresh generations from those inputs are not an assessment on unseen sessions. No additional model calls or human labels were produced during this audit. The 18/18 result measures delivery, not task correctness. Malformed primary envelopes can still fail, code can still be withheld when bounded repair fails, and complete annotations can still be factually wrong. Broader field evaluation and human reference review remain necessary to assess answer quality.

## Review and remaining work

The original recorded run and human-review store remain intact. A separate review directory contains the fresh run after the main fix, using the same frozen perception outputs and reference drafts. Delivery adjustments are visible in the response trace.

Task inference and perception quality remain separate questions. The [OCR/VLM comparison](ocr-vlm-tradeoffs.md) records the measured timing, API-equivalent cost assumptions, and tradeoffs before making an OCR replacement decision. No OCR implementation or model selection was changed as part of this validation fix.
