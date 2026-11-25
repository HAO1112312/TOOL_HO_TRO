"""Native Windows color-key rendering remains text-only after HUD preferences."""
import ctypes
from ctypes import wintypes
import gc
import os
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.name == "nt", "Windows layered Tk window")
class TextOnlyOverlayNativeTests(unittest.TestCase):
    def test_action_text_keeps_transparent_background_after_opacity_changes(self):
        import tkinter as tk
        import odx_overlay as module

        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetParent.argtypes = [wintypes.HWND]
        user.GetParent.restype = wintypes.HWND
        user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetWindowLongW.restype = wintypes.LONG
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.IsWindowVisible.restype = wintypes.BOOL
        user.GetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
        user.GetLayeredWindowAttributes.restype = wintypes.BOOL

        root, overlay = tk.Tk(), None
        root.withdraw()
        callback_errors = []
        root.report_callback_exception = lambda *args: callback_errors.append(args)
        real_toplevel = tk.Toplevel

        def hidden_toplevel(*args, **kwargs):
            # Keep every helper window withdrawn, including constructor layout.
            window = real_toplevel(*args, **kwargs)
            window.withdraw()
            return window

        try:
            with patch.object(module.tk, "Toplevel", side_effect=hidden_toplevel):
                overlay = module.OdxOverlay(root=root)
            window, canvas = overlay.action_win, overlay.action_canvas
            self.assertEqual(window.cget("background"), module.ACTION_TRANSPARENT_COLOR)
            self.assertEqual(canvas.cget("background"), module.ACTION_TRANSPARENT_COLOR)
            self.assertEqual(str(window.attributes("-transparentcolor")).lower(), module.ACTION_TRANSPARENT_COLOR)
            self.assertEqual(float(canvas.cget("borderwidth")), 0)
            self.assertEqual(float(canvas.cget("highlightthickness")), 0)
            self.assertEqual(window.winfo_children(), [canvas])

            # Exercise the actual cue render without mapping visible text.
            self.assertTrue(overlay.show_action_change("private-task-01", cue_id="native-check"))
            command, args = overlay.cmd_queue.get_nowait()
            self.assertEqual(command, "SHOW_ACTION")
            with patch.object(window, "state", return_value="withdrawn"):
                self.assertTrue(overlay._show_action_ui(*args, preferences=overlay._preferences))

            rgb = root.winfo_rgb(module.ACTION_TRANSPARENT_COLOR)
            expected_key = (rgb[0] >> 8) | ((rgb[1] >> 8) << 8) | ((rgb[2] >> 8) << 16)
            for opacity in (.95, .6, 1.0):
                with self.subTest(opacity=opacity):
                    preferences = dict(module.DEFAULT_PREFERENCES, hud_opacity=opacity)
                    overlay._apply_window_preferences(preferences, root.winfo_screenwidth(), root.winfo_screenheight())
                    root.update_idletasks()
                    items = canvas.find_all()
                    self.assertTrue(items)
                    self.assertEqual({canvas.type(item) for item in items}, {"text"})
                    self.assertEqual({canvas.itemcget(item, "text") for item in items}, {module.ACTION_PROMPT_TEXT})
                    self.assertEqual(set(items), set(canvas.find_withtag("action_label")))

                    hwnd = user.GetParent(window.winfo_id()) or window.winfo_id()
                    key, alpha, flags = wintypes.DWORD(), wintypes.BYTE(), wintypes.DWORD()
                    self.assertTrue(user.GetLayeredWindowAttributes(
                        hwnd, ctypes.byref(key), ctypes.byref(alpha), ctypes.byref(flags)))
                    self.assertEqual(key.value, expected_key)
                    self.assertTrue(flags.value & 1, "LWA_COLORKEY must survive opacity updates")
                    self.assertEqual(alpha.value, round(opacity * 255))
                    style = user.GetWindowLongW(hwnd, module.GWL_EXSTYLE)
                    self.assertTrue(style & module.WS_EX_NOACTIVATE)
                    self.assertTrue(style & module.WS_EX_TRANSPARENT)
                    self.assertFalse(user.IsWindowVisible(hwnd))
            self.assertEqual(root.state(), "withdrawn")
            self.assertEqual(window.state(), "withdrawn")
        finally:
            if overlay is not None:
                overlay.close()
                root.after(80, root.quit)
                root.mainloop()
            root.destroy()
            overlay, root = None, None
            gc.collect()
        self.assertEqual(callback_errors, [])


if __name__ == "__main__":
    unittest.main()
