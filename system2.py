import os, json, queue, traceback

import time, threading, mido, base64, atexit
from typing import List, Tuple, Optional, Dict
import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image
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
_product_view_mode, _product_results_shown, _product_full_sections, _product_diagram_path = 0, False, None, None
_product_diagram_status, _product_diagram_started_at = "idle", None
_pair_view_mode, _pair_full_sections = 0, None
_project_view_mode, _project_text_pages = 0, []
_is_recording, _audio_data, _transcription_model, _system_audio_capture = False, [], None, None
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

class UserJourneyStep(BaseModel):
    step: int; actor: str; action: str; system_component: str; result: str

class Tradeoff(BaseModel):
    decision: str; options_considered: List[str]; chosen_option: str; rationale: str

class EdgeCase(BaseModel):
    scenario: str; handling: str

class FrontendDesign(BaseModel):
    surfaces: List[str]; client_state: Optional[str] = None; realtime_or_offline_behavior: Optional[str] = None; error_states: Optional[List[str]] = None

class LLMDesign(BaseModel):
    needed: bool; use_cases: Optional[List[str]] = None; components: Optional[List[str]] = None; risks: Optional[List[str]] = None; mitigations: Optional[List[str]] = None

class ProductSystemDesignResponse(BaseModel):
    functional_requirements: List[str]; non_functional_requirements: List[str]; capacity_estimation: Optional[CapacityEstimation] = None
    user_journey: List[UserJourneyStep]; frontend_design: Optional[FrontendDesign] = None; llm_design: Optional[LLMDesign] = None
    core_entities: List[str]; api_design: ApiDesign; data_flow: Optional[List[str]] = None; high_level_design: HighLevelDesign
    data_models: List[DataModel]; component_descriptions: Optional[List[ComponentDescription]] = None
    tradeoffs: List[Tradeoff]; edge_cases: List[EdgeCase]; deep_dives: List[DeepDive]

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

class PairProcessResponse(BaseModel):
    summary: str
    likely_request: str
    recommendation: str
    code_changes: List[str]
    questions: List[str]

def take_screenshot() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S"); ms = int((time.time() % 1) * 1000); fn = f"screenshot_{ts}_{ms:03d}.png"
    pyautogui.screenshot(region=SCREENSHOT_REGION).save(fn); return fn

def ocr_with_tesseract(image_path: str) -> str:
    try: return pytesseract.image_to_string(Image.open(image_path)).strip()
    except Exception as e: return f"(OCR error: {e})"

def _mk_color(r, g, b, a=0.9): return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)

SECTION_COLOR = {
    "Mode": _mk_color(20, 70, 160),
    "Functional Requirements": _mk_color(25, 60, 140), "Non-Functional Reqs": _mk_color(0, 110, 140),
    "Capacity Estimation": _mk_color(120, 40, 80), "Core Entities": _mk_color(0, 100, 80),
    "API Design": _mk_color(0, 85, 150), "Data Flow": _mk_color(90, 60, 0), "High-Level Design": _mk_color(90, 0, 140),
    "Data Models": _mk_color(0, 110, 90), "Component Descriptions": _mk_color(140, 50, 0), "Deep Dives": _mk_color(140, 0, 60),
    "User Journey": _mk_color(0, 100, 130), "Frontend Design": _mk_color(90, 0, 140), "Tradeoffs": _mk_color(120, 40, 80),
    "Edge Cases": _mk_color(0, 110, 90), "LLM Design": _mk_color(100, 60, 160),
    "Controls": _mk_color(80, 80, 80), "Voice Question": _mk_color(0, 100, 130), "Previous": _mk_color(100, 100, 100),
    "Answer": _mk_color(0, 130, 80), "Deep Dive": _mk_color(100, 60, 160), "Diagram": _mk_color(60, 60, 140), "Status": _mk_color(200, 120, 0), "status:": _mk_color(45, 45, 45),
    "Clarifying Questions": _mk_color(25, 60, 140), "Optimal Solution": _mk_color(90, 0, 140),
    "Test Cases": _mk_color(0, 85, 150), "Limitations": _mk_color(120, 40, 80),
    "Pair Controls": _mk_color(80, 80, 80), "Pair Status": _mk_color(200, 120, 0), "Context Files": _mk_color(0, 85, 150),
    "Terminal Output": _mk_color(90, 60, 0), "Transcript": _mk_color(0, 100, 130), "Pair Response": _mk_color(0, 130, 80),
    "Pair Details": _mk_color(100, 60, 160), "Notes": _mk_color(100, 60, 160), "Open Questions": _mk_color(140, 0, 60),
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

def call_openai_product_system(ocr_text: str, image_path: Optional[str]) -> Optional[ProductSystemDesignResponse]:
    try: client = _openai_client()
    except Exception as e:
        _record_openai_error("OpenAI PRODUCT", e)
        return None
    messages = [{"role": "user", "content": build_product_system_prompt(ocr_text)}]
    try:
        response = client.responses.parse(
            model=OPENAI_REASONING_MODEL,
            input=_openai_input(messages, image_path=image_path),
            text_format=ProductSystemDesignResponse,
            reasoning=OPENAI_HIGH_REASONING,
            max_output_tokens=36000,
        )
        return response.output_parsed
    except Exception as e:
        _record_openai_error("OpenAI PRODUCT", e)
        return None

def generate_product_diagram_async(data: ProductSystemDesignResponse):
    global _product_diagram_path, _product_diagram_status, _product_diagram_started_at
    _product_diagram_status, _product_diagram_started_at = "generating", time.time()
    try:
        client = _openai_client()
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=build_product_system_diagram_prompt(data),
            size="1536x1024",
            quality="high",
            output_format="png",
        )
        if response.data and response.data[0].b64_json:
            path = os.path.abspath(f"diagram_product_{time.strftime('%Y%m%d_%H%M%S')}.png")
            with open(path, "wb") as f: f.write(base64.b64decode(response.data[0].b64_json))
            _product_diagram_path, _product_diagram_status = path, "ready"
            if _current_mode == "product" and _product_view_mode == len(_PRODUCT_PAGES): AppHelper.callAfter(_show_product_diagram_view)
            return
        _product_diagram_status = "failed"
        _record_openai_error("OpenAI PRODUCT IMAGE", RuntimeError("image response did not include b64_json data"))
    except Exception as e:
        _product_diagram_status = "failed"
        _record_openai_error("OpenAI PRODUCT IMAGE", e)
    finally:
        if _current_mode == "product" and _product_view_mode == len(_PRODUCT_PAGES): AppHelper.callAfter(_show_product_diagram_view)

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
_pair_last_response: Optional[PairProcessResponse] = None
_pair_last_response_path: Optional[str] = None
_pair_status = "idle"
_pair_process_running = threading.Event()

def _model_to_dict(model):
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()

def _pair_context_json() -> str:
    with _pair_context_lock:
        data = _model_to_dict(_pair_context)
    return json.dumps(data, indent=2)

def _set_pair_status(status: str):
    global _pair_status
    _pair_status = status
    if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)

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

def _save_pair_response(response: PairProcessResponse) -> str:
    path = _pair_session_path(f"pair_response_{time.strftime('%Y%m%d_%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(_model_to_dict(response), f, indent=2)
    return path

def _capture_screen_without_overlay(path: str):
    was_hidden = _overlay_hidden
    if not was_hidden:
        window.orderOut_(None)
        time.sleep(0.08)
    try:
        pyautogui.screenshot(region=SCREENSHOT_REGION).save(path)
    finally:
        if not was_hidden:
            try: window.orderFrontRegardless()
            except Exception: window.makeKeyAndOrderFront_(None)
            time.sleep(0.02)

def take_pair_screenshot() -> str:
    ts = time.strftime("%Y%m%d_%H%M%S"); ms = int((time.time() % 1) * 1000)
    path = _pair_session_path(f"capture_{ts}_{ms:03d}.png")
    _capture_screen_without_overlay(path)
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
    if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)

def _persist_pair_audio_chunk(mic_audio: Optional[np.ndarray], system_audio: Optional[np.ndarray], session_dir: Optional[str] = None, elapsed: Optional[str] = None):
    global _pair_audio_chunk_index
    if mic_audio is None and system_audio is None: return
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
        _write_audio_chunk(mic_path, mic_audio)
    if system_audio is not None and len(system_audio) > 0:
        system_path = _audio_path(f"audio_{idx:04d}_system.wav")
        _write_audio_chunk(system_path, system_audio)
    try:
        mic_text = transcribe_audio_file(mic_path, show_status=False)
        system_text = transcribe_audio_file(system_path, show_status=False)
        _append_pair_transcript(idx, mic_text, system_text, mic_path, system_path, elapsed=elapsed)
        _set_pair_status(f"audio chunk {idx} transcribed")
    except Exception as e:
        _log_runtime_error("pair audio transcription", e)
        _set_pair_status(f"audio transcription error: {e}")

def _pair_flush_audio_chunk(block: bool = False):
    if not _pair_active: return
    if not _pair_audio_flush_lock.acquire(blocking=block):
        _set_pair_status("audio flush already running; using transcript so far")
        return
    try:
        _persist_pair_audio_chunk(_drain_pair_mic_audio(), _pair_system_audio_capture.drain() if _pair_system_audio_capture else None)
    finally:
        _pair_audio_flush_lock.release()

def _pair_audio_loop():
    while not _pair_audio_stop.wait(_PAIR_AUDIO_CHUNK_SEC):
        _pair_flush_audio_chunk()
    _pair_flush_audio_chunk()

def call_openai_pair_context(ocr_text: str, image_path: Optional[str]) -> Optional[PairContextUpdate]:
    try: client = _openai_client()
    except Exception as e:
        _record_openai_error("OpenAI PAIR CONTEXT", e)
        return None
    prompt = build_pair_context_prompt(_pair_context_json(), ocr_text)
    try:
        response = client.responses.parse(
            model=OPENAI_PAIR_CONTEXT_MODEL,
            instructions=_PAIR_CONTEXT_INSTRUCTIONS,
            input=_openai_input([{"role": "user", "content": prompt}], image_path=image_path),
            text_format=PairContextUpdate,
            reasoning=OPENAI_FAST_REASONING,
            max_output_tokens=16000,
        )
        return response.output_parsed
    except Exception as e:
        _record_openai_error("OpenAI PAIR CONTEXT", e)
        return None

def _pair_capture_worker():
    global _pair_capture_worker_active, _pair_context, _pair_processed_capture_count
    try:
        while True:
            try: capture = _pair_capture_queue.get_nowait()
            except queue.Empty: break
            try:
                if capture.get("generation") != _pair_generation: continue
                _set_pair_status(f"processing capture {capture['index']}")
                ocr_text = ocr_with_tesseract(capture["path"])
                updated = call_openai_pair_context(ocr_text, image_path=capture["path"])
                if capture.get("generation") != _pair_generation: continue
                if updated:
                    with _pair_context_lock: _pair_context = updated
                    _pair_processed_capture_count += 1
                    _set_pair_status(f"capture {capture['index']} merged")
                else:
                    _set_pair_status(_last_openai_error or f"capture {capture['index']} failed")
            except Exception as e:
                _log_runtime_error("pair capture worker", e)
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

def _wait_for_pair_captures(timeout_sec: float) -> bool:
    deadline = time.time() + timeout_sec
    while _pair_pending_capture_count() > 0:
        _ensure_pair_capture_worker()
        if time.time() >= deadline: return False
        time.sleep(0.1)
    return True

def pair_capture_screen():
    global _pair_capture_count
    if _current_mode != "pair":
        _set_pair_status("pair capture only runs in pair mode")
        return
    if not _pair_active:
        start_pair_mode()
        return
    try:
        shot = take_pair_screenshot()
        _pair_capture_count += 1
        _pair_capture_queue.put({"index": _pair_capture_count, "path": shot, "ts": time.time(), "generation": _pair_generation})
        _set_pair_status(f"queued capture {_pair_capture_count}")
        _ensure_pair_capture_worker()
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
    try: client = _openai_client()
    except Exception as e:
        _record_openai_error("OpenAI PAIR PROCESS", e)
        return None
    prompt = build_pair_process_prompt(_pair_context_json(), _pair_transcript_text())
    try:
        response = client.responses.parse(
            model=OPENAI_PAIR_PROCESS_MODEL,
            input=[{"role": "user", "content": prompt}],
            text_format=PairProcessResponse,
            reasoning=OPENAI_HIGH_REASONING,
            max_output_tokens=16000,
        )
        return response.output_parsed
    except Exception as e:
        _record_openai_error("OpenAI PAIR PROCESS", e)
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
    _pair_view_mode = 0
    def _run():
        global _pair_last_response, _pair_last_response_path, _pair_process_count, _pair_view_mode
        _pair_process_running.set()
        try:
            _set_pair_status("processing accumulated context")
            pending = _pair_pending_capture_count()
            if pending > 0:
                _set_pair_status(f"waiting up to {_PAIR_CAPTURE_WAIT_SEC:.0f}s for {pending} captures")
                _ensure_pair_capture_worker()
                if not _wait_for_pair_captures(_PAIR_CAPTURE_WAIT_SEC):
                    _set_pair_status(f"capture merge still running; processing merged context so far")
            _pair_flush_audio_chunk(block=False)
            response = call_openai_pair_process()
            if response:
                _pair_last_response = response
                _pair_last_response_path = _save_pair_response(response)
                _pair_process_count += 1
                _pair_view_mode = 0
                _set_pair_status(f"process {_pair_process_count} complete")
            else:
                _set_pair_status(_last_openai_error or "process failed")
        except Exception as e:
            _log_runtime_error("pair process", e)
            _set_pair_status(f"process error: {e}")
        finally:
            _pair_process_running.clear()
            if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)
    threading.Thread(target=_run, daemon=True).start()

def start_pair_mode():
    global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at, _pair_session_dir, _pair_view_mode
    if _current_mode != "pair":
        _set_pair_status("pair recording only starts in pair mode")
        return
    _pair_view_mode = 0
    if _pair_active:
        if _current_mode == "pair": AppHelper.callAfter(_render_pair_view)
        return
    _pair_active = True
    _pair_started_at = time.time()
    _pair_audio_stop.clear()
    _pair_session_dir = os.path.abspath(f"pair_session_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(_pair_session_dir, exist_ok=True)
    try:
        _pair_system_audio_capture = SystemAudioCapture(); _pair_system_audio_capture.start()
    except Exception as e:
        _pair_system_audio_capture = None
        _set_pair_status(f"system audio unavailable: {e}")
    try:
        mic_device = _find_microphone_device()
        if mic_device is not None:
            _pair_audio_stream = sd.InputStream(device=mic_device, samplerate=_SAMPLE_RATE, channels=1, dtype='float32', callback=_pair_audio_callback, blocksize=1024)
            _pair_audio_stream.start()
    except Exception as e:
        _pair_audio_stream = None
        _set_pair_status(f"mic unavailable: {e}")
    _pair_audio_thread = threading.Thread(target=_pair_audio_loop, daemon=True)
    _pair_audio_thread.start()
    _set_pair_status("started; queued initial capture")
    pair_capture_screen()

def stop_pair_mode():
    global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at
    if not _pair_active: return
    session_dir, elapsed = _pair_session_dir, _pair_elapsed()
    _set_pair_status("stopping pair recording")
    _pair_active = False
    _pair_audio_stop.set()

    def _stop():
        global _pair_active, _pair_audio_stream, _pair_system_audio_capture, _pair_audio_thread, _pair_started_at
        try:
            if _pair_audio_flush_lock.acquire(blocking=True):
                try:
                    mic_audio = system_audio = None
                    if _pair_audio_stream:
                        try: _pair_audio_stream.stop(); _pair_audio_stream.close()
                        except Exception as e: _log_runtime_error("pair mic stop", e)
                        _pair_audio_stream = None
                    if _pair_system_audio_capture:
                        try: system_audio = _pair_system_audio_capture.stop()
                        except Exception as e: _log_runtime_error("pair system audio stop", e)
                        _pair_system_audio_capture = None
                    mic_audio = _drain_pair_mic_audio()
                    _persist_pair_audio_chunk(mic_audio, system_audio, session_dir=session_dir, elapsed=elapsed)
                finally:
                    _pair_audio_flush_lock.release()
            _pair_started_at = None
            _pair_audio_thread = None
            _set_pair_status("pair recording stopped")
        except Exception as e:
            _log_runtime_error("pair stop", e)
            _pair_active = False
            _set_pair_status(f"pair stop error: {e}")
    threading.Thread(target=_stop, daemon=True).start()

def clear_pair_context():
    global _pair_context, _pair_transcript_segments, _pair_last_response, _pair_last_response_path, _pair_capture_count, _pair_processed_capture_count, _pair_process_count, _pair_audio_chunk_index, _pair_session_dir, _pair_started_at, _pair_generation
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
            try: _pair_capture_queue.task_done()
            except ValueError as e: _log_runtime_error("pair clear task_done", e)
        except queue.Empty:
            break
    _set_pair_status("context cleared; recording still running" if _pair_active else "context cleared")

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
    x_cols = [LEFT_MARGIN, LEFT_MARGIN + col_width + GUTTER]
    y, col = [available_h - TOP_MARGIN for _ in range(COLUMNS)], 0
    for title, lines in sections:
        color = SECTION_COLOR.get(title, _mk_color(45, 45, 45))
        if y[col] - TITLE_HEIGHT < BOTTOM_MARGIN:
            col += 1
            if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); break
            y[col] = available_h - TOP_MARGIN
        _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
        for line in lines:
            if y[col] - LINE_HEIGHT < BOTTOM_MARGIN:
                col += 1
                if col >= COLUMNS: _place_label("…", x_cols[-1], BOTTOM_MARGIN, col_width, LINE_HEIGHT, LINE_FONT, _mk_color(45, 45, 45)); return
                y[col] = available_h - TOP_MARGIN
                _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
            _place_label(line, x_cols[col], y[col], col_width, LINE_HEIGHT, LINE_FONT, color); y[col] -= LINE_HEIGHT

def ui_update(lines: List[str]): AppHelper.callAfter(_update_overlay_sections, [('status:', lines)])
def ui_update_sections(sections: List[Tuple[str, List[str]]]): AppHelper.callAfter(_update_overlay_sections, sections)

def format_pair_for_overlay_sections() -> List[Tuple[str, List[str]]]:
    with _pair_context_lock:
        ctx = _model_to_dict(_pair_context)
    files = ctx.get("files", []) or []
    terminal = ctx.get("terminal_output", []) or []
    notes = ctx.get("notes", []) or []
    questions = ctx.get("open_questions", []) or []
    sections: List[Tuple[str, List[str]]] = [_mode_section("pair", "Pair coding mode with screenshot context, transcript chunks, and process responses. Audio records only while in this mode.")]
    sections.append(("Pair Controls", [
        "47=Process  48=Switch Mode  49=Flip Page  50=Capture",
        "51=Clear Context  43=Hide/Show  F1=Quit  F2=Interactive",
        f"Audio chunk: {_PAIR_AUDIO_CHUNK_SEC}s",
    ]))
    sections.append(("Pair Status", [
        f"Active: {'yes' if _pair_active else 'no'}  Elapsed: {_pair_elapsed()}",
        f"Captures: {_pair_processed_capture_count}/{_pair_capture_count} processed  Queue: {_pair_capture_queue.qsize()}",
        f"Transcript chunks: {len(_pair_transcript_segments)}  Process runs: {_pair_process_count}",
        f"Status: {_pair_status}",
    ]))
    file_lines = []
    for f in files[:12]:
        content = f.get("content", "") or ""
        line_count = len(content.splitlines())
        lang = f.get("language") or "text"
        file_lines += wrap_lines(f"- {f.get('path', '(unknown path)')} ({lang}, {line_count} lines)", CHARS_PER_LINE)
    if len(files) > 12: file_lines.append(f"... {len(files) - 12} more files")
    sections.append(("Context Files", file_lines or ["No file context yet."]))
    transcript_lines = []
    for seg in _pair_transcript_segments[-8:]:
        if seg.get("system"):
            _extend_wrapped_limited(transcript_lines, seg["system"], 36, f"[{seg['elapsed']}] Interviewer: ")
        if seg.get("mic"):
            _extend_wrapped_limited(transcript_lines, seg["mic"], 36, f"[{seg['elapsed']}] User: ")
    sections.append(("Transcript", transcript_lines or ["No transcript chunks yet."]))
    terminal_lines = []
    for item in terminal[-12:]:
        _extend_wrapped_limited(terminal_lines, item, 36, "- ")
        if len(terminal_lines) >= 36: break
    sections.append(("Terminal Output", terminal_lines or ["No terminal output captured yet."]))
    note_lines = []
    if ctx.get("summary"): _extend_wrapped_limited(note_lines, ctx.get("summary"), 12, "Summary: ")
    for item in notes[-10:]:
        _extend_wrapped_limited(note_lines, item, 28, "- ")
        if len(note_lines) >= 28: break
    sections.append(("Notes", note_lines or ["No notes yet."]))
    question_lines = []
    for q in questions[-8:]:
        _extend_wrapped_limited(question_lines, q, 16, "- ")
        if len(question_lines) >= 16: break
    sections.append(("Open Questions", question_lines or ["No open questions yet."]))
    response_lines = [f"[STATUS]: {_pair_status}"]
    if _pair_last_response:
        resp = _model_to_dict(_pair_last_response)
        response_lines.append("Suggested code changes:")
        for c in resp.get("code_changes", []):
            response_lines += wrap_lines(f"- {c}", CHARS_PER_LINE)
        sections.append(("Pair Response", response_lines))
        detail_lines = []
        detail_lines += wrap_lines(f"Summary: {resp.get('summary', '')}", CHARS_PER_LINE)
        detail_lines += wrap_lines(f"Likely request: {resp.get('likely_request', '')}", CHARS_PER_LINE)
        detail_lines += wrap_lines(f"Recommendation: {resp.get('recommendation', '')}", CHARS_PER_LINE)
        detail_lines.append("Questions:")
        detail_lines += [*sum((wrap_lines(f"- {q}", CHARS_PER_LINE) for q in resp.get("questions", [])), [])]
        if _pair_last_response_path: detail_lines += wrap_lines(f"Full response: {_pair_last_response_path}", CHARS_PER_LINE)
        sections.append(("Pair Details", detail_lines))
    else:
        response_lines.append("Press 47 to process accumulated context.")
        sections.append(("Pair Response", response_lines))
        sections.append(("Pair Details", ["No process response yet."]))
    return sections

def _render_pair_view():
    global _pair_full_sections
    _pair_full_sections = format_pair_for_overlay_sections()
    _update_overlay_sections(_get_page_sections(_pair_full_sections, _pair_view_mode, "pair"))
    _update_status(f"[PAIR] Page {_pair_view_mode + 1}/{len(_PAIR_PAGES)} | 47=process 50=capture 51=clear 43=hide/show")

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
    pages = _MODE_A_PAGES if mode == "mode_a" else _PAIR_PAGES if mode == "pair" else _PRODUCT_PAGES if mode == "product" else _SYSTEM_PAGES
    if page >= len(pages): return []
    return [s for s in sections if s[0] in set(pages[page])]

def _show_product_diagram_view():
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame(); page_num, total_pages = len(_PRODUCT_PAGES) + 1, len(_PRODUCT_PAGES) + 1
    if _product_diagram_path and os.path.exists(_product_diagram_path):
        img = NSImage.alloc().initWithContentsOfFile_(_product_diagram_path)
        if img:
            img_view = NSImageView.alloc().initWithFrame_(((10, 10), (frame.size.width - 20, frame.size.height - 60)))
            img_view.setImage_(img); img_view.setImageScaling_(NSImageScaleProportionallyUpOrDown)
            window.contentView().addSubview_(img_view)
            _update_status(f"[PRODUCT] Diagram ({page_num}/{total_pages}) | {_MIDI_HELP_PRODUCT}"); return
    elapsed = int(time.time() - _product_diagram_started_at) if _product_diagram_started_at else 0
    if _product_diagram_status == "failed":
        msg = _last_openai_error or "Diagram generation failed."
        _place_label(msg[:220], 20, frame.size.height - 50, frame.size.width - 40, TITLE_HEIGHT, TITLE_FONT, _mk_color(140, 0, 60))
        _update_status(f"[PRODUCT] Diagram failed ({page_num}/{total_pages}) | {_MIDI_HELP_PRODUCT}"); return
    if _product_diagram_status == "generating" and elapsed > 180:
        msg = f"Diagram still generating after {elapsed}s. It may be slow or stalled."
        _place_label(msg, 20, frame.size.height - 50, frame.size.width - 40, TITLE_HEIGHT, TITLE_FONT, _mk_color(200, 120, 0))
        _update_status(f"[PRODUCT] Diagram slow ({page_num}/{total_pages}) | {_MIDI_HELP_PRODUCT}"); return
    _place_label(f"Diagram generating... {elapsed}s", 20, frame.size.height - 50, 500, TITLE_HEIGHT, TITLE_FONT, _mk_color(60, 60, 140))
    _update_status(f"[PRODUCT] Diagram ({page_num}/{total_pages}) generating... | {_MIDI_HELP_PRODUCT}")

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
    elif _current_mode == "project":
        AppHelper.callAfter(_render_project_view)
        return
    elif _current_mode == "product":
        pages, view_mode, full_sections = _PRODUCT_PAGES, _product_view_mode, _product_full_sections
        total_pages, midi_help = len(pages) + 1, _MIDI_HELP_PRODUCT
    else:
        pages, view_mode, full_sections = _SYSTEM_PAGES, _system_view_mode, _system_full_sections
        total_pages, midi_help = len(pages) + 1, _MIDI_HELP_SYSTEM
    mode_label = "DSA" if _current_mode == "mode_a" else "PRODUCT" if _current_mode == "product" else "SYSTEM"
    if view_mode < len(pages):
        if full_sections:
            AppHelper.callAfter(_update_overlay_sections, _get_page_sections(full_sections, view_mode, _current_mode))
            AppHelper.callAfter(_update_status, f"[{mode_label}] Page {view_mode + 1}/{total_pages} | {midi_help}")
    else:
        AppHelper.callAfter(_show_product_diagram_view if _current_mode == "product" else _show_diagram_view)

def run_pipeline():
    global _mode_a_results_shown, _system_results_shown, _product_results_shown, _mode_a_view_mode, _system_view_mode, _product_view_mode
    global _mode_a_full_sections, _system_full_sections, _product_full_sections, _system_diagram_path, _product_diagram_path
    global _mode_a_conversation_history, _mode_a_turn_count, _last_openai_error, _system_diagram_status, _system_diagram_started_at, _product_diagram_status, _product_diagram_started_at
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
    if mode == "mode_a":
        _mode_a_view_mode, _mode_a_conversation_history, _mode_a_turn_count = 0, [], 1
        cheat = mode_a_cheat_sheet
    elif mode == "product":
        _product_diagram_path, _product_view_mode = None, 0
        _product_diagram_status, _product_diagram_started_at = "idle", None
        cheat = product_system_cheat_sheet
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
        elif mode == "product":
            data = call_openai_product_system(ocr_text, image_path=shot)
            if data:
                sections = format_product_system_for_overlay_sections(data)
                def _render():
                    global _product_full_sections
                    _product_full_sections = sections; _update_overlay_sections(sections); _update_status(f"[{mode_label}] All sections | {_MIDI_HELP_PRODUCT}")
                AppHelper.callAfter(_render); _product_results_shown = True
                threading.Thread(target=generate_product_diagram_async, args=(data,), daemon=True).start()
            else:
                ui_update([_last_openai_error or f"[{mode_label}] (No structured response)"]); _product_results_shown = False
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
        elif mode == "product": _product_results_shown = False
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

_WORK_EXPERIENCE = """Work Experience
Corbalt June 2024 - Present
Lead Software Engineer
• Log-forwarding automation: Built an orchestration, provisioning, and health automation system for Drupal
sites hosted on Acquia, deployed on ECS with TLS (TCP-SSL) and API authentication.
• Deployment reliability: Developed an ECS deployment tool to coordinate multi-service rollouts and enable
safer rollbacks during production changes.
• Platform tooling: Delivered developer experience and platform operations tools in Go on AWS for a CMS
infrastructure program.

ATX LED March 2023 – June 2024
Principal Software Engineer (Contract)
Coordinated work integrations and feature development with software development teams in Turkey,
prototype work with firmware engineers in romania, 
and prototype work with hardware manufacturers / firmware engineers in china

• IoT control plane: Led design and development of a home automation server integrating cloud connectivity
with local device control.
• Hardware integrations: Implemented Python-based I/O integrations using GPIO and UART for physical
device interfaces (e.g., DMX).
• Lighting protocols: Implemented DALI (Digital Addressable Lighting Interface) support and device control
workflows.
Amazon Web Services (AWS) Feb 2021 – March 2023
Software Engineer
• Service onboarding automation: Built a framework enabling customers to define data models and APIs via
an internal federated API platform (SUDS), reducing setup from several days to ~1 hour.
• Extensible permissions model: Developed a permissions framework allowing customers to bring their own
datasets, schemas, APIs, and permission schemas.
• Integration testing enablement: Built an extensible integration testing framework to mock SUDS data
types/features across lower and production environments when direct access wasn’t feasible.
• Conditional authorization: Implemented a contingent authorization framework to support conditional access
decisions while maintaining an authorization trail at scale.
Amazon Web Services (AWS) May 2020 - Aug 2020
Software Engineer Intern
• Data access auditing: Built a platform for auditing access to marketing and sales data to improve
traceability of sensitive data usage.
• Investigator UI: Developed the front-end for querying and reviewing audit records to support faster access
investigations."""

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

product_system_cheat_sheet = [
    _mode_section("product", "Product-first system design answer centered on user journey, frontend behavior, tradeoffs, and edge cases."),
    ("Controls", ["47=Run/Clear  48=Switch Mode  49/F10=Flip Page  43=Hide/Show", "39=Voice  38=Voice Follow-up", "40=Smaller 41=Bigger | Fn+Arrow or MIDI 42/44/45/46=Move", "F1=Quit  F2=Interactive"]),
    ("Functional Requirements", ["- Start with user-centered requirements: 'Users should be able to...'.", "- Keep scope to the core product journey.", "- Include admin/creator/moderator only when central."]),
    ("User Journey", ["- Treat the end-to-end user flow as the spine.", "- Step through client -> API -> services -> storage/LLM -> response.", "- Call out async, realtime, offline, or error branches only when relevant."]),
    ("Frontend Design", ["- Include surfaces, client state, realtime/offline behavior, and error states.", "- Explain frontend/backend boundaries when they affect UX, API contracts, latency, or consistency."]),
    ("High-Level Design", ["- Use product-specific component labels.", "- Add frontend, backend, storage, async, external, and LLM components only when justified.", "- Avoid infrastructure dumps and unjustified queues/caches/vector stores."]),
    ("Tradeoffs", ["- Cover likely probes: simple vs scalable, sync vs async, consistency vs latency.", "- Include product edge cases, abuse/privacy/reliability risks, and LLM-specific risks when relevant."]),
]

_system_full_sections = system_cheat_sheet
_render_project_view()

def _flip_page():
    global _mode_a_view_mode, _system_view_mode, _product_view_mode, _pair_view_mode, _project_view_mode
    if _current_mode == "mode_a":
        _mode_a_view_mode = (_mode_a_view_mode + 1) % len(_MODE_A_PAGES)
    elif _current_mode == "product":
        _product_view_mode = (_product_view_mode + 1) % (len(_PRODUCT_PAGES) + 1)
    elif _current_mode == "pair":
        _pair_view_mode = (_pair_view_mode + 1) % len(_PAIR_PAGES)
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
    global _current_mode
    previous_mode = _current_mode
    _current_mode = _next_mode(_current_mode)
    if previous_mode == "pair" and _current_mode != "pair":
        stop_pair_mode()
    mode_label, midi_help = _mode_label_and_help(_current_mode)
    if _current_mode == "mode_a":
        if _mode_a_results_shown and _mode_a_full_sections: _rerender_current_view()
        else: ui_update_sections(mode_a_cheat_sheet); AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")
    elif _current_mode == "product":
        if _product_results_shown and _product_full_sections: _rerender_current_view()
        else: ui_update_sections(product_system_cheat_sheet); AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")
    elif _current_mode == "pair":
        if not _pair_active: start_pair_mode()
        else: _render_pair_view()
    elif _current_mode == "project":
        _render_project_view()
    else:
        if _system_results_shown and _system_full_sections: _rerender_current_view()
        else: ui_update_sections(system_cheat_sheet); AppHelper.callAfter(_update_status, f"[{mode_label}] Cheat Sheet | {midi_help}")

_last_midi_ts = {}

def _midi_run_or_clear():
    global _mode_a_results_shown, _system_results_shown, _product_results_shown, _mode_a_view_mode, _system_view_mode, _product_view_mode, _project_view_mode
    global _mode_a_conversation_history, _mode_a_turn_count
    if _current_mode == "mode_a":
        if _mode_a_results_shown:
            _mode_a_results_shown, _mode_a_view_mode, _mode_a_conversation_history, _mode_a_turn_count = False, 0, [], 0
            ui_update_sections(mode_a_cheat_sheet); _update_status(f"[DSA] Cleared | {_MIDI_HELP_DSA}"); return
    elif _current_mode == "product":
        if _product_results_shown:
            _product_results_shown, _product_view_mode = False, 0
            ui_update_sections(product_system_cheat_sheet); _update_status(f"[PRODUCT] Cleared | {_MIDI_HELP_PRODUCT}"); return
    elif _current_mode == "pair":
        pair_process_context(); return
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
            if _current_mode != "mode_a": AppHelper.callAfter(ui_update, ["Continue only available in DSA mode."]); return
            if not _mode_a_conversation_history: AppHelper.callAfter(ui_update, ["No conversation to continue. Run first (47)."]); return
            if _is_running.is_set(): AppHelper.callAfter(ui_update, ["Already running..."]); return
            _is_running.set(); threading.Thread(target=continue_mode_a, daemon=True).start()
        elif note == 51:
            if _current_mode == "pair": AppHelper.callAfter(clear_pair_context); return
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
