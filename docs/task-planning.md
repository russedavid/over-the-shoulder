# Task planning, generated images, and audio-only capture

September 10, 2026. This changes the current Python/AppKit application; it does not add a second UI framework or engine service.

## Task-specific plans and ongoing judgment

The quick answer still runs independently. Before each deep answer, Astra reviews the current task, available evidence, previous answer, and previous plan. It either keeps the existing plan or replaces it with a task-specific approach, generation instructions, named output fields, and quality checks. Its reason is retained in the **Plan** artifact. A naming correction should preserve the output contract; moving from an algorithm to an architecture drawing should change it.

Sections are invented for the task. Text, annotated code, arbitrary JSON, and generated images are renderer capabilities, not task modes. A JSON section can contain unfamiliar objects, open maps, arrays, numbers, booleans, and nulls. The host preserves its actual value while rendering it readably. A schema that cannot use the provider's strict structured-output subset travels as JSON text and is validated after decoding; this also preserves absent optional properties. Schema references cannot trigger network access. Invalid sections are identified without hiding valid siblings.

The accepted plan is part of task memory and checkpoints. Every deep request reconsiders whether it still fits. Changes to pane size or browsing position do not cause planning or generation. The ordinary task/request revision checks also cover planned answers.

## Images

The deep answer prepares a drawing brief from the completed design, using consistent names, boundaries, and flows across the image and text. The default image model is `gpt-image-2.5-sunburst`, editable in Settings. It uses the configured OpenAI Images API account, separate from Codex subscription usage. The [current API guide](https://developers.openai.com/api/docs/guides/image-generation) describes Sunburst as the choice for editing precision.

Text arrives before raster rendering finishes. One bounded image worker coalesces pending work by task/section/brief. An unchanged design keeps its in-flight or completed image; a changed design can edit the previous verified PNG. Minor task edits do not restart the image when the newly accepted plan reaffirms the same brief. New tasks, removed sections, and replaced briefs cannot receive obsolete completions.

The app validates returned PNG data and stores it privately under its `generated-images` directory. Display and revision accept only app-owned files with matching content hashes, never arbitrary model-supplied URLs or host paths. The image fits the pane width and supports vertical scrolling and the existing MIDI page controls. Copy/export produces PNG. Image completion appends a history entry, updates current model memory, and preserves any frozen output exactly. An API failure marks that section failed while keeping usable text.

Existing saved node/edge diagrams and the synthetic design demo remain viewable/exportable as SVG. The new task planner requests actual images.

## Screen-sharing indicator

The original prototype used one-shot PyAutoGUI/Pillow screenshots **and** a ScreenCaptureKit stream for system audio. The latter is a continuing capture session even when it supplies only audio. Screenshot capture was already one-shot and remains so.

The new default is a Core Audio process tap on macOS 14.2+, using a private audio aggregate and no display/ScreenCaptureKit stream. This requires Terminal's System Audio Recording permission. Microphone input remains separate, and speaker roles remain channel hints. A legacy `screencapturekit` backend is explicitly selectable for older Macs. This change does not attempt to conceal or bypass macOS privacy indicators. See Apple's [Core Audio tap example](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps).

No capture starts at launch. The default Sharing Off behavior and optional 10 ms screenshot preparation delay remain unchanged. Legacy audio startup also rejects late callbacks after stop, preventing an orphan stream after a timeout.

## Verification and honest limits

The development run `otsc-task-plan-live-rj9vvl7r` used actual Astra medium/Fast planning and generation with synthetic task inputs. The first algorithm answer took 45.31 s, its naming follow-up 30.37 s, and the switch to a webhook design 64.03 s. The naming follow-up reused all three section keys and changed `result` to `merged`; the design pivot replaced those sections with a drawing, delivery behavior, and unresolved design questions. No sections were rejected. The later unfamiliar tool-library handoff took 23.10 s and preserved an open borrower map, arrays, booleans, a numeric deposit, and an unknown deposit as null. These are finite development examples, not accuracy or latency benchmarks.

Actual image generation and revision were exercised. GPT Image 2 generated the initial PNG in 98.97 s and revised it in 94.87 s. Inspection found duplicated queue representations and incorrect arrow destinations in these attempts; they remain in the review rather than being presented as passes. Sunburst corrected the requested retry/state arrow destinations in a subsequent 33.73 s edit. The resulting drawing still requires human review, including boundary geometry. A generated bitmap does not guarantee a correct architecture. The case informed general instructions to resolve the design before drawing, avoid duplicated representations, use unambiguous connections, and leave implementation detail in supporting text. The manual revision prompts are retained and are not claimed to be an autonomous image-review loop.

The Core Audio path captured a generated tone after Terminal permission was granted: 48 kHz source, 16 kHz mono output, 55,808 samples, peak 0.09165, and stop/cleanup in about 0.01 s. A separate generated spoken question reached local transcription as `system` / `other_people`; its final word “queue” became “Q.” and a chunk boundary repeated “to.” The literal-word test therefore did not pass. This is evidence of functioning routing and imperfect transcription, not a claim of perfect ASR.

All 129 offline tests, Ruff, and the offline evaluation-corpus check passed. The native smoke run passed 22 checks, including dynamic schemas, optional properties, plan reuse/revision, section fault isolation, code annotations, image generation/edit protocols, cancellation/coalescing, hash/path verification, audio decoding/resampling/cleanup, frozen image delivery, structured copy, pane resizing, and MIDI navigation across the combined suites. The silent native live run `otsc-live-workflow-tdg0xrvk` passed with quick help at 5.4 s and deep assistance, including planning, at 61.9 s. It used actual OCR, local ASR, and Codex through the app controller, with rendered/generated fixtures and no desktop capture or audible playback.

The standard eval and recording-replay entry points now use task planning for deep text generation, but do not run the separate image renderer. Their manifests say so; a drawing brief remains `needs review`, rather than counting as a rendered-diagram pass. Historical model-speed comparisons retain their original isolated provider workflow.

Reviews are assistant-authored and provisional. Original attempts are preserved; no held-out cases were used to tune these changes. Generated images, local review pages, capture fixtures, and credentials stay outside Git.
