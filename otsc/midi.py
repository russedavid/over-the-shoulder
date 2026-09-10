"""Channel-1 keypad bindings, dispatched onto the application's event queue."""

import os
import threading
import time

from otsc.privacy import redact

NOTE_ACTIONS = {
    36: "older_output",       # Knob counterclockwise
    37: "newer_output",       # Knob clockwise; pressing it changes hardware mode
    38: "help",               # Circle
    39: "follow",             # Triangle
    40: "pin",                # Square
    41: "visibility",         # X
    42: "view_artifact",      # M1
    43: "new_task",           # Num/Clear
    44: "previous_artifact",  # /
    45: "next_artifact",      # *
    46: "smaller",            # -
    47: "view_conversation",  # M2
    48: "page_up",            # 7
    49: "up",                 # 8
    50: "page_down",          # 9
    51: "bigger",             # +
    52: "view_context",       # M3
    53: "left",               # 4
    54: "down",               # 5
    55: "right",              # 6
    56: "view_files",         # M4
    57: "copy_clean",         # 1
    58: "center",             # 2
    59: "capture",            # 3
    60: "latest_output",      # Enter
    61: "view_history",       # M5
    62: "voice_question",     # 0
    63: "click_through",      # .
}
KNOB_NOTES = {36, 37}


class MidiInput:
    def __init__(self, events, *, port_name=None, backend_factory=None, poll_seconds=1.0, clock=time.monotonic):
        self.events = events
        self.port_name = port_name if port_name is not None else os.getenv("OTSC_MIDI_PORT", "")
        self.backend_factory = backend_factory
        self.poll_seconds, self.clock = poll_seconds, clock
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.last_notes = {}
        self.last_status = None
        self.thread = None

    def receive(self, message):
        if self.stop_event.is_set() or getattr(message, "type", None) != "note_on":
            return
        # MIDI channel 1 is channel 0 in mido's API.
        if getattr(message, "channel", None) != 0:
            return
        if getattr(message, "velocity", 0) <= 0:
            return
        note = getattr(message, "note", None)
        action = NOTE_ACTIONS.get(note)
        if not action:
            return
        now = self.clock()
        with self.lock:
            if note not in KNOB_NOTES and now - self.last_notes.get(note, float("-inf")) < 0.15:
                return
            self.last_notes[note] = now
        self.events.put({"type": "midi", "note": note, "action": action})

    def status(self, text):
        if text != self.last_status:
            self.last_status = text
            self.events.put({"type": "midi_status", "message": redact(text)})

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="otsc-midi", daemon=True)
        self.thread.start()

    def _run(self):
        port = None
        try:
            if self.backend_factory:
                backend = self.backend_factory()
            else:
                import mido

                backend = mido.Backend("mido.backends.rtmidi")
            while not self.stop_event.is_set():
                try:
                    names = backend.get_input_names()
                    if port is not None and (port.closed or port.name not in names):
                        port.close()
                        port = None
                    if port is None:
                        target = self.port_name if self.port_name in names else None
                        if not self.port_name and names:
                            target = names[0]
                        if target:
                            port = backend.open_input(target, callback=self.receive)
                            self.status("MIDI connected: " + target)
                        else:
                            self.status(
                                "MIDI waiting for " + self.port_name
                                if self.port_name
                                else "MIDI: no controller connected"
                            )
                except Exception as error:
                    if port is not None:
                        port.close()
                        port = None
                    self.status("MIDI: " + str(error))
                self.stop_event.wait(self.poll_seconds)
        except Exception as error:
            self.status("MIDI unavailable: " + str(error))
        finally:
            if port is not None:
                port.close()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.5)
