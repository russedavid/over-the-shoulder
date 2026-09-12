# Automatic answers only when the task needs an update

September 10, 2026. This addresses repeated deep answers being added to history while the task has not materially changed.

## What changed

Previously, the scheduler treated every context revision as a reason to generate quick and deep answers. Context-builder edits could advance that revision without new evidence. Repeated VLM readings also varied their descriptions and uncertainty wording, even when they showed the same code. The planner could retain the same plan but still unconditionally generate another answer.

Automatic refresh now has three checks:

1. The scheduler compares the session and **evidence revision**, rather than interpreting a memory rewrite as fresh input. A context-builder-only change starts no new answer request.
2. The existing planning call explicitly decides `answer_needed` separately from `reuse_previous`. It compares current OCR, attributed speech, connected file contents, and task context with the previous substantive answer. If that answer remains sufficient, it returns `NoAnswerUpdate` and neither automatic answer generator runs. A new question may require an answer while retaining the same plan.
3. If generation still returns identical content, a publication fingerprint suppresses the duplicate. Citation IDs and Plan-review bookkeeping do not make a new answer. Actual artifact content, questions, source utterances, speaker roles, and channels remain significant. Evidence/cache updates can be accepted without republishing the answer.

Initial assistance and **Help now** still start quick and deep work immediately. During later automatic updates, quick assistance waits for the planner to approve an update, then runs alongside deep generation. Help now deliberately bypasses the gate, including when the user wants another pass over unchanged input.

Capture, transcription, and context building continue while the answer is held. The output pane and history stay unchanged. The latest refresh reason appears in **Context** and the status line; it is not injected back as new task evidence. In-flight images can finish without generating another answer. No existing history entries are deleted.

Passive speech or OCR arriving during a review remains available to the next review. A no-change decision applies only to its original input. Stale task/request decisions cannot release a quick worker or publish an old answer. A failed review remains an error, with quick assistance available; it is not treated as proof that nothing changed.

## Validation

The targeted regression suite covers memory-only updates, no-change suppression, manual override, quick/deep ordering, new speech arriving during review, stale/canceled work, connected-file visibility, citation/Plan-only duplicates, distinct questions and speakers, held pending answers, and frozen UI/history preservation.

The live development run `refresh-gate-20260910-163635` used the actual configured Astra medium/Fast planning call and current planning schema on seven synthetic inputs. It did not capture the desktop, play audio, or call an image API. Expected decisions were assistant-authored and were not supplied to the model.

| Change after the previous answer | Expected | Observed |
|---|---|---|
| Same code, rephrased OCR descriptions/uncertainty | Keep answer | Keep answer |
| “Okay, thanks. That makes sense.” | Keep answer | Keep answer |
| Unrelated editor-update notice | Keep answer | Keep answer |
| New question about invalid measurements | Update | Update |
| Empty-input contract changes from None to NaN | Update | Update |
| Code guard changes from `== 0` to `!= 0` | Update | Update |
| Participant requests a concrete clarification/example | Update | Update, same plan |

All seven decisions matched the expected behavior. The review calls took 7.13–15.27 seconds in this small run. A later explicit request to shorten the answer for a nonprogrammer also correctly requested an update, in 9.52 seconds; intentional revision requests remain meaningful even without new task facts. This is a development check of the judgment stage, not a field accuracy or end-to-end latency benchmark. Offline tests exercise the scheduler/provider/presentation integration separately. Captured inputs, model reasons, and the run manifest remain in the private evaluation directory. Existing held-out evaluations were not used for tuning.

The full 142-test suite, Ruff, offline corpus validation, and 22 native smoke checks passed. One existing race test was corrected to accept either rejection of a completed stale result or earlier cancellation before generation; both paths must reject the obsolete request.

The semantic judgment can still be wrong; **Help now** provides a direct override. The change reuses the existing planner call instead of adding another model worker or a similarity threshold that could hide a small but consequential edit.

The September 12 [background-planning comparison](background-planning-comparison.md) tested moving this review into the context builder. It reduced calls but did not improve fresh-answer latency, and waiting for an occupied context worker added delay. The application retains the existing independent context builder and planning path.
