"""Small AppKit helpers, shared by the existing-stack desktop views."""

import AppKit as A
import objc
from Foundation import NSMakeRect


def color(hex_value):
    value = hex_value.lstrip("#")
    return A.NSColor.colorWithCalibratedRed_green_blue_alpha_(
        int(value[:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255, 1
    )


class FlippedView(A.NSView):
    def drawRect_(self, rect):
        color("f8f4ec").setFill()
        A.NSRectFill(self.bounds())

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
