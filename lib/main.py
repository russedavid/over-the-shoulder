import os, sys, json, queue, traceback
import importlib.util
import shutil
import subprocess
import tempfile

import time, threading, mido, base64, atexit
from pathlib import Path
from typing import List, Tuple, Optional, Dict
import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image, ImageDraw
from pydantic import BaseModel
from openai import OpenAI
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile
from AppKit import NSApplication, NSWindow, NSColor, NSFloatingWindowLevel, NSTextField, NSFont, NSLineBreakByClipping, NSImageView, NSImage, NSImageScaleProportionallyUpOrDown
from Foundation import NSObject
from PyObjCTools import AppHelper
import ScreenCaptureKit as SCK
import CoreMedia
import dispatch
import objc

from constants import *
from control import *
from formatting import *
from prompts import *

window_width, window_height = BASE_WINDOW_WIDTH, BASE_WINDOW_HEIGHT
_is_running, _awaiting_second, _interactive_mode, _overlay_hidden = threading.Event(), False, False, False
_status_label = None
_current_mode = "project"
_mode_a_view_mode, _mode_a_results_shown, _mode_a_full_sections = 0, False, None
_system_view_mode, _system_results_shown, _system_full_sections, _system_diagram_path = 0, False, None, None
_system_diagram_status, _system_diagram_started_at = "idle", None
_pair_view_mode, _pair_full_sections = 0, None
_behavioral_pair_view_mode, _behavioral_pair_full_sections, _behavioral_pair_showing_cheat = 0, None, False
_project_view_mode, _project_text_pages = 0, []
_is_recording, _audio_data, _transcription_model, _system_audio_capture = False, [], None, None
_transcription_model_lock = threading.Lock()
_voice_conversation: List[dict] = []
_voice_is_continuation, _last_voice_sections = False, []
_voice_mic_path, _voice_system_path = None, None
_last_openai_error: Optional[str] = None
class SCKAudioDelegate(NSObject):
    def init(self):
        self = objc.super(SCKAudioDelegate, self).init()
        if self:
            self.chunks = []
            self.lock = threading.Lock()
        return self
    def stream_didOutputSampleBuffer_ofType_(self, stream, buf, typ):
        if typ != SCK.SCStreamOutputTypeAudio: return
        block = CoreMedia.CMSampleBufferGetDataBuffer(buf)
        if not block: return
        length = CoreMedia.CMBlockBufferGetDataLength(block)
        data = bytearray(length)
        CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, data)
        with self.lock: self.chunks.append(np.frombuffer(data, dtype=np.float32).copy())

class SystemAudioCapture:
    def __init__(self): self.stream, self.delegate, self._queue = None, None, None
    def start(self):
        self.delegate = SCKAudioDelegate.alloc().init()
        evt = threading.Event()
        def got_content(content, err):
            if err or not content: evt.set(); return
            displays = content.displays()
            if not displays: evt.set(); return
            filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setCapturesAudio_(True); cfg.setExcludesCurrentProcessAudio_(True); cfg.setSampleRate_(_SAMPLE_RATE); cfg.setChannelCount_(1)
            self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, None)
            self._queue = dispatch.dispatch_queue_create(b"audio", None)
            self.stream.addStreamOutput_type_sampleHandlerQueue_error_(self.delegate, SCK.SCStreamOutputTypeAudio, self._queue, None)
            self.stream.startCaptureWithCompletionHandler_(lambda e: None)
            evt.set()
        SCK.SCShareableContent.getShareableContentWithCompletionHandler_(got_content); evt.wait(timeout=2)
    def stop(self) -> Optional[np.ndarray]:
        if self.stream: self.stream.stopCaptureWithCompletionHandler_(lambda e: None); self.stream = None
        audio = self.drain()
        self.delegate = None
        return audio
    def drain(self) -> Optional[np.ndarray]:
        if not self.delegate: return None
        with self.delegate.lock:
            chunks = self.delegate.chunks
            self.delegate.chunks = []
        return np.concatenate(chunks).flatten() if chunks else None

def _openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key: raise RuntimeError("OPENAI_API_KEY is not set in this process")
    return OpenAI(api_key=api_key, timeout=_OPENAI_TIMEOUT_SEC)

def _record_openai_error(context: str, exc: Exception):
    global _last_openai_error
    _last_openai_error = f"[{context}] {type(exc).__name__}: {exc}"

def _log_runtime_error(context: str, exc: Exception):
    path = os.path.abspath("system2_runtime_errors.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}: {type(exc).__name__}: {exc}\n")
        f.write(traceback.format_exc())

class OptimalSolution(BaseModel):
    approach: str; time_complexity: str; space_complexity: str; language_specific_implementation: str

class DSAAssistantResponse(BaseModel):
    clarifying_questions: List[str]; edge_cases: List[str]; optimal_solution: OptimalSolution; test_cases: List[str]; limitations: List[str]

class CapacityEstimation(BaseModel):
    calculations: List[str]; impact_on_design: str

class ApiDesign(BaseModel):
    protocol: str; endpoints: List[str]

class HighLevelDesign(BaseModel):
    components: List[str]; data_flow_description: str

class DataModel(BaseModel):
    entity: str; key_fields: List[str]

class DeepDive(BaseModel):
    area: str; problem: str; solution: str; tradeoffs: List[str]; implementation_details: List[str]

class ComponentDescription(BaseModel):
    component: str; role: str; rationale: str; implementation_details: List[str]

class SystemDesignResponse(BaseModel):
    functional_requirements: List[str]; non_functional_requirements: List[str]; capacity_estimation: Optional[CapacityEstimation] = None
    core_entities: List[str]; api_design: ApiDesign; data_flow: Optional[List[str]] = None; high_level_design: HighLevelDesign
    data_models: List[DataModel]; component_descriptions: Optional[List[ComponentDescription]] = None; deep_dives: List[DeepDive]

class PairContextFile(BaseModel):
    path: str
    content: str
    language: Optional[str] = None
    notes: Optional[str] = None

class PairContextUpdate(BaseModel):
    files: List[PairContextFile] = []
    terminal_output: List[str] = []
    notes: List[str] = []
    open_questions: List[str] = []
    summary: str = ""

class PairCodeSuggestion(BaseModel):
    title: str
    code: str
    language: Optional[str] = None
    path: Optional[str] = None
    notes: Optional[str] = None

class PairProcessResponse(BaseModel):
    summary: str
    likely_request: str
    questions: List[str]
    implementation_plan: List[str]
    code_suggestions: List[PairCodeSuggestion]
    conversational_response: str

class CodexPairFinalMessage(BaseModel):
    summary: str
    likely_request: str
    implementation_plan: List[str]
    test_notes: List[str]
    conversational_response: str

class CodexPairResponse(BaseModel):
    summary: str
    likely_request: str
    implementation_plan: List[str]
    changed_files: List[str]
    suggested_diff: str
    test_notes: List[str]
    conversational_response: str
    repo_root: str

class BehavioralPairResponse(BaseModel):
    latest_question: str
    closest_match: str
    bullet_points: List[str]

def take_screenshot() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S"); ms = int((time.time() % 1) * 1000); fn = f"screenshot_{ts}_{ms:03d}.png"
    pyautogui.screenshot(region=SCREENSHOT_REGION).save(fn); return fn

def ocr_with_tesseract(image_path: str) -> str:
    try: return pytesseract.image_to_string(Image.open(image_path)).strip()
    except Exception as e: return f"(OCR error: {e})"

_pair_section_tool = None
_pair_section_tool_error: Optional[str] = None

def _load_pair_section_tool():
    global _pair_section_tool, _pair_section_tool_error
    if _pair_section_tool is not None:
        return _pair_section_tool
    if _pair_section_tool_error is not None:
        return None

    tool_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "gui_section_boxes.py")
    try:
        spec = importlib.util.spec_from_file_location("gui_section_boxes_tool", tool_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load section tool from {tool_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not hasattr(module, "detect_gui_boxes"):
            raise RuntimeError("section tool missing detect_gui_boxes")
        _pair_section_tool = module
        return _pair_section_tool
    except Exception as e:
        _pair_section_tool_error = str(e)
        return None

def _box_coords(box, image_size: Tuple[int, int]) -> Optional[Tuple[int, int, int, int]]:
    width, height = image_size
    try:
        x1, y1, x2, y2 = int(box.x1), int(box.y1), int(box.x2), int(box.y2)
    except Exception:
        return None
    x1, y1 = max(0, min(x1, width)), max(0, min(y1, height))
    x2, y2 = max(0, min(x2, width)), max(0, min(y2, height))
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2

def _save_pair_box_debug_image(image_path: str, boxes) -> Optional[str]:
    start = time.perf_counter()
    try:
        root, ext = os.path.splitext(image_path)
        out_path = f"{root}_boxes{ext or '.png'}"
        drawn = 0
        with Image.open(image_path) as img:
            canvas = img.convert("RGB")
            draw = ImageDraw.Draw(canvas)
            for idx, box in enumerate(boxes or [], start=1):
                coords = _box_coords(box, canvas.size)
                if coords is None:
                    continue
                x1, y1, x2, y2 = coords
                draw.rectangle((x1, y1, x2, y2), outline=(255, 0, 0), width=5)
                label = f"block{idx}"
                label_box = draw.textbbox((x1 + 6, y1 + 6), label)
                draw.rectangle(label_box, fill=(255, 0, 0))
                draw.text((x1 + 6, y1 + 6), label, fill=(255, 255, 255))
                drawn += 1
            canvas.save(out_path)
        _pair_profile("box_debug_image_save", start, image_path=image_path, debug_image_path=out_path, boxes=len(boxes or []), drawn=drawn)
        return out_path
    except Exception as e:
        _pair_profile("box_debug_image_save", start, image_path=image_path, success=False, error=str(e))
        _log_runtime_error("pair box debug image", e)
        return None

def ocr_pair_screenshot_blocks(image_path: str) -> str:
    total_start = time.perf_counter()
    tool_start = time.perf_counter()
    tool = _load_pair_section_tool()
    _pair_profile("ocr_section_tool_load", tool_start, image_path=image_path, available=bool(tool), error=_pair_section_tool_error or "")
    if tool is None:
        fallback_start = time.perf_counter()
        fallback = ocr_with_tesseract(image_path)
        reason = _pair_section_tool_error or "section tool unavailable"
        _pair_profile("ocr_fullscreen_fallback", fallback_start, image_path=image_path, reason=reason, chars=len(fallback or ""))
        _pair_profile("ocr_total", total_start, image_path=image_path, mode="fallback_no_tool", chars=len(fallback or ""))
        return f"Sectioned OCR unavailable ({reason}); falling back to full screenshot OCR.\n\n{fallback}"

    try:
        detection_start = time.perf_counter()
        boxes = tool.detect_gui_boxes(image_path)
        _pair_profile("ocr_box_detection", detection_start, image_path=image_path, boxes=len(boxes or []))
        _save_pair_box_debug_image(image_path, boxes)
    except Exception as e:
        fallback_start = time.perf_counter()
        fallback = ocr_with_tesseract(image_path)
        _pair_profile("ocr_fullscreen_fallback", fallback_start, image_path=image_path, reason=f"box_detection_error: {e}", chars=len(fallback or ""))
        _pair_profile("ocr_total", total_start, image_path=image_path, mode="fallback_detection_error", chars=len(fallback or ""))
        return f"Sectioned OCR error ({e}); falling back to full screenshot OCR.\n\n{fallback}"

    if not boxes:
        fallback_start = time.perf_counter()
        fallback = ocr_with_tesseract(image_path)
        _pair_profile("ocr_fullscreen_fallback", fallback_start, image_path=image_path, reason="no_boxes", chars=len(fallback or ""))
        _pair_profile("ocr_total", total_start, image_path=image_path, mode="fallback_no_boxes", chars=len(fallback or ""))
        return "No closed GUI sections detected. Full screenshot OCR fallback:\n\n" + fallback

    blocks: List[str] = []
    included_blocks = 0
    try:
        open_start = time.perf_counter()
        with Image.open(image_path) as img:
            _pair_profile("ocr_image_open", open_start, image_path=image_path, size=list(img.size))
            blocks_start = time.perf_counter()
            for idx, box in enumerate(boxes, start=1):
                coords = _box_coords(box, img.size)
                if coords is None:
                    _pair_profile("ocr_block_skipped", block=f"block{idx}", reason="invalid_bbox")
                    continue
                x1, y1, x2, y2 = coords
                block_start = time.perf_counter()
                text = pytesseract.image_to_string(img.crop((x1, y1, x2, y2))).strip()
                _pair_profile("ocr_block", block_start, block=f"block{idx}", bbox=[x1, y1, x2, y2], chars=len(text or ""))
                if not text:
                    continue
                included_blocks += 1
                blocks.append(f"""<block id="block{idx}" bbox_xyxy="[{x1}, {y1}, {x2}, {y2}]">
{text}
</block>""")
            _pair_profile("ocr_blocks_total", blocks_start, image_path=image_path, boxes=len(boxes), included_blocks=included_blocks)
    except Exception as e:
        fallback_start = time.perf_counter()
        fallback = ocr_with_tesseract(image_path)
        _pair_profile("ocr_fullscreen_fallback", fallback_start, image_path=image_path, reason=f"crop_error: {e}", chars=len(fallback or ""))
        _pair_profile("ocr_total", total_start, image_path=image_path, mode="fallback_crop_error", boxes=len(boxes), chars=len(fallback or ""))
        return f"Sectioned OCR crop error ({e}); falling back to full screenshot OCR.\n\n{fallback}"

    if not blocks:
        _pair_profile("ocr_total", total_start, image_path=image_path, mode="sectioned_empty", boxes=len(boxes), included_blocks=0, chars=0)
        return "Closed GUI sections were detected, but none produced OCR text."
    result = "OCR split by detected GUI sections. Use block ids only as source references.\n<screen_blocks>\n" + "\n\n".join(blocks) + "\n</screen_blocks>"
    _pair_profile("ocr_total", total_start, image_path=image_path, mode="sectioned", boxes=len(boxes), included_blocks=included_blocks, chars=len(result))
    return result

def _mk_color(r, g, b, a=0.9): return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)

SECTION_COLOR = {
    "Mode": _mk_color(20, 70, 160),
    "Functional Requirements": _mk_color(25, 60, 140), "Non-Functional Reqs": _mk_color(0, 110, 140),
    "Capacity Estimation": _mk_color(120, 40, 80), "Core Entities": _mk_color(0, 100, 80),
    "API Design": _mk_color(0, 85, 150), "Data Flow": _mk_color(90, 60, 0), "High-Level Design": _mk_color(90, 0, 140),
    "Data Models": _mk_color(0, 110, 90), "Component Descriptions": _mk_color(140, 50, 0), "Deep Dives": _mk_color(140, 0, 60),
    "Edge Cases": _mk_color(0, 110, 90),
    "Controls": _mk_color(80, 80, 80), "Voice Question": _mk_color(0, 100, 130), "Previous": _mk_color(100, 100, 100),
    "Answer": _mk_color(0, 130, 80), "Deep Dive": _mk_color(100, 60, 160), "Diagram": _mk_color(60, 60, 140), "Status": _mk_color(200, 120, 0), "status:": _mk_color(45, 45, 45),
    "Clarifying Questions": _mk_color(25, 60, 140), "Optimal Solution": _mk_color(90, 0, 140),
    "Test Cases": _mk_color(0, 85, 150), "Limitations": _mk_color(120, 40, 80),
    "Pair Controls": _mk_color(80, 80, 80), "Pair Status": _mk_color(200, 120, 0), "Context Files": _mk_color(0, 85, 150),
    "Terminal Output": _mk_color(90, 60, 0), "Transcript": _mk_color(0, 100, 130), "Pair Response": _mk_color(0, 130, 80),
    "Pair Conversation": _mk_color(210, 45, 255),
    "Changed Files": _mk_color(0, 85, 150), "Suggested Diff": _mk_color(60, 60, 60), "Codex Notes": _mk_color(210, 45, 255),
    "Implementation Plan": _mk_color(90, 0, 140), "Code Suggestions": _mk_color(0, 120, 255), "Code Suggestions Detail": _mk_color(0, 120, 255),
    "Task Focus": _mk_color(0, 130, 80), "Repo Setup": _mk_color(25, 60, 140), "Install": _mk_color(0, 85, 150),
    "Database Setup": _mk_color(90, 0, 140), "Run API Server": _mk_color(0, 100, 130), "Run Scripts": _mk_color(0, 85, 150),
    "Validate Scripts": _mk_color(120, 40, 80), "Other Useful Commands": _mk_color(100, 60, 160),
    "Pair Metadata": _mk_color(100, 60, 160), "Pair Details": _mk_color(100, 60, 160), "Notes": _mk_color(100, 60, 160), "Open Questions": _mk_color(140, 0, 60),
    "Behavioral Response": _mk_color(0, 130, 80), "Behavioral Transcript": _mk_color(0, 100, 130), "Behavioral Metadata": _mk_color(100, 60, 160),
    "Headline and Key Ideas": _mk_color(0, 130, 80),
}
TITLE_FONT = NSFont.boldSystemFontOfSize_(13)
LINE_FONT = NSFont.userFixedPitchFontOfSize_(12) or NSFont.systemFontOfSize_(12)

def _estimate_char_px(font=LINE_FONT) -> float: return 7.5

def _ensure_window_width_for_columns():
    global window_width
    col_width_needed = int(CHARS_PER_LINE * _estimate_char_px(LINE_FONT)) + EXTRA_COL_PADDING_PX
    needed_width = LEFT_MARGIN + RIGHT_MARGIN + (COLUMNS * col_width_needed) + ((COLUMNS - 1) * GUTTER)
    if needed_width > window_width:
        window_width = needed_width
        frame = window.frame()
        window.setFrame_display_(((frame.origin.x, frame.origin.y), (window_width, frame.size.height)), True)

def _place_label(text, x, y_val, width, height, font, color):
    field = NSTextField.alloc().initWithFrame_(((x, y_val), (width, height)))
    field.setStringValue_(text); field.setTextColor_(color); field.setDrawsBackground_(False)
    field.setBackgroundColor_(NSColor.clearColor()); field.setBordered_(False); field.setSelectable_(False)
    field.setEditable_(False); field.setFont_(font); field.cell().setLineBreakMode_(NSLineBreakByClipping)
    field.setUsesSingleLineMode_(True); window.contentView().addSubview_(field)

def _project_total_pages() -> int:
    return len(_project_text_pages) + 1

_mode_a_conversation_history: List[dict] = []
_mode_a_turn_count = 0

def _openai_input(messages: List[dict], image_path: Optional[str] = None) -> List[dict]:
    input_items = []
    image_url = None
    if image_path:
        with open(image_path, "rb") as f: image_url = f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
    for idx, msg in enumerate(messages):
        content = msg["content"]
        if image_url and idx == len(messages) - 1 and msg.get("role") == "user":
            content = [{"type": "input_text", "text": content}, {"type": "input_image", "image_url": image_url}]
        input_items.append({"role": msg["role"], "content": content})
    return input_items

def call_openai_mode_a(ocr_text: str, image_path: Optional[str], messages: Optional[List[dict]] = None) -> Tuple[Optional[DSAAssistantResponse], List[dict]]:
    global _mode_a_conversation_history
    try: client = _openai_client()
    except Exception as e:
        _record_openai_error("OpenAI DSA", e)
        return None, messages or []
    if messages is None: messages = [{"role": "user", "content": build_mode_a_initial_prompt(ocr_text)}]
    try:
        response = client.responses.parse(
            model=OPENAI_REASONING_MODEL,
            instructions=_MODE_A_SYSTEM_PROMPT,
            input=_openai_input(messages, image_path=image_path),
            text_format=DSAAssistantResponse,
            reasoning=OPENAI_XHIGH_REASONING,
            max_output_tokens=24000,
        )
        response_text = response.output_text.strip()
        updated_messages = messages + [{"role": "assistant", "content": response_text}]
        return response.output_parsed, updated_messages
    except Exception as e:
        _record_openai_error("OpenAI DSA", e)
        return None, messages

def call_openai_system(ocr_text: str, image_path: Optional[str]) -> Optional[SystemDesignResponse]:
    try: client = _openai_client()
    except Exception as e:
        _record_openai_error("OpenAI SYSTEM", e)
        return None
    messages = [{"role": "user", "content": build_system_prompt(ocr_text)}]
    try:
        response = client.responses.parse(
            model=OPENAI_REASONING_MODEL,
            input=_openai_input(messages, image_path=image_path),
            text_format=SystemDesignResponse,
            reasoning=OPENAI_HIGH_REASONING,
            max_output_tokens=32000,
        )
        return response.output_parsed
    except Exception as e:
        _record_openai_error("OpenAI SYSTEM", e)
        return None

def generate_diagram_async(data: SystemDesignResponse):
    global _system_diagram_path, _system_diagram_status, _system_diagram_started_at
    _system_diagram_status, _system_diagram_started_at = "generating", time.time()
    try:
        client = _openai_client()
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=build_system_diagram_prompt(data),
            size="1536x1024",
            quality="high",
            output_format="png",
        )
        if response.data and response.data[0].b64_json:
            path = os.path.abspath(f"diagram_system_{time.strftime('%Y%m%d_%H%M%S')}.png")
            with open(path, "wb") as f: f.write(base64.b64decode(response.data[0].b64_json))
            _system_diagram_path, _system_diagram_status = path, "ready"
            if _current_mode == "system" and _system_view_mode == len(_SYSTEM_PAGES): AppHelper.callAfter(_show_diagram_view)
            return
        _system_diagram_status = "failed"
        _record_openai_error("OpenAI IMAGE", RuntimeError("image response did not include b64_json data"))
    except Exception as e:
        _system_diagram_status = "failed"
        _record_openai_error("OpenAI IMAGE", e)
    finally:
        if _current_mode == "system" and _system_view_mode == len(_SYSTEM_PAGES): AppHelper.callAfter(_show_diagram_view)

_pair_context_lock = threading.Lock()
_pair_context = PairContextUpdate()
_pair_capture_queue = queue.Queue()
_pair_capture_worker_active = False
_pair_active, _pair_audio_stop = False, threading.Event()
_pair_audio_lock, _pair_audio_data = threading.Lock(), []
_pair_audio_flush_lock = threading.Lock()
_pair_audio_stream, _pair_system_audio_capture = None, None
_pair_audio_thread = None
_pair_session_dir: Optional[str] = None
_pair_started_at: Optional[float] = None
_pair_audio_chunk_index = 0
_pair_capture_count, _pair_processed_capture_count, _pair_process_count = 0, 0, 0
_pair_generation = 0
_pair_transcript_segments: List[Dict[str, str]] = []
_pair_last_response: Optional[BaseModel] = None
_pair_last_response_path: Optional[str] = None
_behavioral_pair_last_response: Optional[BehavioralPairResponse] = None
_behavioral_pair_last_response_path: Optional[str] = None
_behavioral_pair_process_count = 0
_pair_status = "idle"
_pair_process_running = threading.Event()
_pair_profile_lock = threading.Lock()

def _model_to_dict(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()

def _pair_context_json() -> str:
    with _pair_context_lock:
        data = _model_to_dict(_pair_context)
    return json.dumps(data, indent=2)

def _is_pair_audio_mode(mode: Optional[str] = None) -> bool:
    return (mode or _current_mode) in {"pair", "behavioral_pair"}

def _render_pair_audio_mode_view():
    if _current_mode == "behavioral_pair":
        if _behavioral_pair_showing_cheat and _behavioral_pair_last_response is None and not _pair_process_running.is_set():
            _update_status(f"[BEHAVIORAL] {_pair_status} | {_MIDI_HELP_BEHAVIORAL_PAIR}")
            return
        _render_behavioral_pair_view()
    elif _current_mode == "pair":
        _render_pair_view()

def _set_pair_status(status: str):
    global _pair_status
    _pair_status = status
    if _is_pair_audio_mode(): AppHelper.callAfter(_render_pair_audio_mode_view)

def _pair_elapsed() -> str:
    if not _pair_started_at: return "0:00"
    elapsed = int(time.time() - _pair_started_at)
    return f"{elapsed // 60}:{elapsed % 60:02d}"

def _pair_session_path(name: str) -> str:
    global _pair_session_dir
    if _pair_session_dir is None:
        _pair_session_dir = os.path.abspath(f"pair_session_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_pair_session_dir, exist_ok=True)
    return os.path.join(_pair_session_dir, name)

def _pair_profile_safe(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_pair_profile_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _pair_profile_safe(v) for k, v in value.items()}
    return str(value)

def _pair_profile_print_value(key: str, value) -> str:
    if value is None:
        return "none"
    if key.endswith("_path") or key in {"path", "image_path"}:
        value = os.path.basename(str(value))
    text = str(value)
    return text if len(text) <= 90 else text[:87] + "..."

def _pair_profile(event: str, start: Optional[float] = None, **fields):
    duration_ms = round((time.perf_counter() - start) * 1000, 1) if start is not None else None
    safe_fields = {k: _pair_profile_safe(v) for k, v in fields.items()}
    record = {
        "event": event,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": _pair_elapsed(),
        "generation": _pair_generation,
        "captures": {
            "queued": _pair_capture_count,
            "processed": _pair_processed_capture_count,
            "pending": _pair_pending_capture_count(),
        },
        "audio_chunk_count": _pair_audio_chunk_index,
        "process_count": _pair_process_count,
        **safe_fields,
    }
    if duration_ms is not None:
        record["duration_ms"] = duration_ms
    try:
        with _pair_profile_lock:
            with open(_pair_session_path("pair_profile.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except Exception as e:
        _log_runtime_error(f"pair profile log {event}", e)
    parts = [f"{duration_ms}ms"] if duration_ms is not None else []
    for key, value in safe_fields.items():
        parts.append(f"{key}={_pair_profile_print_value(key, value)}")
    print(f"[PAIR PROFILE] {event}" + (f" {' '.join(parts)}" if parts else ""), flush=True)

def _save_pair_response(response: BaseModel) -> str:
    start = time.perf_counter()
    path = _pair_session_path(f"pair_response_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_model_to_dict(response), f, indent=2)
    _pair_profile("pair_response_save", start, response_path=path, bytes=os.path.getsize(path))
    return path

def _capture_screen_without_overlay(path: str):
    was_hidden = _overlay_hidden
    if not was_hidden:
        hide_start = time.perf_counter()
        window.orderOut_(None)
        time.sleep(0.08)
        _pair_profile("pair_overlay_hide", hide_start)
    try:
        screenshot_start = time.perf_counter()
        pyautogui.screenshot(region=SCREENSHOT_REGION).save(path)
        _pair_profile("pair_pyautogui_screenshot", screenshot_start, image_path=path)
    finally:
        if not was_hidden:
            restore_start = time.perf_counter()
            try: window.orderFrontRegardless()
            except Exception: window.makeKeyAndOrderFront_(None)
            time.sleep(0.02)
            _pair_profile("pair_overlay_restore", restore_start)

def take_pair_screenshot() -> str:
    start = time.perf_counter()
    ts = time.strftime("%Y%m%d_%H%M%S"); ms = int((time.time() % 1) * 1000)
    path = _pair_session_path(f"capture_{ts}_{ms:03d}.png")
    _capture_screen_without_overlay(path)
    _pair_profile("pair_screenshot_capture", start, image_path=path)
    return path

def _pair_audio_callback(indata, frames, time_info, status):
    if _pair_active:
        with _pair_audio_lock: _pair_audio_data.append(indata.copy())

def _drain_pair_mic_audio() -> Optional[np.ndarray]:
    with _pair_audio_lock:
        chunks = list(_pair_audio_data)
        _pair_audio_data.clear()
    return np.concatenate(chunks, axis=0).flatten() if chunks else None

def _write_audio_chunk(path: str, audio: np.ndarray):
    clipped = np.clip(audio, -1.0, 1.0)
    wavfile.write(path, _SAMPLE_RATE, (clipped * 32767).astype(np.int16))

def _append_pair_transcript(index: int, mic_text: str, system_text: str, mic_path: Optional[str], system_path: Optional[str], elapsed: Optional[str] = None):
    start = time.perf_counter()
    if not mic_text and not system_text: return
    segment = {
        "index": str(index),
        "elapsed": elapsed or _pair_elapsed(),
        "mic": mic_text,
        "system": system_text,
        "mic_path": mic_path or "",
        "system_path": system_path or "",
    }
    _pair_transcript_segments.append(segment)
    _pair_profile("audio_transcript_append", start, chunk=index, mic_chars=len(mic_text or ""), system_chars=len(system_text or ""))
    if _is_pair_audio_mode(): AppHelper.callAfter(_render_pair_audio_mode_view)

def _persist_pair_audio_chunk(mic_audio: Optional[np.ndarray], system_audio: Optional[np.ndarray], session_dir: Optional[str] = None, elapsed: Optional[str] = None):
    global _pair_audio_chunk_index
    if mic_audio is None and system_audio is None: return
    total_start = time.perf_counter()
    _pair_audio_chunk_index += 1
    idx = _pair_audio_chunk_index
    mic_path = system_path = None
    def _audio_path(name: str) -> str:
        if session_dir:
            os.makedirs(session_dir, exist_ok=True)
            return os.path.join(session_dir, name)
        return _pair_session_path(name)
    if mic_audio is not None and len(mic_audio) > 0:
        mic_path = _audio_path(f"audio_{idx:04d}_mic.wav")
        write_start = time.perf_counter()
        _write_audio_chunk(mic_path, mic_audio)
        _pair_profile("audio_write_mic", write_start, chunk=idx, audio_path=mic_path, samples=len(mic_audio))
    if system_audio is not None and len(system_audio) > 0:
        system_path = _audio_path(f"audio_{idx:04d}_system.wav")
        write_start = time.perf_counter()
        _write_audio_chunk(system_path, system_audio)
        _pair_profile("audio_write_system", write_start, chunk=idx, audio_path=system_path, samples=len(system_audio))
    try:
        transcribe_start = time.perf_counter()
        mic_text = transcribe_audio_file(mic_path, show_status=False)
        _pair_profile("audio_transcribe_mic", transcribe_start, chunk=idx, audio_path=mic_path or "", chars=len(mic_text or ""))
        transcribe_start = time.perf_counter()
        system_text = transcribe_audio_file(system_path, show_status=False)
        _pair_profile("audio_transcribe_system", transcribe_start, chunk=idx, audio_path=system_path or "", chars=len(system_text or ""))
        _append_pair_transcript(idx, mic_text, system_text, mic_path, system_path, elapsed=elapsed)
        _set_pair_status(f"audio chunk {idx} transcribed")
    except Exception as e:
        _log_runtime_error("pair audio transcription", e)
        _set_pair_status(f"audio transcription error: {e}")
        _pair_profile("audio_chunk_total", total_start, chunk=idx, success=False, error=str(e))
        return
    _pair_profile("audio_chunk_total", total_start, chunk=idx, success=True, mic_chars=len(mic_text or ""), system_chars=len(system_text or ""))

def _pair_flush_audio_chunk(block: bool = False):
    if not _pair_active: return
    total_start = time.perf_counter()
    if not _pair_audio_flush_lock.acquire(blocking=block):
        _set_pair_status("audio flush already running; using transcript so far")
        _pair_profile("audio_flush_skipped", total_start, reason="lock_busy", blocking=block)
        return
    try:
        drain_start = time.perf_counter()
        mic_audio = _drain_pair_mic_audio()
        system_audio = _pair_system_audio_capture.drain() if _pair_system_audio_capture else None
        _pair_profile(
            "audio_flush_drain",
            drain_start,
            mic_samples=len(mic_audio) if mic_audio is not None else 0,
            system_samples=len(system_audio) if system_audio is not None else 0,
        )
        _persist_pair_audio_chunk(mic_audio, system_audio)
    finally:
        _pair_audio_flush_lock.release()
        _pair_profile("audio_flush_total", total_start, blocking=block)

def _start_pair_process_audio_flush() -> threading.Thread:
    def _run():
        start = time.perf_counter()
        try:
            _pair_flush_audio_chunk(block=True)
            _pair_profile("pair_process_audio_flush_thread", start, success=True)
        except Exception as e:
            _log_runtime_error("pair process audio flush", e)
            _set_pair_status(f"audio flush error: {e}")
            _pair_profile("pair_process_audio_flush_thread", start, success=False, error=str(e))
    thread = threading.Thread(target=_run, daemon=True, name="pair-process-audio-flush")
    thread.start()
    return thread

def _pair_audio_loop():
    while not _pair_audio_stop.wait(_PAIR_AUDIO_CHUNK_SEC):
        _pair_flush_audio_chunk()
    _pair_flush_audio_chunk()

def _warm_pair_transcription_model():
    def _run():
        start = time.perf_counter()
        try:
            _get_transcription_model(show_status=False)
            _pair_profile("pair_transcription_model_warm", start, success=True)
        except Exception as e:
            _log_runtime_error("pair transcription model warm", e)
            _pair_profile("pair_transcription_model_warm", start, success=False, error=str(e))
    threading.Thread(target=_run, daemon=True, name="pair-transcription-warm").start()

def call_openai_pair_context(ocr_text: str, image_path: Optional[str]) -> Optional[PairContextUpdate]:
    client_start = time.perf_counter()
    try:
        client = _openai_client()
        _pair_profile("pair_context_client", client_start, success=True)
    except Exception as e:
        _record_openai_error("OpenAI PAIR CONTEXT", e)
        _pair_profile("pair_context_client", client_start, success=False, error=str(e))
        return None
    prompt_start = time.perf_counter()
    prompt = build_pair_context_prompt(_pair_context_json(), ocr_text)
    _pair_profile("pair_context_prompt_build", prompt_start, prompt_chars=len(prompt), ocr_chars=len(ocr_text or ""))
    openai_start = time.perf_counter()
    try:
        response = client.responses.parse(
            model=OPENAI_PAIR_CONTEXT_MODEL,
            instructions=_PAIR_CONTEXT_INSTRUCTIONS,
            input=_openai_input([{"role": "user", "content": prompt}], image_path=None),
            text_format=PairContextUpdate,
            reasoning=OPENAI_FAST_REASONING,
            max_output_tokens=16000,
        )
        parsed = response.output_parsed
        data = _model_to_dict(parsed)
        _pair_profile(
            "pair_context_openai",
            openai_start,
            success=True,
            image_path=image_path or "",
            ocr_chars=len(ocr_text or ""),
            files=len(data.get("files", []) or []),
            notes=len(data.get("notes", []) or []),
            questions=len(data.get("open_questions", []) or []),
        )
        return parsed
    except Exception as e:
        _record_openai_error("OpenAI PAIR CONTEXT", e)
        _pair_profile("pair_context_openai", openai_start, success=False, image_path=image_path or "", ocr_chars=len(ocr_text or ""), error=str(e))
        return None

def _pair_capture_worker():
    global _pair_capture_worker_active, _pair_context, _pair_processed_capture_count
    try:
        while True:
            try: capture = _pair_capture_queue.get_nowait()
            except queue.Empty: break
            try:
                capture_start = time.perf_counter()
                if capture.get("generation") != _pair_generation:
                    _pair_profile("capture_worker_skipped", capture=capture.get("index"), reason="stale_generation")
                    _pair_profile("capture_worker_total", capture_start, capture=capture.get("index"), success=False, stale=True)
                    continue
                _set_pair_status(f"processing capture {capture['index']}")
                ocr_text = ocr_pair_screenshot_blocks(capture["path"])
                updated = call_openai_pair_context(ocr_text, image_path=capture["path"])
                if capture.get("generation") != _pair_generation:
                    _pair_profile("capture_worker_skipped", capture=capture.get("index"), reason="stale_after_context_merge")
                    _pair_profile("capture_worker_total", capture_start, capture=capture.get("index"), success=False, stale=True, ocr_chars=len(ocr_text or ""))
                    continue
                if updated:
                    persist_start = time.perf_counter()
                    with _pair_context_lock: _pair_context = updated
                    _pair_processed_capture_count += 1
                    data = _model_to_dict(updated)
                    _pair_profile(
                        "capture_context_persist",
                        persist_start,
                        capture=capture.get("index"),
                        files=len(data.get("files", []) or []),
                        notes=len(data.get("notes", []) or []),
                        questions=len(data.get("open_questions", []) or []),
                    )
                    _set_pair_status(f"capture {capture['index']} merged")
                else:
                    _pair_profile("capture_context_failed", capture=capture.get("index"), openai_error=_last_openai_error or "")
                    _set_pair_status(_last_openai_error or f"capture {capture['index']} failed")
                _pair_profile("capture_worker_total", capture_start, capture=capture.get("index"), success=bool(updated), ocr_chars=len(ocr_text or ""))
            except Exception as e:
                _log_runtime_error("pair capture worker", e)
                _pair_profile("capture_worker_total", capture_start if "capture_start" in locals() else None, capture=capture.get("index"), success=False, error=str(e))
                _set_pair_status(f"capture worker error: {e}")
            finally:
                try: _pair_capture_queue.task_done()
                except ValueError as e: _log_runtime_error("pair capture task_done", e)
    finally:
        _pair_capture_worker_active = False
        if not _pair_capture_queue.empty(): _ensure_pair_capture_worker()
        if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)

def _ensure_pair_capture_worker():
    global _pair_capture_worker_active
    if _pair_capture_worker_active: return
    _pair_capture_worker_active = True
    threading.Thread(target=_pair_capture_worker, daemon=True).start()

def _pair_pending_capture_count() -> int:
    return getattr(_pair_capture_queue, "unfinished_tasks", _pair_capture_queue.qsize())

def _wait_for_pair_captures():
    start = time.perf_counter()
    pending = _pair_pending_capture_count()
    _ensure_pair_capture_worker()
    _pair_capture_queue.join()
    _pair_profile("capture_queue_wait", start, pending_before=pending, pending_after=_pair_pending_capture_count())

def _discard_pair_capture_queue(reason: str) -> int:
    global _pair_generation
    start = time.perf_counter()
    _pair_generation += 1
    cleared = 0
    while True:
        try:
            _pair_capture_queue.get_nowait()
            cleared += 1
            try: _pair_capture_queue.task_done()
            except ValueError as e: _log_runtime_error("pair discard task_done", e)
        except queue.Empty:
            break
    _pair_profile("capture_queue_discard", start, reason=reason, cleared=cleared)
    return cleared

def _queue_pair_capture(reason: str) -> dict:
    global _pair_capture_count
    start = time.perf_counter()
    shot = take_pair_screenshot()
    _pair_capture_count += 1
    capture = {"index": _pair_capture_count, "path": shot, "ts": time.time(), "generation": _pair_generation, "reason": reason}
    _pair_capture_queue.put(capture)
    _ensure_pair_capture_worker()
    _pair_profile("capture_queued", start, capture=_pair_capture_count, reason=reason, pending=_pair_pending_capture_count(), image_path=shot)
    return capture

def pair_capture_screen():
    if _current_mode != "pair":
        _set_pair_status("pair capture only runs in pair mode")
        return
    if not _pair_active:
        start_pair_mode()
        return
    try:
        capture = _queue_pair_capture("manual")
        _set_pair_status(f"queued capture {_pair_capture_count}")
    except Exception as e:
        _set_pair_status(f"capture error: {e}")

def _pair_transcript_text() -> str:
    lines = []
    for seg in _pair_transcript_segments:
        header = f"[{seg['elapsed']}] chunk {seg['index']}"
        if seg.get("system"): lines.append(f"{header} interviewer/system: {seg['system']}")
        if seg.get("mic"): lines.append(f"{header} user/mic: {seg['mic']}")
    return "\n".join(lines)

def call_openai_pair_process() -> Optional[PairProcessResponse]:
    client_start = time.perf_counter()
    try:
        client = _openai_client()
        _pair_profile("pair_process_client", client_start, success=True)
    except Exception as e:
        _record_openai_error("OpenAI PAIR PROCESS", e)
        _pair_profile("pair_process_client", client_start, success=False, error=str(e))
        return None
    prompt_start = time.perf_counter()
    transcript_text = _pair_transcript_text()
    prompt = build_pair_process_prompt(_pair_context_json(), transcript_text)
    _pair_profile("pair_process_prompt_build", prompt_start, prompt_chars=len(prompt), transcript_chars=len(transcript_text))
    openai_start = time.perf_counter()
    try:
        response = client.responses.parse(
            model=OPENAI_PAIR_PROCESS_MODEL,
            input=[{"role": "user", "content": prompt}],
            text_format=PairProcessResponse,
            reasoning=OPENAI_HIGH_REASONING,
            max_output_tokens=16000,
        )
        parsed = response.output_parsed
        data = _model_to_dict(parsed)
        _pair_profile(
            "pair_process_openai",
            openai_start,
            success=True,
            questions=len(data.get("questions", []) or []),
            plan_items=len(data.get("implementation_plan", []) or []),
            code_suggestions=len(data.get("code_suggestions", []) or []),
            conversational_chars=len(data.get("conversational_response", "") or ""),
        )
        return parsed
    except Exception as e:
        _record_openai_error("OpenAI PAIR PROCESS", e)
        _pair_profile("pair_process_openai", openai_start, success=False, error=str(e))
        return None

_CODEX_PAIR_FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "likely_request": {"type": "string"},
        "implementation_plan": {"type": "array", "items": {"type": "string"}},
        "test_notes": {"type": "array", "items": {"type": "string"}},
        "conversational_response": {"type": "string"},
    },
    "required": ["summary", "likely_request", "implementation_plan", "test_notes", "conversational_response"],
    "additionalProperties": False,
}
_CODEX_COPY_IGNORE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
}
_CODEX_COPY_IGNORE_NAMES = {
    ".DS_Store",
    ".env",
    ".env.local",
    ".codex_pair_final.json",
    ".codex_pair_schema.json",
}

def _resolve_pair_repo_root() -> str:
    configured = PAIR_REPO_ROOT.strip()
    base = Path(configured).expanduser() if configured else Path.cwd()
    try:
        base = base.resolve()
    except Exception:
        base = Path(os.path.abspath(str(base)))
    if not base.exists():
        source = "PAIR_REPO_ROOT" if configured else "current working directory"
        raise RuntimeError(f"{source} does not exist: {base}")
    proc = subprocess.run(
        ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        if configured:
            raise RuntimeError(f"PAIR_REPO_ROOT must point inside a Git repo: {base}")
        raise RuntimeError("Run from inside a Git repo or set PAIR_REPO_ROOT=/path/to/repo")
    return str(Path(proc.stdout.strip()).resolve())

def _codex_copy_skip(rel_path: str) -> bool:
    rel = Path(rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        return True
    for part in rel.parts:
        if part in _CODEX_COPY_IGNORE_DIRS or part.startswith("pair_session_") or part.startswith("behavioral_pair_session_"):
            return True
    name = rel.name
    return name in _CODEX_COPY_IGNORE_NAMES or name.startswith(".env.") or name.endswith((".pyc", ".pyo"))

def _source_repo_files(repo_root: str) -> List[str]:
    proc = subprocess.run(
        ["git", "-C", repo_root, "ls-files", "-co", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    )
    files = [p.decode("utf-8", errors="surrogateescape") for p in proc.stdout.split(b"\0") if p]
    return [p for p in files if not _codex_copy_skip(p)]

def _copy_repo_to_scratch(repo_root: str) -> Tuple[str, str]:
    repo_root_path = Path(repo_root).resolve()
    scratch_parent = tempfile.mkdtemp(prefix="codex_pair_")
    scratch_repo_path = Path(scratch_parent) / "repo"
    scratch_repo_path.mkdir(parents=True)

    copied = 0
    for rel_path in _source_repo_files(str(repo_root_path)):
        src = repo_root_path / rel_path
        dst = scratch_repo_path / rel_path
        if not src.exists() and not src.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_symlink():
            os.symlink(os.readlink(src), dst)
        elif src.is_file():
            shutil.copy2(src, dst)
        else:
            continue
        copied += 1

    subprocess.run(["git", "init", "-q"], cwd=scratch_repo_path, check=True)
    subprocess.run(["git", "config", "user.email", "codex-pair@example.local"], cwd=scratch_repo_path, check=True)
    subprocess.run(["git", "config", "user.name", "codex-pair"], cwd=scratch_repo_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=scratch_repo_path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "baseline"], cwd=scratch_repo_path, check=True)
    _pair_profile("codex_scratch_copy", repo_root=str(repo_root_path), files=copied, scratch=scratch_repo_path)
    return str(scratch_repo_path), scratch_parent

def _write_codex_pair_schema(scratch_parent: str) -> str:
    schema_path = os.path.join(scratch_parent, "codex_pair_schema.json")
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(_CODEX_PAIR_FINAL_SCHEMA, f, indent=2)
    return schema_path

def build_codex_pair_prompt(transcript_text: str, pair_context_json: str) -> str:
    return f"""You are running inside a disposable scratch copy of the user's local repository.

Use the audio transcript to infer the latest concrete full-stack coding request. Inspect the repository as needed.
Make suggested edits only in this scratch copy. Do not commit. Do not access the network.
Do not modify credentials, secrets, generated lockfiles, or unrelated files.
Prefer the smallest coherent patch.
If the request is unclear, do not edit files; explain the blocking question.

This is language- and framework-agnostic. Infer the stack from the repository and existing files.
Depending on the request, relevant changes may be in frontend UI, backend APIs, data models,
database queries/migrations, validation, tests, config, or documentation. Follow existing project patterns.

The caller will extract and display the git diff from this scratch copy.
Your final response must fit the provided JSON schema.

Supplemental screen/OCR context, possibly noisy:
<pair_context_json>
{pair_context_json}
</pair_context_json>

Audio transcript:
<transcript>
{transcript_text}
</transcript>""".strip()

def _run_codex_exec(scratch_repo: str, scratch_parent: str, prompt: str) -> str:
    final_path = os.path.join(scratch_parent, "codex_pair_final.json")
    schema_path = _write_codex_pair_schema(scratch_parent)
    cmd = ["codex"]
    if PAIR_CODEX_MODEL:
        cmd += ["-m", PAIR_CODEX_MODEL]
    cmd += [
        "-a", "never",
        "exec",
        "--cd", scratch_repo,
        "--sandbox", "workspace-write",
        "--ephemeral",
        "--output-schema", schema_path,
        "--output-last-message", final_path,
        "--color", "never",
        "-",
    ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        cwd=scratch_repo,
        text=True,
        capture_output=True,
        timeout=_CODEX_TIMEOUT_SEC,
    )
    final_message = ""
    if os.path.exists(final_path):
        with open(final_path, "r", encoding="utf-8") as f:
            final_message = f.read().strip()
    if proc.returncode != 0:
        raise RuntimeError(
            "Codex failed.\n"
            f"stdout:\n{proc.stdout[-4000:]}\n\n"
            f"stderr:\n{proc.stderr[-4000:]}\n\n"
            f"final_message:\n{final_message[-4000:]}"
        )
    return final_message or proc.stdout.strip()

def _scratch_diff(scratch_repo: str) -> Tuple[List[str], str]:
    subprocess.run(["git", "add", "-N", "."], cwd=scratch_repo, check=False, capture_output=True, text=True)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=3", "--binary", "HEAD"],
        cwd=scratch_repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return changed, diff

def _parse_codex_final_message(final_message: str) -> CodexPairFinalMessage:
    text = final_message.strip()
    try:
        data = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start:end + 1])
        else:
            data = {
                "summary": "Codex completed.",
                "likely_request": "Inferred from latest audio transcript",
                "implementation_plan": [],
                "test_notes": [],
                "conversational_response": text or "Codex completed with no final message.",
            }
    data.setdefault("implementation_plan", [])
    data.setdefault("test_notes", [])
    return CodexPairFinalMessage(**data)

def call_codex_pair_process(transcript_text: str, pair_context_json: str) -> Optional[CodexPairResponse]:
    scratch_repo = scratch_parent = None
    total_start = time.perf_counter()
    try:
        repo_root = _resolve_pair_repo_root()
        _set_pair_status(f"copying repo: {os.path.basename(repo_root)}")
        scratch_repo, scratch_parent = _copy_repo_to_scratch(repo_root)
        prompt = build_codex_pair_prompt(transcript_text, pair_context_json)
        _pair_profile("codex_pair_prompt_build", prompt_chars=len(prompt), transcript_chars=len(transcript_text), repo_root=repo_root)

        _set_pair_status("running codex in scratch repo")
        codex_start = time.perf_counter()
        final_message = _run_codex_exec(scratch_repo, scratch_parent, prompt)
        _pair_profile("codex_exec", codex_start, final_chars=len(final_message))

        changed_files, diff = _scratch_diff(scratch_repo)
        if not diff.strip():
            diff = "(no file edits suggested)"
        final = _parse_codex_final_message(final_message)
        _pair_profile("codex_pair_process", total_start, success=True, files=len(changed_files), diff_chars=len(diff))
        return CodexPairResponse(
            summary=final.summary,
            likely_request=final.likely_request,
            implementation_plan=final.implementation_plan,
            changed_files=changed_files,
            suggested_diff=diff,
            test_notes=final.test_notes,
            conversational_response=final.conversational_response,
            repo_root=repo_root,
        )
    except Exception as e:
        _record_openai_error("CODEX PAIR PROCESS", e)
        _log_runtime_error("codex pair process", e)
        _pair_profile("codex_pair_process", total_start, success=False, error=str(e))
        return None
    finally:
        if scratch_parent:
            shutil.rmtree(scratch_parent, ignore_errors=True)

def call_pair_process():
    backend = PAIR_PROCESS_BACKEND or "codex"
    if backend == "openai":
        return call_openai_pair_process()
    if backend != "codex":
        _record_openai_error("PAIR PROCESS", RuntimeError(f"unknown PAIR_PROCESS_BACKEND: {PAIR_PROCESS_BACKEND}"))
        return None
    transcript_text = _pair_transcript_text()
    return call_codex_pair_process(transcript_text, _pair_context_json())

def _load_interview_dimension_mapping() -> str:
    with open(_INTERVIEW_DIMENSION_MAPPING_PATH, "r", encoding="utf-8") as f:
        return f.read()

def _save_behavioral_pair_response(response: BehavioralPairResponse) -> str:
    start = time.perf_counter()
    path = _pair_session_path(f"behavioral_pair_response_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_model_to_dict(response), f, indent=2)
    _pair_profile("behavioral_pair_response_save", start, response_path=path, bytes=os.path.getsize(path))
    return path

def call_openai_behavioral_pair_process() -> Optional[BehavioralPairResponse]:
    client_start = time.perf_counter()
    try:
        client = _openai_client()
        _pair_profile("behavioral_pair_client", client_start, success=True)
    except Exception as e:
        _record_openai_error("OpenAI BEHAVIORAL PAIR", e)
        _pair_profile("behavioral_pair_client", client_start, success=False, error=str(e))
        return None
    prompt_start = time.perf_counter()
    try:
        mapping_doc = _load_interview_dimension_mapping()
    except Exception as e:
        _record_openai_error("Behavioral mapping load", e)
        _pair_profile("behavioral_pair_mapping_load", prompt_start, success=False, path=_INTERVIEW_DIMENSION_MAPPING_PATH, error=str(e))
        return None
    transcript_text = _pair_transcript_text()
    prompt = build_behavioral_pair_prompt(mapping_doc, transcript_text)
    _pair_profile("behavioral_pair_prompt_build", prompt_start, prompt_chars=len(prompt), transcript_chars=len(transcript_text), mapping_chars=len(mapping_doc))
    openai_start = time.perf_counter()
    try:
        response = client.responses.parse(
            model=OPENAI_BEHAVIORAL_PAIR_MODEL,
            instructions=_BEHAVIORAL_PAIR_INSTRUCTIONS,
            input=[{"role": "user", "content": prompt}],
            text_format=BehavioralPairResponse,
            reasoning=OPENAI_FAST_REASONING,
            max_output_tokens=4096,
        )
        parsed = response.output_parsed
        data = _model_to_dict(parsed)
        _pair_profile(
            "behavioral_pair_openai",
            openai_start,
            success=True,
            model=OPENAI_BEHAVIORAL_PAIR_MODEL,
            bullets=len(data.get("bullet_points", []) or []),
            question_chars=len(data.get("latest_question", "") or ""),
            match_chars=len(data.get("closest_match", "") or ""),
        )
        return parsed
    except Exception as e:
        _record_openai_error("OpenAI BEHAVIORAL PAIR", e)
        _pair_profile("behavioral_pair_openai", openai_start, success=False, model=OPENAI_BEHAVIORAL_PAIR_MODEL, error=str(e))
        return None

def pair_process_context():
    global _pair_last_response, _pair_last_response_path, _pair_process_count, _pair_view_mode
    if _current_mode != "pair":
        _set_pair_status("pair processing only runs in pair mode")
        return
    if _pair_process_running.is_set():
        _set_pair_status("process already running")
        return
    if not _pair_active: start_pair_mode()
    else:
        try:
            capture = _queue_pair_capture("pre_process")
            _set_pair_status(f"queued pre-process capture {capture['index']}")
        except Exception as e:
            _set_pair_status(f"pre-process capture error: {e}")
            return
    _pair_view_mode = 1
    if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)
    def _run():
        global _pair_last_response, _pair_last_response_path, _pair_process_count, _pair_view_mode
        total_start = time.perf_counter()
        response = None
        audio_thread: Optional[threading.Thread] = None
        _pair_process_running.set()
        try:
            _set_pair_status("processing accumulated context")
            audio_thread = _start_pair_process_audio_flush()
            pending = _pair_pending_capture_count()
            if pending > 0:
                _set_pair_status(f"waiting for {pending} capture merges")
                _wait_for_pair_captures()
            else:
                _pair_profile("capture_queue_wait", pending_before=0, pending_after=0)
            audio_wait_start = time.perf_counter()
            if audio_thread:
                audio_thread.join()
            _pair_profile("pair_process_audio_flush_wait", audio_wait_start)
            response = call_pair_process()
            if response:
                _pair_last_response = response
                _pair_last_response_path = _save_pair_response(response)
                _pair_process_count += 1
                _pair_view_mode = 1
                _set_pair_status(f"process {_pair_process_count} complete")
            else:
                _set_pair_status(_last_openai_error or "process failed")
        except Exception as e:
            _log_runtime_error("pair process", e)
            _set_pair_status(f"process error: {e}")
        finally:
            _pair_profile("pair_process_total", total_start, success=bool(response))
            _pair_process_running.clear()
            if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)
    threading.Thread(target=_run, daemon=True).start()

def behavioral_pair_process_context():
    global _behavioral_pair_last_response, _behavioral_pair_last_response_path, _behavioral_pair_process_count, _behavioral_pair_view_mode
    if _current_mode != "behavioral_pair":
        _set_pair_status("behavioral processing only runs in behavioral mode")
        return
    if _pair_process_running.is_set():
        _set_pair_status("process already running")
        return
    if not _pair_active:
        start_pair_mode(capture_initial=False)
    _behavioral_pair_view_mode = 0
    def _run():
        global _behavioral_pair_last_response, _behavioral_pair_last_response_path, _behavioral_pair_process_count, _behavioral_pair_view_mode
        total_start = time.perf_counter()
        response = None
        audio_thread: Optional[threading.Thread] = None
        _pair_process_running.set()
        try:
            _set_pair_status("processing behavioral transcript")
            audio_thread = _start_pair_process_audio_flush()
            audio_wait_start = time.perf_counter()
            if audio_thread:
                audio_thread.join()
            _pair_profile("behavioral_pair_audio_flush_wait", audio_wait_start)
            response = call_openai_behavioral_pair_process()
            if response:
                _behavioral_pair_last_response = response
                _behavioral_pair_last_response_path = _save_behavioral_pair_response(response)
                _behavioral_pair_process_count += 1
                _behavioral_pair_view_mode = 0
                _set_pair_status(f"behavioral process {_behavioral_pair_process_count} complete")
            else:
                _set_pair_status(_last_openai_error or "behavioral process failed")
        except Exception as e:
            _log_runtime_error("behavioral pair process", e)
            _set_pair_status(f"behavioral process error: {e}")
        finally:
            _pair_profile("behavioral_pair_process_total", total_start, success=bool(response))
            _pair_process_running.clear()
            if _current_mode == "behavioral_pair": AppHelper.callAfter(_render_behavioral_pair_view)
    threading.Thread(target=_run, daemon=True).start()

def start_pair_mode(capture_initial: bool = True):
    global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at, _pair_session_dir, _pair_view_mode, _behavioral_pair_view_mode
    if not _is_pair_audio_mode():
        _set_pair_status("pair audio recording only starts in pair or behavioral mode")
        return
    if _current_mode == "behavioral_pair": _behavioral_pair_view_mode = 0
    else: _pair_view_mode = 0
    if _pair_active:
        if _is_pair_audio_mode(): AppHelper.callAfter(_render_pair_audio_mode_view)
        return
    total_start = time.perf_counter()
    _pair_active = True
    _pair_started_at = time.time()
    _pair_audio_stop.clear()
    session_prefix = "behavioral_pair_session" if _current_mode == "behavioral_pair" else "pair_session"
    _pair_session_dir = os.path.abspath(f"{session_prefix}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_pair_session_dir, exist_ok=True)
    _pair_profile("pair_start_session", session_dir=_pair_session_dir, mode=_current_mode)
    if _current_mode == "behavioral_pair":
        _discard_pair_capture_queue("behavioral_start")
    _warm_pair_transcription_model()
    system_start = time.perf_counter()
    try:
        _pair_system_audio_capture = SystemAudioCapture(); _pair_system_audio_capture.start()
        _pair_profile("pair_start_system_audio", system_start, success=True)
    except Exception as e:
        _pair_system_audio_capture = None
        _set_pair_status(f"system audio unavailable: {e}")
        _pair_profile("pair_start_system_audio", system_start, success=False, error=str(e))
    mic_start = time.perf_counter()
    mic_device = None
    try:
        mic_device = _find_microphone_device()
        if mic_device is not None:
            _pair_audio_stream = sd.InputStream(device=mic_device, samplerate=_SAMPLE_RATE, channels=1, dtype='float32', callback=_pair_audio_callback, blocksize=1024)
            _pair_audio_stream.start()
        _pair_profile("pair_start_mic", mic_start, success=_pair_audio_stream is not None, device=mic_device if mic_device is not None else "")
    except Exception as e:
        _pair_audio_stream = None
        _set_pair_status(f"mic unavailable: {e}")
        _pair_profile("pair_start_mic", mic_start, success=False, device=mic_device if mic_device is not None else "", error=str(e))
    thread_start = time.perf_counter()
    _pair_audio_thread = threading.Thread(target=_pair_audio_loop, daemon=True)
    _pair_audio_thread.start()
    _pair_profile("pair_start_audio_thread", thread_start, success=True)
    if capture_initial and _current_mode == "pair":
        try:
            capture = _queue_pair_capture("initial")
            _set_pair_status(f"started; queued initial capture {capture['index']}")
        except Exception as e:
            _set_pair_status(f"started; initial capture error: {e}")
            _pair_profile("pair_start_initial_capture", success=False, error=str(e))
    else:
        _set_pair_status("started audio-only behavioral recording" if _current_mode == "behavioral_pair" else "started audio recording")
    _pair_profile("pair_start_total", total_start, active=_pair_active)

def stop_pair_mode(render_status: bool = True):
    global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at
    if not _pair_active: return
    session_dir, elapsed = _pair_session_dir, _pair_elapsed()
    if render_status: _set_pair_status("stopping audio recording")
    _pair_active = False
    _pair_audio_stop.set()

    def _stop():
        global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at
        total_start = time.perf_counter()
        try:
            if _pair_audio_flush_lock.acquire(blocking=True):
                try:
                    mic_audio = system_audio = None
                    if _pair_audio_stream:
                        mic_stop_start = time.perf_counter()
                        try:
                            _pair_audio_stream.stop(); _pair_audio_stream.close()
                            _pair_profile("pair_stop_mic", mic_stop_start, success=True)
                        except Exception as e:
                            _log_runtime_error("pair mic stop", e)
                            _pair_profile("pair_stop_mic", mic_stop_start, success=False, error=str(e))
                        _pair_audio_stream = None
                    if _pair_system_audio_capture:
                        system_stop_start = time.perf_counter()
                        try:
                            system_audio = _pair_system_audio_capture.stop()
                            _pair_profile("pair_stop_system_audio", system_stop_start, success=True, samples=len(system_audio) if system_audio is not None else 0)
                        except Exception as e:
                            _log_runtime_error("pair system audio stop", e)
                            _pair_profile("pair_stop_system_audio", system_stop_start, success=False, error=str(e))
                        _pair_system_audio_capture = None
                    drain_start = time.perf_counter()
                    mic_audio = _drain_pair_mic_audio()
                    _pair_profile("pair_stop_mic_drain", drain_start, samples=len(mic_audio) if mic_audio is not None else 0)
                    _persist_pair_audio_chunk(mic_audio, system_audio, session_dir=session_dir, elapsed=elapsed)
                finally:
                    _pair_audio_flush_lock.release()
            _pair_started_at = None
            _pair_audio_thread = None
            if render_status: _set_pair_status("audio recording stopped")
            _pair_profile("pair_stop_total", total_start, success=True)
        except Exception as e:
            _log_runtime_error("pair stop", e)
            _pair_active = False
            _set_pair_status(f"pair stop error: {e}")
            _pair_profile("pair_stop_total", total_start, success=False, error=str(e))
    threading.Thread(target=_stop, daemon=True).start()

def clear_pair_context():
    global _pair_context, _pair_transcript_segments, _pair_last_response, _pair_last_response_path, _pair_capture_count, _pair_processed_capture_count, _pair_process_count, _pair_audio_chunk_index, _pair_session_dir, _pair_started_at, _pair_generation
    start = time.perf_counter()
    cleared_queued = 0
    _pair_generation += 1
    with _pair_context_lock: _pair_context = PairContextUpdate()
    _pair_transcript_segments = []
    _pair_last_response = None
    _pair_last_response_path = None
    _pair_capture_count = _pair_processed_capture_count = _pair_process_count = 0
    _pair_audio_chunk_index = 0
    _pair_started_at = time.time() if _pair_active else None
    _pair_session_dir = os.path.abspath(f"pair_session_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_pair_session_dir, exist_ok=True)
    with _pair_audio_lock: _pair_audio_data.clear()
    if _pair_system_audio_capture: _pair_system_audio_capture.drain()
    while True:
        try:
            _pair_capture_queue.get_nowait()
            cleared_queued += 1
            try: _pair_capture_queue.task_done()
            except ValueError as e: _log_runtime_error("pair clear task_done", e)
        except queue.Empty:
            break
    _set_pair_status("context cleared; recording still running" if _pair_active else "context cleared")
    _pair_profile("pair_context_clear", start, active=_pair_active, cleared_queued=cleared_queued)

def clear_behavioral_pair_context():
    global _pair_transcript_segments, _behavioral_pair_last_response, _behavioral_pair_last_response_path, _behavioral_pair_process_count, _pair_audio_chunk_index, _pair_session_dir, _pair_started_at, _behavioral_pair_view_mode, _behavioral_pair_showing_cheat
    start = time.perf_counter()
    _pair_transcript_segments = []
    _behavioral_pair_last_response = None
    _behavioral_pair_last_response_path = None
    _behavioral_pair_process_count = 0
    _behavioral_pair_view_mode = 0
    _pair_audio_chunk_index = 0
    _pair_started_at = time.time() if _pair_active else None
    _pair_session_dir = os.path.abspath(f"behavioral_pair_session_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_pair_session_dir, exist_ok=True)
    with _pair_audio_lock: _pair_audio_data.clear()
    if _pair_system_audio_capture: _pair_system_audio_capture.drain()
    _discard_pair_capture_queue("behavioral_clear")
    _behavioral_pair_showing_cheat = True
    ui_update_sections(behavioral_pair_cheat_sheet)
    _set_pair_status("behavioral transcript cleared; recording still running" if _pair_active else "behavioral transcript cleared")
    _pair_profile("behavioral_pair_clear", start, active=_pair_active)

def _ensure_status_label():
    global _status_label
    if _status_label is not None and _status_label.superview() is not None: return _status_label
    lbl = NSTextField.alloc().initWithFrame_(((8, 8), (420, 18)))
    lbl.setBordered_(False); lbl.setEditable_(False); lbl.setSelectable_(False); lbl.setDrawsBackground_(True)
    lbl.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
    lbl.setTextColor_(_mk_color(45, 45, 45)); lbl.setFont_(NSFont.userFixedPitchFontOfSize_(11) or NSFont.systemFontOfSize_(11))
    window.contentView().addSubview_(lbl); _status_label = lbl
    return lbl

def _update_status(text: str): lbl = _ensure_status_label(); lbl.setStringValue_(text); lbl.setFrameOrigin_((8, 8))

def _update_overlay_sections(sections: List[Tuple[str, List[str]]]):
    _ensure_window_width_for_columns()
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame(); available_w, available_h = frame.size.width, frame.size.height
    col_width = (available_w - (LEFT_MARGIN + RIGHT_MARGIN + GUTTER)) / COLUMNS
    display_chars = max(24, int((col_width - EXTRA_COL_PADDING_PX) / _estimate_char_px(LINE_FONT)) - 2)
    x_cols = [LEFT_MARGIN, LEFT_MARGIN + col_width + GUTTER]
    y, col = [available_h - TOP_MARGIN for _ in range(COLUMNS)], 0
    for title, lines in sections:
        color = SECTION_COLOR.get(title, _mk_color(45, 45, 45))
        if _current_mode == "behavioral_pair":
            color = _mk_color(0, 0, 0)
        if y[col] - TITLE_HEIGHT < BOTTOM_MARGIN:
            col += 1
            if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); break
            y[col] = available_h - TOP_MARGIN
        if title != "Behavioral Response":
            _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
        display_lines: List[str] = []
        for line in lines:
            display_lines.extend(wrap_lines(str(line), display_chars))
        for line in display_lines:
            if y[col] - LINE_HEIGHT < BOTTOM_MARGIN:
                col += 1
                if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); return
                y[col] = available_h - TOP_MARGIN
                if title != "Behavioral Response":
                    _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
            _place_label(line, x_cols[col], y[col], col_width, LINE_HEIGHT, LINE_FONT, color); y[col] -= LINE_HEIGHT

def ui_update(lines: List[str]): AppHelper.callAfter(_update_overlay_sections, [('status:', lines)])
def ui_update_sections(sections: List[Tuple[str, List[str]]]): AppHelper.callAfter(_update_overlay_sections, sections)

def format_pair_for_overlay_sections() -> List[Tuple[str, List[str]]]:
    sections: List[Tuple[str, List[str]]] = []
    status_line = f"Status: {_pair_status}"
    if _pair_last_response:
        resp = _model_to_dict(_pair_last_response)
        if "suggested_diff" in resp:
            conversational = resp.get("conversational_response", "") or ""
            summary_lines = [status_line]
            summary_lines += wrap_lines(f"Summary: {resp.get('summary', '')}", CHARS_PER_LINE)
            summary_lines += wrap_lines(f"Likely request: {resp.get('likely_request', '')}", CHARS_PER_LINE)
            summary_lines += wrap_lines(f"Repo: {resp.get('repo_root', '')}", CHARS_PER_LINE)
            sections.append(("Pair Response", summary_lines))
            sections.append(("Changed Files", resp.get("changed_files", []) or ["(none)"]))
            plan_items = resp.get("implementation_plan", []) or []
            sections.append(("Implementation Plan", [*sum((wrap_lines(f"- {item}", CHARS_PER_LINE) for item in plan_items), [])] or ["(none)"]))
            sections.append(("Code Suggestions", ["See Suggested Diff for the generated patch."]))
            sections.append(("Suggested Diff", wrap_lines(resp.get("suggested_diff", "") or "(empty)", CHARS_PER_LINE)))

            notes_lines: List[str] = []
            test_notes = resp.get("test_notes", []) or []
            if test_notes:
                notes_lines.append("Tests/checks:")
                notes_lines += [*sum((wrap_lines(f"- {note}", CHARS_PER_LINE) for note in test_notes), [])]
                notes_lines.append("")
            notes_lines += wrap_lines(conversational or "(empty)", CHARS_PER_LINE)
            sections.append(("Codex Notes", notes_lines))
            sections.append(("Pair Conversation", [status_line] + wrap_lines(conversational or "(empty)", CHARS_PER_LINE)))

            metadata_lines = [status_line]
            metadata_lines += wrap_lines(f"Backend: {PAIR_PROCESS_BACKEND}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Repo: {resp.get('repo_root', '')}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Changed files: {len(resp.get('changed_files', []) or [])}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Diff chars: {len(resp.get('suggested_diff', '') or '')}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Responses: {_pair_process_count}", CHARS_PER_LINE)
            if _pair_last_response_path:
                metadata_lines += wrap_lines(f"Saved: {_pair_last_response_path}", CHARS_PER_LINE)
            sections.append(("Pair Metadata", metadata_lines))
            return sections

        if "code_suggestions" in resp:
            conversational = resp.get("conversational_response", "") or ""
            summary_lines = [status_line]
            summary_lines += wrap_lines(f"Summary: {resp.get('summary', '')}", CHARS_PER_LINE)
            summary_lines += wrap_lines(f"Likely request: {resp.get('likely_request', '')}", CHARS_PER_LINE)
            questions = resp.get("questions", []) or []
            if questions:
                summary_lines.append("Questions:")
                summary_lines += [*sum((wrap_lines(f"- {q}", CHARS_PER_LINE) for q in questions), [])]
            sections.append(("Pair Response", summary_lines))

            plan_items = resp.get("implementation_plan", []) or []
            sections.append(("Implementation Plan", [*sum((wrap_lines(f"- {item}", CHARS_PER_LINE) for item in plan_items), [])] or ["(none)"]))

            suggestions = resp.get("code_suggestions", []) or []
            preview_lines: List[str] = []
            detail_lines: List[str] = []
            for idx, suggestion in enumerate(suggestions, start=1):
                title = str(suggestion.get("title", f"Suggestion {idx}") if isinstance(suggestion, dict) else f"Suggestion {idx}")
                language = str(suggestion.get("language") or "") if isinstance(suggestion, dict) else ""
                path = str(suggestion.get("path") or "") if isinstance(suggestion, dict) else ""
                notes = str(suggestion.get("notes") or "") if isinstance(suggestion, dict) else ""
                code = str(suggestion.get("code") or "") if isinstance(suggestion, dict) else str(suggestion)
                label = f"{idx}. {title}"
                if path:
                    label += f" ({path})"
                elif language:
                    label += f" ({language})"
                preview_lines += wrap_lines(label, CHARS_PER_LINE)
                if notes:
                    preview_lines += wrap_lines(f"   {notes}", CHARS_PER_LINE)
                detail_lines += wrap_lines(label, CHARS_PER_LINE)
                if notes:
                    detail_lines += wrap_lines(f"Notes: {notes}", CHARS_PER_LINE)
                detail_lines += wrap_lines(code or "(empty)", CHARS_PER_LINE)
                detail_lines.append("")
            sections.append(("Code Suggestions", preview_lines or ["(none)"]))
            sections.append(("Code Suggestions Detail", detail_lines or ["(none)"]))
            sections.append(("Pair Conversation", [status_line] + wrap_lines(conversational or "(empty)", CHARS_PER_LINE)))

            metadata_lines = [status_line]
            metadata_lines += wrap_lines(f"Backend: {PAIR_PROCESS_BACKEND}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Suggestions: {len(suggestions)}", CHARS_PER_LINE)
            metadata_lines += wrap_lines(f"Responses: {_pair_process_count}", CHARS_PER_LINE)
            if _pair_last_response_path:
                metadata_lines += wrap_lines(f"Saved: {_pair_last_response_path}", CHARS_PER_LINE)
            sections.append(("Pair Metadata", metadata_lines))
            return sections

        conversational = resp.get("conversational_response", "") or ""
        summary_lines = [status_line]
        summary_lines += wrap_lines(f"Summary: {resp.get('summary', '')}", CHARS_PER_LINE)
        summary_lines += wrap_lines(f"Likely request: {resp.get('likely_request', '')}", CHARS_PER_LINE)
        sections.append(("Pair Response", summary_lines))
        plan_items = resp.get("implementation_plan", []) or []
        sections.append(("Implementation Plan", [*sum((wrap_lines(f"- {item}", CHARS_PER_LINE) for item in plan_items), [])] or ["(none)"]))
        sections.append(("Code Suggestions", ["Unrecognized pair response shape. See conversation for details."]))
        sections.append(("Pair Conversation", [status_line] + wrap_lines(conversational or "(empty)", CHARS_PER_LINE)))

        metadata_lines = [status_line]
        metadata_lines += wrap_lines(f"Backend: {PAIR_PROCESS_BACKEND}", CHARS_PER_LINE)
        metadata_lines.append("Questions:")
        resp_questions = resp.get("questions", []) or []
        if resp_questions:
            metadata_lines += [*sum((wrap_lines(f"- {q}", CHARS_PER_LINE) for q in resp_questions), [])]
        else:
            metadata_lines.append("(none)")
        sections.append(("Pair Metadata", metadata_lines))
    else:
        repo_hint = PAIR_REPO_ROOT or "(current Git repo)"
        sections.append(("Pair Response", [
            status_line,
            "No process response yet.",
            "Press 47 to process accumulated context.",
            f"Backend: {PAIR_PROCESS_BACKEND}",
            f"Repo: {repo_hint}",
        ]))
        sections.append(("Changed Files", ["(none)"]))
        sections.append(("Suggested Diff", ["(empty)"]))
        sections.append(("Codex Notes", ["(empty)"]))
        sections.append(("Implementation Plan", ["(empty)"]))
        sections.append(("Code Suggestions", ["(empty)"]))
        sections.append(("Code Suggestions Detail", ["(empty)"]))
        sections.append(("Pair Conversation", [status_line, "(empty)"]))
        sections.append(("Pair Metadata", [status_line, "Summary:", "(empty)", "Likely request:", "(empty)", "Questions:", "(none)"]))
    return sections

def format_behavioral_pair_for_overlay_sections() -> List[Tuple[str, List[str]]]:
    sections: List[Tuple[str, List[str]]] = []
    status_line = f"Status: {_pair_status}"
    if _behavioral_pair_last_response:
        resp = _model_to_dict(_behavioral_pair_last_response)
        bullets = resp.get("bullet_points", []) or []
        response_lines = []
        response_lines += wrap_lines(f"Match: {resp.get('closest_match', '')}", CHARS_PER_LINE)
        response_lines.append("")
        response_lines += [*sum((wrap_lines(f"- {bullet}", CHARS_PER_LINE) for bullet in bullets), [])]
        response_lines.append("")
        response_lines.append(status_line)
        response_lines += wrap_lines(f"Question: {resp.get('latest_question', '')}", CHARS_PER_LINE)
        sections.append(("Behavioral Response", response_lines))
    else:
        sections.append(("Behavioral Response", [status_line, "No behavioral response yet.", "Press 47 to process audio transcript."]))

    transcript = _pair_transcript_text()
    transcript_lines = wrap_lines(transcript or "(empty)", CHARS_PER_LINE)
    sections.append(("Behavioral Transcript", [status_line] + transcript_lines))

    metadata_lines = [status_line]
    metadata_lines += wrap_lines(f"Model: {OPENAI_BEHAVIORAL_PAIR_MODEL}", CHARS_PER_LINE)
    metadata_lines += wrap_lines(f"Mapping: {_INTERVIEW_DIMENSION_MAPPING_PATH}", CHARS_PER_LINE)
    metadata_lines += wrap_lines(f"Chunks: {_pair_audio_chunk_index}", CHARS_PER_LINE)
    metadata_lines += wrap_lines(f"Responses: {_behavioral_pair_process_count}", CHARS_PER_LINE)
    if _behavioral_pair_last_response_path:
        metadata_lines += wrap_lines(f"Saved: {_behavioral_pair_last_response_path}", CHARS_PER_LINE)
    sections.append(("Behavioral Metadata", metadata_lines))
    return sections

def _render_pair_view():
    global _pair_full_sections
    _pair_full_sections = format_pair_for_overlay_sections() + pair_program_cheat_sheet
    _update_overlay_sections(_get_page_sections(_pair_full_sections, _pair_view_mode, "pair"))
    _update_status(f"[PAIR] Page {_pair_view_mode + 1}/{len(_PAIR_PAGES)} | 47=process 50=capture 51=clear 43=hide/show")

def _render_behavioral_pair_view():
    global _behavioral_pair_full_sections, _behavioral_pair_showing_cheat
    _behavioral_pair_showing_cheat = False
    _behavioral_pair_full_sections = format_behavioral_pair_for_overlay_sections() + behavioral_pair_cheat_sheet
    _update_overlay_sections(_get_page_sections(_behavioral_pair_full_sections, _behavioral_pair_view_mode, "behavioral_pair"))
    _update_status(f"[BEHAVIORAL] Page {_behavioral_pair_view_mode + 1}/{len(_BEHAVIORAL_PAIR_PAGES)} | {_MIDI_HELP_BEHAVIORAL_PAIR}")

def _ensure_project_pages_loaded():
    global _project_text_pages
    if not _project_text_pages:
        _project_text_pages = _load_project_text_pages()

def _expand_for_project_image():
    frame = window.frame()
    target_w = max(frame.size.width, PROJECT_IMAGE_WINDOW_WIDTH)
    target_h = max(frame.size.height, PROJECT_IMAGE_WINDOW_HEIGHT)
    if target_w != frame.size.width or target_h != frame.size.height:
        window.setFrame_display_(((frame.origin.x, frame.origin.y), (target_w, target_h)), True)

def _show_project_image_view():
    _expand_for_project_image()
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame()
    page_num, total_pages = len(_project_text_pages) + 1, _project_total_pages()
    if os.path.exists(_PROJECT_IMAGE_PATH):
        img = NSImage.alloc().initWithContentsOfFile_(_PROJECT_IMAGE_PATH)
        if img:
            img_view = NSImageView.alloc().initWithFrame_(((10, 35), (frame.size.width - 20, frame.size.height - 85)))
            img_view.setImage_(img); img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            window.contentView().addSubview_(img_view)
            _update_status(f"[PROJECT] ZPDS diagram ({page_num}/{total_pages}) | {_MIDI_HELP_PROJECT}")
            return
    _place_label(f"Missing image: {_PROJECT_IMAGE_PATH}", 20, frame.size.height - 50, frame.size.width - 40, TITLE_HEIGHT, TITLE_FONT, _mk_color(140, 0, 60))
    _update_status(f"[PROJECT] Missing diagram ({page_num}/{total_pages}) | {_MIDI_HELP_PROJECT}")

def _render_project_view():
    global _project_view_mode
    _ensure_project_pages_loaded()
    total_pages = _project_total_pages()
    _project_view_mode = _project_view_mode % total_pages
    if _project_view_mode >= len(_project_text_pages):
        _show_project_image_view()
        return
    _update_overlay_sections(_project_text_pages[_project_view_mode])
    _update_status(f"[PROJECT] Page {_project_view_mode + 1}/{total_pages} | {_MIDI_HELP_PROJECT}")

def _extend_wrapped_limited(out: List[str], text: str, max_lines: int, prefix: str = "") -> int:
    remaining = max_lines - len(out)
    if remaining <= 0: return 0
    lines = wrap_lines(f"{prefix}{text}", CHARS_PER_LINE)
    out.extend(lines[:remaining])
    return max(0, len(lines) - remaining)

def _get_page_sections(sections: List[Tuple[str, List[str]]], page: int, mode: str) -> List[Tuple[str, List[str]]]:
    pages = _MODE_A_PAGES if mode == "mode_a" else _PAIR_PAGES if mode == "pair" else _BEHAVIORAL_PAIR_PAGES if mode == "behavioral_pair" else _SYSTEM_PAGES
    if page >= len(pages): return []
    return [s for s in sections if s[0] in set(pages[page])]

def _show_diagram_view():
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame(); page_num, total_pages = len(_SYSTEM_PAGES) + 1, len(_SYSTEM_PAGES) + 1
    if _system_diagram_path and os.path.exists(_system_diagram_path):
        img = NSImage.alloc().initWithContentsOfFile_(_system_diagram_path)
        if img:
            img_view = NSImageView.alloc().initWithFrame_(((10, 10), (frame.size.width - 20, frame.size.height - 60)))
            img_view.setImage_(img); img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            window.contentView().addSubview_(img_view)
            _update_status(f"[SYSTEM] Diagram ({page_num}/{total_pages}) | {_MIDI_HELP_SYSTEM}"); return
    elapsed = int(time.time() - _system_diagram_started_at) if _system_diagram_started_at else 0
    if _system_diagram_status == "failed":
        msg = _last_openai_error or "Diagram generation failed."
        _place_label(msg[:220], 20, frame.size.height - 50, frame.size.width - 40, TITLE_HEIGHT, TITLE_FONT, _mk_color(140, 0, 60))
        _update_status(f"[SYSTEM] Diagram failed ({page_num}/{total_pages}) | {_MIDI_HELP_SYSTEM}"); return
    if _system_diagram_status == "generating" and elapsed > 180:
        msg = f"Diagram still generating after {elapsed}s. It may be slow or stalled."
        _place_label(msg, 20, frame.size.height - 50, frame.size.width - 40, TITLE_HEIGHT, TITLE_FONT, _mk_color(200, 120, 0))
        _update_status(f"[SYSTEM] Diagram slow ({page_num}/{total_pages}) | {_MIDI_HELP_SYSTEM}"); return
    _place_label(f"Diagram generating... {elapsed}s", 20, frame.size.height - 50, 500, TITLE_HEIGHT, TITLE_FONT, _mk_color(60, 60, 140))
    _update_status(f"[SYSTEM] Diagram ({page_num}/{total_pages}) generating... | {_MIDI_HELP_SYSTEM}")

def _rerender_current_view():
    if _current_mode == "mode_a":
        pages, view_mode, full_sections = _MODE_A_PAGES, _mode_a_view_mode, _mode_a_full_sections
        total_pages, midi_help = len(pages), _MIDI_HELP_DSA
    elif _current_mode == "pair":
        if not _pair_full_sections: _render_pair_view()
        else:
            AppHelper.callAfter(_update_overlay_sections, _get_page_sections(_pair_full_sections, _pair_view_mode, "pair"))
            AppHelper.callAfter(_update_status, f"[PAIR] Page {_pair_view_mode + 1}/{len(_PAIR_PAGES)} | 47=process 50=capture 51=clear 43=hide/show")
        return
    elif _current_mode == "behavioral_pair":
        if _behavioral_pair_showing_cheat:
            AppHelper.callAfter(_update_overlay_sections, behavioral_pair_cheat_sheet)
            AppHelper.callAfter(_update_status, f"[BEHAVIORAL] Cheat Sheet | {_MIDI_HELP_BEHAVIORAL_PAIR}")
            return
        if not _behavioral_pair_full_sections: _render_behavioral_pair_view()
        else:
            AppHelper.callAfter(_update_overlay_sections, _get_page_sections(_behavioral_pair_full_sections, _behavioral_pair_view_mode, "behavioral_pair"))
            AppHelper.callAfter(_update_status, f"[BEHAVIORAL] Page {_behavioral_pair_view_mode + 1}/{len(_BEHAVIORAL_PAIR_PAGES)} | {_MIDI_HELP_BEHAVIORAL_PAIR}")
        return
    elif _current_mode == "project":
        AppHelper.callAfter(_render_project_view)
        return
    else:
        pages, view_mode, full_sections = _SYSTEM_PAGES, _system_view_mode, _system_full_sections
        total_pages, midi_help = len(pages) + 1, _MIDI_HELP_SYSTEM
    mode_label, _ = _mode_label_and_help(_current_mode)
    if view_mode < len(pages):
        if full_sections:
            AppHelper.callAfter(_update_overlay_sections, _get_page_sections(full_sections, view_mode, _current_mode))
            AppHelper.callAfter(_update_status, f"[{mode_label}] Page {view_mode + 1}/{total_pages} | {midi_help}")
    else:
        AppHelper.callAfter(_show_diagram_view)

def run_pipeline():
    global _mode_a_results_shown, _system_results_shown, _mode_a_view_mode, _system_view_mode
    global _mode_a_full_sections, _system_full_sections, _system_diagram_path
    global _mode_a_conversation_history, _mode_a_turn_count, _last_openai_error, _system_diagram_status, _system_diagram_started_at
    mode = _current_mode
    _last_openai_error = None
    if mode == "project":
        AppHelper.callAfter(_render_project_view)
        _is_running.clear()
        return
    if mode == "pair":
        AppHelper.callAfter(pair_process_context)
        _is_running.clear()
        return
    if mode == "behavioral_pair":
        AppHelper.callAfter(behavioral_pair_process_context)
        _is_running.clear()
        return
    if mode == "mode_a":
        _mode_a_view_mode, _mode_a_conversation_history, _mode_a_turn_count = 0, [], 1
        cheat = mode_a_cheat_sheet
    else:
        _system_diagram_path, _system_view_mode = None, 0
        _system_diagram_status, _system_diagram_started_at = "idle", None
        cheat = system_cheat_sheet
    mode_label, midi_help = _mode_label_and_help(mode)
    try:
        ui_update_sections([("Status", [f"[{mode_label}] Generating... | {midi_help}"])] + cheat)
        try: shot = take_screenshot()
        except Exception as e: ui_update([f"Screenshot error: {e}"]); return
        ocr_text = ocr_with_tesseract(shot)
        if mode == "mode_a":
            data, _mode_a_conversation_history = call_openai_mode_a(ocr_text, image_path=shot)
            if data:
                sections, turn = format_mode_a_for_overlay_sections(data), _mode_a_turn_count
                def _render():
                    global _mode_a_full_sections
                    _mode_a_full_sections = sections; _update_overlay_sections(sections); _update_status(f"[{mode_label}] Turn {turn} | {_MIDI_HELP_DSA}")
                AppHelper.callAfter(_render); _mode_a_results_shown = True
            else: ui_update([_last_openai_error or f"[{mode_label}] (No response)"]); _mode_a_results_shown = False
        else:
            data = call_openai_system(ocr_text, image_path=shot)
            if data:
                sections = format_system_for_overlay_sections(data)
                def _render():
                    global _system_full_sections
                    _system_full_sections = sections; _update_overlay_sections(sections); _update_status(f"[{mode_label}] All sections | {_MIDI_HELP_SYSTEM}")
                AppHelper.callAfter(_render); _system_results_shown = True
                threading.Thread(target=generate_diagram_async, args=(data,), daemon=True).start()
            else: ui_update([_last_openai_error or f"[{mode_label}] (No structured response)"]); _system_results_shown = False
    except Exception as e:
        ui_update([f"Pipeline error: {e}"])
        if mode == "mode_a": _mode_a_results_shown = False
        else: _system_results_shown = False
    finally: _is_running.clear()

def continue_mode_a():
    global _mode_a_results_shown, _mode_a_full_sections, _mode_a_conversation_history, _mode_a_turn_count, _last_openai_error
    try:
        _last_openai_error = None
        ui_update([f"[DSA] Continuing (turn {_mode_a_turn_count + 1})... | {_MIDI_HELP_DSA}"])
        try: shot = take_screenshot()
        except Exception as e: ui_update([f"Screenshot error: {e}"]); return
        ocr_text = ocr_with_tesseract(shot)
        messages = _mode_a_conversation_history + [{"role": "user", "content": build_mode_a_continuation_prompt(ocr_text)}]
        data, _mode_a_conversation_history = call_openai_mode_a(ocr_text, image_path=shot, messages=messages)
        if data:
            _mode_a_turn_count += 1; sections, turn = format_mode_a_for_overlay_sections(data), _mode_a_turn_count
            def _render():
                global _mode_a_full_sections
                _mode_a_full_sections = sections; _update_overlay_sections(sections); _update_status(f"[DSA] Turn {turn} | {_MIDI_HELP_DSA}")
            AppHelper.callAfter(_render); _mode_a_results_shown = True
        else: ui_update([_last_openai_error or "[DSA] (No response on continuation)"])
    finally: _is_running.clear()

def simplify_mode_a():
    global _mode_a_results_shown, _mode_a_full_sections, _mode_a_conversation_history, _mode_a_turn_count, _last_openai_error
    try:
        _last_openai_error = None
        ui_update([f"[DSA] Simplifying... | {_MIDI_HELP_DSA}"])
        messages = _mode_a_conversation_history + [{"role": "user", "content": build_mode_a_simplify_prompt()}]
        data, _mode_a_conversation_history = call_openai_mode_a("", image_path=None, messages=messages)
        if data:
            _mode_a_turn_count += 1; sections, turn = format_mode_a_for_overlay_sections(data), _mode_a_turn_count
            def _render():
                global _mode_a_full_sections
                _mode_a_full_sections = sections; _update_overlay_sections(sections); _update_status(f"[DSA] Turn {turn} (simplified) | {_MIDI_HELP_DSA}")
            AppHelper.callAfter(_render); _mode_a_results_shown = True
        else: ui_update([_last_openai_error or "[DSA] (No response on simplify)"])
    finally: _is_running.clear()

def _get_transcription_model(show_status=True):
    global _transcription_model
    if _transcription_model is not None: return _transcription_model
    with _transcription_model_lock:
        if _transcription_model is not None: return _transcription_model
        if show_status: ui_update(["Loading Parakeet v3..."])
        from mlx_audio.stt.utils import load
        _transcription_model = load(_PARAKEET_MODEL)
        return _transcription_model

def transcribe_audio_file(path: Optional[str], show_status=True) -> str:
    if not path or not os.path.exists(path): return ""
    model = _get_transcription_model(show_status=show_status)
    result = model.generate(path)
    return getattr(result, "text", "").strip()

def _audio_callback(indata, frames, time_info, status):
    if _is_recording: _audio_data.append(indata.copy())

_audio_stream = None

def _find_microphone_device():
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0 and 'MacBook' in dev['name']: return i
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0 and 'Microphone' in dev['name'] and 'Steam' not in dev['name']: return i
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0: return i
    return None

def start_recording(is_continuation=False):
    global _is_recording, _audio_data, _audio_stream, _voice_conversation, _voice_is_continuation, _system_audio_capture
    _audio_data, _is_recording, _voice_is_continuation = [], True, is_continuation
    if not is_continuation: _voice_conversation = []
    try:
        _system_audio_capture = SystemAudioCapture(); _system_audio_capture.start()
        mic_device = _find_microphone_device()
        if mic_device is None: ui_update(["[VOICE] No microphone found!"]); return
        _audio_stream = sd.InputStream(device=mic_device, samplerate=_SAMPLE_RATE, channels=1, dtype='float32', callback=_audio_callback, blocksize=1024)
        _audio_stream.start()
        stop_btn = "38" if is_continuation else "39"
        if is_continuation and _last_voice_sections:
            ui_update_sections([("Recording", [f"Started continuation recording... Press {stop_btn} to stop."])] + _last_voice_sections)
        else: ui_update([f"[VOICE] Recording mic + system... Press {stop_btn} to stop."])
    except Exception as e: ui_update([f"[VOICE] Recording error: {e}"])

def stop_recording_and_ask():
    global _is_recording, _audio_stream, _system_audio_capture, _voice_mic_path, _voice_system_path
    _is_recording = False
    if _audio_stream: _audio_stream.stop(); _audio_stream.close(); _audio_stream = None
    system_audio = _system_audio_capture.stop() if _system_audio_capture else None
    _system_audio_capture = None
    mic_audio, mic_path, system_path = None, None, None
    if _audio_data:
        mic_audio = np.concatenate(_audio_data, axis=0).flatten()
        mic_path = os.path.abspath("mic_recording.wav")
        _write_audio_chunk(mic_path, mic_audio)
    if system_audio is not None and len(system_audio) > 0:
        system_path = os.path.abspath("system_recording.wav")
        _write_audio_chunk(system_path, system_audio)
    _voice_mic_path, _voice_system_path = mic_path, system_path
    if not _audio_data and system_audio is None: ui_update(["[VOICE] No audio recorded."]); return
    if _voice_is_continuation and _last_voice_sections:
        ui_update_sections([("Processing", ["Finished continuation recording, generating response..."])] + _last_voice_sections)
    threading.Thread(target=_process_voice_question, daemon=True).start()

def _fetch_voice_deep_dive(transcript: str, quick_answer: str, conversation_history: List[dict], turn_num: int):
    global _last_voice_sections
    try:
        client = _openai_client()
        context_parts = []
        for i in range(0, len(conversation_history) - 2, 2):
            q = conversation_history[i]['content'][:100]
            a = conversation_history[i+1]['content'][:100]
            context_parts.append(f"Q: {q}...\nA: {a}...")
        context_summary = "\n".join(context_parts)
        deep_dive_prompt = build_voice_deep_dive_prompt(_WORK_EXPERIENCE, transcript, quick_answer, context_summary)

        response = client.responses.create(
            model=OPENAI_REASONING_MODEL,
            input=deep_dive_prompt,
            reasoning=OPENAI_XHIGH_REASONING,
            max_output_tokens=12000,
            text={"verbosity": "medium"},
        )
        deep_dive = response.output_text.strip()

        def _update_with_deep_dive():
            global _last_voice_sections
            if _last_voice_sections:
                new_sections = _last_voice_sections + [("Deep Dive", wrap_lines(deep_dive, CHARS_PER_LINE))]
                _last_voice_sections = new_sections
                ui_update_sections(new_sections)
                _update_status(f"[VOICE] Turn {turn_num} + Deep Dive | 38=follow-up 39=new")
        AppHelper.callAfter(_update_with_deep_dive)
    except Exception as e:
        AppHelper.callAfter(_update_status, f"[VOICE] Deep dive error: {e}")

def _process_voice_question():
    global _voice_conversation, _last_voice_sections
    try:
        if _voice_is_continuation and _last_voice_sections:
            ui_update_sections([("Processing", ["Transcribing..."])] + _last_voice_sections)
        else: ui_update(["[VOICE] Transcribing..."])
        stt_start = time.time()
        mic_transcript = transcribe_audio_file(_voice_mic_path)
        system_transcript = transcribe_audio_file(_voice_system_path)
        stt_time = time.time() - stt_start
        if not mic_transcript and not system_transcript: ui_update(["[VOICE] No speech detected."]); return
        combined_parts = []
        if system_transcript: combined_parts.append(f"[Interviewer]: {system_transcript}")
        if mic_transcript: combined_parts.append(f"[User]: {mic_transcript}")
        transcript = "\n".join(combined_parts) if combined_parts else ""
        turn_num = len(_voice_conversation) // 2 + 1
        if _voice_is_continuation and _last_voice_sections:
            ui_update_sections([("Processing", [f"Q{turn_num}: {transcript[:80]}...", "Asking Mini..."])] + _last_voice_sections)
        else: ui_update([f"[VOICE] Q{turn_num}: {transcript[:100]}...", "Asking Mini..."])
        if not _voice_conversation:
            system_context = build_voice_initial_system_context()
            messages = [{"role": "user", "content": build_voice_initial_prompt(_WORK_EXPERIENCE, transcript)}]
        else:
            system_context = build_voice_followup_system_context()
            messages = _voice_conversation + [{"role": "user", "content": transcript}]
        client = _openai_client()
        quick_start = time.time()
        response = client.responses.create(
            model=OPENAI_FAST_VOICE_MODEL,
            instructions=system_context,
            input=messages,
            reasoning=OPENAI_FAST_REASONING,
            max_output_tokens=4096,
            text={"verbosity": "low"},
        )
        quick_time = time.time() - quick_start
        answer = response.output_text.strip()
        _voice_conversation.append({"role": "user", "content": transcript})
        _voice_conversation.append({"role": "assistant", "content": answer})
        sections = []
        if len(_voice_conversation) > 2:
            history_lines = [f"Q{i//2 + 1}: {_voice_conversation[i]['content'][:50]}..." for i in range(0, len(_voice_conversation) - 2, 2)]
            sections.append(("Previous", history_lines))
        sections.append((f"Question {turn_num}", wrap_lines(transcript, CHARS_PER_LINE)))
        sections.append(("Answer", wrap_lines(answer, CHARS_PER_LINE)))
        sections.append(("Deep Dive", ["Loading..."]))
        _last_voice_sections = sections.copy()
        AppHelper.callAfter(ui_update_sections, sections)
        AppHelper.callAfter(_update_status, f"[VOICE] Turn {turn_num} | STT: {stt_time:.1f}s | Mini: {quick_time:.1f}s | Fetching deep dive...")
        threading.Thread(target=_fetch_voice_deep_dive, args=(transcript, answer, _voice_conversation.copy(), turn_num), daemon=True).start()
    except Exception as e: AppHelper.callAfter(ui_update, [f"[VOICE] Error: {e}"])

app = NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_((OVERLAY_ORIGIN, (window_width, window_height)), 15, 2, False)
window.setTitlebarAppearsTransparent_(True); window.setSharingType_(0)
window.setBackgroundColor_(NSColor.clearColor()); window.setOpaque_(False); window.setHasShadow_(False)
window.setAlphaValue_(1.0); window.setLevel_(NSFloatingWindowLevel); window.setIgnoresMouseEvents_(True); window.makeKeyAndOrderFront_(None)

class WindowDelegate(NSObject):
    def _report(self):
        frame = window.frame(); _update_status(f"origin=({int(frame.origin.x)}, {int(frame.origin.y)})  size=({int(frame.size.width)} x {int(frame.size.height)})")
    def windowDidResize_(self, notification): self._report()
    def windowDidMove_(self, notification): self._report()

delegate = WindowDelegate.alloc().init(); window.setDelegate_(delegate)

def _apply_interactive_mode():
    global _status_label
    if _interactive_mode:
        window.setIgnoresMouseEvents_(False); window.setHasShadow_(True); window.setMovableByWindowBackground_(True); window.setAlphaValue_(0.7)
        _ensure_status_label(); ui_update(["Interactive mode ON. Press F2 to turn OFF."]); delegate._report()
    else:
        window.setIgnoresMouseEvents_(True); window.setHasShadow_(False); window.setAlphaValue_(1.0)
        if _status_label is not None: _status_label.removeFromSuperview(); _status_label = None
        mode_label, midi_help = _mode_label_and_help(_current_mode)
        ui_update([f'[{mode_label}] Interactive OFF. {midi_help}'])

def toggle_interactive_mode():
    global _interactive_mode
    _interactive_mode = not _interactive_mode; AppHelper.callAfter(_apply_interactive_mode)

def toggle_overlay_visibility():
    global _overlay_hidden
    _overlay_hidden = not _overlay_hidden
    if _overlay_hidden:
        window.orderOut_(None)
    else:
        try: window.orderFrontRegardless()
        except Exception: window.makeKeyAndOrderFront_(None)
        mode_label, midi_help = _mode_label_and_help(_current_mode)
        _update_status(f"[{mode_label}] Visible | {midi_help}")

_ensure_window_width_for_columns()

def _load_behavioral_headline_key_points() -> List[str]:
    try:
        with open(_INTERVIEW_DIMENSION_MAPPING_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except Exception as e:
        return [f"Missing interview dimension mapping: {e}"]
    start = None
    for i, line in enumerate(lines):
        heading = line.strip().lower()
        if heading.startswith("## headline and key"):
            start = i + 1
            break
    if start is None:
        return ["Could not find the Headline and Key Ideas section."]
    body: List[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        cleaned = line.rstrip()
        if cleaned:
            body.append(cleaned)
        elif body and body[-1] != "":
            body.append("")
    return body or ["Headline and Key Ideas section is empty."]

mode_a_cheat_sheet = [
    _mode_section("mode_a", "Coding and DSA interview assistant for problem solving, continuations, and simplification."),
    ("Controls", ["47=Run/Clear  48=Switch Mode  49/F10=Flip Page  43=Hide/Show", "50=Continue  51=Simplify  39=Voice  38=Voice Follow-up", "40=Smaller 41=Bigger | Fn+Arrow or MIDI 42/44/45/46=Move", "F1=Quit  F2=Interactive"]),
    ("Optimal Solution", ["- Clarify inputs, outputs, constraints", "- Think aloud: brute force -> optimize", "- State time/space complexity", "- Test with examples before coding"]),
    ("Clarifying Questions", ["- Input size/range? Sorted? Duplicates?", "- Edge cases: empty, single, negative?", "- In-place or new structure?", "- Optimize for time or space?"]),
    ("Edge Cases", ["- Empty input, single element", "- All same values, sorted/reverse", "- Min/max int, overflow", "- Null, negative, zero"]),
    ("Test Cases", ["- Happy path, edge cases", "- Boundary conditions", "- Invalid inputs"]),
]

system_cheat_sheet = [
    _mode_section("system", "Backend/system architecture interview answer with requirements, APIs, data models, and deep dives."),
    ("Controls", ["47=Run/Clear  48=Switch Mode  49/F10=Flip Page  43=Hide/Show", "39=Voice  38=Voice Follow-up", "40=Smaller 41=Bigger | Fn+Arrow or MIDI 42/44/45/46=Move", "F1=Quit  F2=Interactive"]),
    ("Functional Requirements", ["- Define top 3 user/client actions ('Users should be able to...').", "- Ask clarifying PM-style questions.", "- Keep scope small; prioritize essentials over nice-to-have."]),
    ("Non-Functional Reqs", ["- Define system qualities ('The system should be...').", "- Pick 3-5 most relevant (not generic).", "- Quantify when possible (e.g., latency <200ms, 99.9% uptime).", "- Probe: availability vs consistency, scalability, latency, durability."]),
    ("Core Entities", ["- Identify main nouns/resources; refine later.", "- Ask: 'Who are the actors?' 'What data do we store?'"]),
    ("API Design", ["- REST by default; GraphQL for diverse clients; RPC for internal.", "- Format: POST /v1/resources, GET /v1/resources/{id}"]),
    ("High-Level Design", ["- API gateway/load balancer", "- Application servers", "- Databases and caches", "- Queues/streaming for async work", "- Background workers/services", "- Build step-by-step from endpoints"]),
    ("Deep Dives", ["- Address NFRs and bottlenecks", "- Scaling strategies, Caching, Sharding", "- Consistency models, Rate limiting", "- For each: problem, solution, tradeoffs, implementation"]),
]

pair_program_cheat_sheet = [
    ("Task Focus", [
        "First-round task: Red Planet Top Workplaces in README-Red-Planet.md.",
        "Ignore backend/frontend small-ticket READMEs unless the email says otherwise.",
        "Use --silent for output checks because required script output must be strict JSON only.",
    ]),
    ("Repo Setup", [
        "cd /Users/davidrussell/Documents/CodeScreen_zk3mh2ml",
        "nvm use",
        "Overlay target if launched elsewhere:",
        "PAIR_REPO_ROOT=/Users/davidrussell/Documents/CodeScreen_zk3mh2ml python3 lib/main.py",
    ]),
    ("Install", [
        "npm run setup",
        "Installs server and client dependencies. For the first task, mainly use server.",
        "If root tooling like Prettier is needed: npm install",
    ]),
    ("Database Setup", [
        "cd server",
        "npx prisma@6 migrate dev --name init",
        "",
        "Reset and reseed local dev DB:",
        "cd server",
        "npx prisma@6 migrate reset",
    ]),
    ("Run API Server", [
        "Terminal 1:",
        "cd server",
        "npm run start:dev",
        "Default API URL: http://localhost:3000",
    ]),
    ("Run Scripts", [
        "Terminal 2, from repo root:",
        "npm run start:topWorkplaces --silent",
        "npm run start:topWorkers --silent",
        "",
        "Or from server:",
        "npm run start:topWorkplaces --silent",
        "npm run start:topWorkers --silent",
        "",
        "Override API target:",
        "API_BASE_URL=http://localhost:3000 npm run start:topWorkplaces --silent",
    ]),
    ("Validate Scripts", [
        "With the API server already running:",
        "cd server",
        "npm run test:scripts",
        "Checks that both top scripts run and emit parseable JSON.",
    ]),
    ("Other Useful Commands", [
        "cd server",
        "npm run build",
        "npm run lint",
        "npm run test:e2e",
        "",
        "cd client",
        "npm run start:dev",
        "npm run build",
        "npm run lint",
    ]),
]

behavioral_pair_cheat_sheet = [
    _mode_section("behavioral_pair", "Audio-only behavioral interview matcher. Uses transcript plus interview_dimension_mapping.md; no screenshots."),
    ("Headline and Key Ideas", _load_behavioral_headline_key_points()),
]

_system_full_sections = system_cheat_sheet
_behavioral_pair_full_sections = behavioral_pair_cheat_sheet
_render_project_view()

def _flip_page():
    global _mode_a_view_mode, _system_view_mode, _pair_view_mode, _behavioral_pair_view_mode, _project_view_mode
    if _current_mode == "mode_a":
        _mode_a_view_mode = (_mode_a_view_mode + 1) % len(_MODE_A_PAGES)
    elif _current_mode == "pair":
        _pair_view_mode = (_pair_view_mode + 1) % len(_PAIR_PAGES)
    elif _current_mode == "behavioral_pair":
        _behavioral_pair_view_mode = (_behavioral_pair_view_mode + 1) % len(_BEHAVIORAL_PAIR_PAGES)
    elif _current_mode == "project":
        _ensure_project_pages_loaded()
        _project_view_mode = (_project_view_mode + 1) % _project_total_pages()
    else:
        _system_view_mode = (_system_view_mode + 1) % (len(_SYSTEM_PAGES) + 1)
    _rerender_current_view()

def _cleanup_audio():
    global _audio_stream, _is_recording, _system_audio_capture, _pair_audio_stream, _pair_system_audio_capture, _pair_active
    _is_recording = False
    _pair_active = False
    _pair_audio_stop.set()
    if _audio_stream:
        try: _audio_stream.stop(); _audio_stream.close()
        except: pass
        _audio_stream = None
    if _system_audio_capture:
        try: _system_audio_capture.stop()
        except: pass
        _system_audio_capture = None
    if _pair_audio_stream:
        try: _pair_audio_stream.stop(); _pair_audio_stream.close()
        except: pass
        _pair_audio_stream = None
    if _pair_system_audio_capture:
        try: _pair_system_audio_capture.stop()
        except: pass
        _pair_system_audio_capture = None

atexit.register(_cleanup_audio)

def _quit_app():
    _cleanup_audio()
    def _close(): window.close(); AppHelper.stopEventLoop()
    AppHelper.callAfter(_close)

def on_release(key):
    global _awaiting_second, _mode_a_results_shown, _system_results_shown, _mode_a_view_mode, _system_view_mode
    global _mode_a_conversation_history, _mode_a_turn_count, _project_view_mode
    try:
        if key == keyboard.Key.f1: _quit_app(); return False
        if key == keyboard.Key.f2: toggle_interactive_mode(); return
        if key == keyboard.Key.f10: _flip_page(); return
        if key in _FN_ARROW_MOVES:
            _move_window(window, _FN_ARROW_MOVES[key])
            return
        vk = _extract_vk(key)
        if vk is None: return
        if vk == _FN_VK:
            _awaiting_second = False
            return
    except Exception as e:
        _log_runtime_error("keyboard handler", e)
        ui_update([f"Key handling error: {e}"])

listener = keyboard.Listener(on_release=on_release); listener.start()

def _switch_mode():
    global _current_mode, _behavioral_pair_showing_cheat
    previous_mode = _current_mode
    _current_mode = _next_mode(_current_mode)
    switched_between_audio_modes = _is_pair_audio_mode(previous_mode) and _is_pair_audio_mode(_current_mode) and previous_mode != _current_mode
    if _is_pair_audio_mode(previous_mode) and previous_mode != _current_mode:
        stop_pair_mode(render_status=not switched_between_audio_modes)
    mode_label, midi_help = _mode_label_and_help(_current_mode)
    if _current_mode == "mode_a":
        if _mode_a_results_shown and _mode_a_full_sections: _rerender_current_view()
        else: ui_update_sections(mode_a_cheat_sheet); AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")
    elif _current_mode == "pair":
        if switched_between_audio_modes:
            ui_update_sections([("Status", ["Switching audio mode..."])])
            threading.Timer(0.7, lambda: AppHelper.callAfter(start_pair_mode)).start()
        elif not _pair_active: start_pair_mode()
        else: _render_pair_view()
    elif _current_mode == "behavioral_pair":
        _behavioral_pair_showing_cheat = True
        ui_update_sections(behavioral_pair_cheat_sheet)
        if switched_between_audio_modes:
            AppHelper.callAfter(_update_status, f"[{mode_label}] Switching audio mode... | {midi_help}")
            threading.Timer(0.7, lambda: AppHelper.callAfter(start_pair_mode, False)).start()
        elif not _pair_active: start_pair_mode(capture_initial=False)
        else: AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")
    elif _current_mode == "project":
        _render_project_view()
    else:
        if _system_results_shown and _system_full_sections: _rerender_current_view()
        else: ui_update_sections(system_cheat_sheet); AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")

_last_midi_ts = {}

def _midi_run_or_clear():
    global _mode_a_results_shown, _system_results_shown, _mode_a_view_mode, _system_view_mode, _project_view_mode
    global _mode_a_conversation_history, _mode_a_turn_count
    if _current_mode == "mode_a":
        if _mode_a_results_shown:
            _mode_a_results_shown, _mode_a_view_mode, _mode_a_conversation_history, _mode_a_turn_count = False, 0, [], 0
            ui_update_sections(mode_a_cheat_sheet); _update_status(f"[DSA] Cleared | {_MIDI_HELP_DSA}"); return
    elif _current_mode == "pair":
        pair_process_context(); return
    elif _current_mode == "behavioral_pair":
        behavioral_pair_process_context(); return
    elif _current_mode == "project":
        _project_view_mode = 0
        _render_project_view()
        return
    else:
        if _system_results_shown:
            _system_results_shown, _system_view_mode = False, 0
            ui_update_sections(system_cheat_sheet); _update_status(f"[SYSTEM] Cleared | {_MIDI_HELP_SYSTEM}"); return
    if _is_running.is_set(): ui_update(["Already running..."]); return
    _is_running.set(); threading.Thread(target=run_pipeline, daemon=True).start()

def on_midi(msg):
    global _last_midi_ts, _mode_a_conversation_history, _mode_a_turn_count
    try:
        note = _midi_note(msg)
        if note is None: return
        if _is_debounced_note(note, _last_midi_ts): return
        if note == 47: AppHelper.callAfter(_midi_run_or_clear)
        elif note == 48: AppHelper.callAfter(_switch_mode)
        elif note == 49: AppHelper.callAfter(_flip_page)
        elif note == 43: AppHelper.callAfter(toggle_overlay_visibility)
        elif note == 50:
            if _current_mode == "pair": AppHelper.callAfter(pair_capture_screen); return
            if _current_mode == "behavioral_pair": AppHelper.callAfter(_set_pair_status, "screenshots disabled in behavioral mode"); return
            if _current_mode != "mode_a": AppHelper.callAfter(ui_update, ["Continue only available in DSA mode."]); return
            if not _mode_a_conversation_history: AppHelper.callAfter(ui_update, ["No conversation to continue. Run first (47)."]); return
            if _is_running.is_set(): AppHelper.callAfter(ui_update, ["Already running..."]); return
            _is_running.set(); threading.Thread(target=continue_mode_a, daemon=True).start()
        elif note == 51:
            if _current_mode == "pair": AppHelper.callAfter(clear_pair_context); return
            if _current_mode == "behavioral_pair": AppHelper.callAfter(clear_behavioral_pair_context); return
            if _current_mode != "mode_a": AppHelper.callAfter(ui_update, ["Simplify only available in DSA mode."]); return
            if not _mode_a_conversation_history: AppHelper.callAfter(ui_update, ["No conversation to simplify. Run first (47)."]); return
            if _is_running.is_set(): AppHelper.callAfter(ui_update, ["Already running..."]); return
            _is_running.set(); threading.Thread(target=simplify_mode_a, daemon=True).start()
        elif note in _MIDI_MOVE_DIRECTIONS:
            AppHelper.callAfter(_move_window, window, _MIDI_MOVE_DIRECTIONS[note])
        elif note in _MIDI_RESIZE_BIGGER:
            AppHelper.callAfter(_resize_window, window, _MIDI_RESIZE_BIGGER[note])
        elif note == 39:
            if _is_recording: AppHelper.callAfter(stop_recording_and_ask)
            else: AppHelper.callAfter(start_recording, False)
        elif note == 38:
            if _is_recording: AppHelper.callAfter(stop_recording_and_ask)
            elif not _voice_conversation: AppHelper.callAfter(ui_update, ["[VOICE] No conversation yet. Press 39 for new question."])
            else: AppHelper.callAfter(start_recording, True)
    except Exception as e:
        _log_runtime_error("MIDI handler", e)
        AppHelper.callAfter(ui_update, [f"MIDI error: {e}"])

def _open_midi_port():
    try:
        names = mido.get_input_names()
        if not names:
            msg = "No MIDI input ports found. Connect the MIDI controller and restart system2.py."
            print(msg)
            AppHelper.callAfter(ui_update, [msg])
            return None
        return mido.open_input(names[0], callback=on_midi)
    except Exception as e:
        _log_runtime_error("MIDI open", e)
        AppHelper.callAfter(ui_update, [f"MIDI open error: {e}"])
        return None

midi_port = _open_midi_port()
try:
    AppHelper.runEventLoop()
finally:
    if midi_port: midi_port.close()
