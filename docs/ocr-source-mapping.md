# Source code and editor line numbers

The [native continuity study](task-continuity-results.md) found that readable code could fail to enter the observed workspace. OCR preserved the editor's line numbers, while the context builder correctly returned code without them. The literal source check rejected that mismatch; useful proposals survived, but the file cache and companion diff were missing.

Screen readings now carry optional source-region metadata alongside the unchanged literal OCR. Each region identifies a visible relative filename, an exact whole-line quote, and the exact separator between the editor number and source indentation. The host derives code and absolute line origins from consecutive numbered rows. It does not scan arbitrary text for leading numbers or strip indentation.

For example, a region containing `40  def helper():` and `41      return 1`, with a two-space gutter separator, establishes a two-line excerpt starting at 40. The four spaces before `return` remain source code. An unnumbered region has no established absolute origin. Numeric literals, blank source lines, tabs and actual source comments remain intact.

Validation checks one quoted region at a time. A candidate must match its filename, complete source lines, indentation and derived line position. It cannot join different panels, borrow another region's filename, change an operator, or claim an invented origin. Relative/gapped numbering, ambiguous repeated quotes, and overlapping mappings are excluded. Invalid optional mappings do not discard the rest of the OCR response; they also do not authorize a fallback that treats arbitrary text as code.

New inference requests require the mapping field; an empty list means no source region was established. Historical saved readings remain loadable under their original contract. The observed workspace still contains partial, perception-derived fragments, not verified local files. Region classification remains an OCR judgment; byte validation cannot independently certify that a screenshot was interpreted correctly.

## Verification

`otsc-source-mapping-20260912-v2` ran the original two synthetic code screenshots through fresh current OCR, the real context worker, task planning and deep assistance. All eight provider calls completed. Both cases populated `retry.py` as an observed fragment starting at line 1, with no file-cache quarantine, and both delivered a host-derived companion diff. Each proposed retry function passed its five restricted-interpreter boundary checks. Generated code was not executed.

The 21 failed file candidates from the original continuity run were then checked against fresh readings of the same screenshots. Their code and line origins were unchanged; citations were explicitly linked to the new observations. All 21 changed from rejected to accepted. These are repeated candidates over two screenshots, not 21 independent tasks or 21 new context-model generations. The original source journal and results remain unchanged.

The first live attempt, `otsc-source-mapping-20260912-v1`, stopped at a strict provider-schema error: backward-compatible local loading had left the new field optional in the inference schema. The inference contract was corrected, and a schema regression test now checks every object's required fields. The failed attempt remains preserved separately from the successful run.

The 199-test suite covers both the recovered behavior and rejection of changed indentation, altered code, wrong filenames, incorrect line origins, unknown citations, speech masquerading as source, panel splicing, ambiguous mappings and numeric-data gutters. Additional examples cover digit-width changes, blank lines, source comments, numeric literals and unnumbered source. Offline corpus validation and lint also pass. This targeted comparison does not establish noisy-screen accuracy or a latency improvement; model defaults and scheduling are unchanged.

## Reproduce

With a local continuity run available:

```sh
.venv/bin/python -m evals.source_mapping_trial \
  --source-run /path/to/otsc-continuity-20260912-v1 \
  --output /tmp/otsc-source-mapping-new
```

This explicit command makes real configured model calls. It uses the saved synthetic images and previously transcribed speech text, without desktop/audio capture or new ASR. The local HTML report shows mapped OCR, cached fragments, proposed code, diffs, original-candidate comparisons and full model requests. Generated media, responses and reports remain outside Git.
