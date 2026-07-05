# system_design_overlay.py
import os
import time
import threading
import json
from typing import List, Tuple, Optional

import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image
from pydantic import BaseModel, ValidationError

from AppKit import NSApplication, NSWindow, NSColor, NSFloatingWindowLevel, NSTextField, NSFont, NSLineBreakByClipping
from Foundation import NSObject
from PyObjCTools import AppHelper

from system_notes import system_data, format_system_notes

SCREENSHOT_REGION = (120, 240, 2550, 1800)
OVERLAY_ORIGIN = (735, 115)
COLUMNS = 2
CHARS_PER_LINE = 90
LINE_HEIGHT = 15
TITLE_HEIGHT = 22
LEFT_MARGIN, RIGHT_MARGIN = 0, 0
TOP_MARGIN, BOTTOM_MARGIN = 50, 10
GUTTER = 10
EXTRA_COL_PADDING_PX = 0

window_width = 1600
window_height = 1150

_is_running = threading.Event()
_awaiting_second = False
_results_shown = False
_interactive_mode = False
_status_label = None

# --- New globals for MIDI filter toggle ---
_filter_components_only = False
_last_sections: Optional[List[Tuple[str, List[str]]]] = None
_FILTER_TITLES = {"Deep Dives", "Component Descriptions", "High-Level Design"}

cheat_sheet = format_system_notes(system_data)

class CapacityEstimation(BaseModel):
    calculations: List[str]
    impact_on_design: str

class ApiDesign(BaseModel):
    protocol: str
    endpoints: List[str]

class HighLevelDesign(BaseModel):
    components: List[str]
    data_flow_description: str

class DataModel(BaseModel):
    entity: str
    key_fields: List[str]

class DeepDive(BaseModel):
    area: str
    problem: str
    solution: str
    tradeoffs: List[str]
    implementation_details: List[str]

class ComponentDescription(BaseModel):
    component: str
    role: str
    rationale: str
    implementation_details: List[str]

class SystemDesignAssistantResponse(BaseModel):
    functional_requirements: List[str]
    non_functional_requirements: List[str]
    capacity_estimation: Optional[CapacityEstimation] = None
    core_entities: List[str]
    api_design: ApiDesign
    data_flow: Optional[List[str]] = None
    high_level_design: HighLevelDesign
    data_models: List[DataModel]
    component_descriptions: List[ComponentDescription] = None
    deep_dives: List[DeepDive]

def take_screenshot() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    fn = f"screenshot_{ts}.png"
    img = pyautogui.screenshot(region=SCREENSHOT_REGION)
    img.save(fn)
    return fn

def ocr_with_tesseract(image_path: str) -> str:
    try:
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception as e:
        return f"(OCR error: {e})"

def wrap_lines(s: str, max_len: int = CHARS_PER_LINE) -> List[str]:
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len:
            out.append(line[:max_len])
            line = line[max_len:]
        out.append(line)
    return out

def _mk_color(r, g, b, a=0.9): return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)

SECTION_COLOR = {
    "Clarifying Questions": _mk_color(25, 60, 140),
    "High-Level Design": _mk_color(90, 0, 140),
    "APIs": _mk_color(0, 85, 150),
    "Data Models": _mk_color(0, 110, 90),
    "Deep Dives": _mk_color(140, 50, 0),
    "Assumptions": _mk_color(45, 45, 45),
    "Non-Functional Reqs": _mk_color(0, 110, 140),
    "Risks & Mitigations": _mk_color(140, 0, 60),
    "status:": _mk_color(45, 45, 45)
}

TITLE_FONT = NSFont.boldSystemFontOfSize_(13)
LINE_FONT  = NSFont.userFixedPitchFontOfSize_(12) or NSFont.systemFontOfSize_(12)

def _estimate_char_px(font=LINE_FONT) -> float: return 7.5

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
    field.cell().setLineBreakMode_(NSLineBreakByClipping)
    field.setUsesSingleLineMode_(True)
    window.contentView().addSubview_(field)

def format_structured_for_overlay_sections(data: SystemDesignAssistantResponse) -> List[Tuple[str, List[str]]]:
    w = CHARS_PER_LINE
    sections: List[Tuple[str, List[str]]] = []

    sections.append(("Functional Requirements", [*sum((wrap_lines(f"- {req}", w) for req in data.functional_requirements), [])]))
    sections.append(("Non-Functional Reqs", [*sum((wrap_lines(f"- {req}", w) for req in data.non_functional_requirements), [])]))
    if data.capacity_estimation:
        ce_lines: List[str] = []
        ce_lines += [*sum((wrap_lines(f"- {calc}", w) for calc in data.capacity_estimation.calculations), [])]
        ce_lines += wrap_lines(f"Impact: {data.capacity_estimation.impact_on_design}", w)
        sections.append(("Capacity Estimation", ce_lines))
    sections.append(("Core Entities", [*sum((wrap_lines(f"- {entity}", w) for entity in data.core_entities), [])]))
    api_lines: List[str] = []
    api_lines += wrap_lines(f"Protocol: {data.api_design.protocol}", w)
    api_lines += ["Endpoints:"]
    api_lines += [*sum((wrap_lines(f"  - {endpoint}", w) for endpoint in data.api_design.endpoints), [])]
    sections.append(("API Design", api_lines))
    if data.data_flow: sections.append(("Data Flow", [*sum((wrap_lines(f"- {step}", w) for step in data.data_flow), [])]))
    hld_lines: List[str] = []
    hld_lines += ["Components:"]
    hld_lines += [*sum((wrap_lines(f"  - {comp}", w) for comp in data.high_level_design.components), [])]
    hld_lines += [""]
    hld_lines += wrap_lines(f"Flow: {data.high_level_design.data_flow_description}", w)
    sections.append(("High-Level Design", hld_lines))
    dm_lines: List[str] = []
    for dm in data.data_models:
        dm_lines += wrap_lines(f"[{dm.entity}]", w)
        dm_lines += [*sum((wrap_lines(f"  - {field}", w) for field in dm.key_fields), [])]
        dm_lines += [""]
    sections.append(("Data Models", dm_lines))
    if data.component_descriptions:
        cd_lines: List[str] = []
        for cd in data.component_descriptions:
            cd_lines += wrap_lines(f"[{cd.component}]", w)
            cd_lines += wrap_lines(f"  Role: {cd.role}", w)
            cd_lines += wrap_lines(f"  Rationale: {cd.rationale}", w)
            cd_lines += ["  Implementation Details:"]
            cd_lines += [*sum((wrap_lines(f"    • {impl}", w) for impl in cd.implementation_details), [])]
            cd_lines += [""]
        sections.append(("Component Descriptions", cd_lines))
    dd_lines: List[str] = []
    for dd in data.deep_dives:
        dd_lines += wrap_lines(f"[{dd.area}]", w)
        dd_lines += wrap_lines(f"  Problem: {dd.problem}", w)
        dd_lines += wrap_lines(f"  Solution: {dd.solution}", w)
        dd_lines += ["  Tradeoffs:"]
        dd_lines += [*sum((wrap_lines(f"    • {t}", w) for t in dd.tradeoffs), [])]
        dd_lines += ["  Implementation:"]
        dd_lines += [*sum((wrap_lines(f"    • {impl}", w) for impl in dd.implementation_details), [])]
        dd_lines += [""]
    sections.append(("Deep Dives", dd_lines))

    return sections

def _ensure_status_label():
    global _status_label
    if _status_label is not None and _status_label.superview() is not None: return _status_label
    lbl = NSTextField.alloc().initWithFrame_(((8, 8), (420, 18)))
    lbl.setBordered_(False)
    lbl.setEditable_(False)
    lbl.setSelectable_(False)
    lbl.setDrawsBackground_(True)
    lbl.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
    lbl.setTextColor_(_mk_color(45, 45, 45))
    lbl.setFont_(NSFont.userFixedPitchFontOfSize_(11) or NSFont.systemFontOfSize_(11))
    window.contentView().addSubview_(lbl)
    _status_label = lbl
    return lbl

def _update_status(text: str):
    lbl = _ensure_status_label()
    lbl.setStringValue_(text)
    lbl.setFrameOrigin_((8, 8))

def _update_overlay_sections(sections: List[Tuple[str, List[str]]]):
    global _last_sections
    _last_sections = sections  # store whatever we render
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

def ui_update(lines: List[str]): AppHelper.callAfter(_update_overlay_sections, [('status:', lines)])
def ui_update_sections(sections: List[Tuple[str, List[str]]]): AppHelper.callAfter(_update_overlay_sections, sections)

# --- Helpers to filter and re-render based on toggle ---
def _filtered_sections(sections: List[Tuple[str, List[str]]]) -> List[Tuple[str, List[str]]]:
    return [s for s in sections if s[0] in _FILTER_TITLES]

def _rerender_from_full():
    if _full_sections is None:
        return
    if _filter_components_only:
        AppHelper.callAfter(_update_overlay_sections, _filtered_sections(_full_sections))
        AppHelper.callAfter(_update_status, "Components-only view (Deep Dives + Component Descriptions + High-Level Design)")
    else:
        AppHelper.callAfter(_update_overlay_sections, _full_sections)
        AppHelper.callAfter(_update_status, "All sections view")


def _rerender_from_last():
    # chooses filtered or full view based on _filter_components_only
    if _last_sections is None:
        return
    if _filter_components_only:
        AppHelper.callAfter(_update_overlay_sections, _filtered_sections(_last_sections))
        AppHelper.callAfter(_update_status, "Components-only view (Deep Dives + Component Descriptions + High-Level Design)")
    else:
        AppHelper.callAfter(_update_overlay_sections, _last_sections)
        AppHelper.callAfter(_update_status, "All sections view")

import anthropic
import os
import json
import base64
from typing import Optional

ANTHROPIC_MODEL = "claude-opus-4-1-20250805" 

def build_system_design_prompt(ocr_text: str) -> str:
    return f"""
You are a senior system design assistant. Analyze the OCR text of a system design prompt and any attached image to help structure a comprehensive system design interview response.

Respond with ONLY valid JSON (no markdown) matching this EXACT schema following the standard system design interview framework:

{{
  "functional_requirements": ["..."],
  "non_functional_requirements": ["..."],
  "capacity_estimation": {{
    "calculations": ["..."],
    "impact_on_design": "..."
  }},
  "core_entities": ["..."],
  "api_design": {{
    "protocol": "...",
    "endpoints": ["..."]
  }},
  "data_flow": ["..."],
  "high_level_design": {{
    "components": ["..."],
    "data_flow_description": "..."
  }},
  "data_models": [{{
    "entity": "...",
    "key_fields": ["..."]
  }}],
  "component_descriptions": [{{
    "component": "...",
    "role": "...",
    "rationale": "...",
    "implementation_details": ["..."]
  }}],
  "deep_dives": [{{
    "area": "...",
    "problem": "...",
    "solution": "...",
    "tradeoffs": ["..."],
    "implementation_details": ["..."]
  }}],
}}

Guidance per section (following interview best practices):

FUNCTIONAL REQUIREMENTS (1-2 min in interview):
- If the prompt includes functional requirements, extract them here and use them for the rest of the design
- List 3-5 core features as "Users should be able to..." statements
- Be strategic - these drive your entire design, so prioritize carefully
- Keep focused on MVP features, not every possible feature

NON-FUNCTIONAL REQUIREMENTS (1-2 min):
- If the prompt includes NFRs, extract them here and use them for the rest of the design
- Include specific, quantified targets (e.g., "<500ms latency", "99.9% uptime")
- Consider: CAP theorem choice, scalability (read/write ratio, traffic patterns), latency targets,
  durability needs, security requirements, fault tolerance, compliance
- Avoid generic statements like "should be fast" - be specific to the system

CAPACITY ESTIMATION (skip unless it influences design):
- If the prompt includes estimates, extract them here and use them for the rest of the design
- Only include if calculations directly impact architecture choices
- Example: calculating if data fits in memory vs needs sharding
- Skip generic DAU/QPS calculations that just conclude "it's a lot"

CORE ENTITIES (2 min):
- If the prompt includes entities, extract them here and use them for the rest of the design
- List the main nouns/resources your system manages
- These become your API resources and database tables
- Use clear, descriptive names (good naming matters)

API DESIGN (5 min):
- If the prompt includes API requirements, extract them here and use them for the rest of the design
- Default to REST unless you have specific reasons for GraphQL or RPC
- Format: POST /v1/resources , GET /v1/resources/{{id}}
- include response type, body of requests, and responses showing key fields and their types 
- Use plural resource names, put IDs in paths not bodies

DATA FLOW (optional, 5 min):
- Only for data processing systems with multi-step pipelines
- List the sequence: Fetch -> Process -> Transform -> Store -> etc.
- Skip for typical CRUD applications

HIGH LEVEL DESIGN (10-15 min):
- Take into account all prior sections and the prompt-- if the high level design includes the user's desktop, for example, incorporate that into your design
- Components should be generic building blocks that serve a specific purpose, not specific tech choices
- Start simple - entities and arrows that satisfy your API endpoints
- Build incrementally: go through each API endpoint and add components needed
- Common components: Load Balancer, API Gateway, Application Servers, 
  Databases (SQL/NoSQL), Caches (Redis), Message Queues, Object Storage
- Describe data flow for main operations (e.g., how a tweet gets posted and retrieved)
- Note areas for optimization but don't add complexity yet - save for deep dives

DATA MODELS:
- Document schemas next to database components in your diagram
- Only include fields relevant to your design (skip obvious ones like name/email)
- Focus on relationships, indexes, and fields that affect performance

COMPONENT DESCRIPTIONS:
- Address each component from your high-level design, describe how they work in detail
- Common topics: Key-Value Cache, NOSql DB, Relational DB, Message Queue, CDN
- For each: explain its role, why you chose it, and any important implementation details
- Describe how the core functionality of each component works

DEEP DIVES (10 min):
- Address your non-functional requirements and bottlenecks
- Common topics: Scaling strategies, Caching layers, Database sharding,
  Consistency models, Rate limiting, Real-time features
- For each: explain the problem, your solution, tradeoffs, and specific implementation
- Example: "Feed generation: fanout-on-write for celebrities, fanout-on-read for others"

Remember: Stay focused on meeting requirements. Start simple, then add complexity in deep dives.
Don't over-engineer early. Show you can identify and prioritize what matters most.

<ocr_text>
{ocr_text}
</ocr_text>
"""

def call_anthropic_system_design(ocr_text: str, image_path: Optional[str]) -> Optional[dict]:
    try:
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except Exception as e:
        print(f"Failed to initialize Anthropic client: {e}")
        return None

    content = []
    prompt_text = build_system_design_prompt(ocr_text)
    content.append({"type": "text", "text": prompt_text})
    try:
        message = client.messages.create(model=ANTHROPIC_MODEL, max_tokens=8000, messages=[{"role": "user", "content": content}])

        response_text = ""
        for block in message.content:
            if hasattr(block, "type") and block.type == "text":
                response_text += block.text
        response_text = response_text.strip()

        if response_text.startswith("```json"): response_text = response_text[7:]
        elif response_text.startswith("```"): response_text = response_text[3:]
        if response_text.endswith("```"): response_text = response_text[:-3]

        response_text = response_text.strip()

        try:
            data = json.loads(response_text)
            return SystemDesignAssistantResponse(**data)
        except json.JSONDecodeError as e:
            print(f"Failed to parse JSON response: {e}")
            print(f"Raw response: {response_text[:500]}...")
            return None

    except Exception as e:
        print(f"Anthropic API error: {e}")
        return None

def run_pipeline():
    global _results_shown
    try:
        ui_update(["Running…"])

        try:
            shot = take_screenshot()
        except Exception as e:
            ui_update([f"Screenshot error: {e}"])
            return

        ocr_text = ocr_with_tesseract(shot)
        data = call_anthropic_system_design(ocr_text, image_path=shot)

        if data:
            sections = format_structured_for_overlay_sections(data)
            # Store then render according to current filter
            def _render():
                global _last_sections
                global _full_sections
                _full_sections = sections
                _last_sections = sections
                if _filter_components_only:
                    _update_overlay_sections(_filtered_sections(sections[...]))
                    _update_status("Components-only view (Deep Dives + Component Descriptions + High-Level Design)")
                else:
                    _update_overlay_sections(sections)
                    _update_status("All sections view")
            AppHelper.callAfter(_render)
            _results_shown = True
        else:
            ui_update(["(No structured response)"])
            _results_shown = False

    except Exception as e:
        ui_update([f"Pipeline error: {e}"])
        _results_shown = False
    finally:
        _is_running.clear()

app = NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_((OVERLAY_ORIGIN, (window_width, window_height)), 15, 2, False)
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
        window.setAlphaValue_(0.7)
        _ensure_status_label()
        ui_update(["Interactive resize/move: ON. Drag edges/corners or title bar. Press F2 to turn OFF."])
        delegate._report()
    else:
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(False)
        window.setAlphaValue_(1.0)
        global _status_label
        if _status_label is not None:
            _status_label.removeFromSuperview()
            _status_label = None
        ui_update(['Interactive resize/move: OFF. Release key "63" twice to run. Press F2 to enable resize.'])

def toggle_interactive_mode():
    global _interactive_mode
    _interactive_mode = not _interactive_mode
    AppHelper.callAfter(_apply_interactive_mode)

_ensure_window_width_for_columns()
# Initialize with cheat sheet; set _last_sections so toggle immediately works
_full_sections = cheat_sheet
_last_sections = cheat_sheet
ui_update_sections(cheat_sheet)

def _extract_vk(key) -> Optional[int]:
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
    global _awaiting_second, _results_shown
    try:
        if key == keyboard.Key.f1:
            _quit_app()
            return False
        if key == keyboard.Key.f2:
            toggle_interactive_mode()
            return
        vk = _extract_vk(key)
        if vk is None:
            return
        if str(vk) == "63":
            if not _awaiting_second:
                _awaiting_second = True
            else:
                _awaiting_second = False
                if _results_shown:
                    _results_shown = False
                    ui_update(["(Cleared.) Release '63' twice to run again."])
                    return
                if _is_running.is_set():
                    ui_update(["Already running…"])
                    return
                _is_running.set()
                threading.Thread(target=run_pipeline, daemon=True).start()
    except Exception as e:
        ui_update([f"Key handling error: {e}"])

listener = keyboard.Listener(on_release=on_release)
listener.start()

import mido
# --- Add alongside other globals ---
_last_note50_ts = 0.0        # debounce timestamp (seconds)
_NOTE50_DEBOUNCE_SEC = 0.15  # 150ms is usually enough

# ----------------------
# MIDI listener (callback runs in a background thread)
# ----------------------
def on_midi(msg):
    """
    Toggle filter on every real press of MIDI note 50.
    - Ignores note_off and note_on with velocity 0
    - Debounces closely spaced duplicates some controllers emit
    """
    try:
        # Guard for devices that emit dict-like or custom objects
        mtype = getattr(msg, "type", None)
        note  = getattr(msg, "note", None)
        vel   = getattr(msg, "velocity", None)

        if mtype == "note_on" and note == 50 and (vel is None or vel > 0):
            now = time.time()
            global _last_note50_ts
            if now - _last_note50_ts < _NOTE50_DEBOUNCE_SEC:
                return
            _last_note50_ts = now

            def _toggle_and_render():
                global _filter_components_only
                _filter_components_only = not _filter_components_only
                if _filter_components_only:
                    _rerender_from_last()
                else:
                    _rerender_from_full()   # always reapply from the full data
            AppHelper.callAfter(_toggle_and_render)

        # Optional: ignore other messages (or print them for debugging)
        # else:
        #     print(msg)

    except Exception as e:
        AppHelper.callAfter(ui_update, [f"MIDI error: {e}"])


print("Available MIDI devices:")
for name in mido.get_input_names():
    print("  ", name)

# Open first port; keep a reference so it stays alive
midi_port = mido.open_input(mido.get_input_names()[0], callback=on_midi)

AppHelper.runEventLoop()
midi_port.close()