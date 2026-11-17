"""Window restoration regressions; no Odx, capture, input, or network."""
import gc
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from odx_window import WindowPreferences, fit_bounds, get_work_area


class Root:
    def __init__(self, state="normal", requested=(900, 760)):
        self.tk = None
        self.current_state = state
        self.requested = requested
        self.bounds = "880x760+20+30"
        self.callbacks = {}
        self.canceled = []
        self.bound = []
        self.state_changes = []
        self.next_id = 0

    def update_idletasks(self):
        pass

    def winfo_reqwidth(self):
        return self.requested[0]

    def winfo_reqheight(self):
        return self.requested[1]

    def winfo_vrootx(self):
        return 0

    def winfo_vrooty(self):
        return 0

    def winfo_vrootwidth(self):
        return 1920

    def winfo_vrootheight(self):
        return 1080

    def state(self, state=None):
        if state is not None:
            self.state_changes.append(state)
            self.current_state = state
        return self.current_state

    def geometry(self, value=None):
        if value is not None:
            self.bounds = value
        return self.bounds

    def minsize(self, width, height):
        self.minimum = width, height

    def bind(self, event, callback, add=None):
        self.bound.append((event, callback, add))
        return "root-configure"

    def unbind(self, event, identifier):
        self.unbound = event, identifier

    def after(self, milliseconds, callback):
        self.next_id += 1
        self.callbacks[self.next_id] = callback
        return self.next_id

    def after_cancel(self, identifier):
        self.canceled.append(identifier)
        self.callbacks.pop(identifier, None)


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "autopilot_ui.json"
        self.area = patch("odx_window.get_work_area", return_value=(0, 0, 1920, 1040))
        self.work_area = self.area.start()
        self.addCleanup(self.area.stop)

    def saved(self, width=1200, height=850, x=70, y=40, maximized=False):
        self.path.write_text(json.dumps(dict(normal=dict(width=width, height=height, x=x, y=y),
                                             maximized=maximized)), encoding="utf-8")

    def test_first_start_fits_text_and_higher_dpi_natural_size(self):
        for requested, expected in (((900, 760), (1040, 840)), ((1400, 900), (1400, 900))):
            with self.subTest(requested=requested):
                root = Root(requested=requested)
                preferences = WindowPreferences(root, self.path)
                self.assertEqual(preferences.restore()[:2], expected)
                self.assertEqual(root.current_state, "normal")
                self.assertEqual(root.bound[0][2], "+")
                self.assertFalse(self.path.exists(), "Restoring must not write user files")

    def test_small_work_area_limits_window_and_minimum_size(self):
        self.work_area.return_value = (0, 0, 800, 600)
        root = Root(requested=(1600, 1000))
        bounds = WindowPreferences(root, self.path).restore()
        self.assertEqual(bounds, (780, 552, 0, 0))
        self.assertEqual(root.minimum, (780, 552))

    def test_scaled_window_frame_is_kept_inside_work_area(self):
        self.work_area.return_value = (0, 0, 1280, 720)
        root = Root(requested=(1800, 1200))
        with patch("odx_window._frame_padding", return_value=(36, 86)):
            bounds = WindowPreferences(root, self.path).restore()
        self.assertEqual(bounds, (1244, 634, 0, 0))
        self.assertEqual(root.minimum, (820, 620))

    def test_user_resize_and_position_survive_restart_without_manual_drag(self):
        root = Root()
        first = WindowPreferences(root, self.path)
        first.restore()
        root.bounds = "1320x880+140+65"
        first.close()
        second = WindowPreferences(Root(), self.path)
        self.assertEqual(second.restore(), (1320, 880, 140, 65))

    def test_removed_monitor_and_huge_window_are_clamped_to_work_area(self):
        self.saved(width=5000, height=3000, x=7000, y=-900)
        bounds = WindowPreferences(Root(), self.path).restore()
        self.assertEqual(bounds, (1900, 992, 0, 0))

    def test_existing_left_monitor_keeps_negative_native_position(self):
        self.saved(width=1100, height=800, x=-1800, y=40)
        self.work_area.return_value = (-1920, 0, 0, 1040)
        self.assertEqual(WindowPreferences(Root(), self.path).restore(), (1100, 800, -1800, 40))
        self.assertEqual(self.work_area.call_args.args[1], (1100, 800, -1800, 40))

    def test_invalid_preferences_recover_to_default(self):
        for content in ('broken json', '[]', '{"normal":{"width":true,"height":800,"x":0,"y":0}}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                self.assertEqual(WindowPreferences(Root(), self.path).restore()[:2], (1040, 840))

    def test_maximized_and_minimized_states_preserve_normal_bounds(self):
        root = Root()
        preferences = WindowPreferences(root, self.path)
        preferences.restore()
        root.bounds = "1250x850+50+45"
        preferences._on_configure(types.SimpleNamespace(widget=root))
        root.current_state = "zoomed"
        root.bounds = "1920x1040+0+0"
        preferences._on_configure(types.SimpleNamespace(widget=root))
        root.current_state = "iconic"
        preferences.close()
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["normal"], dict(width=1250, height=850, x=50, y=45))
        self.assertTrue(saved["maximized"])
        reopened = Root()
        restored = WindowPreferences(reopened, self.path)
        self.assertEqual(restored.restore(), (1250, 850, 50, 45))
        self.assertEqual(reopened.state_changes, ["zoomed"])

    def test_restoring_saved_maximized_state_does_not_map_hidden_root(self):
        self.saved(maximized=True)
        root = Root(state="withdrawn")
        preferences = WindowPreferences(root, self.path)
        preferences.restore()
        preferences.close()
        self.assertEqual(root.current_state, "withdrawn")
        self.assertEqual(root.state_changes, [])
        self.assertTrue(json.loads(self.path.read_text())["maximized"])

    def test_child_configure_is_ignored_and_resize_saves_are_debounced(self):
        root = Root()
        preferences = WindowPreferences(root, self.path)
        preferences.restore()
        preferences._on_configure(types.SimpleNamespace(widget=object()))
        self.assertFalse(root.callbacks)
        preferences._on_configure(types.SimpleNamespace(widget=root))
        first = preferences._after_id
        root.bounds = "1280x840+100+45"
        preferences._on_configure(types.SimpleNamespace(widget=root))
        self.assertEqual(root.canceled, [first])
        self.assertFalse(self.path.exists())
        root.callbacks[preferences._after_id]()
        self.assertEqual(json.loads(self.path.read_text())["normal"]["width"], 1280)

    def test_failed_atomic_write_keeps_previous_preferences(self):
        self.saved()
        previous = self.path.read_bytes()
        preferences = WindowPreferences(Root(), self.path)
        preferences.restore()
        with patch("odx_window.os.replace", side_effect=PermissionError("file locked")):
            self.assertFalse(preferences._save())
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertIn("file locked", preferences.last_error)
        self.assertEqual(list(self.path.parent.glob(".odx-window-*.tmp")), [])

    def test_close_saves_once_and_cancels_only_its_binding(self):
        root = Root()
        preferences = WindowPreferences(root, self.path)
        preferences.restore()
        preferences._on_configure(types.SimpleNamespace(widget=root))
        preferences.close()
        self.assertEqual(root.unbound, ("<Configure>", "root-configure"))
        self.assertTrue(root.canceled)
        with patch.object(preferences, "_save") as save:
            preferences.close()
        save.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows Tk work area and placement")
class NativeWindowTests(unittest.TestCase):
    def test_hidden_tk_keeps_visibility_and_native_work_area(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        preferences = None
        try:
            with tempfile.TemporaryDirectory(prefix="odx-window-test-") as temporary:
                tk.Label(root, text="Window sizing fixture").pack()
                preferences = WindowPreferences(root, Path(temporary) / "ui.json")
                bounds = preferences.restore()
                left, top, right, bottom = get_work_area(root, bounds)
                self.assertEqual(root.state(), "withdrawn")
                self.assertLessEqual(bounds[0] + 20, right - left)
                self.assertLessEqual(bounds[1] + 48, bottom - top)
                preferences.close()
                self.assertEqual(root.state(), "withdrawn")
        finally:
            if preferences is not None:
                preferences.close()
            root.destroy()
            preferences = root = None
            gc.collect()


if __name__ == "__main__":
    unittest.main()
