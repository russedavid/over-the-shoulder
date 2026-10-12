"""Capture coordinates must stay correct on displays above/left of the primary."""

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from otsc.capture_region import initial_rect, intersects_display, rect_from_region, region_from_rect


class CaptureRegionTests(unittest.TestCase):
    def test_negative_screen_region_survives_saved_preferences(self):
        from otsc.settings import Settings, load_settings, save_settings

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            selected = (-612, -520, 600, 320)
            save_settings(Settings(screen_region=selected), root)
            self.assertEqual(load_settings(root).screen_region, selected)
            save_settings(Settings(screen_region=None), root)
            self.assertIsNone(load_settings(root).screen_region)

    def test_selected_global_region_is_passed_to_one_shot_capture_unchanged(self):
        from otsc.capture import ScreenCapture

        region = (-600, -690, 900, 500)
        frame, callback = Image.new("RGB", (30, 20), "white"), Mock()
        screen = ScreenCapture()
        screenshot = Mock(return_value=frame)
        with patch.dict(sys.modules, {"pyautogui": SimpleNamespace(screenshot=screenshot)}), \
             patch.object(screen, "interpret", return_value=("text", "image")) as interpret:
            self.assertEqual(screen.capture(region, on_captured=callback, full_resolution=True), ("text", "image"))
            screenshot.assert_called_once_with(region=region)
            callback.assert_called_once_with()
            interpret.assert_called_once_with(frame, keep_image=False, full_resolution=True)

    def test_default_uses_one_explicit_primary_display_rectangle_on_macos(self):
        from otsc.capture import ScreenCapture

        screenshot = Mock(return_value=Image.new("RGB", (30, 20), "white"))
        quartz = SimpleNamespace(CGMainDisplayID=lambda: 1,
                                 CGDisplayBounds=lambda _: SimpleNamespace(origin=(0, 0), size=(2336, 1510)))
        screen = ScreenCapture()
        with patch("otsc.capture.sys.platform", "darwin"), \
             patch.dict(sys.modules, {"pyautogui": SimpleNamespace(screenshot=screenshot), "Quartz": quartz}), \
             patch.object(screen, "interpret"):
            screen.capture()
        screenshot.assert_called_once_with(region=(0, 0, 2336, 1510))

    def test_round_trip_across_primary_left_right_and_upper_displays(self):
        for rect, expected in [
            ((100, 200, 800, 500), (100, 810, 800, 500)),
            ((-1800, 200, 600, 400), (-1800, 910, 600, 400)),
            ((2400, 100, 800, 600), (2400, 810, 800, 600)),
            ((-600, 1700, 900, 500), (-600, -690, 900, 500)),
            ((100, -800, 800, 500), (100, 1810, 800, 500)),
            ((-100, 1400, 700, 600), (-100, -490, 700, 600)),
        ]:
            with self.subTest(rect=rect):
                self.assertEqual(region_from_rect(rect, 1510), expected)
                self.assertEqual(rect_from_region(expected, 1510), rect)

    def test_uses_primary_display_top_not_the_active_display_height(self):
        displays = [(0, 0, 2336, 1510), (-772, 1510, 3840, 2160)]
        selected = initial_rect(None, displays, preferred=1)
        self.assertEqual(region_from_rect(selected, 1510), (668, -1380, 960, 600))
        self.assertEqual(initial_rect((668, -1380, 960, 600), displays), selected)

    def test_disconnected_region_reopens_on_a_visible_display_and_spanning_is_allowed(self):
        displays = [(0, 0, 1920, 1080), (-1920, 0, 1920, 1080)]
        stale = (-5000, 0, 600, 400)
        recovered = initial_rect(stale, displays, preferred=1)
        self.assertTrue(intersects_display(recovered, displays))
        spanning = (-300, 200, 600, 400)
        self.assertEqual(initial_rect(spanning, displays), rect_from_region(spanning, 1080))

    def test_fractional_window_geometry_covers_the_clear_interior(self):
        self.assertEqual(region_from_rect((10.25, 20.25, 100.5, 200.5), 1080), (10, 859, 101, 201))
        with self.assertRaises(ValueError):
            rect_from_region((0, 0, 0, 10), 1080)
        with self.assertRaises(ValueError):
            initial_rect(None, [])
