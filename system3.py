import os, sys

class _DummyTqdm:
    def __init__(self, iterable=None, *args, **kwargs): self.iterable = iterable
    def __iter__(self): return iter(self.iterable) if self.iterable else iter([])
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def update(self, n=1): pass
    def close(self): pass
    @classmethod
    def write(cls, s, **kwargs): print(s, **kwargs)

class _DummyTqdmModule:
    tqdm = _DummyTqdm
    def __getattr__(self, name): return _DummyTqdm

sys.modules['tqdm'] = _DummyTqdmModule()
sys.modules['tqdm.auto'] = _DummyTqdmModule()
sys.modules['tqdm.std'] = _DummyTqdmModule()

import time, threading, json, mido, anthropic, base64, atexit
from typing import List, Tuple, Optional
import pyautogui
from pynput import keyboard
import pytesseract
from PIL import Image
import numpy as np
import sounddevice as sd
import scipy.io.wavfile as wavfile
import whisper
from AppKit import NSApplication, NSWindow, NSColor, NSFloatingWindowLevel, NSTextField, NSFont, NSLineBreakByClipping
from Foundation import NSObject
from PyObjCTools import AppHelper
import ScreenCaptureKit as SCK
import CoreMedia
import dispatch

SCREENSHOT_REGION, OVERLAY_ORIGIN = (90, 120, 2550, 1480), (965, 265)
COLUMNS, CHARS_PER_LINE, LINE_HEIGHT, TITLE_HEIGHT = 2, 90, 15, 22
LEFT_MARGIN, RIGHT_MARGIN, TOP_MARGIN, BOTTOM_MARGIN, GUTTER = 0, 0, 50, 10, 10
window_width, window_height = 1600, 1150
_is_running, _interactive_mode = threading.Event(), False
_status_label, _conversation_history, _turn_count = None, [], 0
_results_shown, _full_sections, _last_sections = False, None, []
_current_page, _total_pages = 0, 1
_is_recording, _audio_data, _system_audio_data, _whisper_model = False, [], [], None
_system_audio_capture, _audio_stream = None, None
ANTHROPIC_MODEL, _SAMPLE_RATE = "claude-opus-4-5-20251101", 16000
_MIDI_HELP = "47=run 50=cont 49=page 38=voice | 40/41=size 42/44/45/46=move"

class SCKAudioDelegate(NSObject):
    def init(self):
        self = super().init()
        if self: self.chunks = []
        return self
    def stream_didOutputSampleBuffer_ofType_(self, stream, buf, typ):
        if typ != SCK.SCStreamOutputTypeAudio: return
        block = CoreMedia.CMSampleBufferGetDataBuffer(buf)
        if not block: return
        length = CoreMedia.CMBlockBufferGetDataLength(block)
        data = bytearray(length)
        CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, data)
        self.chunks.append(np.frombuffer(data, dtype=np.float32).copy())

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
    def stop(self):
        if self.stream: self.stream.stopCaptureWithCompletionHandler_(lambda e: None); self.stream = None
        audio = np.concatenate(self.delegate.chunks).flatten() if self.delegate and self.delegate.chunks else None
        self.delegate = None
        return audio

def take_screenshot():
    fn = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
    pyautogui.screenshot(region=SCREENSHOT_REGION).save(fn)
    return fn

def ocr_with_tesseract(path):
    try: return pytesseract.image_to_string(Image.open(path)).strip()
    except Exception as e: return f"(OCR error: {e})"

def wrap_lines(s, max_len=CHARS_PER_LINE):
    out = []
    for line in (s.splitlines() or [""]):
        while len(line) > max_len: out.append(line[:max_len]); line = line[max_len:]
        out.append(line)
    return out

def _mk_color(r, g, b, a=0.9): return NSColor.colorWithCalibratedRed_green_blue_alpha_(r/255.0, g/255.0, b/255.0, a)

SECTION_COLOR = {
    "Bug Analysis": _mk_color(140, 50, 0), "Root Cause": _mk_color(140, 0, 60),
    "Fix": _mk_color(0, 100, 80), "Files to Check": _mk_color(0, 85, 150),
    "Explanation": _mk_color(90, 0, 140), "Status": _mk_color(200, 120, 0),
    "Recording": _mk_color(180, 0, 0), "Processing": _mk_color(150, 100, 0),
    "Voice Input": _mk_color(0, 100, 130), "Controls": _mk_color(80, 80, 80),
    "How to Use": _mk_color(60, 90, 120), "status:": _mk_color(45, 45, 45),
}
TITLE_FONT = NSFont.boldSystemFontOfSize_(13)
LINE_FONT = NSFont.userFixedPitchFontOfSize_(12) or NSFont.systemFontOfSize_(12)

def _place_label(text, x, y_val, width, height, font, color):
    field = NSTextField.alloc().initWithFrame_(((x, y_val), (width, height)))
    field.setStringValue_(text); field.setTextColor_(color); field.setDrawsBackground_(False)
    field.setBackgroundColor_(NSColor.clearColor()); field.setBordered_(False); field.setSelectable_(False)
    field.setEditable_(False); field.setFont_(font); field.cell().setLineBreakMode_(NSLineBreakByClipping)
    field.setUsesSingleLineMode_(True); window.contentView().addSubview_(field)

def _ensure_status_label():
    global _status_label
    if _status_label and _status_label.superview(): return _status_label
    lbl = NSTextField.alloc().initWithFrame_(((8, 8), (420, 18)))
    lbl.setBordered_(False); lbl.setEditable_(False); lbl.setSelectable_(False); lbl.setDrawsBackground_(True)
    lbl.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6))
    lbl.setTextColor_(_mk_color(45, 45, 45)); lbl.setFont_(NSFont.userFixedPitchFontOfSize_(11))
    window.contentView().addSubview_(lbl); _status_label = lbl
    return lbl

def _update_status(text): lbl = _ensure_status_label(); lbl.setStringValue_(text); lbl.setFrameOrigin_((8, 8))

def _update_overlay_sections(sections):
    global _last_sections
    _last_sections = sections
    for sv in list(window.contentView().subviews()): sv.removeFromSuperview()
    frame = window.frame()
    col_width = (frame.size.width - LEFT_MARGIN - RIGHT_MARGIN - GUTTER) / COLUMNS
    x_cols = [LEFT_MARGIN, LEFT_MARGIN + col_width + GUTTER]
    y, col = [frame.size.height - TOP_MARGIN] * COLUMNS, 0
    for title, lines in sections:
        color = SECTION_COLOR.get(title, _mk_color(45, 45, 45))
        if y[col] - TITLE_HEIGHT < BOTTOM_MARGIN:
            col += 1
            if col >= COLUMNS: break
            y[col] = frame.size.height - TOP_MARGIN
        _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
        for line in lines:
            if y[col] - LINE_HEIGHT < BOTTOM_MARGIN:
                col += 1
                if col >= COLUMNS: return
                y[col] = frame.size.height - TOP_MARGIN
                _place_label(title, x_cols[col], y[col], col_width, TITLE_HEIGHT, TITLE_FONT, color); y[col] -= TITLE_HEIGHT
            _place_label(line, x_cols[col], y[col], col_width, LINE_HEIGHT, LINE_FONT, color); y[col] -= LINE_HEIGHT

def ui_update(lines): AppHelper.callAfter(_update_overlay_sections, [('status:', lines)])
def ui_update_sections(sections): AppHelper.callAfter(_update_overlay_sections, sections)

def _paginate_sections(sections):
    if not sections: return [[]]
    lines_per_page = int((window.frame().size.height - TOP_MARGIN - BOTTOM_MARGIN) / LINE_HEIGHT) * COLUMNS
    pages, current_page, current_lines = [], [], 0
    for title, lines in sections:
        section_lines = 1 + len(lines)
        if current_lines + section_lines <= lines_per_page:
            current_page.append((title, lines)); current_lines += section_lines
        else:
            if current_page: pages.append(current_page)
            if section_lines > lines_per_page:
                remaining = lines
                while remaining:
                    chunk = remaining[:lines_per_page - 1]; remaining = remaining[lines_per_page - 1:]
                    pages.append([(title, chunk)])
                current_page, current_lines = [], 0
            else:
                current_page, current_lines = [(title, lines)], section_lines
    if current_page: pages.append(current_page)
    return pages or [[]]

def _show_page(page_num):
    global _current_page, _total_pages, _last_sections
    if not _full_sections: return
    pages = _paginate_sections(_full_sections)
    _total_pages = len(pages)
    _current_page = page_num % _total_pages
    _last_sections = pages[_current_page]
    _update_overlay_sections(_last_sections)
    _update_status(f"[BUG FIX] Turn {_turn_count} | Page {_current_page + 1}/{_total_pages} | {_MIDI_HELP}")

_BUG_FIX_SYSTEM_PROMPT = """You are an expert TypeScript/React debugger. Analyze IDE screenshots showing code, terminal output, and errors.
Respond with ONLY valid JSON: {"bug_analysis": "...", "root_cause": "...", "files_to_check": ["..."], "fix": "...", "explanation": "..."}"""

def build_initial_prompt(ocr): return f"Debugging TypeScript React. Screen content:\n<screen>{ocr}</screen>\nAnalyze and fix. JSON only."
def build_continuation_prompt(ocr): return f"Additional context:\n<content>{ocr}</content>\nUpdated analysis. JSON only."
def build_voice_prompt(transcript): return f"Voice input:\n<voice>{transcript}</voice>\nUpdated analysis. JSON only."

def format_response_sections(data):
    sections = []
    if "bug_analysis" in data: sections.append(("Bug Analysis", wrap_lines(data["bug_analysis"])))
    if "root_cause" in data: sections.append(("Root Cause", wrap_lines(data["root_cause"])))
    if data.get("files_to_check"): sections.append(("Files to Check", [f"- {f}" for f in data["files_to_check"]]))
    if "fix" in data: sections.append(("Fix", wrap_lines(data["fix"])))
    if "explanation" in data: sections.append(("Explanation", wrap_lines(data["explanation"])))
    return sections

def call_anthropic(messages, image_path=None):
    try: client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    except: return None, messages
    if image_path and messages:
        last = messages[-1]
        if last["role"] == "user":
            with open(image_path, "rb") as f: img = base64.b64encode(f.read()).decode("utf-8")
            last["content"] = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}, {"type": "text", "text": last["content"]}]
    try:
        resp = client.messages.create(model=ANTHROPIC_MODEL, max_tokens=4096, temperature=0.1, system=_BUG_FIX_SYSTEM_PROMPT, messages=messages)
        text = resp.content[0].text.strip()
        if text.startswith("```json"): text = text[7:]
        elif text.startswith("```"): text = text[3:]
        if text.endswith("```"): text = text[:-3]
        return json.loads(text.strip()), messages + [{"role": "assistant", "content": text}]
    except: return None, messages

def run_pipeline():
    global _results_shown, _full_sections, _conversation_history, _turn_count, _current_page
    _conversation_history, _turn_count = [], 1
    try:
        ui_update_sections([("Status", [f"Analyzing... | {_MIDI_HELP}"])])
        shot = take_screenshot()
        ocr = ocr_with_tesseract(shot)
        data, _conversation_history = call_anthropic([{"role": "user", "content": build_initial_prompt(ocr)}], shot)
        if data:
            sections = format_response_sections(data)
            def _render():
                global _full_sections, _current_page
                _full_sections = sections; _current_page = 0; _show_page(0)
            AppHelper.callAfter(_render); _results_shown = True
        else: ui_update(["(No response)"]); _results_shown = False
    except Exception as e: ui_update([f"Error: {e}"]); _results_shown = False
    finally: _is_running.clear()

def continue_conversation():
    global _results_shown, _full_sections, _conversation_history, _turn_count, _current_page
    try:
        if _last_sections: ui_update_sections([("Status", [f"Continuing (turn {_turn_count + 1})..."])] + _last_sections)
        shot = take_screenshot()
        ocr = ocr_with_tesseract(shot)
        data, _conversation_history = call_anthropic(_conversation_history + [{"role": "user", "content": build_continuation_prompt(ocr)}], shot)
        if data:
            _turn_count += 1
            sections = format_response_sections(data)
            def _render():
                global _full_sections, _current_page
                _full_sections = sections; _current_page = 0; _show_page(0)
            AppHelper.callAfter(_render); _results_shown = True
        else: ui_update(["(No response)"])
    finally: _is_running.clear()

def _get_whisper_model():
    global _whisper_model
    if not _whisper_model: _whisper_model = whisper.load_model("base")
    return _whisper_model

def _audio_callback(indata, frames, time_info, status):
    if _is_recording: _audio_data.append(indata.copy())

def _find_mic():
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'MacBook' in d['name']: return i
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0 and 'Microphone' in d['name'] and 'Steam' not in d['name']: return i
    for i, d in enumerate(devices):
        if d['max_input_channels'] > 0: return i
    return None

def start_voice_recording():
    global _is_recording, _audio_data, _audio_stream, _system_audio_capture
    if not _conversation_history: ui_update(["No conversation. Press 47 first."]); return
    _audio_data, _is_recording = [], True
    try:
        _system_audio_capture = SystemAudioCapture(); _system_audio_capture.start()
        mic = _find_mic()
        if mic is None: ui_update(["No microphone!"]); return
        _audio_stream = sd.InputStream(device=mic, samplerate=_SAMPLE_RATE, channels=1, dtype='float32', callback=_audio_callback, blocksize=1024)
        _audio_stream.start()
        if _last_sections: ui_update_sections([("Recording", ["Press 38 to stop."])] + _last_sections)
    except Exception as e: ui_update([f"Recording error: {e}"])

def stop_voice_and_continue():
    global _is_recording, _audio_stream, _system_audio_capture, _system_audio_data
    _is_recording = False
    if _audio_stream: _audio_stream.stop(); _audio_stream.close(); _audio_stream = None
    system_audio = _system_audio_capture.stop() if _system_audio_capture else None
    _system_audio_capture = None
    mic_audio = np.concatenate(_audio_data, axis=0).flatten() if _audio_data else None
    if mic_audio is not None: wavfile.write("mic_recording.wav", _SAMPLE_RATE, (mic_audio * 32767).astype(np.int16))
    if system_audio is not None: wavfile.write("system_recording.wav", _SAMPLE_RATE, (system_audio * 32767).astype(np.int16))
    _system_audio_data = system_audio
    if not _audio_data and system_audio is None: ui_update(["No audio."]); return
    if _last_sections: ui_update_sections([("Processing", ["Transcribing..."])] + _last_sections)
    threading.Thread(target=_process_voice, daemon=True).start()

def _process_voice():
    global _conversation_history, _turn_count, _results_shown, _full_sections, _current_page
    try:
        model = _get_whisper_model()
        mic_text = model.transcribe(np.concatenate(_audio_data, axis=0).flatten(), fp16=False, verbose=False)["text"].strip() if _audio_data else ""
        sys_text = model.transcribe(_system_audio_data, fp16=False, verbose=False)["text"].strip() if _system_audio_data is not None else ""
        if not mic_text and not sys_text: ui_update(["No speech."]); return
        parts = []
        if sys_text: parts.append(f"[System]: {sys_text}")
        if mic_text: parts.append(f"[User]: {mic_text}")
        transcript = "\n".join(parts)
        if _last_sections: ui_update_sections([("Processing", ["Getting response..."])] + _last_sections)
        data, _conversation_history = call_anthropic(_conversation_history + [{"role": "user", "content": build_voice_prompt(transcript)}])
        if data:
            _turn_count += 1
            sections = [("Voice Input", wrap_lines(transcript))] + format_response_sections(data)
            def _render():
                global _full_sections, _current_page
                _full_sections = sections; _current_page = 0; _show_page(0)
            AppHelper.callAfter(_render); _results_shown = True
        else: AppHelper.callAfter(ui_update, ["(No response)"])
    except Exception as e: AppHelper.callAfter(ui_update, [f"Error: {e}"])
    finally: _is_running.clear()

app = NSApplication.sharedApplication()
window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_((OVERLAY_ORIGIN, (window_width, window_height)), 15, 2, False)
window.setTitlebarAppearsTransparent_(True); window.setSharingType_(0); window.setBackgroundColor_(NSColor.clearColor())
window.setOpaque_(False); window.setHasShadow_(False); window.setAlphaValue_(1.0)
window.setLevel_(NSFloatingWindowLevel); window.setIgnoresMouseEvents_(True); window.makeKeyAndOrderFront_(None)

class WindowDelegate(NSObject):
    def windowDidResize_(self, n): pass
    def windowDidMove_(self, n): pass
delegate = WindowDelegate.alloc().init(); window.setDelegate_(delegate)

def _apply_interactive_mode():
    global _status_label
    if _interactive_mode:
        window.setIgnoresMouseEvents_(False); window.setHasShadow_(True); window.setMovableByWindowBackground_(True); window.setAlphaValue_(0.7)
        _ensure_status_label(); ui_update(["Interactive ON. F2 to turn OFF."])
    else:
        window.setIgnoresMouseEvents_(True); window.setHasShadow_(False); window.setAlphaValue_(1.0)
        if _status_label: _status_label.removeFromSuperview(); _status_label = None
        ui_update([f'Interactive OFF. {_MIDI_HELP}'])

def toggle_interactive_mode():
    global _interactive_mode
    _interactive_mode = not _interactive_mode; AppHelper.callAfter(_apply_interactive_mode)

initial_help = [
    ("Controls", ["47=Run/Clear  50=Continue  49=Page  38=Voice", "40/41=Size  42/44/45/46=Move  F1=Quit  F2=Interactive"]),
    ("How to Use", ["1. Show buggy code in IDE", "2. Press 47 to analyze", "3. Show more context, press 50", "4. Use 49 for pages, 38 for voice"]),
]
ui_update_sections(initial_help)
AppHelper.callAfter(lambda: _update_status(f"[BUG FIX] Ready | {_MIDI_HELP}"))

def _cleanup_audio():
    global _audio_stream, _is_recording, _system_audio_capture
    _is_recording = False
    if _audio_stream:
        try: _audio_stream.stop(); _audio_stream.close()
        except: pass
        _audio_stream = None
    if _system_audio_capture:
        try: _system_audio_capture.stop()
        except: pass
        _system_audio_capture = None
atexit.register(_cleanup_audio)

def _quit_app():
    _cleanup_audio()
    AppHelper.callAfter(lambda: (window.close(), AppHelper.stopEventLoop()))

def on_release(key):
    if key == keyboard.Key.f1: _quit_app(); return False
    if key == keyboard.Key.f2: toggle_interactive_mode()
listener = keyboard.Listener(on_release=on_release); listener.start()

_last_midi_ts = {}

def _midi_run_or_clear():
    global _results_shown, _conversation_history, _turn_count, _full_sections, _current_page, _total_pages
    if _results_shown:
        _results_shown, _conversation_history, _turn_count = False, [], 0
        _full_sections, _current_page, _total_pages = None, 0, 1
        ui_update_sections(initial_help); _update_status(f"[BUG FIX] Cleared | {_MIDI_HELP}"); return
    if _is_running.is_set(): ui_update(["Running..."]); return
    _is_running.set(); threading.Thread(target=run_pipeline, daemon=True).start()

def on_midi(msg):
    global _last_midi_ts, _current_page
    mtype, note, vel = getattr(msg, "type", None), getattr(msg, "note", None), getattr(msg, "velocity", None)
    if mtype != "note_on" or (vel is not None and vel == 0): return
    now = time.time()
    if note in _last_midi_ts and now - _last_midi_ts[note] < 0.15: return
    _last_midi_ts[note] = now

    if note == 47: AppHelper.callAfter(_midi_run_or_clear)
    elif note == 49:
        if _results_shown and _full_sections:
            def _flip(): global _current_page; _current_page = (_current_page + 1) % _total_pages; _show_page(_current_page)
            AppHelper.callAfter(_flip)
    elif note == 50:
        if not _conversation_history: AppHelper.callAfter(ui_update, ["Press 47 first."]); return
        if _is_running.is_set(): return
        _is_running.set(); threading.Thread(target=continue_conversation, daemon=True).start()
    elif note == 38:
        if _is_recording: AppHelper.callAfter(stop_voice_and_continue)
        elif _conversation_history: AppHelper.callAfter(start_voice_recording)
        else: AppHelper.callAfter(ui_update, ["Press 47 first."])
    elif note in (42, 44, 45, 46):
        def _move(d):
            f = window.frame(); x, y = f.origin.x, f.origin.y
            if d == 42: x -= 50
            elif d == 46: x += 50
            elif d == 45: y += 50
            elif d == 44: y -= 50
            window.setFrameOrigin_((x, y))
        AppHelper.callAfter(_move, note)
    elif note in (40, 41):
        def _resize(bigger):
            f = window.frame(); w, h = f.size.width, f.size.height
            if bigger: w += 50; h += 50
            else: w = max(200, w - 50); h = max(200, h - 50)
            window.setFrame_display_(((f.origin.x, f.origin.y), (w, h)), True)
        AppHelper.callAfter(_resize, note == 41)

midi_port = mido.open_input(mido.get_input_names()[0], callback=on_midi)
AppHelper.runEventLoop(); midi_port.close()
