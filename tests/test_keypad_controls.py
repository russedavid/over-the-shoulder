import queue
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from otsc.midi import MidiInput
from otsc.output_browser import CONTEXT, EVENTS, FILES, REPLIES


@unittest.skipUnless(sys.platform == "darwin", "Native controller uses PyObjC")
class KeypadControllerTests(unittest.TestCase):
    def setUp(self):
        from otsc.app import Controller

        self.Controller = Controller
        self.events = queue.Queue()
        self.now = 0
        self.midi = MidiInput(self.events, clock=lambda: self.now)
        self.ui = SimpleNamespace(
            **{name: Mock() for name in (
                "helpNow_", "captureNow_", "newTask_", "toggleVisibility_", "olderOutput_", "newerOutput_",
                "latestOutput_", "toggleRunning_", "togglePin_", "copyClean_", "toggleClickThrough_",
                "voice_question", "update_navigation_controls", "show_frozen_status", "set_debug",
                "show_selected_output", "advance_page",
            )},
            window=Mock(), browser=Mock(), debug_mode=False, graph_scroll=Mock(), body_scroll=Mock(), image_scroll=Mock(), code_scroll=Mock(),
        )
        self.ui.browser.actionable.return_value = "artifact:code"
        self.ui.browser.state.types = {REPLIES: None}
        self.ui.window.frame.return_value = SimpleNamespace(
            origin=SimpleNamespace(x=100, y=200), size=SimpleNamespace(width=900, height=750),
        )
        self.ui.window.minSize.return_value = SimpleNamespace(width=820, height=650)
        self.ui.page_output = lambda step: Controller.page_output(self.ui, step)
        self.ui.output_scroll = lambda: Controller.output_scroll(self.ui)
        self.ui.image_scroll.isHidden.return_value = True
        self.ui.code_scroll.isHidden.return_value = True
        self.ui.cycle_artifact = lambda step: Controller.cycle_artifact(self.ui, step)

    def press(self, number):
        self.now += 0.2
        self.midi.receive(SimpleNamespace(type="note_on", channel=0, note=number, velocity=100))
        event = self.events.get_nowait()
        self.Controller.midi_action(self.ui, event["action"])

    def test_history_assistance_and_visibility_keys_reach_the_native_handlers(self):
        for number, method in {
            36: "olderOutput_", 37: "newerOutput_", 38: "helpNow_", 39: "toggleRunning_", 40: "togglePin_",
            41: "toggleVisibility_", 43: "newTask_", 57: "copyClean_", 59: "captureNow_",
            60: "latestOutput_", 63: "toggleClickThrough_",
        }.items():
            with self.subTest(note=number):
                self.press(number)
                getattr(self.ui, method).assert_called_once_with(None)
        self.press(62)
        self.ui.voice_question.assert_called_once_with()

    def test_eight_four_five_six_form_the_arrow_cluster(self):
        for note, target in ((49, (100, 250)), (53, (50, 200)), (54, (100, 150)), (55, (150, 200))):
            with self.subTest(note=note):
                self.press(note)
                self.ui.window.setFrameOrigin_.assert_called_with(target)
        self.press(58)
        self.ui.window.center.assert_called_once_with()

    def test_plus_grows_and_minus_shrinks_without_crossing_the_minimum(self):
        self.press(51)
        self.assertEqual(self.ui.window.setFrame_display_.call_args.args[0][1], (950, 800))
        self.press(46)
        self.assertEqual(self.ui.window.setFrame_display_.call_args.args[0][1], (850, 700))
        self.ui.window.frame.return_value.size = SimpleNamespace(width=820, height=650)
        self.press(46)
        self.assertEqual(self.ui.window.setFrame_display_.call_args.args[0][1], (820, 650))

    def test_m_keys_return_to_outputs_or_explicitly_enter_debug_views(self):
        for note, key, debug in ((42, "artifact:code", False), (47, REPLIES, False), (52, CONTEXT, True), (56, FILES, True), (61, EVENTS, True)):
            self.press(note)
            self.ui.set_debug.assert_called_with(debug)
            self.ui.browser.select_type.assert_called_with(key)
            self.ui.show_selected_output.assert_called()

    def test_slash_and_star_cycle_types_separately_from_version_history(self):
        self.press(44)
        self.ui.browser.cycle_type.assert_called_with(-1, debug=False)
        self.press(45)
        self.ui.browser.cycle_type.assert_called_with(1, debug=False)
        self.ui.show_selected_output.assert_called()

    def test_page_keys_freeze_and_scroll_the_active_pane_without_switching_artifacts(self):
        for graph_visible in (False, True):
            with self.subTest(graph_visible=graph_visible):
                self.ui.graph_scroll.isHidden.return_value = not graph_visible
                scroll = self.ui.graph_scroll if graph_visible else self.ui.body_scroll
                clip = scroll.contentView.return_value
                clip.bounds.return_value = SimpleNamespace(origin=SimpleNamespace(x=0, y=100), size=SimpleNamespace(height=200))
                scroll.documentView.return_value.bounds.return_value = SimpleNamespace(size=SimpleNamespace(height=650))
                self.press(48)
                clip.scrollToPoint_.assert_called_with((0, 0))
                self.press(50)
                clip.scrollToPoint_.assert_called_with((0, 276))
                clip.bounds.return_value.origin.y = 450
                self.press(50)
                clip.scrollToPoint_.assert_called_with((0, 450))
        self.ui.browser.freeze.assert_called()
        self.ui.browser.cycle_type.assert_not_called()
        self.ui.advance_page.assert_not_called()


if __name__ == "__main__":
    unittest.main()
