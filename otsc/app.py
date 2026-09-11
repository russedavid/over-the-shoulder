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
    OutputTypeButton,
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
from otsc.output_browser import (
    CONTEXT,
    DETAILS,
    EVENTS,
    FILES,
    GUIDANCE,
    LIVE_DEBUG,
    REPLIES,
    OutputBrowser,
    artifact_key,
)
from otsc.output_history import OutputHistory
from otsc.perception import read_screen
from otsc.planning import TaskPlanningProvider
from otsc.preferences import Preferences
from otsc.privacy import private_write, redact
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
        self.browser = OutputBrowser()
        self.debug_mode = False
        self.composer_open = False
        self.notice_text = ""
        self.type_buttons = {}
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
            self.notice_text = self.settings_error
            self.relayout()
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
        self.window.setMinSize_((760, 520))
        self.window.setDelegate_(self)
        self.window.setBackgroundColor_(color("f8f4ec"))
        self.window.setLevel_(A.NSFloatingWindowLevel)
        self.root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
        self.window.setContentView_(self.root)
        self.controls = {}
        for name, title, action in [
            ("help", "Help now", "helpNow:"),
            ("capture", "Capture now", "captureNow:"),
            ("start", "Start following", "toggleRunning:"),
            ("settings", "Settings", "settings:"),
            ("project", "Project…", "selectProject:"),
            ("new", "New task", "newTask:"),
            ("sharing", "Sharing: Off", "toggleSharing:"),
            ("task", "Task…", "toggleComposer:"),
            ("debug", "Debug", "toggleDebug:"),
        ]:
            self.controls[name] = button(self.root, title, self, action, checkbox=name == "debug")
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
        self.types_label = label(self.root, "Outputs", size=13, bold=True)
        self.types_scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 400))
        self.types_scroll.setHasVerticalScroller_(True)
        self.types_scroll.setAutohidesScrollers_(True)
        self.types_scroll.setDrawsBackground_(False)
        self.types_canvas = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 400))
        self.types_scroll.setDocumentView_(self.types_canvas)
        self.root.addSubview_(self.types_scroll)
        self.output_title = label(self.root, "Ready", size=17, bold=True)
        self.version_label = label(self.root, "", size=12)
        self.activity = label(self.root, "Paused", size=12)
        self.notice_label = label(self.root, "", size=12)
        self.notice_label.setTextColor_(color("a12d29"))
        self.annotation = button(self.root, "Explain each line", self, "changeView:", checkbox=True)
        self.annotation.setState_(1)
        self.pin = button(self.root, "Pin", self, "togglePin:")
        self.older = button(self.root, "Older", self, "olderOutput:")
        self.newer = button(self.root, "Newer", self, "newerOutput:")
        self.latest = button(self.root, "Latest", self, "latestOutput:")
        self.older.setToolTip_("Previous version of the selected output type.")
        self.newer.setToolTip_("Next version of the selected output type; keep it held.")
        self.latest.setToolTip_("Show the latest version and resume updates for this type.")
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
        self.render_response()
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
            ("Toggle debug view", "toggleDebug:", "d"),
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
            if action in {"toggleClickThrough:", "toggleDebug:"}:
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
        x = 12
        for name, width in [("help", 88), ("start", 130), ("capture", 100), ("task", 72), ("new", 86), ("settings", 82), ("debug", 76)]:
            frame(self.controls[name], x, 12, width, 30)
            x += width + 4
        expanded = self.composer_open or self.debug_mode
        for control in (self.goal, self.source, self.add_button, self.input_scroll, self.controls["project"], self.controls["sharing"]):
            control.setHidden_(not expanded)
        top = 58
        if expanded:
            frame(self.goal, 16, top, w - 250, 28)
            frame(self.controls["project"], w - 226, top, 100, 28)
            frame(self.controls["sharing"], w - 124, top, 110, 28)
            frame(self.source, 14, top + 36, 176, 28)
            frame(self.add_button, 14, top + 69, 176, 28)
            frame(self.input_scroll, 198, top + 36, w - 214, 62)
            top += 112
        sidebar = min(236, max(194, w * .22))
        main_x, main_w = sidebar + 24, w - sidebar - 40
        bottom = h - (148 if self.debug_mode else 106)
        frame(self.types_label, 18, top, sidebar - 12, 24)
        frame(self.types_scroll, 12, top + 30, sidebar, h - top - 70)
        frame(self.output_title, main_x, top, main_w - 174, 27)
        frame(self.annotation, w - 188, top, 176, 28)
        notice_height = 42 if self.notice_text else 0
        self.notice_label.setHidden_(not self.notice_text)
        self.notice_label.setStringValue_(self.notice_text)
        frame(self.notice_label, main_x, top + 32, main_w, 40)
        for scroll in (self.body_scroll, self.graph_scroll, self.image_scroll):
            frame(scroll, main_x, top + 38 + notice_height, main_w, bottom - top - 38 - notice_height)
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
        for control, offset, width in [(self.pin, 0, 72), (self.copy, 76, 100), (self.copy_notes, 180, 130), (self.export, 314, 88)]:
            frame(control, main_x + offset, bottom + 10, width, 30)
        frame(self.version_label, main_x, bottom + 52, max(90, main_w - 260), 25)
        frame(self.older, w - 258, bottom + 48, 76, 30)
        frame(self.newer, w - 178, bottom + 48, 76, 30)
        frame(self.latest, w - 98, bottom + 48, 82, 30)
        frame(self.activity, 18, h - 36, sidebar - 10, 24)
        self.status.setHidden_(not self.debug_mode)
        frame(self.status, main_x, h - 57, main_w, 46)
        rows = self.browser.rows(self.debug_mode)
        width = self.types_scroll.contentSize().width
        self.types_canvas.setFrameSize_((width, max(self.types_scroll.contentSize().height, len(rows) * 44)))
        for index, row in enumerate(rows):
            if row[0] in self.type_buttons:
                frame(self.type_buttons[row[0]], 0, index * 44, width, 42)

    @objc.python_method
    def apply_settings(self, settings):
        self.pause()
        self.settings = settings
        self.notice_text = ""
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
        self.update_activity()

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
                self.notice_text = event["error"][:350]
                self.relayout()
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
                        self.notice_text = "Recent speech could not be finished. Check the audio settings."
                        self.relayout()
                    else:
                        self.send_manual_help_if_ready()
            elif kind == "speech":
                self.apply_context_event(event)
                self.status.setStringValue_(f"Heard {event['speaker']} through {event['channel']}. Context updated.")
                self.refresh_passive_views()
            else:
                if kind == "audio_error":
                    self.notice_text = event["message"][:350]
                    self.relayout()
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
            self.notice_text = ""
            self.update_activity()
            self.relayout()
            self.status.setStringValue_(
                "Checking whether new evidence warrants another answer…" if event.get("reviewing")
                else f"Quick and deep assistance started · context {event['revision']}."
            )
        elif (kind == "progress" and event["request_id"] == self.coordinator.request_id
              and event["lane"] in self.coordinator.active_lanes):
            self.status.setStringValue_(event["lane"].capitalize() + ": " + event["message"][:280])
            if (
                event["lane"] == "quick"
                and event["message"].startswith("Draft: ")
                and not self.browser.frozen
                and self.browser.active_key in {"", GUIDANCE}
                and self.browser.visible is None
                and not self.body.selectedRange().length
                and self.coordinator.published_lane < 1
                and self.context.task_revision == self.coordinator.last_requested_task_revision
            ):
                self.output_title.setStringValue_("Quick guidance · Draft")
                retain_text(self.body, event["message"][7:])
        elif kind in {"result", "error", "cancelled", "unchanged"}:
            if not self.coordinator.accept(event):
                return
            if kind == "unchanged" or event.get("duplicate"):
                # Reaffirm an unchanged in-flight image after a minor task edit,
                # without creating another output/history entry.
                latest = self.output_history.latest
                if latest and latest.session_id == self.context.session_id and not self.demo:
                    self.image_worker.submit(latest, self.context.session_id, self.context.task_revision)
                self.status.setStringValue_("Keeping the current answer. " + self.coordinator.last_refresh_reason[:280])
                self.refresh_passive_views()
                return
            if kind == "result":
                if self.body.selectedRange().length:
                    self.browser.freeze()
                job = event["job"]
                entry = self.output_history.append(
                    event["response"], identity=job.request_id + ":" + job.lane,
                    session_id=job.snapshot.session_id, goal=job.snapshot.goal,
                    lane=job.lane, sources=job.snapshot.observations,
                )
                self.browser.ingest(entry)
                self.notice_text = " ".join(note.get("message", "") for note in event["response"]._delivery_notes if note.get("message"))[:350]
                if job.lane == "deep" and not self.demo:
                    self.image_worker.submit(event["response"], job.snapshot.session_id, job.snapshot.task_revision)
                self.render_response()
                self.status.setStringValue_(f"{job.lane.capitalize()} response · {event['elapsed']:.1f}s · context {job.snapshot.revision}.")
            elif kind == "error":
                self.notice_text = event["message"][:350]
                self.render_response()
                self.status.setStringValue_(event["job"].lane.capitalize() + ": " + event["message"])
        elif kind == "notice":
            self.status.setStringValue_(event["message"])
            self.notice_text = event["message"][:350]
            self.relayout()
            self.update_activity()

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
        if self.body.selectedRange().length:
            self.browser.freeze()
        # Publish a new immutable display entry. Older/pinned entries stay exact.
        response = latest.response.model_copy(deep=True)
        response.artifacts = artifacts
        entry = self.output_history.append(response, session_id=latest.session_id, goal=latest.goal, lane="image",
                                           sources=latest.sources, artifacts=artifacts)
        self.browser.ingest(entry)
        update_image_memory(self.context, job, event.get("asset"), event.get("error"))
        if self.coordinator.current:
            self.coordinator.current.artifacts, _ = resolve_image(self.coordinator.current.artifacts, job,
                                                                  event.get("asset"), event.get("error"))
        self.notice_text = event.get("error", "")[:350]
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
        if not self.debug_mode and self.browser.active_key and self.browser.active_key not in {row[0] for row in self.browser.rows()}:
            key = self.browser.actionable()
            if key:
                self.browser.select_type(key)
            else:
                self.browser.state.active_key = ""
        output = self.browser.visible
        self.displayed_artifacts = output.response.artifacts if output else []
        self.artifact_context = (output.session_id, output.goal) if output else None
        artifact = self.current_artifact()
        self.selected_id = artifact.id if artifact else ""
        self.update_type_list()
        self.refresh_body()
        self.update_navigation_controls()

    @objc.python_method
    def update_type_list(self):
        rows = self.browser.rows(self.debug_mode)
        visible_keys = {row[0] for row in rows}
        known = set(self.browser.state.types) | LIVE_DEBUG.keys()
        for key in list(self.type_buttons):
            if key not in known:
                self.type_buttons.pop(key).removeFromSuperview()
        for key, control in self.type_buttons.items():
            control.setHidden_(key not in visible_keys)
        for key, title, count, status in rows:
            if key not in self.type_buttons:
                control = OutputTypeButton.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 42))
                control.setBordered_(False)
                control.setTarget_(self)
                control.setAction_("chooseOutputType:")
                control.output_key = key
                self.types_canvas.addSubview_(control)
                self.type_buttons[key] = control
            control = self.type_buttons[key]
            control.setTitle_(title + (" …" if status == "pending" else " !" if status == "failed" else ""))
            stream = self.browser.state.types.get(key)
            version = stream.visible or (stream.versions[-1] if stream.versions else None) if stream else None
            artifact = version.artifact if version else None
            kind = artifact.kind if artifact else "image" if stream and stream.status else ""
            heading = {"code": "Code", "patch": "Changes", "image": "Image", "diagram": "Diagram"}.get(kind, title)
            control.output_heading = heading + (" …" if status == "pending" else " !" if status == "failed" else "")
            control.output_subtitle = title if heading != title else ""
            control.output_selected = key == self.browser.active_key
            control.newer_count = count
            control.setToolTip_(f"{title} · {count} newer version{'s' if count != 1 else ''}. Select to hold this type's viewing position.")
            control.setAccessibilityLabel_(title)
            control.setAccessibilityValue_(f"{count} newer versions" + (", selected" if control.output_selected else ""))
            control.setNeedsDisplay_(True)
        self.relayout()

    @objc.python_method
    def displayed_response(self):
        output = self.browser.visible
        return output.response if output else None

    @objc.python_method
    def refresh_passive_views(self):
        if self.body.selectedRange().length:
            self.browser.freeze()
            self.update_navigation_controls()
        self.update_activity()
        if self.browser.active_key in LIVE_DEBUG:
            self.refresh_body()

    @objc.python_method
    def update_activity(self):
        state = "Listening" if self.voice_recording else "Following" if self.running else "Paused"
        if self.coordinator.active_lanes:
            state += " · Working"
        if self.notice_text:
            state = "Needs attention"
        if str(self.activity.stringValue()) != state:
            self.activity.setStringValue_(state)
        self.activity.setToolTip_(self.notice_text or state)

    @objc.python_method
    def update_navigation_controls(self):
        stream = self.browser.current
        index, count = self.browser.position, len(stream.versions) if stream else 0
        self.older.setEnabled_(index > 0)
        self.newer.setEnabled_(0 <= index < count - 1)
        self.latest.setEnabled_(bool(count))
        self.pin.setEnabled_(bool(count))
        self.pin.setTitle_("Unpin" if self.browser.frozen else "Pin")
        self.version_label.setStringValue_(f"{index + 1} / {count} · {'Held' if self.browser.frozen else 'Live'}" if index >= 0 else "")
        self.copy.setEnabled_(self.browser.visible is not None or self.browser.active_key in LIVE_DEBUG)
        self.export.setEnabled_(self.browser.visible is not None)
        artifact = self.current_artifact()
        code = bool(artifact and artifact.kind in {"code", "patch"})
        self.annotation.setHidden_(not code)
        self.copy_notes.setHidden_(not code)
        self.update_activity()

    @objc.python_method
    def show_frozen_status(self):
        self.status.setStringValue_(f"Selected type held · {self.browser.newer_count} newer versions available. Latest resumes this type.")
        self.update_type_list()
        self.update_navigation_controls()

    @objc.python_method
    def current_artifact(self):
        return self.browser.visible.artifact if self.browser.visible else None

    @objc.python_method
    def reply_text(self, debug=False):
        output = self.browser.visible
        if not output:
            return ""
        sources = {source.id: source for source in output.sources}
        rows = []
        for reply in output.response.conversation:
            text = reply.text
            if debug:
                source = sources.get(reply.source_id)
                who = {"primary_user": "You", "other_people": "Other person", "uncertain": "Uncertain speaker"}.get(source.speaker, "Context") if source else "Context"
                text = f"{who}: {source.text if source else 'Earlier conversation'}\n\n{reply.action.capitalize()}\n{text}\n\n{reply.artifact_effect}"
            rows.append(text)
        return "\n\n——————\n\n".join(rows)

    @objc.python_method
    def refresh_body(self):
        key = self.browser.active_key
        stream = self.browser.current
        response = self.displayed_response()
        artifact = self.current_artifact()
        self.output_title.setStringValue_(stream.label if stream else LIVE_DEBUG.get(key, "Ready"))
        show_diagram = bool(artifact and artifact.kind == "diagram")
        show_image = False
        image_error = ""
        if artifact and artifact.kind == "image":
            try:
                data = json.loads(artifact.content)
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
                image_error = "Image unavailable. " + str(error)
        self.graph_scroll.setHidden_(not show_diagram)
        self.image_scroll.setHidden_(not show_image)
        self.body_scroll.setHidden_(show_diagram or show_image)
        if key == CONTEXT and self.debug_mode:
            review = self.coordinator.last_refresh_reason
            text = ("Latest answer-refresh decision\n" + review + "\n\n" if review else "")
            text += "Working context\n\n" + "\n\n".join(
                f"{item.kind} · {item.basis} · {', '.join(item.source_ids)}\n{item.text}"
                for item in self.context.context_items.values()
            ) + "\n\nCaptured observations\n\n" + "\n\n".join(
                f"{o.id} · {o.channel} · {o.speaker} · {o.confidence}\n{o.text}" for o in self.context.observations)
        elif key == FILES and self.debug_mode:
            text = "Observed fragments (not complete files)\n\n" + "\n\n".join(
                f"{path} · line {fragment.first_line or '?'}\n{fragment.content}"
                for path, fragments in self.context.files.items() for fragment in fragments)
            text += "\n\nSelected project files\n" + "\n".join(
                f"{path} ({len(content.splitlines())} lines)" for path, content in self.context.verified_files.items())
        elif key == EVENTS and self.debug_mode:
            text = "Accepted output events\n\n" + "\n\n".join(
                entry.title() + "\n" + json.dumps(entry.response.model_dump(), ensure_ascii=False, indent=2)
                for entry in reversed(self.output_history.entries))
        elif key == REPLIES:
            text = self.reply_text(self.debug_mode)
        elif artifact:
            prefix = ""
            if artifact.kind in {"code", "patch"}:
                basis = {"example": "Example code", "observed_fragment": "Partial observed code", "verified_file": "Proposed change", "discussion": "Suggested code"}[artifact.basis]
                if digest(artifact.model_dump()) in self.restored_artifact_keys and artifact.basis == "verified_file":
                    current = self.context.verified_files.get(artifact.path)
                    original = self.coordinator.artifact_bases.get(digest(artifact.model_dump()), {}).get("base_hash")
                    basis = "Saved proposal · base confirmed" if current is not None and digest(current) == original else "Saved proposal · current base unconfirmed"
                prefix = basis + (" · " + artifact.path if artifact.path else "") + "\n\n"
            if self.debug_mode:
                prefix += f"Basis: {artifact.basis}\nSources: {', '.join(artifact.source_ids)}\n\n"
            if artifact.kind == "diagram":
                self.graph.artifact = artifact
                self.relayout()
            text = prefix + (image_error or (artifact.annotated_text() if self.annotation.state() or artifact.kind in {"structured", "image"} else artifact.clean_text()))
        elif response:
            text = (response.task + "\n\n" if key == DETAILS else "") + response.summary
            if response.open_questions:
                text += "\n\n" + "\n".join(response.open_questions)
        elif stream and stream.status:
            text = "The image is being created. Other outputs remain available." if stream.status == "pending" else "Image generation failed. Check Settings or ask Help now to retry."
            if self.debug_mode and stream.error:
                text += "\n\n" + stream.error
        else:
            text = "Press Help now for a response, or Start following to work with your screen and conversation. Use Task… to add instructions."
        retain_text(self.body, text)
        self.body.setFont_(A.NSFont.monospacedSystemFontOfSize_weight_(14, A.NSFontWeightRegular) if artifact and artifact.kind in {"code", "patch"} or self.debug_mode else A.NSFont.systemFontOfSize_(15))

    def changeView_(self, sender):
        # Kept as the annotation checkbox action; selecting a type is explicit.
        self.refresh_body()

    def chooseOutputType_(self, sender):
        if self.browser.select_type(sender.output_key):
            self.show_selected_output()

    def toggleComposer_(self, sender):
        self.composer_open = not self.composer_open
        self.controls["task"].setTitle_("Close task" if self.composer_open else "Task…")
        self.relayout()

    def toggleDebug_(self, sender):
        self.set_debug(not self.debug_mode)

    @objc.python_method
    def set_debug(self, enabled):
        self.debug_mode = enabled
        self.controls["debug"].setState_(int(enabled))
        if not enabled and self.browser.active_key not in {row[0] for row in self.browser.rows()}:
            key = self.browser.actionable()
            if key:
                self.browser.select_type(key)
            else:
                self.browser.state.active_key = ""
        self.render_response()
        for scroll in (self.body_scroll, self.graph_scroll, self.image_scroll):
            scroll.reflectScrolledClipView_(scroll.contentView())

    def togglePin_(self, sender):
        if self.browser.frozen:
            self.latestOutput_(None)
        else:
            self.browser.freeze()
            self.show_frozen_status()

    @objc.python_method
    def show_selected_output(self):
        self.body.setSelectedRange_((0, 0))
        for scroll in (self.body_scroll, self.graph_scroll, self.image_scroll):
            scroll.contentView().scrollToPoint_((0, 0))
        self.render_response()
        for scroll in (self.body_scroll, self.graph_scroll, self.image_scroll):
            scroll.reflectScrolledClipView_(scroll.contentView())

    def olderOutput_(self, sender):
        if self.browser.move(-1):
            self.show_selected_output()

    def newerOutput_(self, sender):
        if self.browser.move(1):
            self.show_selected_output()

    def latestOutput_(self, sender):
        self.browser.resume()
        self.show_selected_output()

    @objc.python_method
    def copy_text(self, explained=False):
        artifact = self.current_artifact()
        if artifact:
            return artifact.annotated_text() if explained else artifact.clean_text()
        if self.browser.active_key == REPLIES:
            return self.reply_text(debug=explained)
        response = self.displayed_response()
        if response and self.browser.active_key == GUIDANCE:
            return response.summary + ("\n\n" + "\n".join(response.open_questions) if response.open_questions else "")
        return str(self.body.string())

    def copyClean_(self, sender):
        pasteboard = A.NSPasteboard.generalPasteboard()
        artifact = self.current_artifact()
        if artifact and artifact.kind == "image":
            try:
                picture = A.NSImage.alloc().initWithContentsOfFile_(str(image_path(json.loads(artifact.content))))
                pasteboard.clearContents()
                pasteboard.writeObjects_([picture])
                self.status.setStringValue_("Generated image copied.")
            except (ValueError, OSError) as error:
                self.status.setStringValue_(str(error))
                self.notice_text = str(error)[:350]
                self.relayout()
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
            selected_view="Artifact" if self.current_artifact() else "Conversation" if self.browser.active_key == REPLIES else "Context",
            output_history=self.output_history,
            output_browser=self.browser,
            debug_mode=self.debug_mode,
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
        self.coordinator.last_refresh_reason = ""
        # Browsing freezes presentation only; generation/context keep the latest.
        self.coordinator.pinned = False
        self.output_history.clear()
        if document.outputs:
            self.output_history.entries = [item.model_copy(update={"session_id": self.context.session_id}, deep=True)
                                           for item in document.outputs]
        else:
            for response in document.history:
                self.output_history.append(response, session_id=self.context.session_id, goal=self.context.goal,
                                           lane="saved", sources=self.context.observations, at=document.saved_at)
            if document.current:
                self.output_history.append(document.current, session_id=self.context.session_id, goal=self.context.goal,
                                           lane="saved", sources=self.context.observations, at=document.saved_at,
                                           artifacts=document.displayed_artifacts)
        self.browser = OutputBrowser(document.output_browser)
        if document.output_browser:
            for stream in self.browser.state.types.values():
                for version in stream.versions:
                    version.session_id = self.context.session_id
            self.debug_mode = document.debug_mode
        else:
            for entry in self.output_history.entries:
                self.browser.ingest(entry)
            legacy_key = artifact_key(document.selected_id) if document.selected_view == "Artifact" else {
                "Conversation": REPLIES, "Context": CONTEXT, "Observed files": FILES, "History": EVENTS,
            }.get(document.selected_view, "")
            if legacy_key:
                self.browser.select_type(legacy_key, follow=not document.pinned)
                stream = self.browser.current
                if stream and document.selected_output_id:
                    match = next((version for version in stream.versions if version.parent_id == document.selected_output_id), None)
                    if match:
                        stream.cursor = match.id
            self.debug_mode = legacy_key in LIVE_DEBUG or legacy_key == artifact_key("_assistance_plan")
        self.coordinator.artifact_bases = dict(document.artifact_bases)
        self.loaded_base_hashes = dict(document.base_hashes)
        self.restored_artifact_keys = {digest(a.model_dump()) for a in document.displayed_artifacts}
        self.restored_artifact_keys.update(digest(a.model_dump()) for item in document.outputs for a in item.artifacts)
        self.restored_artifact_keys.update(digest(version.artifact.model_dump()) for stream in self.browser.state.types.values()
                                          for version in stream.versions if version.artifact)
        self.goal.setStringValue_(self.context.goal)
        self.controls["debug"].setState_(int(self.debug_mode))
        self.body.setSelectedRange_((0, 0))
        self.render_response()
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
            digest(self.browser.state.model_dump()),
            self.debug_mode,
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
        if not self.browser.visible:
            return
        panel = A.NSSavePanel.savePanel()
        suffix = {"diagram": ".svg", "image": ".png", "patch": ".diff", "structured": ".json"}.get(artifact.kind if artifact else "text", ".txt")
        panel.setNameFieldStringValue_(Path(artifact.id).name + suffix if artifact else "output.txt")
        if panel.runModal() == A.NSModalResponseOK:
            try:
                target = Path(str(panel.URL().path()))
                if artifact and artifact.kind == "image":
                    target.write_bytes(image_path(json.loads(artifact.content)).read_bytes())
                else:
                    target.write_text(diagram_svg(artifact) if artifact and artifact.kind == "diagram" else self.copy_text())
                self.status.setStringValue_("Artifact exported.")
            except (OSError, ValueError) as error:
                self.status.setStringValue_(str(error))
                self.notice_text = str(error)[:350]
                self.relayout()

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
            panes=[(self.body_scroll, self.body), (self.types_scroll, None),
                   (self.input_scroll, self.input), (self.graph_scroll, None), (self.image_scroll, None)],
            fields=[self.goal],
            buttons=[*self.controls.values(), self.add_button, self.source,
                     self.annotation, self.pin, self.older, self.newer, self.latest, self.copy, self.copy_notes, self.export],
            canvases=[self.graph, self.image_canvas, self.types_canvas],
        )
        for control in self.type_buttons.values():
            control.setNeedsDisplay_(True)
        self.relayout()

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
        debug_views = {"view_context": CONTEXT, "view_files": FILES, "view_history": EVENTS}
        if action in handlers:
            handlers[action](None)
        elif action in debug_views:
            self.set_debug(True)
            self.browser.select_type(debug_views[action])
            self.show_selected_output()
        elif action in {"view_artifact", "view_conversation"}:
            self.set_debug(False)
            key = self.browser.actionable(artifacts=True) if action == "view_artifact" else REPLIES if REPLIES in self.browser.state.types else GUIDANCE
            if key and self.browser.select_type(key):
                self.show_selected_output()
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
            self.cycle_artifact(1)
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
        if self.browser.cycle_type(step, debug=self.debug_mode):
            self.show_selected_output()

    @objc.python_method
    def page_output(self, direction):
        self.browser.freeze()
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
        self.page_output(1)

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
        self.coordinator.current = self.coordinator.pending = None
        self.coordinator.pending_version = None
        self.coordinator.pinned = False
        self.coordinator.last_requested_revision = -1
        self.coordinator.last_refresh_reason = ""
        self.coordinator.history.clear()
        self.output_history.clear()
        self.displayed_artifacts = []
        self.loaded_base_hashes = {}
        self.restored_artifact_keys = set()
        self.artifact_context = None
        self.body.setSelectedRange_((0, 0))
        self.pin.setTitle_("Pin")
        self.selected_id = ""
        self.browser.clear()
        self.notice_text = ""
        self.update_navigation_controls()
        self.render_response()

    @objc.python_method
    def load_demo(self, design):
        self.pause()
        self.displayed_artifacts = []
        self.artifact_context = None
        self.body.setSelectedRange_((0, 0))
        self.demo = True
        self.context.set_repo("")
        self.coordinator.current = self.coordinator.pending = None
        self.coordinator.pinned = False
        self.output_history.clear()
        self.browser.clear()
        seed_demo(self.context, design=design)
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
    def smoke_tick(self):
        from otsc.native_smoke import smoke_tick

        smoke_tick(self)


def run(options):
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    controller = Controller.alloc().initWithOptions_(options)
    app.setDelegate_(controller)
    AppHelper.runEventLoop()
