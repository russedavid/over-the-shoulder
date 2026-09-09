"""Opt-in screen/OCR and separately attributed microphone/system audio capture."""

import io
import queue
import re
import shutil
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path
from uuid import uuid4

from otsc.privacy import app_directory, private_directory, private_write, redact
from otsc.settings import Credentials, ModelChoice


class ScreenCapture:
    def __init__(self):
        self.signature = ""
        self.paths = deque()

    def capture(self, region=None, *, keep_image=False, on_captured=None):
        import pyautogui

        image = pyautogui.screenshot(region=region)
        if on_captured:
            on_captured()
        return self.interpret(image, keep_image=keep_image)

    def interpret(self, image, *, keep_image=False):
        import pytesseract
        from PIL import ImageDraw

        if not shutil.which("tesseract") and Path("/opt/homebrew/bin/tesseract").exists():
            pytesseract.pytesseract.tesseract_cmd = "/opt/homebrew/bin/tesseract"
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT, timeout=20)
        rows = {}
        for index, text in enumerate(data["text"]):
            if text.strip():
                key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
                rows.setdefault(key, []).append(index)
        # Separate an aligned editor gutter from the code it numbers. Keep its range
        # as evidence, rather than making line numbers look like Python/JS source.
        numbered = []
        for key, indices in rows.items():
            first = indices[0]
            if len(indices) > 1 and data["text"][first].isdigit():
                next_word = indices[1]
                if data["left"][next_word] - data["left"][first] - data["width"][first] >= 8:
                    numbered.append((key, int(data["text"][first]), data["left"][first]))
        all_text = " ".join(data["text"])
        gutter = set()
        code_left = 0
        if (
            len(numbered) >= 2
            and re.search(r"\b[\w-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|c|cpp|rb|swift)\b", all_text)
            and re.search(r"\b(?:def|return|class|function|const|import|public|fn)\b", all_text)
            and max(n[2] for n in numbered) - min(n[2] for n in numbered) < 12
            and all(b[1] == a[1] + 1 for a, b in zip(numbered, numbered[1:]))
        ):
            gutter = {n[0] for n in numbered}
            code_left = min(data["left"][rows[key][1]] for key in gutter)
        lines, draw = [], ImageDraw.Draw(image)
        for key, indices in rows.items():
            words = indices[1:] if key in gutter else indices
            text = " ".join(data["text"][i] for i in words)
            if key in gutter:
                first = words[0]
                character_width = data["width"][first] / max(1, len(data["text"][first]))
                indent = round((data["left"][first] - code_left) / max(1, character_width))
                text = " " * min(40, max(0, indent)) + text
            cleaned = redact(text)
            lines.append(cleaned)
            if cleaned != text:
                left = min(data["left"][i] for i in indices)
                top = min(data["top"][i] for i in indices)
                right = max(data["left"][i] + data["width"][i] for i in indices)
                bottom = max(data["top"][i] + data["height"][i] for i in indices)
                draw.rectangle((left - 3, top - 3, right + 3, bottom + 3), fill="black")
        pixels = list(image.convert("L").resize((16, 16)).tobytes())
        average = sum(pixels) / len(pixels)
        signature = "".join("1" if value > average else "0" for value in pixels)
        if not self.signature or sum(a != b for a, b in zip(signature, self.signature)) > 14:
            self.signature = signature
        import hashlib

        visual = hashlib.sha256(self.signature.encode()).hexdigest()[:12]
        text = "\n".join(lines) or "No legible text detected. The image may contain a diagram or non-text content."
        if gutter:
            text += (
                f"\n[Detected editor line numbers: {numbered[0][1]}–{numbered[-1][1]}; spacing reconstructed by OCR.]"
            )
        text += "\n[Visual layout reference: " + visual + "]"
        path = ""
        if keep_image:
            directory = private_directory(app_directory() / "captures")
            target = directory / (uuid4().hex + ".png")
            image.thumbnail((1920, 1440))
            output = io.BytesIO()
            image.save(output, format="PNG")
            private_write(target, output.getvalue())
            self.paths.append(target)
            # Only this process's recent redacted frames; raw screenshots are never persisted.
            while len(self.paths) > 12:
                self.paths.popleft().unlink(missing_ok=True)
            path = str(target)
        return text, path

    def close(self):
        for path in self.paths:
            path.unlink(missing_ok=True)
        self.paths.clear()


def make_system_audio():
    """Imports and constructs native capture only after Start has been pressed."""
    import CoreMedia
    import dispatch
    import numpy as np
    import objc
    import ScreenCaptureKit as SCK
    from Foundation import NSObject

    # A single Objective-C class registration per process, even across capture restarts.
    try:
        Delegate = objc.lookUpClass("OTSCSystemAudioDelegate")
    except objc.nosuchclass_error:

        class OTSCSystemAudioDelegate(NSObject):
            def init(self):
                self = objc.super(OTSCSystemAudioDelegate, self).init()
                if self is not None:
                    self.chunks = deque()
                    self.sample_count = 0
                    self.lock = threading.Lock()
                return self

            def stream_didOutputSampleBuffer_ofType_(self, stream, buffer, kind):
                if kind != SCK.SCStreamOutputTypeAudio:
                    return
                block = CoreMedia.CMSampleBufferGetDataBuffer(buffer)
                if not block:
                    return
                length = CoreMedia.CMBlockBufferGetDataLength(block)
                status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
                if status == 0:
                    with self.lock:
                        samples = np.frombuffer(data, dtype=np.float32).copy()
                        self.chunks.append(samples)
                        self.sample_count += samples.size
                        while self.sample_count > 16000 * 25 and self.chunks:
                            self.sample_count -= self.chunks.popleft().size

        Delegate = OTSCSystemAudioDelegate

    class SystemAudio:
        def __init__(self):
            self.stream = None
            self.delegate = Delegate.alloc().init()
            self.dispatch_queue = dispatch.dispatch_queue_create(b"otsc.audio", None)

        def start(self):
            ready, errors = threading.Event(), []

            def got_content(content, error):
                if error or not content or not content.displays():
                    errors.append(str(error or "No display available for system audio"))
                    ready.set()
                    return
                config = SCK.SCStreamConfiguration.alloc().init()
                config.setCapturesAudio_(True)
                config.setExcludesCurrentProcessAudio_(True)
                config.setSampleRate_(16000)
                config.setChannelCount_(1)
                filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(content.displays()[0], [])
                self.stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, config, None)
                success, error = self.stream.addStreamOutput_type_sampleHandlerQueue_error_(
                    self.delegate, SCK.SCStreamOutputTypeAudio, self.dispatch_queue, None
                )
                if not success:
                    errors.append(str(error))
                    ready.set()
                    return

                def started(error):
                    if error:
                        errors.append(str(error))
                    ready.set()

                self.stream.startCaptureWithCompletionHandler_(started)

            SCK.SCShareableContent.getShareableContentWithCompletionHandler_(got_content)
            if not ready.wait(12):
                raise RuntimeError("System audio did not start; check macOS Screen & System Audio Recording permission")
            if errors:
                raise RuntimeError("System audio: " + errors[0])

        def drain(self):
            with self.delegate.lock:
                chunks = list(self.delegate.chunks)
                self.delegate.chunks.clear()
                self.delegate.sample_count = 0
            return np.concatenate(chunks) if chunks else np.array([], dtype=np.float32)

        def stop(self):
            if self.stream:
                self.stream.stopCaptureWithCompletionHandler_(lambda error: None)
                self.stream = None

    return SystemAudio()


def wav_bytes(samples, sample_rate=16000):
    import numpy as np

    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


class Transcriber:
    def __init__(self, settings, credentials=None):
        self.settings = settings
        self.credentials = credentials or Credentials()
        self.local_model = None
        self.local_lock = threading.Lock()

    def transcribe(self, samples):
        data = wav_bytes(samples)
        provider = self.settings.transcription
        if provider == "local":
            with self.local_lock:
                try:
                    from mlx_audio.stt.utils import load
                except ImportError as exc:
                    raise RuntimeError("Local transcription needs: uv sync --extra mac --extra local-asr") from exc
                if self.local_model is None:
                    self.local_model = load(self.settings.local_asr_model)
                with tempfile.TemporaryDirectory(prefix="otsc-asr-") as temp:
                    path = Path(temp) / "chunk.wav"
                    private_write(path, data)
                    result = self.local_model.generate(str(path))
                    return redact(getattr(result, "text", "").strip())
        if provider not in {"openai", "groq"}:
            return ""
        import httpx

        model = self.settings.transcription_model or ("whisper-large-v3-turbo" if provider == "groq" else "whisper-1")
        choice = ModelChoice(provider=provider, model=model)
        key = self.credentials.get(choice, "transcription")
        if not key:
            raise ValueError("Add the transcription API key in Settings")
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            result = client.post(
                choice.endpoint() + "/audio/transcriptions",
                headers={"Authorization": "Bearer " + key},
                data={"model": model, "response_format": "json"},
                files={"file": ("audio.wav", data, "audio/wav")},
            )
            if result.status_code >= 300:
                raise RuntimeError(f"Transcription HTTP {result.status_code}: {redact(result.text)[:350]}")
            return redact(result.json().get("text", "").strip())


class AudioCapture:
    def __init__(self, settings, events, credentials=None):
        self.settings, self.events = settings.model_copy(deep=True), events
        self.transcriber = Transcriber(self.settings, credentials)
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.flush_lock = threading.Lock()
        self.flush_request = None
        self.mic_chunks, self.mic_lock = deque(maxlen=384), threading.Lock()
        self.mic = self.system = None
        self.queues = {channel: queue.Queue(maxsize=3) for channel in ("microphone", "system")}
        self.id = uuid4().hex

    def start(self):
        if self.settings.transcription == "disabled":
            if self.settings.microphone or self.settings.system_audio:
                raise ValueError("Choose a transcription provider before enabling audio")
            return
        threading.Thread(target=self._run, name="otsc-audio-capture", daemon=True).start()

    def flush_for_help(self):
        request_id = uuid4().hex
        with self.flush_lock:
            self.flush_request = request_id
        self.wake.set()
        return request_id

    def _run(self):
        import numpy as np

        try:
            for channel in self.queues:
                threading.Thread(target=self._transcribe, args=(channel,), daemon=True).start()
            if self.settings.microphone:
                import sounddevice as sd

                def callback(data, frames, timing, status):
                    with self.mic_lock:
                        self.mic_chunks.append(data[:, 0].copy())

                self.mic = sd.InputStream(
                    samplerate=16000,
                    channels=1,
                    dtype="float32",
                    blocksize=1024,
                    device=self.settings.microphone_device,
                    callback=callback,
                )
                self.mic.start()
            if self.settings.system_audio:
                self.system = make_system_audio()
                self.system.start()
            if self.stop_event.is_set():
                return
            self.events.put({"type": "audio_status", "capture_id": self.id, "message": "Audio capture is active."})
            while not self.stop_event.is_set():
                self.wake.wait(self.settings.audio_chunk_seconds)
                self.wake.clear()
                if self.stop_event.is_set():
                    break
                with self.flush_lock:
                    flush_id, self.flush_request = self.flush_request, None
                with self.mic_lock:
                    chunks = list(self.mic_chunks)
                    self.mic_chunks.clear()
                recordings = {
                    "microphone": np.concatenate(chunks) if chunks else np.array([]),
                    "system": self.system.drain() if self.system else np.array([]),
                }
                for channel, samples in recordings.items():
                    if samples.size < 4000 or np.sqrt(np.mean(samples**2)) < 0.003:
                        continue
                    try:
                        self.queues[channel].put_nowait((samples, time.time()))
                    except queue.Full:
                        self.events.put(
                            {
                                "type": "audio_status",
                                "capture_id": self.id,
                                "message": f"{channel} transcription is behind; a chunk was skipped.",
                            }
                        )
                if flush_id:
                    deadline = time.monotonic() + 30
                    while (
                        any(q.unfinished_tasks for q in self.queues.values())
                        and time.monotonic() < deadline
                        and not self.stop_event.is_set()
                    ):
                        self.stop_event.wait(0.02)
                    self.events.put(
                        {
                            "type": "audio_flushed",
                            "capture_id": self.id,
                            "flush_id": flush_id,
                            "complete": not any(q.unfinished_tasks for q in self.queues.values())
                            and not self.stop_event.is_set(),
                        }
                    )
        except Exception as error:
            self.events.put({"type": "audio_error", "capture_id": self.id, "message": redact(str(error))})
        finally:
            self.stop_event.set()
            if self.mic:
                self.mic.stop()
                self.mic.close()
            if self.system:
                self.system.stop()

    def _transcribe(self, channel):
        while not self.stop_event.is_set():
            try:
                samples, at = self.queues[channel].get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                text = self.transcriber.transcribe(samples)
                if text and not self.stop_event.is_set():
                    role = self.settings.microphone_role if channel == "microphone" else self.settings.system_role
                    self.events.put(
                        {
                            "type": "speech",
                            "capture_id": self.id,
                            "channel": channel,
                            "speaker": role,
                            "text": text,
                            "at": at,
                            "confidence": "transcribed",
                        }
                    )
            except Exception as error:
                self.events.put({"type": "audio_error", "capture_id": self.id, "message": redact(str(error))})
                self.stop_event.set()
            finally:
                self.queues[channel].task_done()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
