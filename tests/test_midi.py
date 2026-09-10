import queue
import time
import unittest
from types import SimpleNamespace

from otsc.midi import MidiInput


def note(number, *, kind="note_on", velocity=100, channel=0):
    return SimpleNamespace(type=kind, note=number, velocity=velocity, channel=channel)


class MidiTests(unittest.TestCase):
    def test_keypad_notes_follow_the_physical_layout(self):
        events = queue.Queue()
        controller = MidiInput(events)
        expected = {
            36: "older_output", 37: "newer_output",
            38: "help", 39: "follow", 40: "pin", 41: "visibility",
            42: "view_artifact", 43: "new_task", 44: "previous_artifact", 45: "next_artifact", 46: "smaller",
            47: "view_conversation", 48: "page_up", 49: "up", 50: "page_down", 51: "bigger",
            52: "view_context", 53: "left", 54: "down", 55: "right",
            56: "view_files", 57: "copy_clean", 58: "center", 59: "capture", 60: "latest_output",
            61: "view_history", 62: "voice_question", 63: "click_through",
        }
        for number, action in expected.items():
            controller.receive(note(number))
            self.assertEqual(events.get_nowait(), {"type": "midi", "note": number, "action": action})

    def test_releases_other_message_types_and_duplicate_presses_do_not_trigger(self):
        events = queue.Queue()
        now = [0.0]
        controller = MidiInput(events, clock=lambda: now[0])
        controller.receive(note(38))
        now[0] = 0.10
        controller.receive(note(38))
        controller.receive(note(59))
        controller.receive(note(38, kind="note_off"))
        controller.receive(note(38, velocity=0))
        controller.receive(note(38, kind="control_change"))
        controller.receive(note(99))
        now[0] = 0.16
        controller.receive(note(38))
        self.assertEqual([events.get_nowait()["action"] for _ in range(events.qsize())], ["help", "capture", "help"])
        controller.close()
        now[0] = 1.0
        controller.receive(note(38))
        self.assertTrue(events.empty())

    def test_fast_knob_turns_preserve_every_detent_and_direction(self):
        events = queue.Queue()
        now = [0.0]
        controller = MidiInput(events, clock=lambda: now[0])
        for number in (36, 36, 36, 37, 37, 36):
            controller.receive(note(number))
            controller.receive(note(number, kind="note_off"))
            now[0] += 0.005
        self.assertEqual([events.get_nowait()["action"] for _ in range(events.qsize())],
                         ["older_output", "older_output", "older_output", "newer_output", "newer_output", "older_output"])

    def test_only_channel_one_is_accepted(self):
        events = queue.Queue()
        controller = MidiInput(events)
        for channel in range(1, 16):
            controller.receive(note(36, channel=channel))
            controller.receive(note(38, channel=channel))
        self.assertTrue(events.empty())
        controller.receive(note(36, channel=0))
        self.assertEqual(events.get_nowait()["action"], "older_output")

    def test_controller_reconnects_and_closes_ports_on_its_worker(self):
        class Port:
            def __init__(self, name, callback):
                self.name, self.callback, self.closed = name, callback, False

            def close(self):
                self.closed = True

        class Backend:
            def __init__(self):
                self.names = []
                self.ports = []

            def get_input_names(self):
                return list(self.names)

            def open_input(self, name, callback):
                port = Port(name, callback)
                self.ports.append(port)
                return port

        def eventually(condition):
            deadline = time.monotonic() + 2
            while not condition() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(condition())

        backend = Backend()
        events = queue.Queue()
        controller = MidiInput(events, port_name="controller", backend_factory=lambda: backend, poll_seconds=0.01)
        controller.start()
        try:
            backend.names = ["unrelated", "controller"]
            eventually(lambda: len(backend.ports) == 1)
            self.assertEqual(backend.ports[0].name, "controller")
            backend.ports[0].callback(note(41))
            backend.names = []
            eventually(lambda: backend.ports[0].closed)
            backend.names = ["controller"]
            eventually(lambda: len(backend.ports) == 2)
        finally:
            controller.close()
        self.assertTrue(all(p.closed for p in backend.ports))
        self.assertFalse(controller.thread.is_alive())
        messages = [events.get_nowait() for _ in range(events.qsize())]
        self.assertTrue(any(e.get("action") == "visibility" for e in messages))


if __name__ == "__main__":
    unittest.main()
