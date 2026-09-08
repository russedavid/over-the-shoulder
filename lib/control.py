import time
from typing import Optional

from pynput import keyboard


_MODE_ORDER = ["system", "mode_a", "pair", "behavioral_pair", "project"]
_NOTE_DEBOUNCE_SEC = 0.15
_FN_VK = 63
_FN_ARROW_MOVES = {
    keyboard.Key.home: "left",
    keyboard.Key.end: "right",
    keyboard.Key.page_up: "up",
    keyboard.Key.page_down: "down",
}
_MIDI_MOVE_DIRECTIONS = {42: "left", 44: "down", 45: "up", 46: "right"}
_MIDI_RESIZE_BIGGER = {40: False, 41: True}


def _extract_vk(key) -> Optional[int]:
    try:
        if hasattr(key, "vk") and key.vk is not None: return int(key.vk)
        if hasattr(key, "value") and hasattr(key.value, "vk"): return int(key.value.vk)
        if hasattr(key, "vkCode"): return int(key.vkCode)
    except Exception:
        pass
    return None


def _next_mode(current_mode: str) -> str:
    if current_mode not in _MODE_ORDER:
        return _MODE_ORDER[0]
    return _MODE_ORDER[(_MODE_ORDER.index(current_mode) + 1) % len(_MODE_ORDER)]


def _midi_note(msg):
    mtype, note, vel = getattr(msg, "type", None), getattr(msg, "note", None), getattr(msg, "velocity", None)
    if mtype != "note_on" or (vel is not None and vel == 0): return None
    return note


def _is_debounced_note(note: int, last_midi_ts: dict, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    if note in last_midi_ts and now - last_midi_ts[note] < _NOTE_DEBOUNCE_SEC:
        return True
    last_midi_ts[note] = now
    return False


def _move_window(window, direction: str, step: int = 50):
    frame = window.frame(); x, y = frame.origin.x, frame.origin.y
    if direction == "left": x -= step
    elif direction == "right": x += step
    elif direction == "up": y += step
    elif direction == "down": y -= step
    window.setFrameOrigin_((x, y))


def _resize_window(window, bigger: bool, step: int = 50, min_size: int = 200):
    frame = window.frame(); w, h = frame.size.width, frame.size.height
    if bigger:
        w += step; h += step
    else:
        w = max(min_size, w - step); h = max(min_size, h - step)
    window.setFrame_display_(((frame.origin.x, frame.origin.y), (w, h)), True)


__all__ = [
    "_MODE_ORDER",
    "_NOTE_DEBOUNCE_SEC",
    "_FN_VK",
    "_FN_ARROW_MOVES",
    "_MIDI_MOVE_DIRECTIONS",
    "_MIDI_RESIZE_BIGGER",
    "_extract_vk",
    "_next_mode",
    "_midi_note",
    "_is_debounced_note",
    "_move_window",
    "_resize_window",
]
