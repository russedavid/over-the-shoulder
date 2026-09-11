"""Small AppKit helpers, shared by the existing-stack desktop views."""

import AppKit as A
import objc
from Foundation import NSMakeRect


def color(hex_value, *, alpha=1.0):
    value = hex_value.lstrip("#")
    return A.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        int(value[:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255, alpha
    )


class FlippedView(A.NSView):
    def drawRect_(self, rect):
        color("f8f4ec", alpha=getattr(self, "background_alpha", 1.0)).setFill()
        # Replace the backing pixels; repeated redraws must not accumulate opacity.
        A.NSRectFillUsingOperation(self.bounds(), A.NSCompositingOperationCopy)

    def isOpaque(self):
        return getattr(self, "background_alpha", 1.0) >= 1.0

    def isFlipped(self):
        return True

    def setFrameSize_(self, size):
        objc.super(FlippedView, self).setFrameSize_(size)
        owner = getattr(self, "layout_owner", None)
        if owner:
            owner.relayout()


class OutputTypeButton(A.NSButton):
    """A persistent native selector row with an opaque unread-count badge."""

    def isFlipped(self):
        return True

    def isOpaque(self):
        return False

    def drawRect_(self, rect):
        overlay = bool(self.window() and self.window().ignoresMouseEvents())
        (A.NSColor.clearColor() if overlay else color("f8f4ec")).setFill()
        A.NSRectFillUsingOperation(self.bounds(), A.NSCompositingOperationCopy)
        width, height = self.bounds().size
        selected = bool(getattr(self, "output_selected", False))
        if selected:
            alpha = .05 if overlay else 1.0
            color("e8d8c1", alpha=alpha).setFill()
            A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(1, 1, width - 2, height - 2), 7, 7).fill()
            color("8b6544").setFill()
            A.NSRectFill(NSMakeRect(2, 8, 3, height - 16))
        count = getattr(self, "newer_count", 0)
        badge = str(count) if count < 100 else "99+"
        badge_width = max(24, 10 + 8 * len(badge)) if count else 0
        paragraph = A.NSMutableParagraphStyle.alloc().init()
        paragraph.setLineBreakMode_(A.NSLineBreakByTruncatingTail)
        attrs = {A.NSFontAttributeName: A.NSFont.boldSystemFontOfSize_(13) if selected else A.NSFont.systemFontOfSize_(13),
                 A.NSForegroundColorAttributeName: color("30291f"), A.NSParagraphStyleAttributeName: paragraph}
        heading = getattr(self, "output_heading", str(self.title()))
        subtitle = getattr(self, "output_subtitle", "")
        text_width = width - 24 - (badge_width + 6 if count else 0)
        A.NSString.stringWithString_(heading).drawInRect_withAttributes_(
            NSMakeRect(12, 5 if subtitle else 11, text_width, 20), attrs)
        if subtitle:
            A.NSString.stringWithString_(subtitle).drawInRect_withAttributes_(
                NSMakeRect(12, 24, text_width, 16),
                {**attrs, A.NSFontAttributeName: A.NSFont.systemFontOfSize_(11), A.NSForegroundColorAttributeName: color("6d5947")})
        if count:
            box = NSMakeRect(width - badge_width - 8, (height - 24) / 2, badge_width, 24)
            color("bc3731").setFill()
            A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, 12, 12).fill()
            paragraph = A.NSMutableParagraphStyle.alloc().init()
            paragraph.setAlignment_(A.NSTextAlignmentCenter)
            A.NSString.stringWithString_(badge).drawInRect_withAttributes_(
                NSMakeRect(box.origin.x, box.origin.y + 4, badge_width, 18),
                {A.NSFontAttributeName: A.NSFont.boldSystemFontOfSize_(12), A.NSForegroundColorAttributeName: A.NSColor.whiteColor(),
                 A.NSParagraphStyleAttributeName: paragraph})


def label(parent, text, *, size=13, bold=False):
    view = A.NSTextField.wrappingLabelWithString_(text)
    view.setFont_(A.NSFont.boldSystemFontOfSize_(size) if bold else A.NSFont.systemFontOfSize_(size))
    view.setTextColor_(color("30291f"))
    parent.addSubview_(view)
    return view


def field(parent, value="", placeholder="", *, secure=False):
    cls = A.NSSecureTextField if secure else A.NSTextField
    view = cls.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 26))
    view.setStringValue_(value)
    view.setPlaceholderString_(placeholder)
    parent.addSubview_(view)
    return view


def button(parent, title, target, action, *, checkbox=False):
    view = A.NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 30))
    view.setTitle_(title)
    view.setTarget_(target)
    view.setAction_(action)
    if checkbox:
        view.setButtonType_(A.NSButtonTypeSwitch)
    else:
        view.setBezelStyle_(A.NSBezelStyleRounded)
    parent.addSubview_(view)
    return view


def popup(parent, items, selected=None, target=None, action=None):
    view = A.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 160, 28), False)
    view.addItemsWithTitles_(items)
    if selected in items:
        view.selectItemWithTitle_(selected)
    if target:
        view.setTarget_(target)
        view.setAction_(action)
    parent.addSubview_(view)
    return view


def scroll_text(parent, *, editable=False, monospace=False):
    scroll = A.NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(A.NSBezelBorder)
    text = A.NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 380, 300))
    text.setEditable_(editable)
    text.setSelectable_(True)
    text.setRichText_(False)
    text.setAllowsUndo_(editable)
    text.setVerticallyResizable_(True)
    text.setHorizontallyResizable_(False)
    text.setAutoresizingMask_(A.NSViewWidthSizable)
    text.textContainer().setWidthTracksTextView_(True)
    text.textContainer().setContainerSize_((380, 10_000_000))
    text.setTextContainerInset_((14, 12))
    text.setFont_(
        A.NSFont.monospacedSystemFontOfSize_weight_(14, A.NSFontWeightRegular)
        if monospace
        else A.NSFont.systemFontOfSize_(14)
    )
    text.setBackgroundColor_(color("fffdf9"))
    text.setTextColor_(color("30291f"))
    scroll.setDocumentView_(text)
    parent.addSubview_(scroll)
    return scroll, text


def frame(view, x, y, w, h):
    view.setFrame_(NSMakeRect(x, y, max(1, w), max(1, h)))


def retain_text(view, text):
    """Keep selection and scroll; callers postpone updates while the user is selecting."""
    if str(view.string()) == text:
        return
    selection = view.selectedRange()
    scroll = view.enclosingScrollView()
    position = scroll.contentView().bounds().origin if scroll else None
    view.setString_(text)
    length = view.textStorage().length()
    start = min(selection.location, length)
    view.setSelectedRange_((start, min(selection.length, length - start)))
    if scroll and position:
        scroll.contentView().scrollToPoint_(position)
        scroll.reflectScrolledClipView_(scroll.contentView())


def set_overlay_appearance(window, root, enabled, *, panes, fields=(), buttons=(), canvases=()):
    """Make the backgrounds transparent without fading text or replacing views."""
    saved = getattr(root, "overlay_restore", None)
    if enabled and saved is None:
        changes = []

        def change(obj, getter, setter, value):
            changes.append((getattr(obj, setter), getattr(obj, getter)(), value))

        change(window, "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
        change(window, "isOpaque", "setOpaque_", False)
        change(window, "hasShadow", "setHasShadow_", False)
        change(window, "titlebarAppearsTransparent", "setTitlebarAppearsTransparent_", True)
        for scroll, text in panes:
            change(scroll, "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
            change(scroll, "drawsBackground", "setDrawsBackground_", False)
            change(scroll, "borderType", "setBorderType_", A.NSNoBorder)
            change(scroll, "scrollerStyle", "setScrollerStyle_", A.NSScrollerStyleOverlay)
            change(scroll.contentView(), "drawsBackground", "setDrawsBackground_", False)
            change(scroll.contentView(), "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
            if text is not None:
                change(text, "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
                change(text, "drawsBackground", "setDrawsBackground_", False)
        for field in fields:
            change(field, "isBordered", "setBordered_", False)
            change(field, "isBezeled", "setBezeled_", False)
            change(field, "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
            change(field, "drawsBackground", "setDrawsBackground_", False)
            editor = field.currentEditor()
            if editor is not None:
                change(editor, "backgroundColor", "setBackgroundColor_", A.NSColor.clearColor())
                change(editor, "drawsBackground", "setDrawsBackground_", False)
        for button in buttons:
            change(button, "isBordered", "setBordered_", False)
        alphas = [(view, getattr(view, "background_alpha", 1.0)) for view in (root, *canvases)]
        root.overlay_restore = (changes, alphas)
        for setter, _, value in changes:
            setter(value)
        root.background_alpha = 0.05
        for canvas in canvases:
            canvas.background_alpha = 0.0
    elif not enabled and saved is not None:
        changes, alphas = saved
        for setter, old, _ in reversed(changes):
            setter(old)
        for view, alpha in alphas:
            view.background_alpha = alpha
        root.overlay_restore = None
    # A window alpha multiplies every child, including its text. Keep it at one.
    window.setAlphaValue_(1.0)
    window.setIgnoresMouseEvents_(enabled)
    for view in (root, *canvases):
        view.setNeedsDisplay_(True)
