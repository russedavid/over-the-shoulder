"""A single native AppKit application, with retained views and background workers."""

import json
import math
import queue
import threading
import time
from pathlib import Path

import AppKit as A
import objc
from Foundation import NSMakeRect, NSObject, NSTimer
from PyObjCTools import AppHelper

from otsc.capture import AudioCapture, ScreenCapture
from otsc.context import ContextStore
from otsc.demo import DemoProvider, seed_demo
from otsc.diagram import diagram_layout, diagram_svg, edge_geometry
from otsc.native import FlippedView, button, color, field, frame, label, popup, retain_text, scroll_text
from otsc.preferences import Preferences
from otsc.privacy import private_directory, private_write, redact
from otsc.providers import provider_for
from otsc.scheduler import Coordinator
from otsc.settings import Credentials, Settings, load_settings, save_settings
from otsc.workspace import read_project


class DiagramView(FlippedView):
    def drawRect_(self, rect):
        color("fffdf9").setFill()
        A.NSRectFill(self.bounds())
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
        self.coordinator = Coordinator(self.context, self.get_provider, hourly_limit=self.settings.hourly_requests)
        self.screen = ScreenCapture()
        self.audio = None
        self.running = False
        self.capture_busy = False
        self.capture_generation = 0
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
        return self

    @objc.python_method
    def get_provider(self, lane):
        return DemoProvider() if self.demo else provider_for(getattr(self.settings, lane), self.credentials)

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
        ]:
            self.controls[name] = button(self.root, title, self, action)
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
        self.status = label(
            self.root, "Capture is paused. Cmd-Return asks for help; all capture starts explicitly.", size=12
        )
        self.root.layout_owner = self
        self.relayout()

    @objc.python_method
    def make_menu(self):
        menu = A.NSMenu.alloc().init()
        app_item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("Over The Shoulder Coder", None, "")
        submenu = A.NSMenu.alloc().init()
        for title, action, key in [
            ("Help now", "helpNow:", "\r"),
            ("Settings…", "settings:", ","),
            ("Toggle click-through", "toggleClickThrough:", "i"),
            ("New task", "newTask:", "n"),
            ("Load synthetic code example", "demoCode:", ""),
            ("Load synthetic design example", "demoDesign:", ""),
            ("Quit Over The Shoulder Coder", "quit:", "q"),
        ]:
            item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
            item.setTarget_(self)
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
        if getattr(self.graph, "artifact", None):
            width = self.graph_scroll.contentSize().width
            _, height = diagram_layout(self.graph.artifact, width)
            self.graph.setFrameSize_((width, height + 240))
            self.graph.setNeedsDisplay_(True)
        frame(self.pin, 16, h - 88, 108, 30)
        frame(self.copy, 130, h - 88, 110, 30)
        frame(self.copy_notes, 246, h - 88, 145, 30)
        frame(self.export, 399, h - 88, 100, 30)
        frame(self.status, 20, h - 49, w - 40, 45)

    @objc.python_method
    def apply_settings(self, settings):
        self.pause()
        self.settings = settings
        self.coordinator.hourly_limit = settings.hourly_requests
        self.coordinator.cancel()
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
            self.awaiting_audio_flush = self.audio.flush_for_help()
        self.capture_context(request_after=False)

    @objc.python_method
    def send_manual_help_if_ready(self):
        if self.manual_help_pending and not self.capture_busy and self.awaiting_audio_flush is None:
            self.manual_help_pending = False
            self.pending_manual = False
            self.flush_context()
            self.coordinator.request(manual=True)

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
        self.context.add(kind, text, channel, speaker)
        self.input.setString_("")
        self.status.setStringValue_("Context added. Press Help now, or let the next cadence pick it up.")
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
            self.audio = AudioCapture(self.settings, self.events, self.credentials)
            self.audio.start()
            self.running = True
            self.controls["start"].setTitle_("Pause following")
            self.next_capture = time.monotonic() + self.settings.interval_seconds
            self.capture_context(request_after=True)
        except Exception as error:
            self.pause()
            self.status.setStringValue_(redact(str(error)))

    @objc.python_method
    def pause(self):
        self.running = False
        self.capture_generation += 1
        self.capture_request_after = False
        self.pending_manual = False
        self.manual_help_pending = False
        self.awaiting_audio_flush = None
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
        settings = self.settings.model_copy(deep=True)
        root = self.context.repo_root
        goal = self.context.goal
        if settings.capture_screen:
            self.window.orderOut_(None)
        self.status.setStringValue_("Reading the current work…")

        def worker():
            result = {"type": "capture_done", "generation": generation, "root": root, "at": time.time()}
            try:
                if settings.capture_screen:
                    result["screen"] = self.screen.capture(
                        settings.screen_region,
                        keep_image=settings.quick.send_images or settings.deep.send_images,
                        on_captured=lambda: self.events.put({"type": "screen_taken"}),
                    )
                if root:
                    result["files"], result["files_message"] = read_project(root, focus=goal)
            except Exception as error:
                result["error"] = redact(str(error))[:450]
            self.events.put(result)

        # Hide only this window before the screenshot, then do OCR off the AppKit thread.
        timer = threading.Timer(0.20, worker)
        timer.daemon = True
        timer.start()

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
        for source in (self.events, self.coordinator.events):
            for _ in range(100):
                try:
                    event = source.get_nowait()
                except queue.Empty:
                    break
                self.handle_event(event)
        if self.running and time.monotonic() >= self.next_capture:
            self.next_capture = time.monotonic() + self.settings.interval_seconds
            if self.manual_help_pending:
                pass
            elif self.demo:
                self.coordinator.request()
            else:
                self.capture_context(request_after=True)
        if (
            self.running
            and self.buffered_context
            and not self.coordinator.active_lanes
            and not self.capture_busy
            and not self.manual_help_pending
        ):
            self.flush_context()
            self.coordinator.request()
        if self.options.smoke_test:
            self.smoke_tick()

    @objc.python_method
    def handle_event(self, event):
        kind = event["type"]
        if kind == "screen_taken":
            self.window.orderFront_(None)
        elif kind == "capture_done":
            self.capture_busy = False
            if not self.closed:
                self.window.orderFront_(None)
            if event["generation"] != self.capture_generation:
                return
            if self.coordinator.active_lanes and not self.pending_manual:
                self.buffered_context.append(event)
                self.buffered_context = self.buffered_context[-80:]
            else:
                self.apply_context_event(event)
            self.status.setStringValue_(
                event.get("error") or event.get("files_message") or "Current screen added to context."
            )
            should_request = self.capture_request_after
            self.capture_request_after = False
            if self.manual_help_pending:
                self.send_manual_help_if_ready()
            elif should_request and not self.coordinator.active_lanes:
                self.flush_context()
                self.coordinator.request()
            self.refresh_body()
        elif kind == "project":
            if event["generation"] == self.capture_generation:
                self.context.set_verified_files(event["root"], event["files"])
                self.status.setStringValue_(event["message"])
                self.refresh_body()
        elif kind in {"speech", "audio_status", "audio_error", "audio_flushed"}:
            if not self.audio or self.audio.id != event["capture_id"]:
                return
            if kind == "audio_flushed":
                if event["flush_id"] == self.awaiting_audio_flush:
                    self.awaiting_audio_flush = None
                    if not event["complete"]:
                        self.manual_help_pending = False
                        self.pending_manual = False
                        self.status.setStringValue_(
                            "Recent speech could not be finished. Check the audio settings; the current screen is still available."
                        )
                    else:
                        self.send_manual_help_if_ready()
            elif kind == "speech":
                if self.coordinator.active_lanes:
                    self.buffered_context.append(event)
                    self.buffered_context = self.buffered_context[-80:]
                else:
                    self.apply_context_event(event)
                self.status.setStringValue_(f"Heard {event['speaker']} through {event['channel']}. Context updated.")
                self.refresh_body()
            else:
                if kind == "audio_error":
                    self.manual_help_pending = False
                    self.pending_manual = False
                    self.awaiting_audio_flush = None
                self.status.setStringValue_(event["message"])
        elif kind == "started":
            self.status.setStringValue_(f"Quick and deep assistance started · context {event['revision']}.")
        elif kind == "progress" and event["request_id"] == self.coordinator.request_id:
            self.status.setStringValue_(event["lane"].capitalize() + ": " + event["message"][:280])
            if (
                event["lane"] == "quick"
                and event["message"].startswith("Draft: ")
                and not self.coordinator.pinned
                and not self.summary.selectedRange().length
                and self.coordinator.published_lane < 1
                and self.context.revision == self.coordinator.last_requested_revision
            ):
                retain_text(self.summary, "Quick thought\n\n" + event["message"][7:])
        elif kind in {"result", "error", "cancelled"}:
            if kind == "result" and (self.body.selectedRange().length or self.summary.selectedRange().length):
                self.coordinator.pinned = True
            if not self.coordinator.accept(event):
                return
            if kind == "result":
                self.pin.setTitle_("Unpin" if self.coordinator.pinned else "Pin")
                if self.coordinator.pinned:
                    self.status.setStringValue_(
                        "An update is ready. Unpin when you want to replace the selected or pinned work."
                    )
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
    def apply_context_event(self, event):
        if event["type"] == "speech":
            self.context.add(
                "speech", event["text"], event["channel"], event["speaker"], at=event["at"], confidence="transcribed"
            )
        else:
            if "screen" in event:
                text, path = event["screen"]
                self.context.add("screen", text, "screen", image_path=path, confidence="uncertain", at=event.get("at"))
            if "files" in event:
                self.context.set_verified_files(event["root"], event["files"])

    @objc.python_method
    def flush_context(self):
        pending, self.buffered_context = self.buffered_context, []
        for event in pending:
            self.apply_context_event(event)

    @objc.python_method
    def render_response(self):
        response = self.coordinator.current
        if not response:
            return
        retain_text(self.summary, response.task + "\n\n" + response.summary)
        task_key = (self.context.session_id, self.context.goal)
        if response.artifacts:
            self.displayed_artifacts = response.artifacts
            self.artifact_context = task_key
        elif self.artifact_context != task_key:
            self.displayed_artifacts = []
        previous = self.selected_id
        self.artifacts.removeAllItems()
        titles = [a.title for a in self.displayed_artifacts]
        self.artifacts.addItemsWithTitles_(titles or ["Conversation / next step"])
        index = next((i for i, a in enumerate(self.displayed_artifacts) if a.id == previous), 0)
        self.artifacts.selectItemAtIndex_(index)
        self.selected_id = self.displayed_artifacts[index].id if self.displayed_artifacts else ""
        self.refresh_body()

    @objc.python_method
    def current_artifact(self):
        return next((a for a in self.displayed_artifacts if a.id == self.selected_id), None)

    @objc.python_method
    def refresh_body(self):
        response = self.coordinator.current
        view = str(self.view.titleOfSelectedItem())
        artifact = self.current_artifact()
        self.graph_scroll.setHidden_(True)
        self.body_scroll.setHidden_(False)
        if view == "Context":
            text = "\n\n".join(
                f"{o.id} · {o.channel} · {o.speaker} · {o.confidence}\n{o.text}" for o in self.context.observations
            )
        elif view == "Observed files":
            excerpts = []
            for path, fragments in self.context.files.items():
                for index, fragment in enumerate(fragments, 1):
                    location = (
                        f"starts at line {fragment.first_line}" if fragment.first_line else "line position unknown"
                    )
                    excerpts.append(f"{path} · excerpt {index} · {location}\n{fragment.content}")
            text = "Observed on screen — partial files\n\n" + (
                "\n\n".join(excerpts) or "No file excerpts identified yet."
            )
            text += "\n\nFiles read from the selected project\n" + "\n".join(
                f"{p} ({len(c.splitlines())} lines)" for p, c in self.context.verified_files.items()
            )
            if self.context.repo_root:
                text += "\n\nSelected folder: " + self.context.repo_root
        elif view == "History":
            records = []
            for old in reversed(self.coordinator.history):
                records.append(
                    old.task
                    + "\n"
                    + old.summary
                    + "\n\n"
                    + "\n\n".join(
                        a.title
                        + " · "
                        + a.basis
                        + "\n"
                        + (a.annotated_text() if self.annotation.state() else a.clean_text())
                        for a in old.artifacts
                    )
                )
            text = "\n\n——————\n\n".join(records) or "Previous accepted responses will appear here."
        elif response and (view == "Conversation" or not artifact):
            sources = {o.id: o for o in self.context.observations}
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
            if artifact.kind == "diagram":
                self.graph.artifact = artifact
                self.graph_scroll.setHidden_(False)
                self.body_scroll.setHidden_(True)
                self.relayout()
            text = (
                basis
                + (" · " + artifact.path if artifact.path else "")
                + "\n\n"
                + (artifact.annotated_text() if self.annotation.state() else artifact.clean_text())
            )
        else:
            text = "Your artifact will appear here. You can select and copy text, resize the window, or pin a response while reading."
        retain_text(self.body, text)

    def changeView_(self, sender):
        self.refresh_body()

    def chooseArtifact_(self, sender):
        if self.displayed_artifacts:
            self.selected_id = self.displayed_artifacts[self.artifacts.indexOfSelectedItem()].id
        self.refresh_body()

    def togglePin_(self, sender):
        self.coordinator.toggle_pin()
        self.pin.setTitle_("Unpin" if self.coordinator.pinned else "Pin")
        self.render_response()
        self.status.setStringValue_("Current work pinned." if self.coordinator.pinned else "Current work unpinned.")

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
        pasteboard.clearContents()
        pasteboard.setString_forType_(self.copy_text(), A.NSPasteboardTypeString)
        self.status.setStringValue_("Clean artifact copied. Teaching annotations are separate.")

    def copyExplained_(self, sender):
        pasteboard = A.NSPasteboard.generalPasteboard()
        pasteboard.clearContents()
        pasteboard.setString_forType_(self.copy_text(explained=True), A.NSPasteboardTypeString)
        self.status.setStringValue_("Artifact and line explanations copied.")

    def exportArtifact_(self, sender):
        artifact = self.current_artifact()
        if not artifact:
            return
        panel = A.NSSavePanel.savePanel()
        suffix = ".svg" if artifact.kind == "diagram" else ".diff" if artifact.kind == "patch" else ".txt"
        panel.setNameFieldStringValue_(artifact.id + suffix)
        if panel.runModal() == A.NSModalResponseOK:
            text = diagram_svg(artifact) if artifact.kind == "diagram" else artifact.clean_text()
            try:
                Path(str(panel.URL().path())).write_text(text)
                self.status.setStringValue_("Artifact exported.")
            except OSError as error:
                self.status.setStringValue_(str(error))

    def toggleClickThrough_(self, sender):
        ignore = not self.window.ignoresMouseEvents()
        self.window.setIgnoresMouseEvents_(ignore)
        self.window.setAlphaValue_(0.05 if ignore else 1.0)
        self.status.setStringValue_(
            "Click-through on. Activate this app and press Cmd-Shift-I to restore interaction."
            if ignore
            else "Window interaction restored."
        )

    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.window.setIgnoresMouseEvents_(False)
        self.window.setAlphaValue_(1.0)
        self.window.makeKeyAndOrderFront_(None)
        return True

    def newTask_(self, sender):
        self.pause()
        self.context.goal = ""
        self.context.clear()
        self.goal.setStringValue_("")
        self.input.setString_("")
        self.coordinator.current = self.coordinator.pending = None
        self.coordinator.pending_version = None
        self.coordinator.pinned = False
        self.coordinator.last_requested_revision = -1
        self.coordinator.history.clear()
        self.displayed_artifacts = []
        self.artifact_context = None
        self.body.setSelectedRange_((0, 0))
        self.summary.setSelectedRange_((0, 0))
        self.pin.setTitle_("Pin")
        self.selected_id = ""
        self.artifacts.removeAllItems()
        self.artifacts.addItemWithTitle_("No artifact yet")
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
        self.timer.invalidate()
        self.coordinator.close()
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
        try:
            if time.monotonic() - self.smoke_started > 12:
                raise RuntimeError("Native smoke test timed out waiting for demo results")
            response = self.coordinator.current
            if not response or not response.artifacts:
                return
            directory = private_directory(Path(self.options.smoke_test))
            if self.smoke_step == 0:
                assert response.artifacts[0].kind == "code"
                before = self.copy_text()
                assert "return None" in before and "Represent missing" not in before
                self.body.setSelectedRange_((5, 10))
                self.window.setContentSize_((850, 680))
                assert self.copy_text() == before
                assert self.body.selectedRange().length == 10
                self.toggleClickThrough_(None)
                self.toggleClickThrough_(None)
                assert self.copy_text() == before
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
                # Synthetic incoming audio cannot starve an in-flight deep response.
                from types import SimpleNamespace

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
                        "at": 12345.0,
                    }
                )
                assert self.context.revision == revision and len(self.buffered_context) == 1
                self.coordinator.active_lanes.clear()
                self.flush_context()
                assert self.context.observations[-1].at == 12345.0
                self.audio = None
                self.smoke_step = 1
                self.load_demo(True)
            elif self.smoke_step == 1 and response.artifacts[0].kind == "diagram":
                self.window.setContentSize_((1100, 850))
                self.save_view_image(directory / "diagram.png")
                assert not self.graph_scroll.isHidden()
                self.preferences = Preferences.alloc().initWithController_(self)
                self.preferences.window.orderOut_(None)
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
                                "native diagram",
                                "settings construction",
                                "collaborator response and observed diff",
                                "new audio queued without starving deep work",
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
