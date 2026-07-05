import os
import time
import threading
from typing import List, Tuple
import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image
from pydantic import BaseModel
from AppKit import NSApplication, NSWindow, NSColor, NSFloatingWindowLevel, NSTextField, NSFont, NSLineBreakByClipping
from Foundation import NSObject
from PyObjCTools import AppHelper
import mido

# TODO: multiple solutions, toggle between them with midi


SCREENSHOT_REGION = (60, 60, 2550, 1800)
OVERLAY_ORIGIN = (835, 340)  # x,y of top-left corner of overlay window
COLUMNS = 2
CHARS_PER_LINE = 90
LINE_HEIGHT = 15
TITLE_HEIGHT = 22
LEFT_MARGIN, RIGHT_MARGIN = 0, 0
TOP_MARGIN, BOTTOM_MARGIN = 50, 10
GUTTER = 10
EXTRA_COL_PADDING_PX =  0

window_width = 1600 
window_height = 1150

_is_running = threading.Event()
_awaiting_second = False
_results_shown = False
_interactive_mode = False  # NEW: toggle with F2
_status_label = None
_conversation_history = []  # Stores messages for multi-turn conversation
_turn_count = 0  # Track number of turns in current conversation

class OptimalSolution(BaseModel):
    approach: str
    time_complexity: str
    space_complexity: str
    language_specific_implementation: str

class DSAAssistantResponse(BaseModel):
    clarifying_questions: List[str]
    edge_cases: List[str]
    optimal_solution: OptimalSolution
    test_cases: List[str]
    limitations: List[str]

def take_screenshot():
    ts = time.strftime("%Y%m%d_%H%M%S")
    fn = f"screenshot_{ts}.png"
    img = pyautogui.screenshot(region=SCREENSHOT_REGION)
    img.save(fn)
    return fn

def ocr_with_tesseract(image_path):
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception as e:
        return f"(OCR error: {e})"

def wrap_lines(s, max_len=CHARS_PER_LINE):
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len:
            out.append(line[:max_len])
            line = line[max_len:]
        out.append(line)
    return out

def format_structured_for_overlay_sections(data: DSAAssistantResponse) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE
    sections: List[Tuple[str, List[str]]] = []
    
    opt_lines: List[str] = []
    opt_lines += wrap_lines(f"Approach: {data.optimal_solution.approach}", w)
    opt_lines += wrap_lines(f"Time: {data.optimal_solution.time_complexity}", w)
    opt_lines += wrap_lines(f"Space: {data.optimal_solution.space_complexity}", w)
    opt_lines += ["Implementation:"]
    opt_lines += wrap_lines(data.optimal_solution.language_specific_implementation, w)
    sections.append(("Optimal Solution", opt_lines))
    sections.append(("Clarifying Questions", [*sum((wrap_lines(f"- {q}", w) for q in data.clarifying_questions), [])]))
    sections.append(("Edge Cases", [*sum((wrap_lines(f"- {ec}", w) for ec in data.edge_cases), [])]))
    sections.append(("Test Cases", [*sum((wrap_lines(f"- {tc}", w) for tc in data.test_cases), [])]))
    sections.append(("Limitations", [*sum((wrap_lines(f"- {lim}", w) for lim in data.limitations), [])]))
    return sections

def _mk_color(r, g, b, a=0.9):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)


def _ensure_status_label():
    global _status_label
    if _status_label is not None and _status_label.superview() is not None: return _status_label
    lbl = NSTextField.alloc().initWithFrame_(((8, 8), (420, 18)))
    lbl.setBordered_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setDrawsBackground_(True)
    lbl.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))  # subtle pill
    lbl.setTextColor_(_mk_color(45, 45, 45))
    lbl.setFont_(NSFont.userFixedPitchFontOfSize_(11) or NSFont.systemFontOfSize_(11))
    window.contentView().addSubview_(lbl)
    _status_label = lbl
    return lbl

def _update_status(text: str):
    lbl = _ensure_status_label()
    lbl.setStringValue_(text)
    lbl.setFrameOrigin_((8, 8))


SECTION_COLOR = {
    "Clarifying Questions": _mk_color(25, 60, 140),
    "Edge Cases": _mk_color(0, 110, 90),
    "Optimal Solution": _mk_color(90, 0, 140),
    "Test Cases": _mk_color(0, 85, 150),
    "Limitations": _mk_color(120, 40, 80),
}

TITLE_FONT = NSFont.boldSystemFontOfSize_(13)
LINE_FONT  = NSFont.userFixedPitchFontOfSize_(12) or NSFont.systemFontOfSize_(12)

def _estimate_char_px(font=LINE_FONT):
    return 7.5

def _ensure_window_width_for_columns():
    global window_width
    char_px = _estimate_char_px(LINE_FONT)
    col_width_needed = int(CHARS_PER_LINE * char_px) + EXTRA_COL_PADDING_PX
    needed_width = (LEFT_MARGIN + RIGHT_MARGIN + (COLUMNS * col_width_needed) + ((COLUMNS - 1) * GUTTER))
    if needed_width > window_width:
        window_width = needed_width
        frame = window.frame()
        new_frame = ((frame.origin.x, frame.origin.y), (window_width, frame.size.height))
        window.setFrame_display_(new_frame, True)

def _place_label(text, x, y_val, width, height, font, color):
    field = NSTextField.alloc().initWithFrame_(((x, y_val), (width, height)))
    field.setStringValue_(text)
    field.setTextColor_(color)
    field.setDrawsBackground_(False)
    field.setBackgroundColor_(NSColor.clearColor())
    field.setBordered_(False)
    field.setSelectable_(False)
    field.setEditable_(False)
    field.setFont_(font)
    field.cell().setLineBreakMode_(NSLineBreakByClipping)  # no wrapping, no truncation indicator
    field.setUsesSingleLineMode_(True)
    window.contentView().addSubview_(field)

def _update_overlay_sections(sections: List[Tuple[str, List[str]]]):
    _ensure_window_width_for_columns()
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame()
    available_w = frame.size.width
    available_h = frame.size.height
    col_width = (available_w - (LEFT_MARGIN + RIGHT_MARGIN + GUTTER)) / COLUMNS
    x_cols = [LEFT_MARGIN, LEFT_MARGIN + col_width + GUTTER]
    y = [available_h - TOP_MARGIN for _ in range(COLUMNS)]
    col = 0
    for title, lines in sections:
        color = SECTION_COLOR.get(title, _mk_color(45, 45, 45))
        if y[col] - TITLE_HEIGHT < BOTTOM_MARGIN:
            col += 1
            if col >= COLUMNS:
                _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45))
                break
            y[col] = available_h - TOP_MARGIN

        _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color)
        y[col] -= TITLE_HEIGHT
        for line in lines:
            if y[col] - LINE_HEIGHT < BOTTOM_MARGIN:
                col += 1
                if col >= COLUMNS:
                    _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45))
                    return
                y[col] = available_h - TOP_MARGIN
                _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color)
                y[col] -= TITLE_HEIGHT

            _place_label(line, x_cols[col], y[col], col_width, LINE_HEIGHT, LINE_FONT, color)
            y[col] -= LINE_HEIGHT

def ui_update(lines): AppHelper.callAfter(_update_overlay_sections, [('status:',lines)])

def ui_update_sections(sections): AppHelper.callAfter(_update_overlay_sections, sections)

import json
from anthropic import Anthropic
anth_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
from pydantic import ValidationError
SYSTEM_PROMPT = """You are a helpful coding assistant for technical interviews. You handle TWO types of problems:

1. DSA (Data Structures & Algorithms) problems - LeetCode-style problems with optimal solutions
2. General coding/problem-solving problems - debugging, code review, implementation questions, system problems

Analyze the input and determine which type it is, then provide a structured response.

For DSA problems: Focus on optimal time/space complexity, edge cases, and clean implementation.
For general coding: Focus on correctness, best practices, error handling, and clear explanations.

Always respond with ONLY valid JSON (no markdown, no extra text)."""

def build_initial_prompt(ocr_text: str) -> str:
    return f"""Analyze this problem/code and respond with structured JSON:
<ocr_text>{ocr_text}</ocr_text>

Output JSON with this EXACT structure:
{{
    "clarifying_questions": ["question 1", "question 2", ...],  // 3-5 questions about requirements, inputs, edge cases
    "edge_cases": ["edge case 1", "edge case 2", ...],  // 3-5 edge cases or error scenarios
    "optimal_solution": {{
        "approach": "description of approach and intuition",
        "time_complexity": "O(...)",  // or "N/A" for non-DSA problems
        "space_complexity": "O(...)",  // or "N/A" for non-DSA problems
        "language_specific_implementation": "# Comment for line 1\\ncode_line_1\\n# Comment for line 2\\ncode_line_2..."
    }},
    "test_cases": ["assert func(input) == output", ...],  // 5-7 test cases or validation steps
    "limitations": ["limitation 1", "limitation 2", ...]  // 2-3 limitations or caveats
}}"""

def build_continuation_prompt(ocr_text: str) -> str:
    return f"""Here is additional context (could be error output, more code, or clarification):
<additional_ocr_text>{ocr_text}</additional_ocr_text>

Based on this new information, and the cumulative information received so far, provide an UPDATED response. If this shows an error, fix it. If this shows more code, incorporate it. If this clarifies the problem, refine your solution.

Respond with the same JSON structure as before:
{{
    "clarifying_questions": ["..."],
    "edge_cases": ["..."],
    "optimal_solution": {{
        "approach": "...",
        "time_complexity": "...",
        "space_complexity": "...",
        "language_specific_implementation": "..."
    }},
    "test_cases": ["..."],
    "limitations": ["..."]
}}"""

def build_simplify_prompt() -> str:
    return """The previous solution is too algorithmically complex. I cannot explain the intuition or math behind it in an interview setting.

Please provide a SIMPLER solution that:
1. Is easier to understand and explain step-by-step
2. Uses clear, intuitive logic that I can walk through confidently
3. Avoids complex algorithms, obscure tricks, or advanced math
4. Still aims for good efficiency - only fall back to brute force if there's truly no simpler efficient approach
5. Uses a fundamentally different approach from the immediately previous response, for example, if the previous was djikstra's algorithm, try BFS or DFS instead

I want something I can explain clearly while still demonstrating good problem-solving skills.

Respond with the same JSON structure:
{
    "clarifying_questions": ["..."],
    "edge_cases": ["..."],
    "optimal_solution": {
        "approach": "...",
        "time_complexity": "...",
        "space_complexity": "...",
        "language_specific_implementation": "..."
    },
    "test_cases": ["..."],
    "limitations": ["..."]
}"""

def call_anthropic_structured_with_retry(ocr_text: str, messages: list = None, max_retries: int = 2) -> tuple[DSAAssistantResponse | None, list]:
    """Version with retry logic and conversation history support"""
    if messages is None:
        messages = [{"role": "user", "content": build_initial_prompt(ocr_text)}]

    for attempt in range(max_retries + 1):
        try:
            response = anth_client.messages.create(
                model="claude-opus-4-5-20251101",
                max_tokens=4096,
                temperature=0.1,
                system=SYSTEM_PROMPT,
                messages=messages
            )
            response_text = response.content[0].text.strip()
            if response_text.startswith("```json"): response_text = response_text[7:]
            if response_text.startswith("```"): response_text = response_text[3:]
            if response_text.endswith("```"): response_text = response_text[:-3]
            response_data = json.loads(response_text)
            # Add assistant response to messages for multi-turn
            updated_messages = messages + [{"role": "assistant", "content": response_text}]
            return DSAAssistantResponse(**response_data), updated_messages

        except (json.JSONDecodeError, ValidationError) as e:
            if attempt == max_retries:
                print(f"Failed after {max_retries + 1} attempts: {e}")
                ui_update(wrap_lines(f"(Failed to get structured response: {e})"))
                return None, messages
            print(f"Attempt {attempt + 1} failed, retrying...")

        except Exception as e:
            print(f"Anthropic API error: {e}")
            ui_update(wrap_lines(f"(Anthropic API error: {e})"))
            return None, messages
    return None, messages

def run_pipeline():
    global _results_shown, _conversation_history, _turn_count
    try:
        ui_update(["Running (new conversation)…"])
        try:
            shot = take_screenshot()
        except Exception as e:
            ui_update([f"Screenshot error: {e}"])
            return
        ocr_text = ocr_with_tesseract(shot)
        # Start fresh conversation
        _conversation_history = []
        _turn_count = 1
        data, _conversation_history = call_anthropic_structured_with_retry(ocr_text)
        if data:
            sections = format_structured_for_overlay_sections(data)
            ui_update_sections(sections)
            AppHelper.callAfter(_update_status, f"Turn {_turn_count} | MIDI 49=run/clear | 50=simplify | 51=cont")
            _results_shown = True
        else:
            ui_update(["(No structured response)"])
            _results_shown = False
    finally: _is_running.clear()

def continue_conversation():
    global _results_shown, _conversation_history, _turn_count
    try:
        ui_update([f"Continuing (turn {_turn_count + 1})…"])
        try:
            shot = take_screenshot()
        except Exception as e:
            ui_update([f"Screenshot error: {e}"])
            return
        ocr_text = ocr_with_tesseract(shot)
        # Add continuation to existing conversation
        continuation_msg = {"role": "user", "content": build_continuation_prompt(ocr_text)}
        messages = _conversation_history + [continuation_msg]
        data, _conversation_history = call_anthropic_structured_with_retry(ocr_text, messages=messages)
        if data:
            _turn_count += 1
            sections = format_structured_for_overlay_sections(data)
            ui_update_sections(sections)
            AppHelper.callAfter(_update_status, f"Turn {_turn_count} | MIDI 49=run/clear | 50=simplify | 51=cont")
            _results_shown = True
        else:
            ui_update(["(No structured response on continuation)"])
    finally: _is_running.clear()

def simplify_solution():
    global _results_shown, _conversation_history, _turn_count
    try:
        ui_update(["Requesting simpler solution…"])
        # Add simplify request to existing conversation
        simplify_msg = {"role": "user", "content": build_simplify_prompt()}
        messages = _conversation_history + [simplify_msg]
        data, _conversation_history = call_anthropic_structured_with_retry("", messages=messages)
        if data:
            _turn_count += 1
            sections = format_structured_for_overlay_sections(data)
            ui_update_sections(sections)
            AppHelper.callAfter(_update_status, f"Turn {_turn_count} (simplified) | MIDI 49=run/clear | 50=simplify | 51=cont")
            _results_shown = True
        else:
            ui_update(["(No structured response on simplify)"])
    finally: _is_running.clear()

app = NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_( (OVERLAY_ORIGIN, (window_width, window_height)), 15, 2, False)
window.setTitlebarAppearsTransparent_(True)  # Make title bar translucent
window.setSharingType_(0)  # NSWindowSharingNone - Don't show in screen sharing/recordings
window.setBackgroundColor_(NSColor.clearColor())
window.setOpaque_(False)
window.setHasShadow_(False)
window.setAlphaValue_(1.0)
window.setLevel_(NSFloatingWindowLevel)
window.setIgnoresMouseEvents_(True)
window.makeKeyAndOrderFront_(None)

class WindowDelegate(NSObject):
    def _report(self):
        frame = window.frame()
        origin = frame.origin
        size = frame.size
        msg = f"origin=({int(origin.x)}, {int(origin.y)})  size=({int(size.width)} x {int(size.height)})"
        print(msg, flush=True)
        _update_status(msg)
    def windowDidResize_(self, notification): self._report()
    def windowDidMove_(self, notification): self._report()

delegate = WindowDelegate.alloc().init()
window.setDelegate_(delegate)

def _apply_interactive_mode():
    if _interactive_mode:
        window.setIgnoresMouseEvents_(False)
        window.setHasShadow_(True)
        window.setMovableByWindowBackground_(True)
        window.setAlphaValue_(0.7)   # ~30% transparent so you can see corners/edges
        _ensure_status_label()
        ui_update(["Interactive ON. Drag to move/resize. F2=turn OFF."])
        delegate._report()
    else:
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(False)
        window.setAlphaValue_(1.0)   # back to fully transparent overlay
        global _status_label
        if _status_label is not None:
            _status_label.removeFromSuperview()
            _status_label = None
        ui_update(['Interactive OFF. MIDI 49=run/clear | 50=simplify | 51=cont'])


def toggle_interactive_mode():
    global _interactive_mode
    _interactive_mode = not _interactive_mode
    AppHelper.callAfter(_apply_interactive_mode)

_ensure_window_width_for_columns()
ui_update(['Ready. MIDI 49=run/clear | 50=simplify | 51=cont | F1=quit | F2=resize'])
def _extract_vk(key) -> int | None:
    try:
        if hasattr(key, "vk") and key.vk is not None: return int(key.vk)
        if hasattr(key, "value") and hasattr(key.value, "vk"): return int(key.value.vk)
        if hasattr(key, "vkCode"): return int(key.vkCode)
    except Exception: pass
    return None

def _quit_app():
    def _close():
        window.close()
        AppHelper.stopEventLoop()
    AppHelper.callAfter(_close)

def on_release(key):
    global _awaiting_second, _results_shown, _conversation_history, _turn_count
    try:
        if key == keyboard.Key.f1:
            _quit_app()
            return False
        if key == keyboard.Key.f2:
            toggle_interactive_mode()
            return
        vk = _extract_vk(key)
        if vk is None: return
        if str(vk) == "63":
            if not _awaiting_second: _awaiting_second = True
            else:
                _awaiting_second = False
                if _results_shown:
                    _results_shown = False
                    _conversation_history = []  # Reset conversation on clear
                    _turn_count = 0
                    ui_update(["(Cleared.) MIDI 49=run | 50=simplify | 51=cont"])
                    return
                if _is_running.is_set():
                    ui_update(["Already running…"])
                    return
                _is_running.set()
                threading.Thread(target=run_pipeline, daemon=True).start()
    except Exception as e: ui_update([f"Key handling error: {e}"])

listener = keyboard.Listener(on_release=on_release)
listener.start()

# MIDI handler for conversation continuation and simplification
_last_midi_ts = {}  # Track last timestamp per note
_NOTE_DEBOUNCE_SEC = 0.15

def on_midi(msg):
    global _last_midi_ts, _results_shown, _conversation_history, _turn_count
    try:
        mtype = getattr(msg, "type", None)
        note = getattr(msg, "note", None)
        vel = getattr(msg, "velocity", None)

        # Log all MIDI note_on events
        if mtype == "note_on" and (vel is None or vel > 0):
            print(f"MIDI note: {note}", flush=True)

        if mtype != "note_on" or (vel is not None and vel == 0):
            return

        now = time.time()
        # Debounce check
        if note in _last_midi_ts and now - _last_midi_ts[note] < _NOTE_DEBOUNCE_SEC:
            return
        _last_midi_ts[note] = now

        # Note 51: Continue conversation with new OCR
        if note == 51:
            if not _conversation_history:
                AppHelper.callAfter(ui_update, ["No conversation to continue. Run first (MIDI 49)."])
                return
            if _is_running.is_set():
                AppHelper.callAfter(ui_update, ["Already running…"])
                return
            _is_running.set()
            threading.Thread(target=continue_conversation, daemon=True).start()

        # Note 49: Run/clear
        elif note == 49:
            if _results_shown:
                _results_shown = False
                _conversation_history = []
                _turn_count = 0
                AppHelper.callAfter(ui_update, ["(Cleared.) MIDI 49=run | 50=simplify | 51=cont"])
                return
            if _is_running.is_set():
                AppHelper.callAfter(ui_update, ["Already running…"])
                return
            _is_running.set()
            threading.Thread(target=run_pipeline, daemon=True).start()

        # Note 50: Request simpler solution
        elif note == 50:
            if not _conversation_history:
                AppHelper.callAfter(ui_update, ["No conversation to simplify. Run first (MIDI 49)."])
                return
            if _is_running.is_set():
                AppHelper.callAfter(ui_update, ["Already running…"])
                return
            _is_running.set()
            threading.Thread(target=simplify_solution, daemon=True).start()
    except Exception as e:
        AppHelper.callAfter(ui_update, [f"MIDI error: {e}"])

print("Available MIDI devices:")
for name in mido.get_input_names():
    print("  ", name)
midi_port = mido.open_input(mido.get_input_names()[0], callback=on_midi)
AppHelper.runEventLoop()
midi_port.close()