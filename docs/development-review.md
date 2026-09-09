# Development review

These are **assistant-authored engineering critiques and synthetic checks**, not human-calibrated judgments of live model quality. Following the requested Hamel Husain / Shreya Shankar approach, development starts with concrete traces and failures, groups them into useful categories, and adds targeted regression checks. A passing schema is not a passing assistance-quality judgment.

| Observed problem / risk | Category | Change and evidence |
|---|---|---|
| Legacy rendering discarded selection and clipped content at fixed character limits | Usability | Retained NSTextViews and scroll views; native resize/selection checks |
| Late deep work could replace unrelated current conversation | State correctness | Immutable snapshots, request/session/revision checks, bounded parallel lanes |
| A new request or goal could make an update waiting behind Pin stale | State correctness | Pending-version check before promotion; regression test |
| Continuous new audio could invalidate every slow response | Responsiveness | Queue incoming automatic observations until the active response finishes; manual help supersedes immediately; native synthetic check |
| A collaborator's suggestion could disappear into a generic summary | Task quality | Explicit cited response/action/artifact effect; synthetic empty-input and retry-design examples inspected |
| Generated code could be impossible to distinguish from teaching commentary | Artifact usability | Every code line requires an annotation; separate clean representation and copy checks |
| A described or invented file could look like verified observed code | Grounding | Literal observed-content/source checks and separate verified snapshots; negative tests |
| A model-generated diff could fail to match the input file | Artifact correctness | Host-generated diffs; actual `git apply --check` and application in temporary fixtures |
| First diagram render placed opposing edges and labels on top of each other | Presentation | Direction-aware routes and label placement; native render inspected after correction |
| Credentials, symlinks, or ignored files could enter a project snapshot | Context privacy | Bounded source filter, symlink rejection, secret exclusions, fixture tests |
| A real fast-model response generated code but missed teaching annotations | Responsiveness / output contract | Restrict quick responses to a small answer-only schema; a subsequent real run returned valid help in 5.2 seconds |
| A brief follow-up erased the prior code while deep work ran | Artifact continuity | Retain the current task's displayed artifact separately from the short response; checked in the live AppKit workflow |
| Editor gutters could become part of the reconstructed source | Grounding | Separate aligned line numbers, retain the detected range, and preserve indentation; tested with real OCR |
| A real retry proposal used `>= 500`, implicitly assuming valid HTTP status codes | Input-domain assumptions | State the malformed-input requirement explicitly; the resulting bounded predicate passed ten cases including 499, 500, 599, and 600 |
| Help now could miss speech still in the current capture chunk | Context freshness | Wake audio capture immediately, wait for queued transcripts, and refresh the screen before starting the requested answer |

## Completed live examples

The staged code/speech workflow completed twice with real OCR, local ASR, and real Codex quick/deep models. Quick responses arrived in 4.72 and 5.41 seconds; deep artifacts in 14.01 and 13.71 seconds. The input was a deliberately staged helper and generated conversation, not an unscripted user session. Separate checks exercised native microphone/system capture and a real design response. These observations establish working paths and specific failures fixed; they do not establish a general quality score or human agreement rate.

## First live review corpus

Use short, consented or deliberately staged tasks. Retain raw examples locally with their uncertainty before deriving any quality scores. Never commit captures or keys.

1. A primary user writes a helper; another person proposes a plausible but wrong default. Does the response challenge the proposal with a reason and improve the helper?
2. A primary user explains a design; another person asks a technology question. Does the app answer it without needlessly rewriting the design?
3. A visible code fragment moves off screen. Does remembered context remain partial and preserve its source?
4. The task changes during deep reasoning. Does the newer task win, and does manual help use the latest context?
5. Multiple remote participants and microphone echo occur. Does the app preserve uncertain attribution without treating duplicated speech as multiple decisions?
6. A provider rejects the model/schema or reaches quota. Does the failure leave useful existing work available and allow a clear correction in Settings?
7. The screen stays largely unchanged. Does the app retain its useful proposal and avoid needless inference?
8. An excerpt or verified file changes after a proposal. Does the app distinguish the proposal's old basis from the new state?

Review each task for task usefulness, handling of conversation, grounded claims, artifact correctness, and reading/copying usability. Record a concrete critique and pass/fail for each relevant criterion. Cluster failures before changing prompts or adding evaluators. Live quality claims and human agreement rates remain unmeasured until that review occurs.
