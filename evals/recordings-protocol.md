# Reviewing the recorded sessions

The recorded-input evaluation runs historical inputs through the current application components. It does not grade old saved answers as if they were new output, and it does not use those answers as reference truth.

## Coverage and replay boundary

The first private index contains eight recorded sessions, 554 mono audio chunks in paired microphone/system channels, 12 original session screenshots, 11 unassigned source images, 10 linked OCR debug derivatives, and five historical responses. Debug derivatives are not independent screenshot examples. Unassigned images are assessed separately; no relationship to a session's audio is invented.

New runs transcribe audio through the application's current `Transcriber`. Images pass through `ScreenCapture.interpret` for local credential redaction and `read_screen` for full-resolution Astra low/Fast OCR. The standalone VLM check uses the configured deep model, while the separate reference draft uses Astra medium. These are separately drafted interpretations, not independent human truth; they can share model-family blind spots. Historical runs retain their original Tesseract/candidate-thumbnail/full-resolution-reference results and model identity.

There are 18 checkpoints under the eight session pages: historical help requests, intermediate points in the long session, and session ends. New replay runs feed produced audio/OCR observations into the actual `ContextStore`, background `ContextBuilder`, `Coordinator`, provider adapters, response validation, and retained state. Quick, deep, and context calls start independently at each checkpoint. Context deltas carry forward to later checkpoints, with a virtual clock enforcing the five-minute input window. Historical answers and ideal responses are never supplied to this runtime.

Only events available at or before a checkpoint are included. Audio write times and clip durations estimate intervals; they do not establish sample-accurate synchronization. Relative session time is the review clock. Runtime observation timestamps use a fixed positive virtual epoch plus that relative time, so they should not be interpreted as historical calendar timestamps.

This is accelerated replay at manual-help checkpoints. It preserves the archived audio file boundaries, which are mostly about 20 seconds; it does not reproduce the current live capture setting's smaller chunks. It does not simulate every periodic request, hardware permissions, acoustic capture, or wall-clock queue pressure. Inference timing is reported separately from the precomputed perception passes. Thus it exercises the current processing functions and assistance runtime on recorded examples, not every timing behavior of a live capture session.

## What “gold” means here

A reference response is a useful example of a good answer, together with source-backed acceptance requirements. It is not the only acceptable wording or implementation. Different correct code or designs must be allowed. In ambiguous scenes, a clarifying question is one permissible response; bounded, conditional assistance can also be reasonable. The owner should review the desired level of proactivity before treating those draft criteria as settled.

- Audio references begin with a separate local Whisper large-v3 recognizer. They are provisional transcripts, not human-verified truth. Word edit distance between recognizers is a triage signal, not verified word error rate.
- Screenshot references contain visible text, directly observed facts, inferred task context, and uncertainty. OCR and VLM interpretations receive separate task-specific assessments against the actual image; missing incidental browser chrome is not automatically a failure.
- Response references are drafted independently from the reference perception results and only the evidence available at the checkpoint. Neither historical answers nor new candidate answers are supplied to the reference writer. Source IDs and uncertainty accompany the criteria.
- Model judgments remain provisional. The human reviewer can pass/fail the new output, approve or revise the reference, and record uncertainty separately.

Some sessions contain very little actionable input. A clarification or no substantive artifact can be the appropriate reference. A recorded session is not automatically a distinct, rich engineering task.

All eight sessions are discovery/review material. They are not a held-out sample once they are used to draft references and investigate failures. Related tasks may span sessions, so a later held-out set needs grouping by task/source family rather than individual clip.

## Navigating the trace

Start with a session's primary checkpoint under **Start here** or **New responses**. Compare the new quick/deep output with its reference and criteria. Source buttons jump directly to the relevant audio pair or screenshot. Rejected drafts are distinguished from output the app accepted and displayed.

In **Audio**, play either channel or both, search transcripts, and filter for recognizer disagreements. In **Screens & vision**, zoom the original frame and inspect OCR and VLM output separately. Historical answers are behind a separate, explicitly labeled disclosure.

**Your review** saves two independent judgments: the candidate output verdict and the reference status. Reference edits and notes are stored in an append-only local review log. Editing a reference never rewrites a model output or source recording. An explicitly empty corrected transcript is retained, which matters when a recognizer hallucinates speech during silence.

## Running the workflow

All commands are explicit; importing modules or running ordinary unit tests does not start inference. Use a new directory for a new experiment. Each completed stage can resume from its saved outputs; original experiments must be preserved when making a fresh comparison.

```sh
python -m evals.recordings index --directory /private/path/to/review
python -m evals.recordings audio --directory /private/path/to/review
python -m evals.recordings audio --role reference --directory /private/path/to/review
python -m evals.recordings ocr --directory /private/path/to/review
python -m evals.recordings vision --directory /private/path/to/review
python -m evals.recordings vision --role reference --directory /private/path/to/review
python -m evals.recordings replay --workers 2 --directory /private/path/to/review
python -m evals.recordings references --workers 2 --directory /private/path/to/review
python -m evals.recordings assess --workers 2 --directory /private/path/to/review
python -m evals.recording_review --directory /private/path/to/review --port 8767
```

Run with the repository's `.venv/bin/python` or an activated environment. The candidate uses the existing application settings. Audio evaluation requires the local-ASR extra; the independent recognizer downloads public Whisper weights when needed. Codex evaluation uses the existing CLI sign-in.

Raw inputs and derived outputs stay outside Git. Audio recognition runs locally. Text and redacted images used in model evaluation are sent through the configured Codex provider; a local review page does not imply local model inference. The review server binds only to loopback, serves indexed assets, checks their hashes, supports audio byte ranges, and requires a same-origin review token for writes. It has no endpoint for changing source files, executing code, or initiating model calls. The operating system and local user are trusted.

## Method sources

- Hamel Husain and Shreya Shankar: [begin with expert trace review and error analysis](https://hamel.dev/blog/posts/evals-faq/whats-a-minimum-viable-evaluation-setup.html).
- Husain and Shankar: [use screening signals to find traces worth examining](https://hamel.dev/blog/posts/evals-faq/how-do-i-surface-problematic-traces-for-review-beyond-user-feedback.html).
- OpenAI: [reference-guided grading, evaluator bias, and validation against human judgments](https://developers.openai.com/api/docs/guides/evaluation-best-practices#llm-as-a-judge-and-model-graders).
- MLX Audio: [local Whisper implementation](https://github.com/Blaizzy/mlx-audio/blob/main/docs/models/stt/whisper.md).
