# Actionable outputs and per-type history

September 11, 2026. The native Python/AppKit window now centers on the output the user can act on.

## Normal and Debug views

Normal mode shows code, changes, instructions, images, structured task outputs, and suggested replies. Task inference, response-summary bookkeeping, planning internals, raw observations, and event logs are hidden. Important scope labels for code and actionable error notices remain visible. **Task…** opens the optional task/context editor.

The two dropdowns are replaced with an always-visible list. The selected type has a beige highlight and an accent line. Code, changes, and images have recognizable headings with descriptive subtitles. The names of other sections come from the task's output contract, so unfamiliar structured output needs no new UI mode.

**Debug** adds Plan, response details, Context, Observed files, and Events. The first two have their own versions; raw context/files/events are live inspection views with history navigation disabled. Debug starts off on a fresh launch. An explicitly restored checkpoint can restore its saved Debug selection.

## Versions, cursors, and badges

Every named artifact has a stable type key based on its output ID. Replies and quick guidance have separate histories. A code section and its companion patch are separate types. Each type remembers its own viewing cursor; switching types returns to that position. A type opened for the first time starts at its latest available version.

The red bubble is the number of retained versions newer than that cursor. An unopened type counts all its available versions. Reading an explanation does not mark new code as read. When the active type is following live updates, its cursor advances as new content arrives. Selecting a type, browsing backward/forward, Pin, and selecting output text hold the current version.

Older/Newer and the MIDI knob never cross into another type. Reaching the newest version with Newer still holds it. Latest or Unpin resumes updates for the selected type only. `/` and `*` explicitly switch types. Page controls scroll the current content and stop at its edges.

Only changes to the type's content add a version. Carried artifacts, changed citation IDs, and reworded titles do not inflate counts. Code annotations remain part of its output. Reply comparisons preserve the question, channel, and speaker attribution. A pending image can show progress, but does not count as an available newer version until the PNG finishes. The previous completed image remains available during a revision.

Each type retains 24 recent versions plus an older held anchor. Long sessions are bounded to 64 types; the oldest inactive type is evicted if needed. A separate recent raw-response log remains available in Debug. Model context always follows accepted generation results, not the version being browsed.

## Controls and persistence

M1 returns to actionable artifacts and leaves Debug. M2 returns to suggested replies, or quick guidance when no replies exist. M3/M4/M5 explicitly enter Debug for Context, Observed files, and Events. All channel-1 note numbers, 8/4/5/6 movement, +/- resizing, voice controls, sharing controls, and click-through remain available. Click-through keeps output text and red badges opaque while pane backgrounds remain at most 5% opaque.

Saved sessions include per-type histories, cursors, counts, and the selected type separately from the latest working context. Loading an older checkpoint projects its retained outputs into type histories. Checkpoints contain no raw captures or credentials and do not start capture or inference on restore.

## Verification

All 155 offline tests passed, including new checks for independent cursors, exact badge counts, held versions, unchanged carried artifacts, image readiness, initial quick/deep handoff, reply attribution, retention, and checkpoint restoration.

The native smoke workflow was updated for the actual controls. It uses owned synthetic fixtures, injects the MIDI messages through their normal handler, renders the normal/Debug/image views, and verifies opacity pixels, sidebar highlights/badges, code copying, resizing, image completion, and saved-session recovery. An initial rendering failure exposed transparent sidebar rows appearing black; the corrected renderer paints a light normal background and retains background-only transparency in click-through mode. The final native run passed.

These UI checks made no model calls, captured no desktop or microphone input, and played no audio. They verify presentation and interaction, not new model-quality results.
