# Astra medium: Standard and Fast

One recorded scalability discussion was replayed through the current Codex adapter with GPT-6 Astra at medium reasoning. Standard delivered in **42.409 seconds** and Fast in **24.120 seconds**: an observed **43.13% reduction in response time**. Both answers satisfied the five provisional task criteria in assistant review. This is one sample per setting, not a general performance estimate.

| Measurement | Standard | Fast |
|---|---:|---:|
| Delivered response | Yes | Yes |
| Response time, including validation | 42.409 s | 24.120 s |
| Reported input tokens | 25,498 | 25,495 |
| Reported cached input tokens | 0 | 0 |
| Reported output tokens | 1,181 | 1,090 |
| Annotation repair / withheld artifacts | None | None |
| Provisional source-backed criteria | 5/5 | 5/5 |

## Case and controls

Run: `astra-medium-fast-2026-09-09.2`. Checkpoint: `pair_session_20260706_173258-point-4`, the discussion about handling millions of shifts in the top-worker/top-workplace ranking scripts. The source is the previously reviewed `validation-fix-2026-09-09/fresh-replay` dataset. It is development material, not a held-out task.

Both calls receive the same application prompt, schema, OCR, speaker-attributed transcripts, app-sized screenshot, and previous application state. Reference answers are excluded. Perception is reused rather than remeasured. Source code remained unchanged between the calls, and image/context identity checks passed before and after each. Temporary CLI paths and wrapper overhead can differ; the reported input-token counts differ by three.

Calls run sequentially, Standard first. Neither reported cached input, but output length, model variation and network conditions still affect latency. Fast produced about 8% fewer output tokens. The CLI is configured with `features.fast_mode=true` and `service_tier="fast"`; Standard explicitly disables the Fast feature. The CLI does not expose a server-confirmed processing tier or credit invoice. OpenAI documents Astra Fast at **2.5 times the Standard credit rate**. [Official Fast mode documentation](https://learn.chatgpt.com/docs/agent-configuration/speed#fast-mode).

The first attempted comparison was stopped after discovering that the harness moved the screenshot outside the adapter's permitted capture directory. That incomplete attempt remains at `astra-medium-fast-2026-09-09` with an explicit invalid-harness status. A regression verifies that both modes receive identical permitted image bytes and context. Only the corrected run above supplies the reported measurements.

## Answer review

Both answers address the current scaling question, process completed shifts page by page, account for the remaining count map and full scan, avoid retaining all worker objects, and preserve eligibility before selecting the exact final top three. Both discuss database or disk-backed aggregation when the count map is too large. Fast was slightly more concise; no substantive correctness difference was found.

Both also retain a secondary pagination explanation and open questions that add some clutter. Both chose explanatory artifacts rather than code, which is appropriate for this discussion; this case does not assess code-generation or line-annotation quality. These verdicts are assistant-authored and await human review.

## Follow-up: medium/Fast and low/Fast

The subsequent `astra-low-fast-2026-09-09` run uses the same checkpoint, image, transcripts, prior context and instructions. It generates fresh responses for both settings, with medium/Fast first. Source and input identity checks pass.

| Measurement | Medium/Fast | Low/Fast |
|---|---:|---:|
| Response time | 24.120 s | 21.461 s |
| Reported input tokens | 25,495 | 25,492 |
| Reported cached input tokens | 0 | 0 |
| Reported output tokens | 1,017 | 876 |
| Provisional task criteria | 5/5 | 5/5 |

Low/Fast saved **2.659 seconds (11.02%)** in this pair and produced about 14% fewer output tokens. Both retained the necessary streaming design, count-map memory accounting, full scan, entity eligibility, exact ranking, retry/consistency qualifications and database/disk alternatives. No substantive correctness regression was found. Medium explicitly acknowledges the previously accepted pagination constraint in its conversation response; Low preserves it in the design and remaining-context note. Both produce explanations, so this pair does not assess generated code. These are assistant judgments on one example, not a general accuracy or latency claim.

## Current settings and reproduction

The app uses **GPT-5.3 Codex Spark / low** for quick responses and **GPT-6 Astra / medium / Fast** for deep responses. Fast is a saved per-model setting with a native Settings checkbox; it is independent of reasoning effort. The higher credit rate was explicitly accepted. The old implicit Standard Astra/medium profile migrates to Fast, while an explicitly saved Standard choice and custom models remain intact. The older GPT-5.5/low profile still migrates on launch, including after an old running window saves its settings on exit.

New Astra reference-drafting calls use medium, and judges retain low; their processing remains Standard unless explicitly configured otherwise. Existing evaluation outputs and their original settings are preserved. The generic live-eval command now defaults to medium/Fast generation; `--no-fast` requests Standard and `--reasoning low` requests lower effort. Completed evaluation manifests remain unchanged.

Reproduce a processing comparison on one checkpoint with a new private output directory:

```sh
.venv/bin/python -m evals.processing_comparison \
  --directory /private/path/to/fresh-replay \
  --checkpoint pair_session_20260706_173258-point-4 \
  --output /private/path/to/new-comparison
```

The output includes a side-by-side HTML review, the frozen input, both original responses, delivery adjustments, token usage and separate traces. Runtime media and outputs stay outside Git.

For the medium/Fast versus low/Fast pair, add `--comparison reasoning-fast` to that command and choose another new output directory. The 76-test suite, offline corpus validation, native preservation check, and native Fast on/off/provider-switch checks pass.
