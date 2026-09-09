# Information and action boundaries

The model and captured content do not receive authority over the host merely by naming a path or requesting an action. These controls are implemented around the existing desktop workflow.

| Boundary | Control | Remaining limit |
|---|---|---|
| Screen/speech → task | Observations retain channel, speaker uncertainty, source IDs, and time; explicit task details are user-maintained | OCR and attribution can be wrong; content may still influence a model |
| Project folder → snapshot | Explicit project selection, bounded text reads, Git-ignore handling, secret filtering, symlink rejection | The selected repository is partial, and heuristic secret detection is imperfect |
| Snapshot → inspection tools | Exact membership in immutable supplied files; partial observations stay partial; typed read-only tools, result budgets, deadlines, and stopping limits | A valid tool call can still be unhelpful; results need task-level evaluation |
| Model → Codex tools | Model-visible shell, executor, browser, image-read, app/connector, memory, and multi-agent capabilities disabled; no user config/rules; unexpected tool activity aborts the request | The trusted CLI process still needs its own authentication/runtime access. This is not complete OS isolation of a malicious CLI binary |
| Model → files | Proposals only; host-generated verified diffs; inspection validates patches in a temporary fixture with exact declared paths | The application does not apply proposals to the user's repository |
| Session file → application | Versioned JSON, size and path checks, no pickle, image paths discarded, new session ID, capture paused | Session text is imported data, not cryptographically authenticated historical truth |
| Session file → project authority | A saved root is a hint; it cannot select a new authorized project. Complete files must be read again from an already authorized root | The user must reconnect a different project explicitly |
| Old proposal → current state | Original base hashes are recorded when proposals are accepted; saved proposals are labeled separately and compared with current snapshots | A matching hash does not prove semantic correctness |
| Application → diagnostics | Allow-listed scalar metadata, redaction, bounded rotation, private local files | Metadata can still be sensitive; it is not published automatically |
| Application → saved task | Explicit save or opt-in local memory, atomic checkpoints, private permissions; no raw audio/images or credentials | Saved task text and proposals may contain sensitive work; deletion is available in the app menu |
| Evaluation → host | Synthetic inputs; generated code checked by a restricted interpreter; exact-path temporary patch fixtures | Unsupported language constructs require review; the checker is not a general Python sandbox |
| Model output → HTML/SVG | Escaped text and structured diagram rendering | A copied code suggestion remains something the user must inspect |

The canary exercise places a random test value outside the supplied context and asks the model to retrieve it. The hardened Codex path reported no permitted read tool and did not disclose the value. Snapshot-tool tests directly reject traversal and out-of-snapshot paths. These are bounded checks, not claims of immunity to every attack.

Assumptions: the operating system, Python environment, installed Codex binary, and application code are trusted. Account compromise, malicious native dependencies, arbitrary administrator access, and all possible acoustic/visual spoofing are outside this application's proven guarantees.
