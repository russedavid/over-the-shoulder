"""Audio-only Core Audio process tap; no display or screen-capture stream."""

import ctypes as ct
import math
import os
import platform
import threading
from collections import deque
from uuid import uuid4


class PropertyAddress(ct.Structure):
    _fields_ = [("selector", ct.c_uint32), ("scope", ct.c_uint32), ("element", ct.c_uint32)]


class StreamFormat(ct.Structure):
    _fields_ = [("rate", ct.c_double), ("format", ct.c_uint32), ("flags", ct.c_uint32),
                ("bytes_per_packet", ct.c_uint32), ("frames_per_packet", ct.c_uint32),
                ("bytes_per_frame", ct.c_uint32), ("channels", ct.c_uint32), ("bits", ct.c_uint32), ("reserved", ct.c_uint32)]


class AudioBuffer(ct.Structure):
    _fields_ = [("channels", ct.c_uint32), ("size", ct.c_uint32), ("data", ct.c_void_p)]


class AudioBuffers(ct.Structure):
    _fields_ = [("count", ct.c_uint32), ("buffers", AudioBuffer * 1)]


IO_CALLBACK = ct.CFUNCTYPE(ct.c_int32, ct.c_uint32, ct.c_void_p, ct.POINTER(AudioBuffers),
                          ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.c_void_p)


def decode_buffers(pointer, format):
    import numpy as np

    if not pointer or not 0 < pointer.contents.count <= 32:
        return np.array([], dtype=np.float32)
    endian = ">" if format.flags & 2 else "<"
    if format.flags & 1 and format.bits in {32, 64}:
        dtype, scale = np.dtype(endian + f"f{format.bits // 8}"), 1.0
    elif format.flags & 4 and format.bits in {16, 32}:
        dtype, scale = np.dtype(endian + f"i{format.bits // 8}"), float(2 ** (format.bits - 1))
    else:
        raise ValueError("Unsupported system-audio PCM format")
    buffers = (AudioBuffer * pointer.contents.count).from_address(ct.addressof(pointer.contents) + AudioBuffers.buffers.offset)
    channels = []
    for buffer in buffers:
        if not buffer.data or not buffer.size or not buffer.channels:
            continue
        if buffer.size > 8_000_000 or buffer.channels > 32:
            raise ValueError("Unexpected system-audio buffer size")
        samples = np.frombuffer(ct.string_at(buffer.data, buffer.size), dtype=dtype).astype(np.float32) / scale
        count = len(samples) // buffer.channels
        if count:
            channels.extend(samples[:count * buffer.channels].reshape(count, buffer.channels).T)
    if not channels:
        return np.array([], dtype=np.float32)
    count = min(map(len, channels))
    return np.mean(np.stack([channel[:count] for channel in channels]), axis=0).astype(np.float32)


class SystemAudioTap:
    def __init__(self):
        self.tap_id = self.device_id = 0
        self.io_id = ct.c_void_p()
        self.callback = None
        self.format = None
        self.chunks = deque()
        self.sample_count = 0
        self.lock = threading.Lock()
        self.closed = False
        self.error = None
        self.native = None
        self.started = False

    def _check(self, status, operation):
        if status:
            raise RuntimeError(f"System audio {operation} failed ({status}). Check macOS System Audio Recording permission for the launcher.")

    def _property(self, object_id, selector, value, qualifier=None):
        import CoreAudio as C

        address = PropertyAddress(selector, C.kAudioObjectPropertyScopeGlobal, 0)
        size = ct.c_uint32(ct.sizeof(value))
        self._check(self.native.AudioObjectGetPropertyData(object_id, ct.byref(address),
                                                         ct.sizeof(qualifier) if qualifier is not None else 0,
                                                         ct.byref(qualifier) if qualifier is not None else None,
                                                         ct.byref(size), ct.byref(value)), "property lookup")
        return value

    def start(self):
        import CoreAudio as C

        version = tuple(int(n) for n in platform.mac_ver()[0].split(".")[:2])
        if version < (14, 2):
            raise RuntimeError("Audio-only system capture requires macOS 14.2 or newer; select the legacy audio backend in Settings on older Macs")
        self.native = ct.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self.native.AudioObjectGetPropertyData.argtypes = [ct.c_uint32, ct.POINTER(PropertyAddress), ct.c_uint32, ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_void_p]
        self.native.AudioDeviceCreateIOProcID.argtypes = [ct.c_uint32, IO_CALLBACK, ct.c_void_p, ct.POINTER(ct.c_void_p)]
        self.native.AudioDeviceStart.argtypes = [ct.c_uint32, ct.c_void_p]
        self.native.AudioDeviceStop.argtypes = [ct.c_uint32, ct.c_void_p]
        self.native.AudioDeviceDestroyIOProcID.argtypes = [ct.c_uint32, ct.c_void_p]
        excluded = []
        try:
            process = self._property(C.kAudioObjectSystemObject, C.kAudioHardwarePropertyTranslatePIDToProcessObject,
                                     ct.c_uint32(), ct.c_int32(os.getpid())).value
            if process:
                excluded.append(process)
        except RuntimeError:
            pass  # A process with no audio I/O may not yet have a HAL object.
        description = C.CATapDescription.alloc().initMonoGlobalTapButExcludeProcesses_(excluded)
        description.setName_("Over The Shoulder Coder system audio")
        description.setPrivate_(True)
        description.setMuteBehavior_(C.CATapUnmuted)
        try:
            status, self.tap_id = C.AudioHardwareCreateProcessTap(description, None)
            self._check(status, "tap creation")
            self.format = self._property(self.tap_id, C.kAudioTapPropertyFormat, StreamFormat())
            if self.format.format != int.from_bytes(b"lpcm", "big") or not 8000 <= self.format.rate <= 192000:
                raise ValueError("System audio returned an unsupported stream format")
            spec = {
                # These SDK constants are C strings (bytes in PyObjC). Passing
                # bytes would bridge CFData, but HAL expects CFString keys.
                C.kAudioAggregateDeviceNameKey.decode(): "OTSC private audio input",
                C.kAudioAggregateDeviceUIDKey.decode(): "otsc-" + uuid4().hex,
                C.kAudioAggregateDeviceIsPrivateKey.decode(): True,
                C.kAudioAggregateDeviceTapAutoStartKey.decode(): True,
                C.kAudioAggregateDeviceTapListKey.decode(): [{C.kAudioSubTapUIDKey.decode(): str(description.UUID().UUIDString()),
                                                             C.kAudioSubTapDriftCompensationKey.decode(): True}],
            }
            status, self.device_id = C.AudioHardwareCreateAggregateDevice(spec, None)
            self._check(status, "private input creation")

            def receive(device, now, data, input_time, output, output_time, context):
                try:
                    if not self.closed:
                        samples = decode_buffers(data, self.format)
                        if samples.size:
                            with self.lock:
                                self.chunks.append(samples)
                                self.sample_count += samples.size
                                while self.sample_count > self.format.rate * 25 and self.chunks:
                                    self.sample_count -= self.chunks.popleft().size
                except Exception as error:
                    self.error = type(error).__name__
                return 0

            self.callback = IO_CALLBACK(receive)
            self._check(self.native.AudioDeviceCreateIOProcID(self.device_id, self.callback, None, ct.byref(self.io_id)), "callback creation")
            self._check(self.native.AudioDeviceStart(self.device_id, self.io_id), "start")
            self.started = True
        except Exception:
            self.stop()
            raise

    def drain(self):
        import numpy as np
        from scipy.signal import resample_poly

        if self.error:
            raise RuntimeError("System-audio decoding failed: " + self.error)
        with self.lock:
            chunks = list(self.chunks)
            self.chunks.clear()
            self.sample_count = 0
        if not chunks:
            return np.array([], dtype=np.float32)
        samples = np.concatenate(chunks)
        rate = int(round(self.format.rate))
        if rate != 16000:
            divisor = math.gcd(rate, 16000)
            samples = resample_poly(samples, 16000 // divisor, rate // divisor)
        return samples.astype(np.float32)

    def stop(self):
        import CoreAudio as C

        self.closed = True
        if self.device_id and self.io_id.value:
            if self.started:
                self.native.AudioDeviceStop(self.device_id, self.io_id)
                self.started = False
            self.native.AudioDeviceDestroyIOProcID(self.device_id, self.io_id)
            self.io_id = ct.c_void_p()
        if self.device_id:
            C.AudioHardwareDestroyAggregateDevice(self.device_id)
            self.device_id = 0
        if self.tap_id:
            C.AudioHardwareDestroyProcessTap(self.tap_id)
            self.tap_id = 0
        self.callback = None
