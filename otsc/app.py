"""A single native AppKit application, with retained views and background workers."""

import json
import math
import queue
import threading
import time
from pathlib import Path
from uuid import uuid4

import AppKit as A
import objc
from Foundation import NSMakeRect, NSObject, NSTimer
from PyObjCTools import AppHelper

from otsc.capture import AudioCapture, ScreenCapture
from otsc.context import ContextStore
from otsc.context_builder import ContextBuilder
from otsc.demo import DemoProvider, seed_demo
from otsc.diagram import diagram_layout, diagram_svg, edge_geometry
from otsc.images import ImageCoordinator, image_path, resolve_image, update_image_memory
from otsc.midi import MidiInput
from otsc.native import (
    FlippedView,
    button,
    color,
    field,
    frame,
    label,
    popup,
    retain_text,
    scroll_text,
    set_overlay_appearance,
)
from otsc.output_history import OutputHistory
from otsc.perception import read_screen
from otsc.planning import TaskPlanningProvider
from otsc.preferences import Preferences
from otsc.privacy import private_directory, private_write, redact
from otsc.providers import provider_for
from otsc.scheduler import Cancellation, Cancelled, Coordinator
from otsc.sessions import checkpoint, load_session, restore_context, save_session
from otsc.settings import Credentials, Settings, load_settings, save_settings
from otsc.task_details import TaskDetails
from otsc.telemetry import Progress, TraceStore, digest
from otsc.workspace import read_project


class DiagramView(FlippedView):
    def drawRect_(self, rect):
        color("fffdf9", alpha=getattr(self, "background_alpha", 1.0)).setFill()
        A.NSRectFillUsingOperation(self.bounds(), A.NSCompositingOperationCopy)
        artifact = getattr(self, "artifact", None)
        if not artifact:
            return
        boxes, height = diagram_layout(artifact, self.bounds().size.width)
        paragraph = A.NSMutableParagraphStyle.alloc().init()
        paragraph.setAlignment_(A.NSTextAlignmentCenter)
        attrs = {
            A.NSFontAttributeName: A.NSFont.systemFontOfSize_(14),
            A.NSForegroundColorAttributeName: color("30291f"),
            A.NSParagraphStyleAttributeName: paragraph,
        }
        for edge in artifact.edges:
            start, c1, c2, end, label_position = edge_geometry(edge, boxes)
            color("987454").setStroke()
            path = A.NSBezierPath.bezierPath()
            path.setLineWidth_(1.6)
            path.moveToPoint_(start)
            path.curveToPoint_controlPoint1_controlPoint2_(end, c1, c2)
            path.stroke()
            arrow = A.NSBezierPath.bezierPath()
            angle = math.atan2(end[1] - c2[1], end[0] - c2[0])
            arrow.moveToPoint_((end[0] - 8 * math.cos(angle - 0.5), end[1] - 8 * math.sin(angle - 0.5)))
            arrow.lineToPoint_(end)
            arrow.lineToPoint_((end[0] - 8 * math.cos(angle + 0.5), end[1] - 8 * math.sin(angle + 0.5)))
            arrow.stroke()
            A.NSString.stringWithString_(edge.label).drawInRect_withAttributes_(
                NSMakeRect(label_position[0] - 55, label_position[1], 110, 34),
                {**attrs, A.NSFontAttributeName: A.NSFont.systemFontOfSize_(11)},
            )
        for node in artifact.nodes:
            x, y, w, h = boxes[node.id]
            path = A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(x, y, w, h), 10, 10)
            color("f2e9dc").setFill()
            path.fill()
            color("c6b59f").setStroke()
            path.stroke()
            A.NSString.stringWithString_(node.label).drawInRect_withAttributes_(
                NSMakeRect(x + 10, y + 16, w - 20, h - 20), attrs
            )
        A.NSString.stringWithString_(artifact.content).drawInRect_withAttributes_(
            NSMakeRect(20, height, self.bounds().size.width - 40, 220),
            {A.NSFontAttributeName: A.NSFont.systemFontOfSize_(14), A.NSForegroundColorAttributeName: color("30291f")},
        )


class Controller(NSObject):
    def initWithOptions_(self, options):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.options = options
        self.demo = bool(options.demo or options.design_demo or options.smoke_test)
        self.settings_error = ""
        try:
            self.settings = Settings() if self.demo else load_settings()
        except Exception as error:
            self.settings = Settings()
            self.settings_error = "Settings could not be loaded: " + redact(str(error))[:240]
        self.credentials = Credentials()
        self.context = ContextStore()
        self.context.set_goal(self.settings.goal)
        self.context.set_repo(self.settings.project_folder)
        self.events = queue.Queue()
        self.trace = TraceStore(
            enabled=not self.demo and self.settings.operational_metadata,
            source=getattr(options, "trace_source", "interactive"),
        )
        self.coordinator = Coordinator(
            self.context, self.get_provider, hourly_limit=self.settings.hourly_requests, trace=self.trace
        )
        self.output_history = OutputHistory()
        self.image_worker = ImageCoordinator(self.events, lambda: self.settings.image, self.credentials, trace=self.trace)
        self.history_choices = []
        self.context_builder = ContextBuilder(self.context, lambda: self.get_provider("context_builder"), trace=self.trace)
        self.context_build_pending = False
        self.auto_help_pending = False
        self.next_answer_attempt = 0
        self.checkpoint_signature = None
        self.checkpoint_at = 0
        self.loaded_base_hashes = {}
        self.restored_artifact_keys = set()
        self.task_details_window = None
        self.screen = ScreenCapture()
        self.audio = None
        self.midi = None
        self.overlay_hidden = False
        self.window_sharing = False
        self.capture_hidden_for = ""
        self.capture_frame_pending = False
        self.voice_recording = False
        self.voice_started_audio = False
        self.stop_voice_after_help = False
        self.running = False
        self.capture_busy = False
        self.capture_generation = 0
        self.capture_id = ""
        self.capture_token = None
        self.capture_failures = 0
        self.capture_request_after = False
        self.pending_manual = False
        self.manual_help_pending = False
        self.awaiting_audio_flush = None
        self.buffered_context = []
        self.next_capture = 0
        self.selected_id = ""
        self.displayed_artifacts = []
        self.artifact_context = None
        self.preferences = None
        self.closed = False
        self.smoke_step = 0
        self.smoke_started = time.monotonic()
        self.make_window()
        self.make_menu()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.10, self, "tick:", None, True
        )
        if self.demo:
            seed_demo(self.context, design=bool(options.design_demo))
            self.goal.setStringValue_(self.context.goal)
            self.status.setStringValue_("Synthetic demo · No API calls, microphone, or screen capture.")
            self.coordinator.request(manual=True)
        elif self.settings_error:
            self.status.setStringValue_(self.settings_error)
        if not options.smoke_test:
            self.window.makeKeyAndOrderFront_(None)
            A.NSApp.activateIgnoringOtherApps_(True)
            if not self.demo and not self.settings.configured:
                self.settings_(None)
        if not self.demo and not getattr(options, "disable_midi", False):
            self.midi = MidiInput(self.events)
            self.midi.start()
        return self

    @objc.python_method
    def get_provider(self, lane):
        if self.demo:
            return DemoProvider()
        provider = provider_for(getattr(self.settings, lane), self.credentials)
        if lane == "deep" and hasattr(provider, "generate_json"):
            provider = TaskPlanningProvider(provider, provider_for(self.settings.planner, self.credentials))
        if lane == "deep" and self.settings.inspection_enabled and hasattr(provider, "generate_json"):
            from otsc.inspection import InspectionProvider

            return InspectionProvider(provider)
        return provider

    @objc.python_method
    def make_window(self):
        x, y, w, h = self.settings.window_bounds
        style = (
            A.NSWindowStyleMaskTitled
            | A.NSWindowStyleMaskClosable
            | A.NSWindowStyleMaskMiniaturizable
            | A.NSWindowStyleMaskResizable
        )
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, w, h), style, A.NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Over The Shoulder Coder" + (" · Synthetic demo" if self.demo else ""))
        # Start with the prototype's exclusion from PyAutoGUI/Pillow capture.
        self.window.setSharingType_(A.NSWindowSharingNone)
        self.window.setReleasedWhenClosed_(False)
        self.window.setMinSize_((820, 650))
        self.window.setDelegate_(self)
        self.window.setBackgroundColor_(color("f8f4ec"))
        self.window.setLevel_(A.NSFloatingWindowLevel)
        self.root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.window.setContentView_(self.root)
        self.title = label(self.root, "Over The Shoulder Coder", size=22, bold=True)
        self.subtitle = label(
            self.root, "Code, design, and explanations informed by your screen and conversation.", size=12
        )
        self.controls = {}
        for name, title, action in [
            ("help", "Help now", "helpNow:"),
            ("capture", "Capture now", "captureNow:"),
            ("start", "Start following", "toggleRunning:"),
            ("settings", "Settings", "settings:"),
            ("project", "Project…", "selectProject:"),
            ("new", "New task", "newTask:"),
            ("sharing", "Sharing: Off", "toggleSharing:"),
        ]:
            self.controls[name] = button(self.root, title, self, action)
        self.controls["sharing"].setToolTip_(
            "Off: visible to you, excluded from this app's screenshots. On: allow sharing; hide briefly for this app's captures. Starts Off each launch."
        )
        self.goal = field(
            self.root,
            self.context.goal,
            "What are you working on? You can also let the screen and conversation establish this.",
        )
        self.goal.setTarget_(self)
        self.goal.setAction_("helpNow:")
        self.source = popup(self.root, ["You", "Other person", "Uncertain speaker", "Screen / code"], "You")
        self.input_scroll, self.input = scroll_text(self.root, editable=True)
        self.add_button = button(self.root, "Add context", self, "addContext:")
        self.summary_scroll, self.summary = scroll_text(self.root)
        retain_text(
            self.summary, "Ready when you are. Add context, capture the current work, or start following your task."
        )
        self.view = popup(
            self.root,
            ["Artifact", "Conversation", "Context", "Observed files", "History"],
            "Artifact",
            self,
            "changeView:",
        )
        self.artifacts = popup(self.root, ["No artifact yet"], target=self, action="chooseArtifact:")
        self.annotation = button(self.root, "Explain each line", self, "changeView:", checkbox=True)
        self.annotation.setState_(1)
        self.pin = button(self.root, "Pin", self, "togglePin:")
        self.older = button(self.root, "Older", self, "olderOutput:")
        self.newer = button(self.root, "Newer", self, "newerOutput:")
        self.latest = button(self.root, "Latest", self, "latestOutput:")
        self.older.setToolTip_("Show the previous output and freeze the pane.")
        self.newer.setToolTip_("Show the next output; keep the pane frozen.")
        self.latest.setToolTip_("Jump to the newest output and resume live updates.")
        self.copy = button(self.root, "Copy clean", self, "copyClean:")
        self.copy_notes = button(self.root, "Copy explained", self, "copyExplained:")
        self.export = button(self.root, "Export…", self, "exportArtifact:")
        self.body_scroll, self.body = scroll_text(self.root, monospace=True)
        self.graph_scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        self.graph_scroll.setHasVerticalScroller_(True)
        self.graph_scroll.setAutohidesScrollers_(True)
        self.graph_scroll.setBorderType_(A.NSBezelBorder)
        self.graph = DiagramView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        self.graph_scroll.setDocumentView_(self.graph)
        self.root.addSubview_(self.graph_scroll)
        self.graph_scroll.setHidden_(True)
        self.image_scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        self.image_scroll.setHasVerticalScroller_(True)
        self.image_scroll.setAutohidesScrollers_(True)
        self.image_scroll.setBorderType_(A.NSBezelBorder)
        self.image_canvas = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        self.image_view = A.NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
        self.image_view.setImageScaling_(A.NSImageScaleProportionallyUpOrDown)
        self.image_canvas.addSubview_(self.image_view)
        self.image_scroll.setDocumentView_(self.image_canvas)
        self.root.addSubview_(self.image_scroll)
        self.image_scroll.setHidden_(True)
        self.loaded_image_id = ""
        self.status = label(
            self.root, "Capture is paused. Cmd-Return asks for help; all capture starts explicitly.", size=12
        )
        self.root.layout_owner = self
        self.relayout()
        self.update_navigation_controls()

    @objc.python_method
    def make_menu(self):
        menu = A.NSMenu.alloc().init()
        app_item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Over The Shoulder Coder", None, "")
        submenu = A.NSMenu.alloc().init()
        for title, action, key in [
            ("Help now", "helpNow:", "\r"),
            ("Settings…", "settings:", ","),
            ("Toggle click-through", "toggleClickThrough:", "i"),
            ("Hide / show window", "toggleVisibility:", ""),
            ("New task", "newTask:", "n"),
            ("Task details…", "taskDetails:", ""),
            ("Save task session", "saveSession:", "s"),
            ("Open task session…", "openSession:", "o"),
            ("Resume last saved task", "resumeLastSession:", ""),
            ("Remember sessions locally", "toggleSessionMemory:", ""),
            ("Delete saved task sessions…", "deleteSessions:", ""),
            ("Inspect context before deep responses", "toggleInspection:", ""),
            ("Mark suggestion useful", "markUseful:", ""),
            ("Mark suggestion needs work", "markNeedsWork:", ""),
            ("Show diagnostics", "showDiagnostics:", ""),
            ("Load synthetic code example", "demoCode:", ""),
            ("Load synthetic design example", "demoDesign:", ""),
            ("Quit Over The Shoulder Coder", "quit:", "q"),
        ]:
            item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self)
            if action == "toggleSessionMemory:":
                item.setState_(int(self.settings.local_session_memory))
                self.memory_menu_item = item
            if action == "toggleInspection:":
                item.setState_(int(self.settings.inspection_enabled))
                self.inspection_menu_item = item
            if action == "toggleClickThrough:":
                item.setKeyEquivalentModifierMask_(A.NSEventModifierFlagCommand | A.NSEventModifierFlagShift)
            submenu.addItem_(item)
        app_item.setSubmenu_(submenu)
        menu.addItem_(app_item)
        edit = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Edit", None, "")
        edit_menu = A.NSMenu.alloc().initWithTitle_("Edit")
        for title, action, key in [
            ("Undo", "undo:", "z"),
            ("Cut", "cut:", "x"),
            ("Copy", "copy:", "c"),
            ("Paste", "paste:", "v"),
            ("Select All", "selectAll:", "a"),
        ]:
            edit_menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
        edit.setSubmenu_(edit_menu)
        menu.addItem_(edit)
        A.NSApp.setMainMenu_(menu)

    @objc.python_method
    def relayout(self):
        if not hasattr(self, "status"):
            return
        w, h = self.root.bounds().size
        frame(self.title, 20, 15, w - 40, 30)
        frame(self.subtitle, 20, 47, w - 40, 24)
        x = 16
        for name, width in [
            ("help", 104),
            ("capture", 112),
            ("start", 136),
            ("project", 105),
            ("settings", 94),
            ("new", 94),
            ("sharing", 112),
        ]:
            frame(self.controls[name], x, 77, width, 32)
            x += width + 5
        frame(self.goal, 20, 118, w - 40, 30)
        frame(self.source, 18, 160, 160, 30)
        frame(self.add_button, 20, 194, 156, 30)
        frame(self.input_scroll, 187, 159, w - 207, 68)
        frame(self.summary_scroll, 20, 240, w - 40, 95)
        frame(self.view, 18, 346, 158, 30)
        frame(self.artifacts, 187, 346, w - 407, 30)
        frame(self.annotation, w - 212, 347, 195, 28)
        frame(self.body_scroll, 20, 388, w - 40, h - 485)
        frame(self.graph_scroll, 20, 388, w - 40, h - 485)
        frame(self.image_scroll, 20, 388, w - 40, h - 485)
        picture = self.image_view.image()
        if picture:
            width = self.image_scroll.contentSize().width
            height = width * picture.size().height / max(1, picture.size().width)
            self.image_canvas.setFrameSize_((width, max(height, self.image_scroll.contentSize().height)))
            frame(self.image_view, 0, 0, width, height)
        if getattr(self.graph, "artifact", None):
            width = self.graph_scroll.contentSize().width
            _, height = diagram_layout(self.graph.artifact, width)
            self.graph.setFrameSize_((width, height + 240))
            self.graph.setNeedsDisplay_(True)
        frame(self.pin, 16, h - 88, 108, 30)
        frame(self.copy, 130, h - 88, 110, 30)
        frame(self.copy_notes, 246, h - 88, 145, 30)
        frame(self.export, 399, h - 88, 100, 30)
        frame(self.older, w - 285, h - 88, 80, 30)
        frame(self.newer, w - 200, h - 88, 80, 30)
        frame(self.latest, w - 115, h - 88, 100, 30)
        frame(self.status, 20, h - 49, w - 40, 45)

    @objc.python_method
    def apply_settings(self, settings):
        self.pause()
        self.settings = settings
        self.trace.enabled = settings.operational_metadata and not self.demo
        self.coordinator.hourly_limit = settings.hourly_requests
        self.coordinator.cancel()
        self.image_worker.cancel()
        self.status.setStringValue_("Settings saved. Press Start following or Help now when ready.")

    def settings_(self, sender):
        if self.demo:
            self.status.setStringValue_(
                "Synthetic demo uses no providers. Launch without --demo to configure live models."
            )
            return
        self.pause()
        self.preferences = Preferences.alloc().initWithController_(self)
        self.preferences.window.makeKeyAndOrderFront_(None)

    def helpNow_(self, sender):
        self.context.set_goal(str(self.goal.stringValue()))
        self.flush_context()
        if not self.demo and not self.settings.configured:
            self.settings_(None)
            return
        if self.demo:
            self.coordinator.request(manual=True)
            return
        self.coordinator.cancel()
        self.manual_help_pending = True
        self.pending_manual = True
        self.awaiting_audio_flush = None
        if self.audio and not self.audio.stop_event.is_set() and self.settings.transcription != "disabled":
            self.awaiting_audio_flush = self.audio.flush_for_help(stop_capture=self.stop_voice_after_help)
        self.capture_context(request_after=False)
        self.context_build_pending = True
        self.capture_request_after = True
        self.send_manual_help_if_ready()

    @objc.python_method
    def send_manual_help_if_ready(self):
        if (self.manual_help_pending and self.awaiting_audio_flush is None
                and (self.context.snapshot().observations or not self.capture_busy)):
            self.manual_help_pending = False
            self.pending_manual = False
            self.flush_context()
            self.coordinator.request(manual=True)
            self.finish_voice_capture()

    @objc.python_method
    def finish_voice_capture(self):
        if self.stop_voice_after_help:
            self.stop_voice_after_help = False
            if self.audio and not self.running:
                self.audio.stop()
                self.audio = None

    def addContext_(self, sender):
        text = str(self.input.string()).strip()
        if not text:
            return
        self.context.set_goal(str(self.goal.stringValue()))
        choice = str(self.source.titleOfSelectedItem())
        channel, speaker, kind = {
            "You": ("typed", "primary_user", "note"),
            "Other person": ("typed", "other_people", "note"),
            "Uncertain speaker": ("typed", "uncertain", "note"),
            "Screen / code": ("screen", "not_applicable", "screen"),
        }[choice]
        self.context.add(kind, text, channel, speaker, interrupting=True)
        self.input.setString_("")
        self.status.setStringValue_("Context added. Press Help now, or let following pick it up.")
        self.refresh_body()

    def captureNow_(self, sender):
        if self.demo:
            self.status.setStringValue_(
                "Capture is disabled in the synthetic demo. Add typed context to exercise the workflow."
            )
            return
        self.capture_context(request_after=False)

    def toggleRunning_(self, sender):
        if self.running:
            self.pause()
            return
        if self.demo:
            self.running = True
            self.next_capture = time.monotonic() + self.settings.interval_seconds
            self.controls["start"].setTitle_("Pause following")
            self.status.setStringValue_(
                "Synthetic cadence is active. Unchanged context will not regenerate a response."
            )
            return
        if not self.settings.configured:
            self.settings_(None)
            return
        self.context.set_goal(str(self.goal.stringValue()))
        try:
            if self.audio is None or self.audio.stop_event.is_set():
                self.audio = AudioCapture(self.settings, self.events, self.credentials)
                self.audio.start()
            self.voice_started_audio = False
            self.running = True
            self.controls["start"].setTitle_("Pause following")
            self.next_capture = 0
            self.capture_context(request_after=True)
        except Exception as error:
            self.pause()
            self.status.setStringValue_(redact(str(error)))

    @objc.python_method
    def pause(self):
        self.running = False
        self.capture_generation += 1
        if self.capture_token:
            self.capture_token.cancel()
        self.finish_screen_capture(self.capture_id)
        self.context_builder.cancel()
        self.context_build_pending = self.auto_help_pending = False
        self.capture_request_after = False
        self.pending_manual = False
        self.manual_help_pending = False
        self.awaiting_audio_flush = None
        self.voice_recording = False
        self.voice_started_audio = False
        self.stop_voice_after_help = False
        self.flush_context()
        if self.audio:
            self.audio.stop()
            self.audio = None
        self.coordinator.cancel()
        self.controls["start"].setTitle_("Start following")
        self.status.setStringValue_("Following paused. Current work stays available.")

    @objc.python_method
    def capture_context(self, request_after=False):
        if self.capture_busy:
            self.capture_request_after |= request_after
            return
        self.context.set_goal(str(self.goal.stringValue()))
        self.capture_busy = True
        self.capture_request_after = request_after
        generation = self.capture_generation
        identity = self.capture_id = uuid4().hex
        token = self.capture_token = Cancellation()
        settings = self.settings.model_copy(deep=True)
        root = self.context.repo_root
        goal = self.context.goal
        hide_for_capture = bool(settings.capture_screen and self.window_sharing and not self.overlay_hidden
                                and self.window.isVisible() and not self.window.isMiniaturized())
        if settings.capture_screen:
            self.capture_frame_pending = True
            self.controls["sharing"].setEnabled_(False)
        if hide_for_capture:
            self.capture_hidden_for = identity
            self.window.orderOut_(None)
        self.status.setStringValue_("Reading the screen with Astra low / Fast…" if settings.capture_screen else "Reading the selected project…")

        finish_scheduled = threading.Event()

        def finish_capture():
            if not finish_scheduled.is_set():
                finish_scheduled.set()
                # Dispatch directly to AppKit; do not wait for the 100 ms event poll.
                AppHelper.callAfter(self.finish_screen_capture, identity)

        def worker():
            result = {"type": "capture_done", "generation": generation, "capture_id": identity, "root": root, "at": time.time()}
            started = time.monotonic()
            try:
                try:
                    token.check()
                    if settings.capture_screen:
                        if hide_for_capture:
                            token.event.wait(0.010)
                            token.check()
                        _, path = self.screen.capture(
                            settings.screen_region,
                            keep_image=True, full_resolution=True, on_captured=finish_capture,
                        )
                finally:
                    # Also restore on screenshot failure or cancellation before capture.
                    finish_capture()
                if settings.capture_screen:
                    token.check()
                    progress = Progress(lambda text: None, self.trace, request_id=identity, lane="ocr")
                    reading = read_screen(path, provider_for(settings.ocr, self.credentials), token, progress, at=result["at"])
                    result["screen"] = (reading.visible_text, path)
                    result["reading"] = reading.model_dump()
                if root:
                    token.check()
                    result["files"], result["files_message"] = read_project(root, focus=goal)
                token.check()
            except Cancelled:
                result["cancelled"] = True
            except Exception as error:
                result["error"] = redact(str(error))[:450]
            result["seconds"] = time.monotonic() - started
            self.trace.record("screen_reading_finished", request_id=identity, lane="ocr",
                              outcome="cancelled" if result.get("cancelled") else "error" if result.get("error") else "success",
                              elapsed_ms=round(result["seconds"] * 1000))
            self.events.put(result)

        threading.Thread(target=worker, name="otsc-screen-reader", daemon=True).start()

    @objc.python_method
    def finish_screen_capture(self, identity):
        if identity != self.capture_id:
            return
        self.capture_frame_pending = False
        self.controls["sharing"].setEnabled_(True)
        if identity and self.capture_hidden_for == identity:
            self.capture_hidden_for = ""
            if not self.closed and not self.overlay_hidden:
                self.window.orderFrontRegardless()

    def toggleSharing_(self, sender):
        if self.capture_frame_pending:
            return
        self.window_sharing = not self.window_sharing
        self.window.setSharingType_(A.NSWindowSharingReadOnly if self.window_sharing else A.NSWindowSharingNone)
        self.controls["sharing"].setTitle_("Sharing: On" if self.window_sharing else "Sharing: Off")
        self.status.setStringValue_(
            "Sharing on: window can be shared; hidden briefly for this app's own screenshots."
            if self.window_sharing else "Sharing off: window excluded from this app's screenshots, but still visible to you."
        )

    def selectProject_(self, sender):
        if self.demo:
            self.status.setStringValue_("The synthetic demo does not read local project files.")
            return
        panel = A.NSOpenPanel.openPanel()
        panel.setCanChooseDirectories_(True)
        panel.setCanChooseFiles_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setMessage_(
            "Select the project whose source files may be included in assistance requests. The app only reads this folder."
        )
        if panel.runModal() == A.NSModalResponseOK:
            root = str(panel.URL().path())
            self.context.set_repo(root)
            self.settings.project_folder = root
            save_settings(self.settings)
            self.status.setStringValue_("Reading selected project…")
            generation = self.capture_generation

            def read():
                try:
                    files, message = read_project(root, focus=self.context.goal)
                    self.events.put(
                        {"type": "project", "root": root, "files": files, "message": message, "generation": generation}
                    )
                except Exception as error:
                    self.events.put({"type": "notice", "message": redact(str(error))})

            threading.Thread(target=read, daemon=True).start()

    def tick_(self, timer):
        if self.closed:
            return
        for source in (self.events, self.context_builder.events, self.coordinator.events):
            for _ in range(100):
                try:
                    event = source.get_nowait()
                except queue.Empty:
                    break
                self.handle_event(event)
        now = time.monotonic()
        if self.running and now >= self.next_capture:
            if self.demo:
                self.next_capture = now + self.settings.interval_seconds
                self.coordinator.request()
            elif not self.capture_busy:
                self.capture_context(request_after=True)
        if not self.demo:
            if self.running or self.context_build_pending:
                if self.context_builder.request():
                    self.context_build_pending = False
            if (self.running or self.auto_help_pending) and not self.manual_help_pending and not self.voice_recording:
                if not self.coordinator.active_lanes and now >= self.next_answer_attempt:
                    requested = self.coordinator.request()
                    self.next_answer_attempt = now + (0.1 if requested else 1)
                    if requested or self.context.revision == self.coordinator.last_requested_revision:
                        self.auto_help_pending = False
        if self.options.smoke_test:
            self.smoke_tick()
        elif not self.demo:
            self.checkpoint_if_enabled()

    @objc.python_method
    def handle_event(self, event):
        kind = event["type"]
        if kind == "midi":
            self.midi_action(event["action"])
        elif kind == "midi_status":
            self.status.setStringValue_(event["message"])
        elif kind == "capture_done":
            if event.get("capture_id") != self.capture_id:
                return
            self.capture_busy = False
            self.capture_token = None
            if event["generation"] != self.capture_generation or event.get("cancelled"):
                return
            self.apply_context_event(event)
            if event.get("error"):
                self.capture_failures += 1
                self.next_capture = time.monotonic() + min(60, 2 ** min(self.capture_failures, 6))
            else:
                self.capture_failures = 0
                # Only error retries and screen-disabled project polling wait.
                # Successful visual reading starts the next fresh frame immediately.
                self.next_capture = 0 if self.settings.capture_screen else time.monotonic() + self.settings.interval_seconds
                self.context_build_pending = True
            self.status.setStringValue_(
                event.get("error") or event.get("files_message") or "Current screen added to context."
            )
            should_request = self.capture_request_after
            self.capture_request_after = False
            if self.manual_help_pending:
                self.send_manual_help_if_ready()
            if should_request and not event.get("error"):
                self.auto_help_pending = True
            self.refresh_passive_views()
        elif kind == "project":
            if event["generation"] == self.capture_generation:
                self.context.set_verified_files(event["root"], event["files"])
                self.context_build_pending = self.running
                self.status.setStringValue_(event["message"])
                self.refresh_passive_views()
        elif kind in {"speech", "audio_status", "audio_error", "audio_flushed"}:
            if not self.audio or self.audio.id != event["capture_id"]:
                return
            if kind == "audio_flushed":
                if event["flush_id"] == self.awaiting_audio_flush:
                    self.awaiting_audio_flush = None
                    if not event["complete"]:
                        self.manual_help_pending = False
                        self.pending_manual = False
                        self.finish_voice_capture()
                        self.status.setStringValue_(
                            "Recent speech could not be finished. Check the audio settings; the current screen is still available."
                        )
                    else:
                        self.send_manual_help_if_ready()
            elif kind == "speech":
                self.apply_context_event(event)
                self.status.setStringValue_(f"Heard {event['speaker']} through {event['channel']}. Context updated.")
                self.refresh_passive_views()
            else:
                if kind == "audio_error":
                    self.manual_help_pending = False
                    self.pending_manual = False
                    self.awaiting_audio_flush = None
                    self.voice_recording = False
                    self.voice_started_audio = False
                    self.finish_voice_capture()
                self.status.setStringValue_(event["message"])
        elif kind == "context_built":
            changed = self.context_builder.accept(event)
            if changed:
                self.refresh_passive_views()
            elif event.get("error") and not event["job"][2].event.is_set():
                self.status.setStringValue_("Context update failed; keeping existing context. " + event["error"])
        elif kind == "image_ready":
            self.accept_image(event)
        elif kind == "started":
            self.status.setStringValue_(f"Quick and deep assistance started · context {event['revision']}.")
        elif (kind == "progress" and event["request_id"] == self.coordinator.request_id
              and event["lane"] in self.coordinator.active_lanes):
            self.status.setStringValue_(event["lane"].capitalize() + ": " + event["message"][:280])
            if (
                event["lane"] == "quick"
                and event["message"].startswith("Draft: ")
                and not self.output_history.frozen
                and not self.summary.selectedRange().length
                and not self.body.selectedRange().length
                and self.coordinator.published_lane < 1
                and self.context.task_revision == self.coordinator.last_requested_task_revision
            ):
                retain_text(self.summary, "Quick thought\n\n" + event["message"][7:])
        elif kind in {"result", "error", "cancelled"}:
            if kind == "result" and (self.body.selectedRange().length or self.summary.selectedRange().length):
                self.output_history.freeze()
            if not self.coordinator.accept(event):
                return
            if kind == "result":
                job = event["job"]
                self.output_history.append(
                    event["response"], identity=job.request_id + ":" + job.lane,
                    session_id=job.snapshot.session_id, goal=job.snapshot.goal,
                    lane=job.lane, sources=job.snapshot.observations,
                )
                if job.lane == "deep" and not self.demo:
                    self.image_worker.submit(event["response"], job.snapshot.session_id, job.snapshot.task_revision)
                self.update_navigation_controls()
                if self.output_history.frozen:
                    if str(self.view.titleOfSelectedItem()) == "History":
                        self.populate_output_picker()
                    self.show_frozen_status()
                else:
                    self.render_response()
                    self.status.setStringValue_(
                        f"{event['job'].lane.capitalize()} response · {event['elapsed']:.1f}s · context {event['job'].snapshot.revision}."
                        + (" Synthetic replay." if self.demo else "")
                    )
            elif kind == "error":
                self.status.setStringValue_(event["job"].lane.capitalize() + ": " + event["message"])
        elif kind == "notice":
            self.status.setStringValue_(event["message"])

    @objc.python_method
    def accept_image(self, event):
        job = event["job"]
        if (event.get("cancelled") or job.token.event.is_set() or job.session_id != self.context.session_id
                or job.task_revision != self.context.task_revision):
            return
        latest = self.output_history.latest
        if not latest or latest.session_id != job.session_id:
            return
        artifacts, changed = resolve_image(latest.artifacts, job, event.get("asset"), event.get("error"))
        if not changed:
            return
        if self.body.selectedRange().length or self.summary.selectedRange().length:
            self.output_history.freeze()
        # Publish a new immutable display entry. Older/pinned entries stay exact.
        response = latest.response.model_copy(deep=True)
        response.artifacts = artifacts
        self.output_history.append(response, session_id=latest.session_id, goal=latest.goal, lane="image",
                                   sources=latest.sources, artifacts=artifacts)
        update_image_memory(self.context, job, event.get("asset"), event.get("error"))
        if self.coordinator.current:
            self.coordinator.current.artifacts, _ = resolve_image(self.coordinator.current.artifacts, job,
                                                                  event.get("asset"), event.get("error"))
        self.update_navigation_controls()
        if self.output_history.frozen:
            if str(self.view.titleOfSelectedItem()) == "History":
                self.populate_output_picker()
            self.show_frozen_status()
        else:
            self.render_response()
            self.status.setStringValue_(event.get("error") or f"Generated image ready · {event['seconds']:.1f}s.")

    @objc.python_method
    def apply_context_event(self, event):
        if event["type"] == "speech":
            self.context.add(
                "speech", event["text"], event["channel"], event["speaker"], at=event["at"], confidence="transcribed"
            )
        else:
            if "screen" in event:
                text, path = event["screen"]
                self.context.add("screen", text, "screen", image_path=path, confidence="uncertain", at=event.get("at"),
                                 reading=event.get("reading"), keep_repeats=True)
            if "files" in event:
                self.context.set_verified_files(event["root"], event["files"])

    @objc.python_method
    def flush_context(self):
        pending, self.buffered_context = self.buffered_context, []
        for event in pending:
            self.apply_context_event(event)

    @objc.python_method
    def render_response(self):
        output = self.output_history.visible
        if not output:
            self.update_navigation_controls()
            return
        response = output.response
        retain_text(self.summary, response.task + "\n\n" + response.summary)
        self.displayed_artifacts = output.artifacts
        self.artifact_context = (output.session_id, output.goal)
        self.populate_output_picker()
        self.refresh_body()
        self.update_navigation_controls()

    @objc.python_method
    def populate_output_picker(self):
        if str(self.view.titleOfSelectedItem()) == "History":
            entries = list(reversed(self.output_history.entries))
            self.history_choices = [item.id for item in entries]
            self.artifacts.removeAllItems()
            self.artifacts.addItemsWithTitles_([item.title() for item in entries] or ["No saved output yet"])
            visible = self.output_history.visible
            index = next((i for i, item in enumerate(entries) if visible and item.id == visible.id), 0)
            self.artifacts.selectItemAtIndex_(index)
            return
        output = self.output_history.visible
        previous = (output.selected_artifact_id if output else "") or self.selected_id
        self.artifacts.removeAllItems()
        titles = [a.title for a in self.displayed_artifacts]
        self.artifacts.addItemsWithTitles_(titles or ["Conversation / next step"])
        index = next((i for i, a in enumerate(self.displayed_artifacts) if a.id == previous), 0)
        self.artifacts.selectItemAtIndex_(index)
        self.selected_id = self.displayed_artifacts[index].id if self.displayed_artifacts else ""
        if output:
            output.selected_artifact_id = self.selected_id

    @objc.python_method
    def displayed_response(self):
        output = self.output_history.visible
        return output.response if output else None

    @objc.python_method
    def refresh_passive_views(self):
        if self.body.selectedRange().length or self.summary.selectedRange().length:
            self.output_history.freeze()
            self.update_navigation_controls()
        if not self.output_history.frozen:
            self.refresh_body()

    @objc.python_method
    def update_navigation_controls(self):
        index, count = self.output_history.position, len(self.output_history.entries)
        self.older.setEnabled_(index > 0)
        self.newer.setEnabled_(0 <= index < count - 1)
        self.latest.setEnabled_(self.output_history.latest is not None)
        self.pin.setTitle_("Unpin" if self.output_history.frozen else "Pin")

    @objc.python_method
    def show_frozen_status(self):
        count = self.output_history.newer_count
        self.status.setStringValue_(f"Output frozen · {count} newer output{'s' if count != 1 else ''} available. Latest resumes live updates.")

    @objc.python_method
    def current_artifact(self):
        return next((a for a in self.displayed_artifacts if a.id == self.selected_id), None)

    @objc.python_method
    def refresh_body(self):
        response = self.displayed_response()
        view = str(self.view.titleOfSelectedItem())
        artifact = self.current_artifact()
        show_diagram = bool(view == "Artifact" and response and artifact and artifact.kind == "diagram")
        show_image = False
        image_error = ""
        if view == "Artifact" and artifact and artifact.kind == "image":
            try:
                data = json.loads(artifact.content)
                if data.get("status") == "ready":
                    path = image_path(data)
                    if self.loaded_image_id != data["asset_id"]:
                        picture = A.NSImage.alloc().initWithContentsOfFile_(str(path))
                        if not picture:
                            raise ValueError("Generated image could not be decoded")
                        self.image_view.setImage_(picture)
                        self.loaded_image_id = data["asset_id"]
                    show_image = True
                    self.relayout()
            except (ValueError, OSError, AttributeError) as error:
                image_error = "\n\n" + str(error)
        self.graph_scroll.setHidden_(not show_diagram)
        self.image_scroll.setHidden_(not show_image)
        self.body_scroll.setHidden_(show_diagram or show_image)
        if view == "Context":
            text = "Working context (revisable, source-linked)\n\n" + "\n".join(
                f"{item.kind} · {item.basis} · {', '.join(item.source_ids)}\n{item.text}"
                for item in self.context.context_items.values()
            ) + "\n\nCaptured observations\n\n" + "\n\n".join(
                f"{o.id} · {o.channel} · {o.speaker} · {o.confidence}\n{o.text}" for o in self.context.observations
            )
        elif view == "Observed files":
            excerpts = []
            for path, fragments in self.context.files.items():
                for index, fragment in enumerate(fragments, 1):
                    location = (
                        f"starts at line {fragment.first_line}" if fragment.first_line else "line position unknown"
                    )
                    state = " · retired from working context" if path in self.context.retired_files else ""
                    excerpts.append(f"{path} · excerpt {index} · {location}{state}\n{fragment.content}")
            text = "Observed on screen — partial files\n\n" + (
                "\n\n".join(excerpts) or "No file excerpts identified yet."
            )
            text += "\n\nFiles read from the selected project\n" + "\n".join(
                f"{p} ({len(c.splitlines())} lines)" for p, c in self.context.verified_files.items()
            )
            if self.context.repo_root:
                text += "\n\nSelected folder: " + self.context.repo_root
        elif view == "History":
            output = self.output_history.visible
            text = ("Choose an output from the menu above. Older/Newer browse without resuming updates.\n\n"
                    + output.title() + "\n\n" + response.summary + "\n\n"
                    + "\n\n".join(a.title + "\n" + (a.annotated_text() if self.annotation.state() else a.clean_text())
                                  for a in output.artifacts)) if output else "Previous outputs will appear here."
        elif response and (view == "Conversation" or not artifact):
            output = self.output_history.visible
            sources = {o.id: o for o in output.sources} if output else {}
            replies = []
            for reply in response.conversation:
                source = sources.get(reply.source_id)
                quote = source.text if source else "Earlier conversation"
                who = (
                    {"primary_user": "You", "other_people": "Other person", "uncertain": "Uncertain speaker"}.get(
                        source.speaker, "Context"
                    )
                    if source
                    else "Context"
                )
                replies.append(
                    f"{who}: {quote}\n\n{reply.action.capitalize()}\n{reply.text}\n\n{reply.artifact_effect}"
                )
            text = "\n\n——————\n\n".join(replies)
            if response.open_questions:
                text += "\n\nStill open\n" + "\n".join(response.open_questions)
            text = text or response.summary
        elif artifact:
            basis = {
                "example": "Example",
                "observed_fragment": "Based on a partial observation",
                "verified_file": "Compared with the selected project snapshot",
                "discussion": "Based on discussion",
            }[artifact.basis]
            if artifact.basis == "verified_file" and digest(artifact.model_dump()) in self.restored_artifact_keys:
                current = self.context.verified_files.get(artifact.path)
                original = self.coordinator.artifact_bases.get(digest(artifact.model_dump()), {}).get("base_hash")
                basis = (
                    "Saved proposal — base matches the selected snapshot"
                    if current is not None and digest(current) == original
                    else "Saved proposal — current file base has not been confirmed"
                )
            if artifact.kind == "diagram":
                self.graph.artifact = artifact
                self.relayout()
            text = (
                basis
                + (" · " + artifact.path if artifact.path else "")
                + "\n\n"
                + (artifact.annotated_text() if self.annotation.state() or artifact.kind in {"structured", "image"} else artifact.clean_text())
                + image_error
            )
        else:
            text = "Your artifact will appear here. You can select and copy text, resize the window, or pin a response while reading."
        retain_text(self.body, text)

    def changeView_(self, sender):
        if str(self.view.titleOfSelectedItem()) in {"Artifact", "History"} and sender is not self.annotation:
            self.output_history.freeze()
            self.show_frozen_status()
        self.populate_output_picker()
        self.refresh_body()
        self.update_navigation_controls()

    def chooseArtifact_(self, sender):
        if str(self.view.titleOfSelectedItem()) == "History":
            index = self.artifacts.indexOfSelectedItem()
            if 0 <= index < len(self.history_choices) and self.output_history.select(self.history_choices[index]):
                self.show_selected_output()
            return
        if self.displayed_artifacts:
            self.selected_id = self.displayed_artifacts[self.artifacts.indexOfSelectedItem()].id
            if self.output_history.visible:
                self.output_history.visible.selected_artifact_id = self.selected_id
        self.output_history.freeze()
        self.refresh_body()
        self.update_navigation_controls()
        self.show_frozen_status()

    def togglePin_(self, sender):
        if self.output_history.frozen:
            self.latestOutput_(None)
        else:
            self.output_history.freeze()
            self.update_navigation_controls()
            self.show_frozen_status()

    @objc.python_method
    def show_selected_output(self):
        self.body.setSelectedRange_((0, 0))
        self.summary.setSelectedRange_((0, 0))
        self.body_scroll.contentView().scrollToPoint_((0, 0))
        self.graph_scroll.contentView().scrollToPoint_((0, 0))
        self.image_scroll.contentView().scrollToPoint_((0, 0))
        if str(self.view.titleOfSelectedItem()) != "Conversation":
            self.view.selectItemWithTitle_("Artifact")
        self.render_response()
        if self.output_history.frozen:
            self.show_frozen_status()

    def olderOutput_(self, sender):
        if self.output_history.move(-1):
            self.show_selected_output()

    def newerOutput_(self, sender):
        if self.output_history.move(1):
            self.show_selected_output()

    def latestOutput_(self, sender):
        self.output_history.resume()
        self.show_selected_output()
        self.status.setStringValue_("Following the latest output. New answers will update this pane.")

    @objc.python_method
    def copy_text(self, explained=False):
        if str(self.view.titleOfSelectedItem()) != "Artifact":
            return str(self.body.string())
        artifact = self.current_artifact()
        if artifact:
            return artifact.annotated_text() if explained else artifact.clean_text()
        return str(self.body.string())

    def copyClean_(self, sender):
        pasteboard = A.NSPasteboard.generalPasteboard()
        artifact = self.current_artifact() if str(self.view.titleOfSelectedItem()) == "Artifact" else None
        if artifact and artifact.kind == "image":
            try:
                picture = A.NSImage.alloc().initWithContentsOfFile_(str(image_path(json.loads(artifact.content))))
                pasteboard.clearContents()
                pasteboard.writeObjects_([picture])
                self.status.setStringValue_("Generated image copied.")
            except (ValueError, OSError) as error:
                self.status.setStringValue_(str(error))
            return
        pasteboard.clearContents()
        pasteboard.setString_forType_(self.copy_text(), A.NSPasteboardTypeString)
        self.status.setStringValue_("Clean artifact copied. Teaching annotations are separate.")

    def copyExplained_(self, sender):
        pasteboard = A.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(self.copy_text(explained=True), A.NSPasteboardTypeString)
        self.status.setStringValue_("Artifact and line explanations copied.")

    def taskDetails_(self, sender):
        self.pause()
        self.task_details_window = TaskDetails.alloc().initWithController_(self)
        self.task_details_window.window.makeKeyAndOrderFront_(None)

    @objc.python_method
    def make_checkpoint(self):
        return checkpoint(
            self.context,
            self.coordinator,
            displayed_artifacts=self.displayed_artifacts,
            selected_id=self.selected_id,
            selected_view=str(self.view.titleOfSelectedItem()),
            output_history=self.output_history,
        )

    def saveSession_(self, sender):
        try:
            path = save_session(self.make_checkpoint())
            self.trace.record("session_saved", session_id=self.context.session_id, revision=self.context.revision)
            self.status.setStringValue_("Task session saved locally: " + path.name)
        except (ValueError, OSError):
            self.status.setStringValue_(
                "The task session could not be saved. Check available storage and the session size."
            )

    def openSession_(self, sender):
        self.pause()
        panel = A.NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        from otsc.privacy import app_directory

        directory = app_directory() / "sessions"
        directory.mkdir(exist_ok=True)
        panel.setDirectoryURL_(A.NSURL.fileURLWithPath_(str(directory)))
        if panel.runModal() == A.NSModalResponseOK:
            try:
                self.restore_session(load_session(str(panel.URL().path())))
            except (ValueError, OSError):
                self.status.setStringValue_("This is not a supported task-session file. Existing work has been kept.")

    def resumeLastSession_(self, sender):
        from otsc.privacy import app_directory

        paths = [p for p in (app_directory() / "sessions").glob("*.json") if not p.is_symlink()]
        if not paths:
            self.status.setStringValue_("No saved task session is available yet.")
            return
        try:
            self.restore_session(load_session(max(paths, key=lambda p: p.stat().st_mtime)))
        except (OSError, ValueError):
            self.status.setStringValue_("The latest session could not be restored. Existing work has been kept.")

    @objc.python_method
    def restore_session(self, document):
        authorized = self.context.repo_root
        self.pause()
        self.context = restore_context(document, authorized_project=authorized)
        self.image_worker.cancel()
        self.coordinator.context = self.context
        self.context_builder.context = self.context
        self.coordinator.current = document.current
        self.coordinator.history = list(document.history)
        self.coordinator.pending = None
        self.coordinator.pending_version = None
        self.coordinator.request_id = ""
        self.coordinator.last_requested_revision = -1
        # Browsing freezes presentation only; generation/context keep the latest.
        self.coordinator.pinned = False
        self.output_history.clear()
        if document.outputs:
            self.output_history.entries = [item.model_copy(update={"session_id": self.context.session_id}, deep=True)
                                           for item in document.outputs]
            if document.pinned:
                self.output_history.frozen = True
                self.output_history.selected_id = document.selected_output_id
        else:
            for response in document.history:
                self.output_history.append(response, session_id=self.context.session_id, goal=self.context.goal,
                                           lane="saved", sources=self.context.observations, at=document.saved_at)
            if document.current:
                self.output_history.append(document.current, session_id=self.context.session_id, goal=self.context.goal,
                                           lane="saved", sources=self.context.observations, at=document.saved_at,
                                           artifacts=document.displayed_artifacts)
            if document.pinned:
                self.output_history.freeze()
        self.coordinator.artifact_bases = dict(document.artifact_bases)
        self.displayed_artifacts = list(document.displayed_artifacts)
        self.artifact_context = (self.context.session_id, self.context.goal)
        self.selected_id = document.selected_id
        self.loaded_base_hashes = dict(document.base_hashes)
        self.restored_artifact_keys = {digest(a.model_dump()) for a in document.displayed_artifacts}
        self.restored_artifact_keys.update(digest(a.model_dump()) for item in document.outputs for a in item.artifacts)
        self.goal.setStringValue_(self.context.goal)
        if document.selected_view in {"Artifact", "Conversation", "Context", "Observed files", "History"}:
            self.view.selectItemWithTitle_(document.selected_view)
        self.pin.setTitle_("Unpin" if document.pinned else "Pin")
        self.body.setSelectedRange_((0, 0))
        self.summary.setSelectedRange_((0, 0))
        self.render_response()
        if document.current is None:
            retain_text(self.summary, "Restored task\n\n" + (self.context.goal or "No goal has been set yet."))
            self.artifacts.removeAllItems()
            self.artifacts.addItemsWithTitles_([a.title for a in self.displayed_artifacts] or ["No artifact yet"])
        self.refresh_body()
        self.update_navigation_controls()
        self.trace.record("session_restored", session_id=self.context.session_id, revision=self.context.revision)
        self.status.setStringValue_(
            "Task session restored. Capture is paused; saved proposals need fresh context before reuse."
        )

    def toggleSessionMemory_(self, sender):
        self.settings.local_session_memory = not self.settings.local_session_memory
        self.memory_menu_item.setState_(int(self.settings.local_session_memory))
        if not self.demo:
            save_settings(self.settings)
        self.checkpoint_if_enabled(force=True)
        self.status.setStringValue_(
            "Task text and proposals will be saved locally. Raw audio and images are excluded."
            if self.settings.local_session_memory
            else "Automatic session saving is off."
        )

    def toggleInspection_(self, sender):
        self.settings.inspection_enabled = not self.settings.inspection_enabled
        self.inspection_menu_item.setState_(int(self.settings.inspection_enabled))
        if not self.demo:
            save_settings(self.settings)
        self.status.setStringValue_(
            "Bounded context inspection enabled for deep responses."
            if self.settings.inspection_enabled
            else "Standard deep response path selected."
        )

    def deleteSessions_(self, sender):
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("Delete saved task sessions?")
        alert.setInformativeText_(
            "This removes locally saved task text and proposals. The current open task stays in memory."
        )
        alert.addButtonWithTitle_("Cancel")
        alert.addButtonWithTitle_("Delete saved sessions")
        if alert.runModal() != A.NSAlertSecondButtonReturn:
            return
        from otsc.privacy import app_directory

        directory = app_directory() / "sessions"
        removed = 0
        for pattern in ("task-*.json", "autosave-*.json", "autosave.json"):
            for path in directory.glob(pattern):
                path.unlink(missing_ok=True)
                removed += 1
        self.settings.local_session_memory = False
        self.memory_menu_item.setState_(0)
        if not self.demo:
            save_settings(self.settings)
        self.status.setStringValue_(f"Deleted {removed} saved sessions. Automatic session saving is off.")

    @objc.python_method
    def checkpoint_if_enabled(self, force=False):
        if self.demo or not self.settings.local_session_memory:
            return
        if not force and time.monotonic() - self.checkpoint_at < 2:
            return
        current = self.coordinator.current
        signature = (
            self.context.session_id,
            self.context.revision,
            digest(current.model_dump()) if current else "",
            self.output_history.frozen,
            self.output_history.selected_id,
            self.selected_id,
        )
        if signature == self.checkpoint_signature:
            return
        from otsc.privacy import app_directory

        try:
            save_session(
                self.make_checkpoint(), app_directory() / "sessions" / ("autosave-" + self.context.session_id + ".json")
            )
            self.checkpoint_signature = signature
        except (ValueError, OSError):
            self.trace.record("checkpoint_error", error_type="StorageError")
        self.checkpoint_at = time.monotonic()

    def markUseful_(self, sender):
        self.record_feedback("useful")

    def markNeedsWork_(self, sender):
        self.record_feedback("needs_work")

    @objc.python_method
    def record_feedback(self, rating):
        artifact = self.current_artifact()
        current = self.displayed_response()
        if not current:
            return
        self.trace.record(
            "feedback",
            session_id=self.context.session_id,
            artifact_hash=digest(artifact.model_dump() if artifact else current.model_dump()),
            rating=rating,
        )
        self.status.setStringValue_("Feedback saved locally: " + rating.replace("_", " ") + ".")

    def showDiagnostics_(self, sender):
        report = self.trace.summary()
        alert = A.NSAlert.alloc().init()
        alert.setMessageText_("Local operational diagnostics")
        alert.setInformativeText_(
            f"{report['generations']} generations · {report['failed']} failures\n"
            f"P50: {report['p50_ms']} ms · P95: {report['p95_ms']} ms\n"
            f"Reported tokens: {report['reported_input_tokens']} input / {report['reported_output_tokens']} output\n"
            "Only operational metadata is logged. Captured content and credentials are excluded."
        )
        alert.runModal()

    def exportArtifact_(self, sender):
        artifact = self.current_artifact()
        if not artifact:
            return
        panel = A.NSSavePanel.savePanel()
        suffix = {"diagram": ".svg", "image": ".png", "patch": ".diff", "structured": ".json"}.get(artifact.kind, ".txt")
        panel.setNameFieldStringValue_(Path(artifact.id).name + suffix)
        if panel.runModal() == A.NSModalResponseOK:
            try:
                target = Path(str(panel.URL().path()))
                if artifact.kind == "image":
                    target.write_bytes(image_path(json.loads(artifact.content)).read_bytes())
                else:
                    target.write_text(diagram_svg(artifact) if artifact.kind == "diagram" else artifact.clean_text())
                self.status.setStringValue_("Artifact exported.")
            except (OSError, ValueError) as error:
                self.status.setStringValue_(str(error))

    def toggleClickThrough_(self, sender):
        ignore = not self.window.ignoresMouseEvents()
        self.set_click_through(ignore)
        self.status.setStringValue_(
            "Click-through on: transparent backgrounds, opaque text. Press . (MIDI 63), Cmd-Shift-I, or the Dock icon to restore interaction."
            if ignore
            else "Window interaction restored."
        )

    @objc.python_method
    def set_click_through(self, enabled):
        set_overlay_appearance(
            self.window, self.root, enabled,
            panes=[(self.body_scroll, self.body), (self.summary_scroll, self.summary),
                   (self.input_scroll, self.input), (self.graph_scroll, None), (self.image_scroll, None)],
            fields=[self.goal],
            buttons=[*self.controls.values(), self.add_button, self.source, self.view, self.artifacts,
                     self.annotation, self.pin, self.older, self.newer, self.latest, self.copy, self.copy_notes, self.export],
            canvases=[self.graph, self.image_canvas],
        )

    def toggleVisibility_(self, sender):
        self.overlay_hidden = not self.overlay_hidden
        if self.overlay_hidden:
            self.window.orderOut_(None)
        else:
            self.window.deminiaturize_(None)
            self.window.orderFrontRegardless()

    @objc.python_method
    def midi_action(self, action):
        handlers = {
            "help": self.helpNow_,
            "capture": self.captureNow_,
            "new_task": self.newTask_,
            "visibility": self.toggleVisibility_,
            "older_output": self.olderOutput_,
            "newer_output": self.newerOutput_,
            "latest_output": self.latestOutput_,
            "follow": self.toggleRunning_,
            "pin": self.togglePin_,
            "copy_clean": self.copyClean_,
            "click_through": self.toggleClickThrough_,
        }
        views = {
            "view_artifact": "Artifact", "view_conversation": "Conversation", "view_context": "Context",
            "view_files": "Observed files", "view_history": "History",
        }
        if action in handlers:
            handlers[action](None)
        elif action in views:
            self.view.selectItemWithTitle_(views[action])
            self.changeView_(self.view)
        elif action in {"left", "right", "up", "down"}:
            rect = self.window.frame()
            dx, dy = {"left": (-50, 0), "right": (50, 0), "up": (0, 50), "down": (0, -50)}[action]
            self.window.setFrameOrigin_((rect.origin.x + dx, rect.origin.y + dy))
        elif action == "center":
            self.window.center()
        elif action in {"smaller", "bigger"}:
            rect = self.window.frame()
            step = 50 if action == "bigger" else -50
            minimum = self.window.minSize()
            self.window.setFrame_display_(
                (
                    rect.origin,
                    (max(minimum.width, rect.size.width + step), max(minimum.height, rect.size.height + step)),
                ),
                True,
            )
        elif action == "next_view":
            self.view.selectItemAtIndex_((self.view.indexOfSelectedItem() + 1) % self.view.numberOfItems())
            self.changeView_(None)
        elif action == "next_page":
            self.advance_page()
        elif action in {"page_up", "page_down"}:
            self.page_output(-1 if action == "page_up" else 1)
        elif action in {"previous_artifact", "next_artifact"}:
            self.cycle_artifact(-1 if action == "previous_artifact" else 1)
        elif action in {"voice_question", "voice_followup"}:
            self.voice_question()

    @objc.python_method
    def cycle_artifact(self, step):
        if not self.displayed_artifacts:
            return
        self.view.selectItemWithTitle_("Artifact")
        self.populate_output_picker()
        index = (self.artifacts.indexOfSelectedItem() + step) % len(self.displayed_artifacts)
        self.artifacts.selectItemAtIndex_(index)
        self.chooseArtifact_(None)

    @objc.python_method
    def page_output(self, direction):
        self.output_history.freeze()
        self.update_navigation_controls()
        scroll = self.output_scroll()
        clip = scroll.contentView()
        bounds = clip.bounds()
        limit = max(0, scroll.documentView().bounds().size.height - bounds.size.height)
        y = max(0, min(limit, bounds.origin.y + direction * max(40, bounds.size.height - 24)))
        clip.scrollToPoint_((bounds.origin.x, y))
        scroll.reflectScrolledClipView_(clip)
        self.show_frozen_status()

    @objc.python_method
    def advance_page(self):
        scroll = self.output_scroll()
        clip = scroll.contentView()
        bounds = clip.bounds()
        total = scroll.documentView().bounds().size.height
        if bounds.origin.y + bounds.size.height < total - 2:
            clip.scrollToPoint_(
                (bounds.origin.x, min(total - bounds.size.height, bounds.origin.y + max(40, bounds.size.height - 24)))
            )
            scroll.reflectScrolledClipView_(clip)
            return
        if str(self.view.titleOfSelectedItem()) == "Artifact" and self.displayed_artifacts:
            self.artifacts.selectItemAtIndex_(
                (self.artifacts.indexOfSelectedItem() + 1) % len(self.displayed_artifacts)
            )
            self.chooseArtifact_(None)
        scroll = self.output_scroll()
        scroll.contentView().scrollToPoint_((0, 0))
        scroll.reflectScrolledClipView_(scroll.contentView())

    @objc.python_method
    def output_scroll(self):
        if not self.image_scroll.isHidden():
            return self.image_scroll
        return self.graph_scroll if not self.graph_scroll.isHidden() else self.body_scroll

    @objc.python_method
    def voice_question(self):
        if self.demo:
            self.status.setStringValue_("Voice capture is disabled in the synthetic demo.")
            return
        if self.manual_help_pending:
            return
        if self.voice_recording:
            self.voice_recording = False
            self.stop_voice_after_help = self.voice_started_audio
            self.voice_started_audio = False
            self.helpNow_(None)
            return
        if not self.settings.configured:
            self.settings_(None)
            return
        if not (self.settings.microphone or self.settings.system_audio):
            self.status.setStringValue_("Enable a microphone or system-audio input in Settings for voice capture.")
            return
        try:
            needs_audio = self.audio is None or self.audio.stop_event.is_set()
            self.voice_started_audio = needs_audio and not self.running
            if needs_audio:
                self.audio = AudioCapture(self.settings, self.events, self.credentials)
                self.audio.start()
            self.voice_recording = True
            self.status.setStringValue_("Listening. Press 0 (MIDI 62) again to finish the question and ask for help.")
        except Exception as error:
            self.voice_recording = False
            self.status.setStringValue_(redact(str(error)))

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.overlay_hidden = False
        self.set_click_through(False)
        self.window.makeKeyAndOrderFront_(None)
        return True

    def newTask_(self, sender):
        self.checkpoint_if_enabled(force=True)
        self.pause()
        self.context.goal = ""
        self.context.clear()
        self.image_worker.cancel()
        self.goal.setStringValue_("")
        self.input.setString_("")
        self.view.selectItemWithTitle_("Artifact")
        self.coordinator.current = self.coordinator.pending = None
        self.coordinator.pending_version = None
        self.coordinator.pinned = False
        self.coordinator.last_requested_revision = -1
        self.coordinator.history.clear()
        self.output_history.clear()
        self.displayed_artifacts = []
        self.loaded_base_hashes = {}
        self.restored_artifact_keys = set()
        self.artifact_context = None
        self.body.setSelectedRange_((0, 0))
        self.summary.setSelectedRange_((0, 0))
        self.pin.setTitle_("Pin")
        self.selected_id = ""
        self.artifacts.removeAllItems()
        self.artifacts.addItemWithTitle_("No artifact yet")
        self.update_navigation_controls()
        retain_text(self.summary, "A new task is ready. Add its goal, screen, or conversation.")
        self.refresh_body()

    @objc.python_method
    def load_demo(self, design):
        self.pause()
        self.displayed_artifacts = []
        self.artifact_context = None
        self.body.setSelectedRange_((0, 0))
        self.summary.setSelectedRange_((0, 0))
        self.demo = True
        self.context.set_repo("")
        self.coordinator.current = self.coordinator.pending = None
        self.coordinator.pinned = False
        self.output_history.clear()
        seed_demo(self.context, design=design)
        self.view.selectItemWithTitle_("Artifact")
        self.goal.setStringValue_(self.context.goal)
        self.window.setTitle_("Over The Shoulder Coder · Synthetic demo")
        self.coordinator.request(manual=True)

    def demoCode_(self, sender):
        self.load_demo(False)

    def demoDesign_(self, sender):
        self.load_demo(True)

    def windowWillClose_(self, notification):
        self.quit_(None)

    def applicationShouldTerminate_(self, app):
        self.shutdown()
        return A.NSTerminateNow

    @objc.python_method
    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.pause()
        self.checkpoint_if_enabled(force=True)
        self.timer.invalidate()
        self.coordinator.close()
        self.context_builder.close()
        self.image_worker.close()
        if self.midi:
            self.midi.close()
        self.screen.close()
        if not self.demo:
            self.settings.goal = self.context.goal
            rect = self.window.frame()
            self.settings.window_bounds = tuple(int(n) for n in (*rect.origin, *self.root.bounds().size))
            save_settings(self.settings)

    def quit_(self, sender):
        self.shutdown()
        A.NSApp.terminate_(None)

    @objc.python_method
    def save_view_image(self, path):
        bitmap = self.root.bitmapImageRepForCachingDisplayInRect_(self.root.bounds())
        self.root.cacheDisplayInRect_toBitmapImageRep_(self.root.bounds(), bitmap)
        data = bitmap.representationUsingType_properties_(A.NSBitmapImageFileTypePNG, {})
        private_write(path, bytes(data))

    @objc.python_method
    def smoke_planned_outputs(self, directory):
        import io
        from unittest.mock import patch

        from PIL import Image, ImageDraw

        from otsc.images import ImageJob, image_key, store_png
        from otsc.models import Artifact, Assistance

        source = self.context.snapshot().observations[0].id
        data = {"route_options": [{"name": "Canal path", "available": True, "minutes": None}], "confidence": 0.7}
        structured = Artifact(id="travel_manifest", kind="structured", title="Travel manifest", content=json.dumps(data),
                              language="json", path="", basis="discussion", source_ids=[source], annotations=[], nodes=[], edges=[])
        response = Assistance(task="Synthetic rendering check", summary="Unfamiliar structured data and an image.",
                              artifacts=[structured], conversation=[], observed_files=[], open_questions=[])
        self.output_history.resume()
        self.selected_id = structured.id
        self.output_history.append(response, session_id=self.context.session_id, goal=self.context.goal, lane="deep")
        self.render_response()
        assert "Canal path" in str(self.body.string()) and "null" in str(self.body.string())
        assert json.loads(self.copy_text()) == data
        self.save_view_image(directory / "structured-output.png")
        request = {"status": "pending", "action": "generate", "prompt": "Synthetic image fixture", "caption": "Synthetic image fixture"}
        artifact = structured.model_copy(update={"id": "illustration", "kind": "image", "title": "Illustration",
                                                "content": json.dumps(request), "language": ""}, deep=True)
        response.artifacts = [artifact]
        self.context.previous_artifacts = json.dumps([artifact.model_dump()])
        self.output_history.append(response, session_id=self.context.session_id, goal=self.context.goal, lane="deep")
        self.selected_id = artifact.id
        self.render_response()
        assert "Generating image" in str(self.body.string())
        self.output_history.freeze()
        held = self.output_history.visible
        picture = Image.new("RGB", (900, 1200), "#f3e7d3")
        ImageDraw.Draw(picture).text((80, 120), "SYNTHETIC IMAGE\nNative fit and scroll test", fill="#30291f", font_size=44)
        raw = io.BytesIO()
        picture.save(raw, "PNG")
        with patch("otsc.images.app_directory", return_value=directory):
            asset = store_png(raw.getvalue())
            job = ImageJob(self.context.session_id, artifact.id, image_key(request, self.settings.image), request,
                           self.settings.image, Cancellation(), self.context.task_revision)
            self.accept_image({"job": job, "asset": asset, "seconds": 0})
            assert self.output_history.visible is held
            assert json.loads(held.artifacts[0].content)["status"] == "pending"
            assert json.loads(self.context.previous_artifacts)[0]["content"] != artifact.content
            self.latestOutput_(None)
            assert not self.image_scroll.isHidden() and self.body_scroll.isHidden()
            assert self.output_scroll() is self.image_scroll
            self.window.setContentSize_((900, 740))
            assert abs(self.image_view.frame().size.width - self.image_scroll.contentSize().width) < 1
            assert abs(self.image_view.frame().size.height / self.image_view.frame().size.width - 4 / 3) < .01
            self.page_output(1)
            assert self.image_scroll.contentView().bounds().origin.y > 0
            self.image_scroll.contentView().scrollToPoint_((0, 0))
            self.save_view_image(directory / "image-output.png")
            self.set_click_through(True)
            assert self.window.alphaValue() == 1.0 and self.image_canvas.background_alpha == 0
            assert self.image_view.image() is not None
            self.set_click_through(False)
            # A result for an older task cannot alter the visible image or memory.
            latest_id, memory = self.output_history.latest.id, self.context.previous_artifacts
            job.task_revision -= 1
            self.accept_image({"job": job, "asset": asset, "seconds": 0})
            assert self.output_history.latest.id == latest_id and self.context.previous_artifacts == memory

    @objc.python_method
    def smoke_tick(self):
        try:
            if time.monotonic() - self.smoke_started > 12:
                raise RuntimeError("Native smoke test timed out waiting for demo results")
            response = self.coordinator.current
            if not response or not response.artifacts:
                return
            directory = private_directory(Path(self.options.smoke_test))
            if self.smoke_step == 0:
                from types import SimpleNamespace

                injected = queue.Queue()
                midi_clock = [0.0]
                keypad = MidiInput(injected, clock=lambda: midi_clock[0])

                def press_key(note):
                    midi_clock[0] += 0.2
                    keypad.receive(SimpleNamespace(type="note_on", channel=0, note=note, velocity=100))
                    self.handle_event(injected.get_nowait())

                assert response.artifacts[0].kind == "code"
                before = self.copy_text()
                assert "return None" in before and "Represent missing" not in before
                self.body.setSelectedRange_((5, 10))
                self.window.setContentSize_((850, 680))
                assert self.copy_text() == before
                assert self.body.selectedRange().length == 10
                press_key(63)
                assert self.window.ignoresMouseEvents() and self.window.alphaValue() == 1.0
                assert not self.window.isOpaque() and self.root.background_alpha == 0.05
                assert not self.body.drawsBackground() and not self.body_scroll.drawsBackground()
                assert self.body.textColor().alphaComponent() == 1.0
                self.save_view_image(directory / "transparent-code.png")
                from PIL import Image

                with Image.open(directory / "transparent-code.png") as image:
                    pixels = image.convert("RGBA")
                    scale = pixels.width / self.root.bounds().size.width
                    pane = self.body_scroll.frame()
                    assert pixels.getpixel((int((pane.origin.x + 4) * scale), int((pane.origin.y + 4) * scale)))[3] <= 14
                    ink = pixels.crop((int((pane.origin.x + 20) * scale), int((pane.origin.y + 20) * scale),
                                       int((pane.origin.x + pane.size.width - 20) * scale),
                                       int((pane.origin.y + pane.size.height - 20) * scale)))
                    assert sum(a >= 250 and max(r, g, b) < 150 for r, g, b, a in ink.get_flattened_data()) > 100
                press_key(63)
                assert not self.window.ignoresMouseEvents()
                assert self.body.drawsBackground() and self.body_scroll.drawsBackground()
                assert self.root.background_alpha == 1.0 and self.window.alphaValue() == 1.0
                assert self.copy_text() == before
                assert not self.window_sharing and self.window.sharingType() == A.NSWindowSharingNone
                self.toggleSharing_(None)
                assert self.window_sharing and self.window.sharingType() == A.NSWindowSharingReadOnly
                self.toggleSharing_(None)
                assert not self.window_sharing and self.window.sharingType() == A.NSWindowSharingNone
                assert self.copy_text() == before
                origin = self.window.frame().origin
                press_key(53)  # 4: left
                assert self.window.frame().origin.x == origin.x - 50
                press_key(55)  # 6: right
                assert self.window.frame().origin.x == origin.x
                press_key(49)  # 8: up
                assert self.window.frame().origin.y == origin.y + 50
                press_key(54)  # 5: down
                assert self.window.frame().origin.y == origin.y
                size = self.window.frame().size
                press_key(51)  # +
                assert self.window.frame().size.width == size.width + 50
                press_key(46)  # -
                assert self.window.frame().size.width == size.width
                assert self.copy_text() == before
                press_key(41)
                assert not self.window.isVisible()
                self.handle_event({"type": "screen_taken"})
                assert not self.window.isVisible()
                press_key(41)
                assert self.window.isVisible()
                self.save_view_image(directory / "code.png")
                self.body.setSelectedRange_((0, 0))
                request_id = self.coordinator.request_id
                self.view.selectItemWithTitle_("Conversation")
                self.refresh_body()
                assert "Zero would blur" in self.copy_text()
                self.view.selectItemWithTitle_("Artifact")
                self.selected_id = response.artifacts[1].id
                self.refresh_body()
                assert "@@" in self.copy_text()
                assert self.coordinator.request_id == request_id
                for note, view in ((42, "Artifact"), (47, "Conversation"), (52, "Context"),
                                   (56, "Observed files"), (61, "History")):
                    press_key(note)
                    assert str(self.view.titleOfSelectedItem()) == view
                press_key(42)
                self.artifacts.selectItemAtIndex_(0)
                self.chooseArtifact_(None)
                press_key(45)
                assert self.current_artifact().id == response.artifacts[1].id
                press_key(44)
                assert self.current_artifact().id == response.artifacts[0].id
                selected = self.current_artifact().id
                press_key(48)
                press_key(50)
                assert self.current_artifact().id == selected and self.output_history.frozen
                # Synthetic incoming audio cannot starve an in-flight deep response.
                self.audio = SimpleNamespace(id="synthetic-audio")
                revision = self.context.revision
                self.coordinator.active_lanes = {"deep"}
                self.handle_event(
                    {
                        "type": "speech",
                        "capture_id": "synthetic-audio",
                        "channel": "system",
                        "speaker": "other_people",
                        "text": "What about a generator input?",
                        "at": time.time(),
                    }
                )
                assert self.context.revision > revision and not self.buffered_context
                self.coordinator.active_lanes.clear()
                self.flush_context()
                assert self.context.observations[-1].text == "What about a generator input?"
                self.audio = None
                # Browse the actual native controls while a new valid result arrives.
                from otsc.models import Artifact, Assistance, LineAnnotation
                from otsc.scheduler import Job

                self.artifacts.selectItemAtIndex_(0)
                self.chooseArtifact_(None)
                held_id = self.output_history.visible.id
                held_text = self.copy_text()
                held_summary = str(self.summary.string())
                snapshot = self.context.snapshot()
                newer = Assistance(
                    task=snapshot.goal, summary="Synthetic newer proposal for navigation checks.", conversation=[],
                    observed_files=[], open_questions=[],
                    artifacts=[Artifact(id="navigation-example", kind="code", title="Newer synthetic example",
                                        content="answer = 42\n", language="python", path="", basis="example",
                                        source_ids=[snapshot.observations[0].id],
                                        annotations=[LineAnnotation(line=1, explanation="Assign the example value.")],
                                        nodes=[], edges=[])],
                )
                self.coordinator.request_id = "smoke-navigation"
                self.coordinator.published_lane = -1
                self.coordinator.active_lanes = {"deep"}
                self.handle_event({"type": "result", "response": newer, "elapsed": 0,
                                   "job": Job("smoke-navigation", snapshot, "deep", Cancellation())})
                assert self.output_history.frozen and self.copy_text() == held_text
                assert str(self.summary.string()) == held_summary
                assert self.context.previous_summary == newer.summary
                assert self.newer.isEnabled() and self.latest.isEnabled()
                self.save_view_image(directory / "frozen-output.png")
                self.view.selectItemWithTitle_("History")
                self.changeView_(self.view)
                self.artifacts.selectItemAtIndex_(self.history_choices.index(held_id))
                self.chooseArtifact_(None)
                assert str(self.view.titleOfSelectedItem()) == "Artifact" and self.copy_text() == held_text
                press_key(36)
                press_key(37)
                assert self.copy_text() == held_text
                press_key(37)
                assert self.output_history.frozen and "answer = 42" in self.copy_text()
                press_key(60)
                assert not self.output_history.frozen and not self.body.selectedRange().length
                assert self.displayed_response().summary == newer.summary
                self.output_history.select(held_id)
                self.show_selected_output()
                self.context.set_task_details(["Keep missing data distinct from zero"], ["Use None for empty input"])
                saved_copy = self.copy_text()
                saved = self.make_checkpoint()
                session_path = save_session(saved, directory / "session.json")
                old_session = self.context.session_id
                press_key(43)
                self.restore_session(load_session(session_path))
                assert self.context.session_id != old_session
                assert self.copy_text() == saved_copy
                assert self.output_history.frozen and self.output_history.visible.id == held_id
                assert self.context.previous_summary == newer.summary
                assert json.loads(self.context.previous_artifacts)[0]["content"] == "answer = 42\n"
                assert self.context.constraints == ("Keep missing data distinct from zero",)
                assert not self.running and self.audio is None
                assert all(not o.image_path for o in self.context.observations)
                self.smoke_step = 1
                self.load_demo(True)
            elif self.smoke_step == 1 and response.artifacts[0].kind == "diagram":
                self.window.setContentSize_((1100, 850))
                self.save_view_image(directory / "diagram.png")
                assert not self.graph_scroll.isHidden()
                before = self.copy_text()
                self.set_click_through(True)
                assert self.graph.background_alpha == 0.0 and not self.graph.isOpaque()
                self.save_view_image(directory / "transparent-diagram.png")
                self.applicationShouldHandleReopen_hasVisibleWindows_(None, True)
                assert not self.window.ignoresMouseEvents() and self.window.isOpaque()
                assert self.window.alphaValue() == 1.0 and self.graph.background_alpha == 1.0
                assert self.body.drawsBackground() and self.summary.drawsBackground()
                assert self.copy_text() == before
                self.preferences = Preferences.alloc().initWithController_(self)
                self.preferences.window.orderOut_(None)
                assert "planner" in self.preferences.fields and "image" in self.preferences.fields
                self.smoke_planned_outputs(directory)
                private_write(
                    directory / "result.json",
                    json.dumps(
                        {
                            "passed": True,
                            "checks": [
                                "parallel synthetic responses",
                                "clean copy",
                                "selection retained on resize",
                                "click-through retains artifact",
                                "click-through changes background alpha only; text and window alpha remain opaque",
                                "rendered transparent pane has <=5% background alpha and opaque text pixels",
                                "Dock recovery restores normal text/diagram backgrounds without changing content",
                                "window sharing defaults off and toggles without changing the artifact",
                                "artifact/history selection freezes the exact output while new results arrive",
                                "Older/Newer browse saved outputs; Latest resumes live display",
                                "saved browsing position does not roll back the latest model context",
                                "native diagram",
                                "arbitrary named JSON renders readably and copies as exact structured data",
                                "generated PNG fits resized panes and uses MIDI page navigation",
                                "image arrival updates live memory while preserving the exact pinned output",
                                "obsolete task image completion cannot publish",
                                "settings construction",
                                "collaborator response and observed diff",
                                "new audio updates context without starving deep work",
                                "MIDI window controls retain content and hidden state",
                                "channel-1 keypad: knob history, Enter latest, 8456 arrows, +/- resize, views and artifact/page navigation",
                                "saved task restores exact artifacts and constraints with capture paused",
                            ],
                            "live_capture": False,
                            "live_api_calls": False,
                        },
                        indent=2,
                    ),
                )
                self.quit_(None)
        except Exception as error:
            private_write(
                Path(self.options.smoke_test) / "result.json", json.dumps({"passed": False, "error": str(error)})
            )
            import traceback

            traceback.print_exc()
            self.shutdown()
            import os

            os._exit(1)


def run(options):
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    controller = Controller.alloc().initWithOptions_(options)
    app.setDelegate_(controller)
    AppHelper.runEventLoop()
