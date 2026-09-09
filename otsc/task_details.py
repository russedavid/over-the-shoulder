"""A small optional editor for the user's explicit constraints and decisions."""

import AppKit as A
import objc
from Foundation import NSMakeRect, NSObject

from otsc.native import FlippedView, button, color, frame, label, scroll_text


class TaskDetails(NSObject):
    def initWithController_(self, controller):
        self = objc.super(TaskDetails, self).init()
        if self is None:
            return None
        self.controller = controller
        self.window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(150, 140, 820, 480),
            A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
            A.NSBackingStoreBuffered,
            False,
        )
        self.window.setReleasedWhenClosed_(False)
        self.window.setTitle_("Task details")
        self.window.setBackgroundColor_(color("f8f4ec"))
        root = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, 820, 480))
        self.window.setContentView_(root)
        frame(label(root, "Keep the task's requirements and decisions explicit", size=18, bold=True), 20, 18, 780, 30)
        frame(
            label(
                root,
                "One entry per line. These are your confirmed details; model suggestions do not change them automatically.",
            ),
            20,
            54,
            780,
            42,
        )
        frame(label(root, "Constraints", bold=True), 20, 106, 370, 26)
        frame(label(root, "Decisions", bold=True), 420, 106, 370, 26)
        left, self.constraints = scroll_text(root, editable=True)
        frame(left, 20, 140, 380, 250)
        right, self.decisions = scroll_text(root, editable=True)
        frame(right, 420, 140, 380, 250)
        self.constraints.setString_("\n".join(controller.context.constraints))
        self.decisions.setString_("\n".join(controller.context.decisions))
        self.status = label(root, "")
        frame(self.status, 20, 415, 580, 46)
        frame(button(root, "Save details", self, "save:"), 650, 417, 150, 32)
        self.window.center()
        return self

    def save_(self, sender):
        try:
            self.controller.context.set_task_details(
                str(self.constraints.string()).splitlines(), str(self.decisions.string()).splitlines()
            )
            self.controller.checkpoint_if_enabled(force=True)
            self.controller.status.setStringValue_("Task details saved. Help now uses these constraints and decisions.")
            self.window.orderOut_(None)
        except ValueError as error:
            self.status.setStringValue_(str(error))
