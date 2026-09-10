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
