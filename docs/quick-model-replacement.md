# Spark replacement investigation — September 12, 2026

**Recommendation: GPT-5.6 Terra with `none` reasoning and Fast processing is a viable replacement candidate for the quick-answer role.** The current application setting remains Spark pending the replacement decision. This investigation does not change the deep, OCR, context, or planning models.

## Instant, none, and Fast

OpenAI documents [`chat-latest`](https://developers.openai.com/api/docs/models/chat-latest) as the API alias that follows ChatGPT's current Instant model. The page does not identify that alias as “GPT-5.5 Instant,” and its underlying snapshot changes over time. It supports structured output and streaming. The ChatGPT UI label should not be treated as a versioned Codex model identifier.

The API model pages for [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5), [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), and [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra) all list `reasoning.effort: none`. This is a reasoning setting, not a separate `-instant` model suffix. [Fast processing](https://learn.chatgpt.com/docs/agent-configuration/speed) is a separate setting and credit tradeoff.

The local Codex model catalog advertises Low and higher efforts for those models, but direct live tests through the application's existing `CodexProvider` accepted `model_reasoning_effort="none"` for all three. Each returned a schema-valid, source-valid quick response without an unsupported-setting or fallback warning in captured stderr. That verifies these requests work on this account/client; the adapter does not expose a server-confirmed reasoning-token breakdown. Catalog omission alone was not a sufficient reason to rule out the setting.

These calls used the existing Codex sign-in. No API key was copied or used to change billing paths. Calling `chat-latest` directly through the OpenAI API would be a separate API integration/billing choice.

## Initial quick-answer checks

All requests used the application's actual quick prompt, `QuickAssistance` schema, provider adapter, and delivery/source validation. Inputs were frozen synthetic development fixtures; no desktop or audio was captured. Each model ran once per case, with variant order rotated in the three follow-up cases. Timings include Codex startup and validated response delivery. They exclude OCR, transcription, and the automatic plan-review wait.

| Case | Spark low / Standard | Luna none / Fast | Terra none / Fast |
|---|---:|---:|---:|
| Worked fee example | 7.47 s | 5.22 s | 5.19 s |
| Reject blanket HTTP retries; preserve 500–599 | 6.79 s | 5.72 s | 4.29 s |
| Uncertainty about partially visible code | 3.82 s | 4.51 s | 4.96 s |
| Preserve missing values versus numeric zero | 3.37 s | 4.79 s | 4.50 s |
| Median across these four cases | **5.30 s** | **5.01 s** | **4.73 s** |

The technical content was correct in all twelve outputs. Assistant review identified two additional issues that basic schema/source-existence validation did not catch:

- Luna answered the retry objection while citing the primary user's task observation, rather than the other person's actual question. It also included quick-mode implementation bookkeeping in its artifact-effect text.
- Spark's missing-values response claimed a decision log had been updated. The quick response does not perform that operation.

Terra preserved the participant citation in all four cases, respected the numeric boundary and missing-value requirement, and did not claim knowledge of obscured code. These findings support a provisional selection, not a general accuracy or latency ranking. The sample is small, prompts and output lengths vary, and caching/provider load remain uncontrolled beyond order rotation.

Additional single-case compatibility probes returned valid replies for GPT-5.5 none/Fast (5.17 s), Luna low/Fast (6.67 s), and Terra low/Fast (4.57 s). Lower reasoning is not a guarantee that every individual request finishes faster.

## Provenance and limits

Private run directories:

- `quick-model-probe-20260912-084651`: frozen worked-example input, six model/effort combinations, responses, timings, per-call traces, and captured stderr.
- `quick-replacement-check-20260912-084945`: three additional scenarios, three candidates, frozen inputs, expected behavior kept outside the model request, responses, and per-call traces.

All judgments are assistant-authored and provisional. Saved responses are evidence for review, not human gold labels. No held-out evaluation examples were consumed or tuned against. Raw outputs remain outside Git.

The official [Codex model guide](https://learn.chatgpt.com/docs/models) still lists Spark as a research preview. This investigation did not establish a published Spark-specific retirement date; it must not conflate the documented `gpt-5.3-codex` deprecation with `gpt-5.3-codex-spark`.
