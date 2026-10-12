"""An excluded, transparent native window for editing the screen capture area."""

import AppKit as A
import objc
from Foundation import NSMakeRect, NSObject

from otsc.capture_region import FRAME_INSET, initial_rect, region_from_rect
from otsc.native import color


def display_rects():
    return [(*screen.frame().origin, *screen.frame().size) for screen in A.NSScreen.screens()]


class CaptureRegionPanel(A.NSPanel):
    def constrainFrameRect_toScreen_(self, rect, screen):
        # AppKit's default titlebar constraint can pin a selector to one screen.
        # A selection may live on, or straddle, any attached display.
        return rect


class CaptureRegionView(A.NSView):
    def isOpaque(self):
        return False

    def drawRect_(self, dirty):
        bounds = self.bounds()
        A.NSColor.clearColor().setFill()
        A.NSRectFillUsingOperation(bounds, A.NSCompositingOperationCopy)
        width, height = bounds.size
        # Stroke and handles end at the clear interior, never inside the capture.
        rect = NSMakeRect(6, 6, width - 12, height - 12)
        path = A.NSBezierPath.bezierPathWithRect_(rect)
        color("172b38").setStroke()
        path.setLineWidth_(8)
        path.stroke()
        color("41d6ef").setStroke()
        path.setLineWidth_(4)
        path.stroke()
        for x in (1, width / 2 - 4, width - 9):
            for y in (1, height / 2 - 4, height - 9):
                if x == width / 2 - 4 and y == height / 2 - 4:
                    continue
                color("41d6ef").setFill()
                A.NSRectFill(NSMakeRect(x, y, 8, 8))


class CaptureFrame(NSObject):
    def initWithController_(self, controller):
        self = objc.super(CaptureFrame, self).init()
        if self is None:
            return None
        self.controller = controller
        self.positioning = False
        style = A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable | A.NSWindowStyleMaskResizable
        self.window = CaptureRegionPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(100, 100, 820, 520), style, A.NSBackingStoreBuffered, False)
        self.window.setReleasedWhenClosed_(False)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(A.NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setAlphaValue_(1.0)
        self.window.setSharingType_(A.NSWindowSharingNone)
        self.window.setContentMinSize_((180, 120))
        self.window.setLevel_(A.NSFloatingWindowLevel + 1)
        self.window.setHidesOnDeactivate_(False)
        self.window.setCollectionBehavior_(A.NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | A.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = CaptureRegionView.alloc().initWithFrame_(NSMakeRect(0, 0, 820, 520))
        self.window.setContentView_(self.view)
        self.window.setDelegate_(self)
        return self

    @objc.python_method
    def show(self, region):
        screens = list(A.NSScreen.screens())
        preferred = next((i for i, s in enumerate(screens) if s == self.controller.window.screen()), 0)
        x, y, w, h = initial_rect(region, display_rects(), preferred)
        inset = FRAME_INSET
        self.positioning = True
        try:
            content = NSMakeRect(x - inset, y - inset, w + 2 * inset, h + 2 * inset)
            self.window.setFrame_display_(self.window.frameRectForContentRect_(content), True)
        finally:
            self.positioning = False
        self.window.makeKeyAndOrderFront_(None)
        self.changed()

    @objc.python_method
    def selected_region(self):
        rect = self.window.convertRectToScreen_(self.view.bounds())
        inset = FRAME_INSET
        screens = display_rects()
        if not screens:
            raise ValueError("No display is available for capture")
        return region_from_rect((rect.origin.x + inset, rect.origin.y + inset,
                                 rect.size.width - 2 * inset, rect.size.height - 2 * inset),
                                screens[0][1] + screens[0][3])

    @objc.python_method
    def changed(self):
        if self.positioning:
            return
        region = self.selected_region()
        x, y, w, h = region
        self.window.setTitle_(f"Capture area: {w} × {h} at {x}, {y} · Drag / resize · Close keeps area")
        self.view.setNeedsDisplay_(True)
        self.controller.set_capture_region(region, persist=False)

    def windowDidMove_(self, notification):
        self.changed()

    def windowDidResize_(self, notification):
        self.changed()

    def windowDidEndLiveResize_(self, notification):
        self.controller.set_capture_region(self.selected_region(), persist=True)

    def windowShouldClose_(self, sender):
        self.hide()
        return False

    @objc.python_method
    def hide(self):
        self.window.orderOut_(None)
        self.controller.set_capture_region(self.selected_region(), persist=True)

    @objc.python_method
    def dispose(self):
        self.window.setDelegate_(None)
        self.window.orderOut_(None)
        self.controller = None
