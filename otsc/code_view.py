"""Selectable AppKit code/diff text with a separate source-line gutter."""

import AppKit as A
import objc
from Foundation import NSMakeRect, NSNotificationCenter
from pygments.token import Comment, Keyword, Name, Number, String

from otsc.code_rendering import language_info
from otsc.native import color
from otsc.telemetry import digest


def utf16_offsets(text):
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
    return offsets


class CodeTextView(A.NSTextView):
    def drawViewBackgroundInRect_(self, rect):
        A.NSGraphicsContext.saveGraphicsState()
        try:
            A.NSBezierPath.bezierPathWithRect_(self.bounds()).addClip()
            self.draw_code_background(rect)
        finally:
            A.NSGraphicsContext.restoreGraphicsState()

    @objc.python_method
    def draw_code_background(self, rect):
        overlay = bool(self.window() and self.window().ignoresMouseEvents())
        (A.NSColor.clearColor() if overlay else color("fffdf9")).setFill()
        A.NSRectFillUsingOperation(rect, A.NSCompositingOperationCopy)
        if overlay:
            return
        for row, bounds in self.row_rects():
            if row.kind in {"add", "remove"}:
                color("e6f3e8" if row.kind == "add" else "fbe8e7").setFill()
                A.NSRectFill(NSMakeRect(0, bounds.origin.y, self.bounds().size.width, bounds.size.height))

    @objc.python_method
    def row_rects(self):
        document = getattr(self, "code_document", None)
        if not document or not self.textStorage().length():
            return []
        layout, container = self.layoutManager(), self.textContainer()
        layout.ensureLayoutForTextContainer_(container)
        result = []
        inset = self.textContainerInset()
        for row, (start, length) in zip(document.rows, self.row_ranges, strict=True):
            if start == self.textStorage().length() and not row.text:
                bounds = layout.extraLineFragmentRect()
                result.append((row, NSMakeRect(inset.width, bounds.origin.y + inset.height, 0, max(17, bounds.size.height))))
                continue
            if start >= self.textStorage().length():
                continue
            glyphs, _ = layout.glyphRangeForCharacterRange_actualCharacterRange_((start, max(1, length)), None)
            bounds = layout.boundingRectForGlyphRange_inTextContainer_(glyphs, container)
            result.append((row, NSMakeRect(bounds.origin.x + inset.width, bounds.origin.y + inset.height,
                                          bounds.size.width, max(bounds.size.height, 17))))
        return result

    @objc.python_method
    def set_document(self, document, language, path):
        signature = digest({"text": document.text, "rows": [vars(row) for row in document.rows], "language": language})
        if getattr(self, "document_signature", None) == signature:
            return
        self.document_signature = signature
        selection = self.selectedRange()
        scroll = self.enclosingScrollView()
        origin = scroll.contentView().bounds().origin if scroll else None
        self.code_document = document
        text = document.text
        offsets = utf16_offsets(text)
        paragraph = A.NSMutableParagraphStyle.alloc().init()
        paragraph.setLineSpacing_(2)
        font = A.NSFont.monospacedSystemFontOfSize_weight_(14, A.NSFontWeightRegular)
        space = A.NSString.stringWithString_(" ").sizeWithAttributes_({A.NSFontAttributeName: font}).width
        paragraph.setDefaultTabInterval_(space * 4)
        paragraph.setTabStops_([])
        attrs = {A.NSFontAttributeName: font,
                 A.NSForegroundColorAttributeName: color("30291f"), A.NSParagraphStyleAttributeName: paragraph}
        rendered = A.NSMutableAttributedString.alloc().initWithString_attributes_(text, attrs)
        if not document.diff:
            lexer = language_info(language, path)[2]
            if lexer:
                for start, token, value in lexer.get_tokens_unprocessed(text):
                    tone = "477356" if token in Comment else "8d4d8d" if token in Keyword else "9d5528" if token in String else "2b6592" if token in Number else "246778" if token in Name.Function else None
                    if tone and value and 0 <= start < start + len(value) <= len(text):
                        rendered.addAttribute_value_range_(A.NSForegroundColorAttributeName, color(tone),
                                                           (offsets[start], offsets[start + len(value)] - offsets[start]))
        self.row_ranges = []
        position = 0
        for row in document.rows:
            start, end = offsets[position], offsets[position + len(row.text)]
            self.row_ranges.append((start, end - start))
            tone = {"comment": "477356", "hunk": "74634e", "note": "74634e", "add": "185d32", "remove": "9b3034"}.get(row.kind)
            if tone and end > start:
                rendered.addAttribute_value_range_(A.NSForegroundColorAttributeName, color(tone), (start, end - start))
            if end > start and row.kind not in {"hunk", "note"}:
                wrapped = paragraph.mutableCopy()
                indent = len(row.text.expandtabs(4)) - len(row.text.expandtabs(4).lstrip())
                wrapped.setHeadIndent_(space * (indent + (2 if row.kind == "comment" else 4)))
                rendered.addAttribute_value_range_(A.NSParagraphStyleAttributeName, wrapped, (start, end - start))
            position += len(row.text) + 1
        self.textStorage().setAttributedString_(rendered)
        count = self.textStorage().length()
        self.setSelectedRange_((min(selection.location, count), min(selection.length, max(0, count - selection.location))))
        if scroll:
            if origin:
                scroll.contentView().scrollToPoint_(origin)
                scroll.reflectScrolledClipView_(scroll.contentView())
            ruler = scroll.verticalRulerView()
            digits = max((len(str(n)) for row in document.rows for n in (row.old_line, row.new_line) if n is not None), default=2)
            ruler.column_width = max(32, digits * 8 + 8)
            ruler.setRuleThickness_((ruler.column_width * 2 + 20) if document.diff else (ruler.column_width + 10))
            ruler.setNeedsDisplay_(True)
        self.setNeedsDisplay_(True)


class LineNumberRuler(A.NSRulerView):
    def isFlipped(self):
        return True

    def isOpaque(self):
        return False

    def redraw_(self, notification):
        self.setNeedsDisplay_(True)
        self.clientView().setNeedsDisplay_(True)

    def drawRect_(self, rect):
        A.NSGraphicsContext.saveGraphicsState()
        try:
            A.NSBezierPath.bezierPathWithRect_(self.bounds()).addClip()
            objc.super(LineNumberRuler, self).drawRect_(rect)
        finally:
            A.NSGraphicsContext.restoreGraphicsState()

    def drawHashMarksAndLabelsInRect_(self, rect):
        A.NSGraphicsContext.saveGraphicsState()
        try:
            A.NSBezierPath.bezierPathWithRect_(self.bounds()).addClip()
            self.draw_numbers(rect)
        finally:
            A.NSGraphicsContext.restoreGraphicsState()

    @objc.python_method
    def draw_numbers(self, rect):
        overlay = bool(self.window() and self.window().ignoresMouseEvents())
        (A.NSColor.clearColor() if overlay else color("f3eee5")).setFill()
        A.NSRectFillUsingOperation(rect, A.NSCompositingOperationCopy)
        text = self.clientView()
        document = getattr(text, "code_document", None)
        if not document:
            return
        column = getattr(self, "column_width", 32)
        paragraph = A.NSMutableParagraphStyle.alloc().init()
        paragraph.setAlignment_(A.NSTextAlignmentRight)
        font = A.NSFont.monospacedSystemFontOfSize_weight_(11, A.NSFontWeightRegular)
        for row, bounds in text.row_rects():
            point = self.convertPoint_fromView_((0, bounds.origin.y), text)
            if point.y + bounds.size.height < 0 or point.y > self.bounds().size.height:
                continue
            tone = "18703b" if row.kind == "add" else "aa3438" if row.kind == "remove" else "776956"
            attrs = {A.NSFontAttributeName: font, A.NSForegroundColorAttributeName: color(tone), A.NSParagraphStyleAttributeName: paragraph}
            values = (row.old_line, row.new_line) if document.diff else (row.new_line if row.new_line is not None else row.old_line,)
            for index, number in enumerate(values):
                if number is not None:
                    A.NSString.stringWithString_(str(number)).drawInRect_withAttributes_(NSMakeRect(index * column, point.y + 2, column - 5, 17), attrs)
            if row.marker:
                A.NSString.stringWithString_(row.marker).drawInRect_withAttributes_(NSMakeRect(column * 2, point.y + 1, 14, 18), attrs)


class CodeScrollView(A.NSScrollView):
    def tile(self):
        objc.super(CodeScrollView, self).tile()
        ruler = self.verticalRulerView()
        document = self.documentView()
        if not self.rulersVisible() or ruler is None or document is None:
            return
        # AppKit may tile rulers as overlays even with zero content insets.
        # Reserve their actual width so source text never sits underneath them.
        clip = self.contentView()
        frame = clip.frame()
        left = max(frame.origin.x, ruler.frame().origin.x + ruler.frame().size.width)
        width = max(1, frame.size.width - (left - frame.origin.x))
        clip.setFrame_(NSMakeRect(left, frame.origin.y, width, frame.size.height))
        document.setFrameSize_((width, max(document.frame().size.height, frame.size.height)))


def code_scroll(parent):
    scroll = CodeScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 400, 300))
    scroll.setHasVerticalScroller_(True)
    scroll.setAutohidesScrollers_(True)
    scroll.setBorderType_(A.NSBezelBorder)
    # Use a real gutter beside the text, rather than overlay rulers whose
    # negative scroll origins conflict with ordinary top-of-file navigation.
    scroll.setAutomaticallyAdjustsContentInsets_(False)
    scroll.setContentInsets_((0, 0, 0, 0))
    scroll.contentView().setAutomaticallyAdjustsContentInsets_(False)
    scroll.contentView().setContentInsets_((0, 0, 0, 0))
    text = CodeTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 350, 300))
    text.setEditable_(False)
    text.setSelectable_(True)
    text.setRichText_(False)
    text.setVerticallyResizable_(True)
    text.setHorizontallyResizable_(False)
    text.setAutoresizingMask_(A.NSViewWidthSizable)
    text.textContainer().setWidthTracksTextView_(True)
    text.textContainer().setContainerSize_((350, 10_000_000))
    text.setTextContainerInset_((12, 12))
    text.setBackgroundColor_(color("fffdf9"))
    scroll.setDocumentView_(text)
    ruler = LineNumberRuler.alloc().initWithScrollView_orientation_(scroll, A.NSVerticalRuler)
    ruler.setClientView_(text)
    ruler.setRuleThickness_(48)
    scroll.setVerticalRulerView_(ruler)
    scroll.setHasVerticalRuler_(True)
    scroll.setRulersVisible_(True)
    scroll.contentView().setPostsBoundsChangedNotifications_(True)
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(ruler, "redraw:", A.NSViewBoundsDidChangeNotification, scroll.contentView())
    parent.addSubview_(scroll)
    return scroll, text
