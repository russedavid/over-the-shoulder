# GLM-OCR local trial — September 9, 2026

**Outcome: reject this configuration as a replacement for automatic IDE OCR.** It failed the required combination of accurate code extraction, separate meaningful text regions, and useful latency. Fast recognition of manually selected code crops is a component result, not success for the automatic workflow.

## Setup

The two recorded IDE screenshots are the same full-resolution, redacted 2850 × 1720 frames used in the earlier OCR comparisons. Inference ran locally on the Apple M3 Max with 48 GiB memory. No app dependencies, live OCR implementation, or assistance-model settings were changed.

- GLM-OCR: `mlx-community/GLM-OCR-bf16`, revision `24f15402e83baa0a80eeeaecf5480e172abc6f2e`, using MLX 0.32.2 and Transformers 5.17.0.
- MLX-VLM source: `8f5dc3ddddbb8d7dd2b88ac51015def6f81fed21`. The released 0.7.0 package did not include the newly documented PP-DocLayoutV3 module, so the isolated experiment used this pinned source revision.
- Layout port: `HashNuke/pp-doclayout-v3-mlx`, revision `cd50996ad0c7e35a7e20c27480f99cec1822cf7b`.
- Official layout cross-check: `PaddlePaddle/PP-DocLayoutV3_safetensors`, revision `97d101e6db2642e162a1d05392d1b0231c91033e`, Transformers/PyTorch 2.10 on CPU. Its processor uses 800 × 800 input; the trial uses the GLM SDK's 0.3 threshold.
- Recognition uses the published `Text Recognition:` prompt, greedy generation, and no previous transcript/reference supplied. Hugging Face and Transformers offline modes were enabled after downloading the public weights.

The automatic experiment passes detected rectangles to GLM's text-recognition task. It is not the SDK's table/formula routing policy. Raw detections, output, limits and earlier probes remain preserved in the private run directory `evaluations/glm-ocr-2026-09-09`.

## Measurements

| Path | Workplace screenshot | Pagination diff |
|---|---:|---:|
| Full-screen GLM transcription | 20.176 s | 18.084 s |
| Official detector alone | 0.795 s | 0.571 s |
| Automatic detection + per-box recognition, summed stage times | 21.747 s | 23.811 s |
| Manually selected code crop recognition | 2.451 s | 1.963 s left; 1.887 s right |

These are single measurements excluding model loading/downloads. The combined automatic figure sums separately measured detector and recognition stages; it is not a measurement of a shipped end-to-end application. Manual crop times exclude finding those regions. Peak MLX memory reported for full-screen recognition was about 3.96 GB. An earlier independent full-screen probe took 21.149 seconds for the workplace image and is retained separately.

## Failures that determine the outcome

1. **No useful automatic pane separation.** The official detector proposed a near-full-screen table plus status/footer areas for the workplace image. On the diff it proposed a full-screen table, a terminal region, header/footer areas and a duplicate footer. It did not identify the separate code panes. The community MLX port also failed the same functional requirement; the result is not based only on that port.
2. **Lost before/after information.** Full-screen recognition omitted the right-pane `pageNum ?? FIRST_PAGE` and `shard ?? DEFAULT_SHARD` changes. The two manually selected crops retained both versions.
3. **UI annotations became code.** Cropped code regained indentation and most exact symbols, but GLM included GitLens blame text inside its code fence. That output is not a clean source fragment.
4. **Repetition on a false text region.** A detected toolbar/header crop generated repeated symbols until its 512-token limit. The failed/incomplete output is preserved and flagged in the review.

Selected whitespace-insensitive code checks were 9/9 and 6/8 for full frames, and 9/9 and 8/8 for the manual code crops. Those small checks do not certify usable source or overall OCR accuracy; they miss the annotation contamination and region-separation failures. Judgments are assistant-authored, not human-approved transcripts.

## Review and implication

The private HTML review shows the original screenshot with selectable automatic boxes, each box's OCR output, and a separate manual-crop diagnostic mode. It exposes the whole-screen output, historical reference, raw records and token-limit failure. Browser checks passed for selection/highlighting, the automatic/manual distinction, source-image loading, raw-data links and mobile layout.

GLM may remain useful as a region recognizer, but this trial does not justify integrating it as the automatic OCR replacement. A future candidate must demonstrate accurate text assignment to automatically detected regions and separation of code from IDE annotations on unseen screens. The existing application remains unchanged by the experiment.

## Resolution follow-up

The owner suggested reducing screenshot resolution. `glm-ocr-resolution-2026-09-09` tests fresh full-size controls plus 1920-, 1440- and 1024-pixel-wide versions. A separate preserved follow-up, `glm-ocr-resolution-2026-09-09-intermediate`, tests 2240 pixels. Both use the same pinned BF16 model, MLX GPU backend, prompt, greedy decoding and 4096-token limit. Images use aspect-preserving Lanczos resampling; originals remain unchanged. There is one generation per screenshot/size, and 2240 was selected after inspecting the initial sweep.

| Input resolution | Workplace total time | Pagination diff total time | Observed limitation |
|---|---:|---:|---|
| 2850 × 1720 | 20.128 s | 19.228 s | Baseline: UI annotations/flattened code; right-pane diff changes missing |
| 2240 × 1352 | 14.020 s | 14.067 s | Same selected code-check results as baseline, but terminal transcription errors and diff omissions remain |
| 1920 × 1159 | 12.560 s | 23.790 s* | Workplace logs deteriorate; diff repeats to token limit |
| 1440 × 869 | 21.489 s* | 14.413 s | Corrupted identifiers, syntax, and duplicated/repeated output |
| 1024 × 618 | 20.125 s* | 20.236 s* | No selected code snippets survive; both hit token limit |

An asterisk marks a 4096-token-limit stop, not a completed transcription. Total time includes resizing/writing the test PNG and recognition, excluding model loading. Input-token counts fall from 6016 at original size to 3856, 2845, 1597 and 830 at progressively smaller widths. That reduction does not ensure lower total latency: ambiguous low-resolution inputs can induce much longer repetitive output. The recorded peak-memory values are process high-water marks, not independent per-resolution peaks.

At 2240, the selected whitespace-insensitive code checks remain 9/9 and 6/8, while elapsed time improves by roughly 27–30%. These checks do not establish equal transcription quality. For example, the workplace output changes the visible shift-22 count from 2 to 1, and the diff terminal changes `HEAD~1` to `HEAD-1`. At 1920, the workplace output changes the shift-18 count from 2 to 3 and omits later log rows; the diff contains all eight selected snippets but then repeats until the token limit. The existing pane-separation problem was not fixed or retested by resizing—the layout detector already has its own fixed-size preprocessing.

The moderate reduction is useful performance evidence for further experimentation, but no tested full-frame size establishes a reliable automatic OCR solution. The separate resolution review exposes each exact model input and raw output rather than presenting shorter runtime as a quality pass.
