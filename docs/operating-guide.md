# Operating and recovering a task

Run `python ots.py`. Existing screen/audio controls, MIDI notes, copy behavior, pinning, and 5% click-through opacity remain available.

## Task memory

**Task details…** records explicit constraints and decisions, one per line. Those fields are maintained by the user; model suggestions do not update them automatically.

**Save task session** creates a local checkpoint. **Remember sessions locally** enables periodic per-session checkpoints. Both save task text, observed fragments, proposals, and history; neither stores raw screenshots/audio, provider credentials, or a running capture state. Memory is off by default.

Use **Open task session…** or **Resume last saved task** to restore work. Restore creates a new session identity, cancels old jobs, and leaves capture paused. A project path in the file is a hint, not new permission to read it. Saved proposals retain their original base metadata and are labeled as saved. Refresh or reconnect the selected project to compare with current files.

**Delete saved task sessions…** removes app-owned checkpoints after an explicit confirmation and turns automatic memory off. It preserves the current in-memory task and provider settings.

## Operational record

The app records local metadata for requests, providers, versions, timing, usage when supplied, accepted/discarded results, inspection steps, and explicit useful/needs-work feedback. Raw context, code, audio, images, and credentials are not serialized into that stream. Generation identity includes the source-code hash, not just a possibly dirty Git commit.

```sh
python ots.py ops
python ots.py ops --output /tmp/otsc-operations.html
python ots.py ops --recovery-exercise --output /tmp/otsc-recovery
```

Latency is separated by quick/deep lanes. Token counts are reported only when returned by the provider. Dollar costs are not fabricated from subscription access or missing pricing data. Recovery exercises are labeled as controlled exercises, not field incidents.

If a provider fails, existing useful work remains available. If diagnostics storage fails, capture/inference continues. If a session file is corrupt or unsupported, the current task is kept. Restoring an older task does not permit an old in-flight model response to replace it.

## Optional bounded inspection

**Inspect context before deep responses** adds a single model-directed loop over the supplied snapshot. It can read selected complete files, look up observed fragments or relevant observations, validate a previous patch against a fixture, and stop. It cannot obtain new host paths, capture targets, shell execution, writes, or network access.

The default remains the existing standard workflow. Inspection has a three-step and 30-second inspection budget, followed by the normal response generation. Its latency and outcomes can be compared with the standard path using the eval runner.

## Evaluations

```sh
python ots.py eval
python ots.py eval --live --split development --output /tmp/otsc-development
python ots.py eval --live --split holdout --output /tmp/otsc-holdout
python ots.py eval --live --inspection --limit 4 --output /tmp/otsc-inspection
```

The first command validates the offline corpus without model calls. Live runs create private inputs/outputs, checks, critiques, manifests, and an HTML report. A matching saved judge reference check can be supplied with `--reference-check`. See `evals/protocol.md` for reference-label provenance and limitations.

Use `--cases missing-02,bounds-01` for a targeted development run. After correcting an evaluator, `--replay /path/to/original-run --reference-check /path/to/judge-reference-check.json --output /path/to/new-run` rechecks recorded development responses without generating replacements. It retains previous judgments and refuses to overwrite the original or replay holdout cases. Semantic review may still call the configured reviewer.

## Release and recovery identity

Keep the application code, prompt/schema hashes, model choices, dependencies, and eval corpus/rubric version together when comparing releases. The source-only exporter prepares reviewable code without Git history, captures, model caches, or credentials:

```sh
python ots.py export-source --output /tmp/otsc-source.zip
```

This prepares an archive; it does not publish anything. The capture-bearing historical repository remains private. `.app` packaging is outside the current scope.
