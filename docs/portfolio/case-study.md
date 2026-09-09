# Over The Shoulder Coder

A developer assistant that follows work across screen content, conversation, and incomplete source observations, then produces reviewable code and design proposals.

## The problem

Useful context is distributed: a requirement is spoken, code is visible only in part, a colleague suggests an alternative, and the work changes while a model is responding. Treating each screenshot as an isolated prompt loses continuity. Treating every inferred file as verified creates false certainty. Slow responses can also displace work the user has already moved past.

The product centers on the primary person's task and artifact. It answers or challenges surrounding contributions where appropriate, maintains the distinction between observed and proposed state, and provides a short answer before a deeper artifact.

## Engineering decisions

| Decision | Reason |
|---|---|
| Keep Python/PyObjC/AppKit | The existing stack already supplies native interaction and capture access; a UI migration would not solve the task/context problem |
| Independent quick/deep lanes | Useful early help should not wait for full artifact construction |
| Immutable request snapshots | Late responses must not overwrite a different task or newer context |
| Separate code from teaching annotations | Users can inspect an explanation without copying commentary into their source |
| Preserve observed fragments as fragments | Unseen file content is unknown, not an invitation to fabricate a complete repository |
| Host-calculated diffs and recorded bases | Proposed changes remain tied to exact supplied source |
| User-maintained constraints and decisions | Other participants' suggestions are relevant input, not automatic authorization |
| Optional bounded inspection | Model-directed reads can add value where selection matters; the standard workflow remains available |
| Metadata-only diagnostics plus opt-in task memory | Operational diagnosis and continuity need not imply default recording of private work |
| Preserve MIDI and click-through controls | The assistant must fit the user's working rhythm, including hands-free interaction |

The flow is screen/audio → observations → task snapshot → quick/deep work → validated proposal → retained native views. Optional inspection operates only on supplied snapshot data. A model cannot turn a named host path into an approved resource.

## What broke and what changed

Real development exposed missed annotations in quick output, stale-context risk, a Help now action that could miss the newest audio, and loss of MIDI controls during refactoring. The response was a smaller quick-answer contract, snapshot checks, explicit audio flush/fresh capture, and restored MIDI bindings with regression coverage. Additional session work records a proposal's original base at generation time, instead of accidentally associating it with a later file snapshot.

## Evidence and limits

The project includes portable behavior/security tests, a native UI preservation check, a staged live workflow, an optional inspection runtime, local operational records, and a case-specific evaluation corpus. Real examples exercise code, source-grounding, participant responses, and design output. Runtime reports and raw captures remain local; aggregate findings belong in a reviewed results note with the exact run manifest.

The [current findings](../evaluation-results.md) include 52 passing behavior checks, 20/20 development passes, and a frozen held-out result of 7/8 with one disputed evaluator failure. A four-case inspection comparison produced the same task outcomes while increasing median generation time from 14.32 to 21.59 seconds. That evidence supports keeping the standard workflow as the default. The small sample and provisional labels are explicit parts of the result.

The interactive illustration is a self-authored example, not a live inference endpoint or a claim of customer adoption. The software was developed with Codex assistance; product constraints and reviews came from the owner. Evaluation references and model-assisted critiques are labeled accordingly. The project does not establish enterprise operating scale, universal security, or research novelty.

