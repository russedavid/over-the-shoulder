"""Owned-fixture checks for the native UI. No model, screen, or audio capture calls."""

import ast
import io
import json
import queue
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import AppKit as A
from PIL import Image, ImageDraw

from otsc.images import ImageJob, image_key, store_png
from otsc.midi import MidiInput
from otsc.models import Artifact, Assistance, ConversationResponse
from otsc.output_browser import CONTEXT, EVENTS, FILES, GUIDANCE, REPLIES, artifact_key
from otsc.preferences import Preferences
from otsc.privacy import private_directory, private_write
from otsc.scheduler import Cancellation, Job
from otsc.sessions import load_session, save_session


def publish(controller, response):
    snapshot = controller.context.snapshot()
    identity = uuid4().hex
    controller.coordinator.request_id = identity
    controller.coordinator.published_lane = -1
    controller.coordinator.active_lanes = {"deep"}
    job = Job(identity, snapshot, "deep", Cancellation())
    controller.coordinator.lane_jobs["deep"] = job
    controller.handle_event({"type": "result", "job": job, "response": response, "elapsed": 0})


def smoke_tick(c):
    directory = private_directory(Path(c.options.smoke_test))
    try:
        if time.monotonic() - c.smoke_started > 35:
            raise RuntimeError("Native smoke test timed out")
        response = c.coordinator.current
        if not response or not response.artifacts:
            return
        if c.smoke_step == 0:
            if response.artifacts[0].kind != "code":
                return
            check_code(c, directory, response)
            check_capture_frame(c, directory)
            check_quick_stream(c, directory)
            c.smoke_step = 1
            c.load_demo(True)
        elif c.smoke_step == 1 and response.artifacts[0].kind == "diagram":
            assert not c.graph_scroll.isHidden()
            c.save_view_image(directory / "legacy-diagram.png")
            c.set_click_through(True)
            assert c.window.alphaValue() == 1 and c.graph.background_alpha == 0
            c.applicationShouldHandleReopen_hasVisibleWindows_(None, True)
            assert c.window.isOpaque() and not c.window.ignoresMouseEvents()
            c.preferences = Preferences.alloc().initWithController_(c)
            c.preferences.window.orderOut_(None)
            assert {"planner", "image", "quick", "deep"} <= c.preferences.fields.keys()
            private_write(directory / "result.json", json.dumps({
                "passed": True, "live_capture": False, "live_api_calls": False,
                "checks": [
                    "actionable outputs only by default; task/context/plan metadata hidden",
                    "persistent type list, selected highlight, and rendered red numeric badges",
                    "each type retains its own history cursor and newer-version count",
                    "knob Older/Newer stays in the selected type; Latest resumes only that type",
                    "unchanged carried artifacts do not add versions or badges",
                    "Debug reveals plan/context/files/events; M1/M2 return to actionable output",
                    "MIDI 8456 movement, +/- resize, slash/star type selection and 7/9 paging",
                    "clean code and teaching annotations remain separate",
                    "ordinary language comments, syntax coloring, and separate source line gutter",
                    "colored diff rows with old/new numbers and Current/Proposed views",
                    "copyable patch and commented proposal preserve original code semantics",
                    "text selection and scroll survive resize",
                    "click-through keeps pane backgrounds <=5% and text/badges opaque",
                    "Dock recovery restores interaction and normal backgrounds",
                    "window sharing starts off and toggles without a screenshot",
                    "PNG arrival creates only an image version and preserves a held code version",
                    "image fits resized pane, scrolls vertically and copies as an image",
                    "session saves/restores per-type positions and badges with capture paused",
                    "restoring old viewing positions does not roll back live model context",
                    "legacy diagram rendering and provider settings remain available",
                    "UI navigation, Debug and resize make no inference or capture calls",
                    "transparent capture frame moves/resizes on every connected display without inference",
                    "frame close preserves region, reopening restores it, primary-display reset clears it",
                    "capture frame excluded from screenshots; border outside the clear captured interior",
                    "toolbar wraps at the minimum window width without hiding capture controls",
                    "quick streaming updates existing guidance/replies without adding history or changing frozen output",
                    "stream errors restore the prior validated guidance",
                ],
            }, indent=2))
            c.quit_(None)
    except Exception as error:
        import traceback

        private_write(directory / "result.json", json.dumps({"passed": False, "error": str(error)}))
        traceback.print_exc()
        c.shutdown()
        import os

        os._exit(1)


def check_quick_stream(c, directory):
    observation = c.context.snapshot().observations[0]
    response = Assistance(task="Synthetic stream check", summary="Previous validated guidance.",
                          conversation=[ConversationResponse(source_id=observation.id, action="answer", text="Previous reply.", artifact_effect="")],
                          artifacts=[], observed_files=[], open_questions=[])
    entry = c.output_history.append(response, session_id=c.context.session_id, goal=c.context.goal, lane="quick", sources=[observation])
    c.browser.ingest(entry)
    c.browser.select_type(GUIDANCE, follow=True)
    c.render_response()
    before = len(c.output_history.entries)
    job = Job("synthetic-stream", c.context.snapshot(), "quick", Cancellation())
    c.coordinator.lane_jobs["quick"] = job
    c.coordinator.active_lanes = {"quick"}
    c.coordinator.deep_published_evidence = None
    c.coordinator.last_requested_task_revision = c.context.task_revision
    event = {"type": "progress", "request_id": job.request_id, "lane": "quick", "job": job, "message": "Draft: A useful partial answer"}
    c.handle_event(event)
    assert str(c.body.string()) == "A useful partial answer"
    assert len(c.output_history.entries) == before
    c.browser.select_type(REPLIES, follow=True)
    c.render_response()
    c.handle_event({**event, "message": "Reply draft: A direct reply"})
    assert str(c.body.string()) == "A direct reply"
    c.browser.select_type(GUIDANCE, follow=True)
    c.render_response()
    c.browser.freeze()
    c.handle_event({**event, "message": "Draft: Must remain hidden"})
    assert str(c.body.string()) == "Previous validated guidance."
    c.browser.resume()
    c.handle_event(event)
    c.handle_event({"type": "error", "job": job, "message": "Synthetic stream failure"})
    assert str(c.body.string()) == "Previous validated guidance."
    assert len(c.output_history.entries) == before
    c.save_view_image(directory / "stream-recovery.png")
    c.notice_text = ""


def check_capture_frame(c, directory):
    from otsc.capture_frame import display_rects
    from otsc.capture_region import FRAME_INSET, region_from_rect

    before = c.context.revision
    with patch.object(c.coordinator, "request") as inference, patch.object(c.screen, "capture") as capture:
        c.captureWindow_(None)
        selector = c.capture_selector
        assert selector.window.isVisible()
        assert selector.window.sharingType() == A.NSWindowSharingNone
        assert not selector.window.isOpaque() and selector.window.alphaValue() == 1
        screens = display_rects()
        primary_top = screens[0][1] + screens[0][3]
        for x, y, _, _ in screens:
            # Programmatic native move/resize uses real attached-screen positions.
            target = (x + 120, y + 160, 600, 360)
            inset = FRAME_INSET
            box = A.NSMakeRect(target[0] - inset, target[1] - inset, target[2] + 2 * inset, target[3] + 2 * inset)
            selector.window.setFrame_display_(selector.window.frameRectForContentRect_(box), True)
            selector.changed()
            assert c.settings.screen_region == region_from_rect(target, primary_top)
        bitmap = selector.view.bitmapImageRepForCachingDisplayInRect_(selector.view.bounds())
        selector.view.cacheDisplayInRect_toBitmapImageRep_(selector.view.bounds(), bitmap)
        png = bytes(bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {}))
        private_write(directory / "capture-frame.png", png)
        im = Image.open(io.BytesIO(png)).convert("RGBA")
        assert im.getpixel((im.width // 2, im.height // 2))[3] == 0
        assert max(im.getchannel("A").getextrema()) == 255
        saved = c.settings.screen_region
        assert selector.windowShouldClose_(None) is False
        assert not selector.window.isVisible() and c.settings.screen_region == saved
        c.captureWindow_(None)
        assert c.capture_selector.window.isVisible() and c.settings.screen_region == saved
        c.capturePrimaryDisplay_(None)
        assert c.settings.screen_region is None and c.capture_selector is None
        c.window.setContentSize_((760, 520))
        for control in c.controls.values():
            if not control.isHidden():
                rect = control.frame()
                assert rect.origin.x >= 0 and rect.origin.x + rect.size.width <= c.root.bounds().size.width
        assert c.controls["capture_window"].frame().origin.y < c.types_label.frame().origin.y
        c.save_view_image(directory / "narrow-toolbar.png")
        c.window.setContentSize_((1100, 800))
        assert c.context.revision == before
        inference.assert_not_called()
        capture.assert_not_called()


def check_code(c, directory, original):
    c.window.setContentSize_((1100, 800))
    code = original.artifacts[0].model_copy(deep=True)
    code_key = artifact_key(code.id)
    source = c.context.snapshot().observations[0].id
    notes = Artifact(id="instructions", title="Instructions", kind="explanation", content="Run the empty-input check.",
                     language="", path="", basis="discussion", source_ids=[source], annotations=[], nodes=[], edges=[])
    plan = notes.model_copy(update={"id": "_assistance_plan", "title": "Plan", "kind": "structured",
                                    "content": '{"internal_planning_marker":"do not show by default"}'}, deep=True)
    current = original.model_copy(update={"artifacts": [code, notes, plan], "summary": "INTERNAL TASK INFERENCE MARKER"}, deep=True)
    publish(c, current)
    assert not c.debug_mode and c.goal.isHidden() and c.input_scroll.isHidden() and c.status.isHidden()
    assert not hasattr(c, "view") and not hasattr(c, "artifacts")
    assert c.browser.active_key == code_key
    assert "INTERNAL TASK" not in str(c.output_text().string()) and "internal_planning" not in str(c.output_text().string())
    assert artifact_key("_assistance_plan") not in {row[0] for row in c.browser.rows()}
    assert c.copy_text() == code.content
    assert c.copy_text(explained=True) != c.copy_text()
    assert "# " in str(c.output_text().string()) and "→" not in str(c.output_text().string())
    clean = c.copy_text()
    c.output_text().setSelectedRange_((5, 8))
    c.window.setContentSize_((1000, 740))
    assert c.output_text().selectedRange().length == 8 and c.copy_text() == clean
    c.output_text().setSelectedRange_((0, 0))
    c.browser.select_type(code_key)
    c.show_selected_output()
    held = c.browser.visible.id
    revised_code = code.model_copy(update={"content": code.content.replace("def mean(", "def safe_mean(")}, deep=True)
    if revised_code.content == code.content:
        revised_code.content = code.content + "\n"
    revised_notes = notes.model_copy(update={"content": "Run the empty-input and zero-measurement checks."}, deep=True)
    updated = current.model_copy(update={"artifacts": [revised_code, revised_notes, plan]}, deep=True)
    publish(c, updated)
    assert c.browser.visible.id == held and c.copy_text() == clean
    assert c.browser.newer_count == 1
    assert c.browser.state.types[artifact_key(notes.id)].newer_count == 2
    assert c.type_buttons[code_key].output_selected and c.type_buttons[code_key].newer_count == 1
    c.save_view_image(directory / "actionable-output.png")
    if hasattr(c, "code_scroll"):
        private_write(directory / "code-geometry.json", json.dumps({
            "scroll_frame": str(c.code_scroll.frame()), "clip_frame": str(c.code_scroll.contentView().frame()),
            "clip_bounds": str(c.code_scroll.contentView().bounds()), "text_frame": str(c.code_text.frame()),
            "text_bounds": str(c.code_text.bounds()), "text_origin": str(c.code_text.textContainerOrigin()),
            "ruler_frame": str(c.code_scroll.verticalRulerView().frame()),
        }, indent=2))
    with Image.open(directory / "actionable-output.png").convert("RGBA") as picture:
        row = c.type_buttons[artifact_key(notes.id)].frame()
        sidebar = c.types_scroll.frame()
        scale = picture.width / c.root.bounds().size.width
        pixel = picture.getpixel((int((sidebar.origin.x + 7) * scale), int((sidebar.origin.y + row.origin.y + 38) * scale)))
        assert pixel[3] == 255 and min(pixel[:3]) > 150, "Normal sidebar rows must have a readable light backdrop"
    injected, midi_clock = queue.Queue(), [0.0]
    midi = MidiInput(injected, clock=lambda: midi_clock[0])

    def press(note):
        midi_clock[0] += .2
        midi.receive(SimpleNamespace(type="note_on", channel=0, note=note, velocity=100))
        c.midi_action(injected.get_nowait()["action"])

    with patch.object(c.coordinator, "request") as inference, patch.object(c.screen, "capture") as capture:
        press(37)
        assert c.browser.active_key == code_key and c.current_artifact().content == revised_code.content and c.browser.frozen
        press(36)
        assert c.current_artifact().content == code.content
        press(60)
        assert not c.browser.frozen and c.current_artifact().content == revised_code.content
        assert c.browser.state.types[artifact_key(notes.id)].newer_count == 2
        c.chooseOutputType_(c.type_buttons[artifact_key(notes.id)])
        assert c.browser.frozen and c.browser.newer_count == 0
        notes_cursor = c.browser.visible.id
        press(44)
        assert c.browser.active_key != artifact_key(notes.id)
        press(45)
        assert c.browser.active_key == artifact_key(notes.id) and c.browser.visible.id == notes_cursor
        for note, key in ((52, CONTEXT), (56, FILES), (61, EVENTS)):
            press(note)
            assert c.debug_mode and c.browser.active_key == key
            assert not c.older.isEnabled() and not c.newer.isEnabled()
        c.browser.select_type(artifact_key("_assistance_plan"))
        c.show_selected_output()
        assert "internal planning marker" in str(c.body.string())
        c.save_view_image(directory / "debug-output.png")
        press(42)
        assert not c.debug_mode and c.browser.current.kind == "artifact"
        press(47)
        assert c.browser.active_key in {REPLIES, GUIDANCE}
        assert "obs-" not in str(c.body.string())
        c.browser.select_type(code_key)
        c.show_selected_output()
        origin = c.window.frame().origin
        for note, delta in ((49, (0, 50)), (53, (-50, 0)), (54, (0, -50)), (55, (50, 0))):
            before = c.window.frame().origin
            press(note)
            after = c.window.frame().origin
            assert (after.x, after.y) == (before.x + delta[0], before.y + delta[1])
        assert c.window.frame().origin == origin
        size = c.window.frame().size
        press(51)
        assert c.window.frame().size.width == size.width + 50
        press(46)
        assert c.window.frame().size.width == size.width
        press(58)
        press(57)
        assert str(A.NSPasteboard.generalPasteboard().stringForType_(A.NSPasteboardTypeString)) == c.copy_text()
        assert c.window.sharingType() == A.NSWindowSharingNone
        c.toggleSharing_(None)
        assert c.window.sharingType() == A.NSWindowSharingReadOnly
        c.toggleSharing_(None)
        inference.assert_not_called()
        capture.assert_not_called()
    # Produce another unread code version for the transparent badge check.
    c.browser.move(-1)
    c.show_selected_output()
    c.set_click_through(True)
    assert c.window.alphaValue() == 1 and not c.output_text().drawsBackground()
    c.save_view_image(directory / "transparent-output.png")
    check_pixels(c, directory / "transparent-output.png")
    c.applicationShouldHandleReopen_hasVisibleWindows_(None, True)
    assert not c.window.ignoresMouseEvents() and c.output_text().drawsBackground() and c.window.alphaValue() == 1
    check_diff(c, directory, original)
    c.browser.select_type(code_key)
    c.show_selected_output()
    check_image(c, directory, current, code_key, source)
    saved_key, saved_cursor = c.browser.active_key, c.browser.visible.id
    saved_counts = {key: stream.newer_count for key, stream in c.browser.state.types.items()}
    latest_context = c.context.previous_answer
    path = save_session(c.make_checkpoint(), directory / "session.json")
    c.newTask_(None)
    c.restore_session(load_session(path))
    assert c.browser.active_key == saved_key and c.browser.visible.id == saved_cursor
    assert {key: stream.newer_count for key, stream in c.browser.state.types.items()} == saved_counts
    assert c.context.previous_answer == latest_context and not c.running and c.audio is None
    c.set_debug(False)


def check_pixels(c, path):
    with Image.open(path).convert("RGBA") as picture:
        scale = picture.width / c.root.bounds().size.width
        box = c.output_scroll().frame()
        alpha = picture.getpixel((int((box.origin.x + box.size.width - 30) * scale), int((box.origin.y + box.size.height - 24) * scale)))[3]
        assert alpha <= 14, f"Pane background alpha is {alpha}"
        crop = picture.crop((int((box.origin.x + 10) * scale), int((box.origin.y + 10) * scale),
                             int((box.origin.x + 360) * scale), int((box.origin.y + 140) * scale)))
        assert sum(a > 245 and max(r, g, b) < 130 for r, g, b, a in crop.getdata()) > 40
        sidebar = c.types_scroll.frame()
        crop = picture.crop((int(sidebar.origin.x * scale), int(sidebar.origin.y * scale),
                             int((sidebar.origin.x + sidebar.size.width) * scale), int((sidebar.origin.y + sidebar.size.height) * scale)))
        assert sum(a > 245 and r > 150 and g < 100 and b < 100 for r, g, b, a in crop.getdata()) > 40


def check_diff(c, directory, original):
    patch = next(artifact for artifact in original.artifacts if artifact.kind == "patch")
    c.browser.select_type(artifact_key(patch.id))
    c.show_selected_output()
    assert not c.code_scroll.isHidden() and not c.diff_selector.isHidden()
    assert c.copy_text() == patch.content
    assert c.code_text.code_document.diff
    assert c.code_text.code_document.added and c.code_text.code_document.removed
    assert c.code_scroll.contentView().frame().origin.x >= c.code_scroll.verticalRulerView().frame().size.width
    assert all(row.old_line is None for row in c.code_text.code_document.rows if row.kind == "add")
    assert all(row.new_line is None for row in c.code_text.code_document.rows if row.kind == "remove")
    before = c.context.previous_answer
    c.save_view_image(directory / "diff-output.png")
    c.diff_selector.setSelectedSegment_(1)
    c.changeDiffView_(c.diff_selector)
    assert c.copy_text() == patch.diff_context.before
    assert not c.annotation.isEnabled()
    c.save_view_image(directory / "current-code.png")
    c.diff_selector.setSelectedSegment_(2)
    c.changeDiffView_(c.diff_selector)
    assert c.copy_text() == patch.diff_context.after
    assert ast.dump(ast.parse(c.copy_text(explained=True))) == ast.dump(ast.parse(patch.diff_context.after))
    assert "# " in str(c.code_text.string())
    assert c.context.previous_answer == before
    c.save_view_image(directory / "proposed-code.png")
    c.diff_selector.setSelectedSegment_(0)
    c.changeDiffView_(c.diff_selector)
    c.set_click_through(True)
    c.save_view_image(directory / "transparent-diff.png")
    assert c.window.alphaValue() == 1
    c.set_click_through(False)


def check_image(c, directory, current, code_key, source):
    request = {"status": "pending", "action": "generate", "prompt": "Synthetic tall image", "caption": "Synthetic fixture"}
    image_artifact = Artifact(id="architecture", title="System design", kind="image", content=json.dumps(request),
                              language="", path="", basis="discussion", source_ids=[source], annotations=[], nodes=[], edges=[])
    c.browser.select_type(code_key)
    c.show_selected_output()
    held = c.browser.visible.id
    response = current.model_copy(update={"artifacts": [current.artifacts[0], image_artifact]}, deep=True)
    publish(c, response)
    assert c.browser.state.types[artifact_key(image_artifact.id)].newer_count == 0
    picture = Image.new("RGB", (900, 1200), "#eee0ca")
    ImageDraw.Draw(picture).text((60, 100), "SYNTHETIC IMAGE\nNative fit / scroll fixture", fill="#30291f", font_size=44)
    raw = io.BytesIO()
    picture.save(raw, "PNG")
    with patch("otsc.images.app_directory", return_value=directory):
        asset = store_png(raw.getvalue())
        job = ImageJob(c.context.session_id, image_artifact.id, image_key(request, c.settings.image), request,
                       c.settings.image, Cancellation(), c.context.task_revision)
        before_code_versions = len(c.browser.state.types[code_key].versions)
        c.accept_image({"job": job, "asset": asset, "seconds": 0})
        assert c.browser.visible.id == held
        assert len(c.browser.state.types[code_key].versions) == before_code_versions
        assert c.browser.state.types[artifact_key(image_artifact.id)].newer_count == 1
        c.chooseOutputType_(c.type_buttons[artifact_key(image_artifact.id)])
        assert not c.image_scroll.isHidden() and c.body_scroll.isHidden()
        c.window.setContentSize_((900, 740))
        assert abs(c.image_view.frame().size.width - c.image_scroll.contentSize().width) < 1
        c.page_output(1)
        assert c.image_scroll.contentView().bounds().origin.y > 0
        c.image_scroll.contentView().scrollToPoint_((0, 0))
        c.save_view_image(directory / "image-output.png")
        c.copyClean_(None)
        assert A.NSPasteboard.generalPasteboard().canReadObjectForClasses_options_([A.NSImage], {})
    c.browser.select_type(code_key)
    c.show_selected_output()
