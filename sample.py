# capture_to_aac.py
import os, time, signal, objc, subprocess, sys
from Foundation import NSObject, NSLog
from PyObjCTools import AppHelper
from ScreenCaptureKit import (
    SCShareableContent, SCContentFilter, SCStream, SCStreamConfiguration,
    SCStreamOutputTypeAudio
)
from CoreMedia import (
    CMSampleBufferGetDataBuffer, CMBlockBufferGetDataPointer, kCMBlockBufferNoErr
)

OUT_DIR = os.path.join(os.path.dirname(__file__), "audio")
os.makedirs(OUT_DIR, exist_ok=True)
def out_file():
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(OUT_DIR, f"capture_{ts}.m4a")

class AudioTap(NSObject):
    def init(self):
        self = objc.super(AudioTap, self).init()
        if self is None: return None
        self.stream = None
        self.out_path = out_file()
        # Use full path to ffmpeg if needed: e.g. "/opt/homebrew/bin/ffmpeg"
        self.ffmpeg = subprocess.Popen([
            "ffmpeg",
            "-loglevel", "error",       # show errors if any
            "-y",
            "-f", "f32le",              # try float32; switch to s16le if noise
            "-ar", "48000",
            "-ac", "2",
            "-i", "pipe:0",
            "-c:a", "aac",
            "-b:a", "192k",
            self.out_path
        ], stdin=subprocess.PIPE, stderr=sys.stderr)
        NSLog(f"Encoding to {self.out_path}")
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sampleBuffer, outputType):
        if outputType != SCStreamOutputTypeAudio: return
        block = CMSampleBufferGetDataBuffer(sampleBuffer)
        if not block: return
        status, _, total_len, data_ptr = CMBlockBufferGetDataPointer(block, 0, None, None, None)
        if status == kCMBlockBufferNoErr and data_ptr and total_len > 0:
            try:
                self.ffmpeg.stdin.write(bytes(memoryview(data_ptr)[:total_len]))
            except BrokenPipeError:
                pass

    def _fetch_shareable_content(self, handler):
        if hasattr(SCShareableContent, "currentShareableContentWithCompletionHandler_"):
            SCShareableContent.currentShareableContentWithCompletionHandler_(handler)
        elif hasattr(SCShareableContent, "getShareableContentWithCompletionHandler_"):
            SCShareableContent.getShareableContentWithCompletionHandler_(handler)
        else:
            NSLog("ScreenCaptureKit: no shareable content API")
            handler(None, Exception("No shareable content method"))

    def start_(self, done):
        def got_content(content, err):
            if err or content is None:
                NSLog(f"Error getting content: {err}"); done(); return
            displays = content.displays() if callable(getattr(content, "displays", None)) else content.displays
            if not displays:
                NSLog("No displays found"); done(); return
            display = next((d for d in displays if getattr(d, "isMainDisplay", False)), displays[0])
            filt = SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, None)
            cfg = SCStreamConfiguration.new()
            cfg.setCapturesAudio_(True)
            self.stream = SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, None)

            def added(ok, add_err):
                if add_err: NSLog(f"addStreamOutput error: {add_err}"); done(); return
                def started(start_err):
                    if start_err: NSLog(f"startCapture error: {start_err}"); done(); return
                    NSLog("Audio capture started.")
                self.stream.startCaptureWithCompletionHandler_(started)

            self.stream.addStreamOutput_type_sampleHandlerQueue_completionHandler_(
                self, SCStreamOutputTypeAudio, None, added)

        self._fetch_shareable_content(got_content)

    def stop(self):
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(lambda e: None)
            self.stream = None
        if getattr(self, "ffmpeg", None):
            try:
                self.ffmpeg.stdin.close()
                self.ffmpeg.wait(timeout=5)
            except Exception:
                pass
        NSLog(f"Stopped. File at: {getattr(self, 'out_path', '(unknown)')}")

def main():
    tap = AudioTap.alloc().init()

    def quit_gracefully(*_):
        tap.stop()
        AppHelper.stopEventLoop()

    signal.signal(signal.SIGINT, quit_gracefully)
    tap.start_(lambda: None)  # <-- keep running
    AppHelper.runEventLoop()

if __name__ == "__main__":
    main()

