# Code comments and source-based diffs

September 11, 2026.

## Code presentation

The code pane uses normal language comment syntax: `#` for Python and shell, `//` for JavaScript/TypeScript and related languages, `--` for SQL, and appropriate block comments for CSS and markup. Teaching comments appear immediately before the relevant source line, with its indentation. Syntax coloring distinguishes comments, keywords, strings, and ordinary code.

Line numbers live in a separate gutter, so selecting text does not copy decorative numbers. They refer to source/proposal lines; inserted teaching comments have blank gutters. **Comments** toggles these notes. **Copy clean** preserves the canonical source exactly, while **Copy with comments** includes the teaching notes as ordinary comments. Existing comments in the source are always retained.

Comments must not be injected into multiline strings, template literals, heredocs, or explicit continuations. Notes for those physical lines are grouped before the containing construct with source-line references inside normal comments. Shebangs, Python encoding headers, and XML declarations retain their required positions. Strict JSON and unsupported comment syntaxes remain clean; their annotation metadata is still inspectable through Debug Events.

The renderer uses [Pygments' token-position API](https://pygments.org/docs/api/) for lexical boundaries and syntax coloring. This is a lexical display helper, not a compiler or a proof of semantic equivalence for every language. Python AST and controlled JavaScript/XML fixtures exercise important copying cases. Clean source never depends on the comment-placement heuristic.

## Diff renderer tool

`otsc.diff_rendering.render_diff(before, after, path=..., first_line=...)` is the host tool used when the agent proposes an anchored replacement. It returns the canonical unified patch plus typed display rows containing old/new line numbers and context/add/remove/hunk markers. It never edits the user's project.

**Changes** displays removals in red and additions in green, with separate Old/New columns and `−`/`+` markers. Counts show how many source lines are added and removed. Teaching comments have no change marker, so they are not mistaken for edits to apply.

**Current** displays the exact baseline supplied to the comparison. **Proposed** displays the replacement, optionally with teaching comments. In Changes, **Copy patch** and Export preserve the actual unified patch. Current/Proposed copy or export the corresponding clean source. **Copy with comments** copies the proposed code, never a malformed patch containing review prose.

The UI uses an AppKit text view and [a separate ruler](https://developer.apple.com/documentation/appkit/nsrulerview) for line numbers. It explicitly reserves gutter space instead of relying on [overlay content insets](https://developer.apple.com/documentation/appkit/nsclipview/contentinsets). Background drawing is clipped to the pane. In click-through mode the backgrounds remain transparent while source text, line numbers, and change markers stay opaque.

## Baseline authority and persistence

For a connected project, the baseline is the exact verified file from the request snapshot. For screen-derived work, it is one literal, source-backed observed excerpt. A unique cached excerpt can be reused without forcing the model to re-emit it; the host adds that baseline's evidence IDs to the proposal. Conflicting/disjoint cached fragments are not guessed into a complete file.

Known source offsets are preserved. Unknown excerpt positions remain explicitly excerpt-relative. A screen fragment never becomes a verified complete file merely because a diff was rendered.

Host-generated patches carry `DiffContext` with their before/after text, line origin, and proposal annotations. This metadata must reproduce the exact patch when loaded. It is absent from the model output schema, and any model-supplied version is discarded before host derivation. It persists in local output histories/checkpoints for Current/Proposed inspection, but is omitted from model memory to avoid duplicating large rendering payloads. Legacy patches without a saved baseline still show Changes; unavailable Current/Proposed views are disabled.

## Verification

The 170-test suite, Ruff, and native smoke checks passed. Checks cover ordinary comment syntax, source/clean-copy separation, Python literals and continuations, JavaScript template contents, XML headers, heredocs, comment-delimiter escaping, newline conventions, old/new diff positions, insertion/deletion, multiple hunks, malformed headers/counts, cached baselines, host-only metadata, and existing patch-application checks in temporary fixtures.

Native rendered views cover code, Changes, Current, Proposed, resizing, selection, syntax colors, gutters, per-type history, and click-through. The renderer's first native attempts exposed an unclipped ruler background and an overlay gutter covering source characters; both were corrected before final inspection.

The live development run `code-diff-20260911-133001` used the current planner/generator and a synthetic cached `quota.py` excerpt at line 40. It delivered a source-linked partial diff in 32.26 seconds, with five additions and one removal. The before text matched the cached excerpt exactly. Seven restricted-AST checks passed for unlimited quota, zero, normal subtraction, exhausted quota, and negative usage. Inputs, response, numbered rows, patch, commented proposal, and provisional findings remain in the private evaluation directory. No desktop/audio capture or audible playback was used. This is one development example, not a field-quality benchmark.
