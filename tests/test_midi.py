import queue
import time
import unittest
from types import SimpleNamespace

from otsc.midi import MidiInput


def note(number, *, kind="note_on", velocity=100):
    return SimpleNamespace(type=kind, note=number, velocity=velocity)


class MidiTests(unittest.TestCase):
    def test_original_controls_keep_their_note_numbers(self):
        events = queue.Queue()
        controller = MidiInput(events)
        expected = {
            38: "voice_followup",
            39: "voice_question",
            40: "smaller",
            41: "bigger",
            42: "left",
            43: "visibility",
            44: "down",
            45: "up",
            46: "right",
            47: "help",
            48: "next_view",
            49: "next_page",
            50: "capture",
            51: "new_task",
        }
        for number, action in expected.items():
            controller.receive(note(number))
            self.assertEqual(events.get_nowait(), {"type": "midi", "note": number, "action": action})

    def test_releases_other_message_types_and_duplicate_presses_do_not_trigger(self):
        events = queue.Queue()
        now = [0.0]
        controller = MidiInput(events, clock=lambda: now[0])
        controller.receive(note(47))
        now[0] = 0.10
        controller.receive(note(47))
        controller.receive(note(50))
        controller.receive(note(47, kind="note_off"))
        controller.receive(note(47, velocity=0))
        controller.receive(note(47, kind="control_change"))
        controller.receive(note(99))
        now[0] = 0.16
        controller.receive(note(47))
        self.assertEqual([events.get_nowait()["action"] for _ in range(events.qsize())], ["help", "capture", "help"])
        controller.close()
        now[0] = 1.0
        controller.receive(note(47))
        self.assertTrue(events.empty())

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
            backend.ports[0].callback(note(43))
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
