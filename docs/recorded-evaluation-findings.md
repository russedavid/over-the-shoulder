# Recorded-input evaluation — September 9, 2026

The recorded examples exposed substantially more problems than the synthetic fixtures. The current system produced quick responses at all 18 selected checkpoints, but only nine delivered a deep response. Nine deep drafts were rejected by source-provenance or code-annotation validation. The rejected drafts, errors, and source observations are retained for inspection.

The recordings have not become human-labeled ground truth. A local review interface lets the owner judge the new output, inspect the original media, and correct or approve the independently drafted references.

## What was run

| Layer | Completed work | Reference / review |
|---|---|---|
| Recorded audio | 554 files, 277 microphone/system pairs, processed by the current local Parakeet transcriber | Independent local Whisper large-v3 transcripts for all 554 files; word disagreement is a review aid, not verified accuracy |
| OCR | 23 source images through the current Tesseract/redaction path | Independently drafted visible-text references; original pixels remain available |
| Screenshot interpretation | 23 image-only checks using the configured deep model and normal app-sized frames | Stronger-model readings of redacted full-resolution images; this intentionally differs from the candidate's resolution |
| Current assistance runtime | Eight sessions, 18 checkpoints, current context limits, quick/deep workers, provider adapters, validation, and retained state | 18 independent reference answers with source-backed criteria and uncertainty |
| Human review | Eight session pages with media links, paired audio playback, transcript search, screenshot zoom, and reference editing | No actual owner judgments had been submitted at the end of implementation |

Twelve source screenshots belong to the recorded sessions; 11 older source images have no supported audio-session association. Ten historical OCR debug derivatives are linked to their originals rather than counted as independent examples. Five historical responses are available separately, but were excluded from both reference writing and current-system generation.

The current runtime code hash was `bb71467185582bbf796dc3ca153fb35fd7b067cbd6735c3957cba62cc3dd7c25`, unchanged throughout the replays. The normal quick/deep choices were Codex Spark / GPT-5.5. Reference writing used GPT-6-astra with high reasoning, and provisional assessment used GPT-6-astra with low reasoning. No application behavior was changed to improve these results.

## Recorded outcomes

| Measure | Recorded result |
|---|---:|
| New quick responses delivered | 18/18 |
| New deep responses delivered | 9/18 |
| Rejected deep drafts retained | 9/9 |
| Provisional task passes / failures | 4 / 14 |
| Provisional OCR passes / failures | 7 / 16 |
| Provisional VLM passes / failures | 12 / 11 |

The gate failures are concrete runtime observations. The semantic pass/fail labels are model judgments against draft references and need owner review. They are not a field success rate. Some judgments depend on how proactive the assistant should be when the recording is ambiguous, contains only an acknowledgment, or shows an older task in the background.

Observed failure categories include file paths or code content that cannot be reconciled with the OCR observations, citations that are unavailable in the current observation window, missing teaching annotations, and responses that keep following an older visible task after the conversation moves on. The trace makes it possible to distinguish a poor model proposal from a useful proposal blocked by the application's validation rules.

The first replay was preserved separately. The instrumented replay retained raw rejected proposals, making the failure review materially more useful. Both runs used the same application code; their generated responses can differ. The final review uses the instrumented run and does not treat rerunning a stochastic model as evidence of an application improvement.

## Review without assuming the reference is right

Each checkpoint has a suggested answer and a small set of source-linked acceptance criteria. Those are editable review material. An alternative correct implementation or a reasonable conditional response should not fail merely because it differs from the draft. A clarification question is not automatically mandatory in an ambiguous scene.

Automated assessments are collapsed by default so the owner can form an independent judgment before reading them. Manual output verdicts and reference approvals are saved separately. A corrected transcript may be explicitly empty, which is necessary for reviewing spurious speech. Word error rate is only described as such after the owner approves the reference transcript; unclear speech is excluded from that claim.

## Limits and verification

The archived audio chunks, mostly about 20 seconds, were preserved. This is not a simulation of the current live capture cadence, smaller live chunks, acoustic hardware, periodic request timing, or queue pressure. It is an accelerated replay through the current processing functions and assistance runtime at selected checkpoints. The standalone visual checks use an evaluation prompt; the actual combined image/OCR task prompt is exercised in the assistance replay.

One long session contributes most of the audio. Several short sessions contain little actionable context. All eight sessions are discovery/review material, and related tasks can cross session boundaries; none is presented as a fresh held-out population.

The review's navigation, paired media sources, transcript search, screenshot zoom, reference form, and mobile layout were checked in a browser. An isolated synthetic browser test verified that a corrected empty transcript and separate output/reference judgments persist across reloads, without writing invented human labels into the real dataset. Original asset hashes are checked before serving media, and review writes require a local origin and review token.

See the [recordings protocol](../evals/recordings-protocol.md) for commands, source provenance, privacy boundaries, and the methods drawn from Hamel Husain and Shreya Shankar.
