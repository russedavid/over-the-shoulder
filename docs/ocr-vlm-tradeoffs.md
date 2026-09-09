# OCR versus structured vision — September 9, 2026

The owner subsequently chose continuous full-resolution Astra low/Fast screen reading, implemented in [the continuous context workflow](continuous-context.md). This replaces the earlier recommendation for selective VLM extraction. The measurements and alternatives below retain their original models, dates, and limits.

The later Terra/Luna trial preserved nine selected code fragments in 20.6/17.0 seconds, but both omitted the per-shift debug logs. Astra low/Fast preserved all 24 visible log lines in its 28.9-second full-resolution control, including a potentially relevant count discrepancy. Luna also mistook GitLens blame for a TODO. Faster response time and code-fragment checks alone did not establish equally complete perception; this informed the decision to use Astra.

## What the existing measurements establish

The 23-image recorded evaluation used the current Tesseract path and a separate GPT-5.5 image-only reading prompt. The latter returned visible text, facts, task interpretation, and uncertainty. These are observed times from one pass, including normal provider/CLI overhead, not latency guarantees.

| Measure | Local OCR | Structured VLM, GPT-5.5 |
|---|---:|---:|
| Median observed time | 1.568 s | 46.236 s |
| Observed 95th-percentile time | 3.535 s | 75.117 s |
| Range | 0.470–3.549 s | 13.717–80.978 s |
| Provisional task-relevant readings passed | 7/23 | 12/23 |
| External inference charge | None for the OCR step | Provider-dependent |

The quality labels await human review. The independent references used a stronger model and full-resolution redacted images, while the candidate used the app's resized frames. Model choice and resolution are confounded; the results do not isolate either one's contribution.

## Same extraction request at Astra low/Fast

The `ocr-astra-low-fast-2026-09-09` experiment reruns the two IDE screenshots from session eight using GPT-6 Astra at low reasoning with Fast processing. It supplies the same 2850 × 1720 redacted images and the same `ScreenReading` schema and extraction instructions used for their saved high/Standard references. The request prompt and schema hashes match the historical reference traces. Previous transcriptions and answers are not supplied to the new calls.

| Screenshot | Saved high/Standard | New low/Fast | Less elapsed time |
|---|---:|---:|---:|
| `top-workplaces.ts` | 63.350 s | 30.063 s | 52.54% |
| `pagination.ts` diff | 55.087 s | 30.789 s | 44.11% |

Each timing covers the combined visible-text extraction, visual facts, task interpretation and uncertainty output. Neither new call reports cached input. New reported input/output tokens are 10,543/1,553 for the workplace screenshot and 12,292/1,517 for the diff. The corresponding historical reference counts are 10,546/1,848 and 10,543/1,550; token accounting is not identical across the historical and new calls even though the application request contracts match.

Assistant inspection against the pixels found no consequential errors in the visible ranking code, operators, filters, counts, names, or pagination diff. The low/Fast readings preserve the distinction between terminal compilation results and editor diagnostics, mark the invisible code, and avoid assuming repository state after the displayed reset command. This is a provisional visual review, not a human-approved transcript or a character-error-rate measurement.

There is one new sample per screenshot, compared with historical high runs. Both reasoning effort and processing tier changed, so their separate contributions are not isolated. These results do not supersede the 23-image quality findings above. The private experiment contains the exact input images, requests, original results, traces and a side-by-side HTML review. The app's OCR pipeline and medium/Fast assistance default remain unchanged by this experiment.

## Cost at the measured token volumes

The 23 GPT-5.5 reading calls reported 222,175 input tokens, including 64,512 cached tokens, and 45,412 output tokens. At standard short-context API prices of $5/M input, $0.50/M cached input, and $30/M output, that is an **API-equivalent $2.18 total, or about $0.095 per frame** with the observed cache hits. Without those cache hits it would be about **$0.108 per frame**. These rates were checked on September 9, 2026. [Official pricing](https://developers.openai.com/api/docs/pricing).

These runs used Codex sign-in. The calculation is not an invoice or a claim about the marginal cost of the user's subscription. A direct API integration may also have different prompt overhead. Image inputs are already included in reported input usage; do not add a second image charge. [Image token accounting](https://developers.openai.com/api/docs/guides/images-vision#calculating-costs).

If an equivalent call ran every 30 seconds, 120 calls/hour would be approximately **$11.39–$12.90/hour** at those API-equivalent volumes. That is an explicit ungated scenario, not measured usage. Caching and selective extraction can reduce call counts; their real savings must be measured.

Much of the benchmark response is transcribed text. A production extractor should return only useful regions, code fragments, visible questions/errors, paths where supported, and uncertainty. Reducing output is a plausible latency/cost improvement, not a result established by this run.

## Lightweight approach comparison

| Approach | Benefits | Costs and limitations |
|---|---|---|
| Local OCR alone | Fast; works without a model service; useful for change detection and immediate context | Column mixing, small glyph errors, lost indentation, and poor diagram meaning can corrupt file reconstruction |
| VLM extraction for every changed frame | Can separate panels, interpret diagrams, and return a useful structure directly | Additional provider latency, token usage, and possible invented or “corrected” text; a valid JSON object is not evidence of correctness |
| Local OCR plus cached, selective VLM extraction | Immediate quick assistance; richer interpretation where needed; bounded repeated work | Routing and cache invalidation need evaluation; confident OCR can still be wrong; the slower path must not overwrite newer context |

The current deep response already receives OCR plus an image. A separate extractor would therefore add another model call unless the pipeline reuses its result and avoids duplicate image interpretation. That integration choice matters as much as the price per image.

## Local OCR and separate text regions

The owner requires OCR to detect text regions and present their outputs separately. Preserve screen geometry through detection, recognition, context construction, and review: each region should retain its bounding rectangle, recognized lines and their coordinates, source frame, and uncertainty. Editor panes in a diff, separate terminals, sidebars, and dialogs must not become a single interleaved transcript. Region selection should highlight its source area and display that region's text in its own card. Line boxes alone do not establish semantic panel boundaries; layout grouping remains a separate requirement.

The current app already receives Tesseract word coordinates and block/paragraph/line IDs, then largely discards that structure when building a string. It joins words with single spaces. Its gutter reconstruction requires one aligned, consecutive sequence across the image, which is brittle for folded/skipped lines and multiple editor panes. OCR receives the full image; the app's later downsampling applies to the saved vision frame.

A local experiment, `local-ocr-probe-2026-09-09`, compared the current path, Apple Vision accurate recognition with language correction on/off, Tesseract with code-oriented dictionary settings, and manually selected editor crops with optional scaling. The temporary Apple bridge environment did not change project dependencies or the running app. No screenshot or text was sent to a model service during the experiment.

| Full-image local path | Workplace screenshot | Pagination diff |
|---|---:|---:|
| Current app / Tesseract | 3.570 s | 2.652 s |
| Apple Vision accurate, language correction off | 0.907 s | 0.761 s |

These are individual observations, including a cold/warm ordering difference. Apple Vision was faster but still confused `DTO` with `DT0`, `Id` with `ld`, and `!==` with `!=`. It preserved 3/9 and 7/8 selected critical code snippets, versus 3/9 and 6/8 for the current path. Those targeted, whitespace-insensitive checks are not full-transcript accuracy scores. Cropping/preprocessing had mixed results: scaled Tesseract crops improved the diff's snippet count to 7/8 but worsened the workplace code to 2/9. The crop boxes were manually selected for this diagnostic and are not an automatic region detector.

Apple Vision provides local recognition with text bounding boxes; its accurate mode can serve as a fast candidate, pending code-specific evaluation. [Apple text recognition](https://developer.apple.com/documentation/vision/recognizing-text-in-images). PaddleOCR's detection/recognition pipeline is another candidate that exposes recognized text, confidence and region geometry. It has not been run in this experiment. [PaddleOCR pipeline](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/OCR.html).

Prioritize retaining/grouping regions and preserving code spacing, then compare recognizers on exact identifiers, operators, indentation and separation of panes. Do not silently spell-correct code or infer unseen lines. Tesseract's own guidance recommends appropriate page segmentation and disabling dictionaries for non-dictionary material; those changes alone did not improve the selected full-screen snippets here. [Tesseract quality guidance](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html).

## Changes worth testing next

The broader [local OCR model survey](local-ocr-options-2026-09-09.md) compares current compact recognizers, region-aware pipelines and screen parsers, with specific Mac deployment evidence and text-box limitations.

The subsequent [GLM-OCR trial](glm-ocr-trial.md) was rejected as an automatic IDE OCR replacement. Its fast manual-crop recognition did not compensate for incorrect region detection, missing diff changes, and IDE annotations copied into code.

A resolution follow-up reduced full-frame GLM processing to about 14 seconds at 2240 pixels wide, but retained extraction failures and introduced terminal-text errors. Smaller images sometimes caused repetitive output and became slower. The full measurements and limits are recorded in the GLM trial; downscaling alone did not produce a reliable result.

1. Preserve readable detail for the active code/diff region instead of shrinking the entire desktop to the same thumbnail. Use the same model to compare resolution/cropping changes separately from model choice. OpenAI recommends original detail, when supported, for OCR and dense visual tasks; the app's own earlier downsampling cannot be undone by the API. [Vision sizing and detail guidance](https://developers.openai.com/api/docs/guides/images-vision#choose-an-image-detail-level).
2. Use a compact extraction schema that separates literal visible text, layout, inferred meaning, and uncertainty. Include the frame identity and keep all file fragments partial.
3. Cache by meaningful visual content and reuse the interpretation across requests. Do not rerun solely because a clock, cursor, or incidental log line changed.
4. Keep the quick response independent of the slower visual pass. Bind enrichment to its original task/frame so stale analysis cannot replace newer work.
5. Compare a smaller vision model on these same images before adopting it. Lower published prices alone do not establish adequate OCR, code-symbol, or diagram accuracy.

The validation fix is separate: it preserves usable assistance while rejecting unconfirmed file-cache entries. That must not become a reason to treat VLM-reconstructed files as verified local source. OCR and VLM are perception hypotheses; actual selected files retain a different authority level.
