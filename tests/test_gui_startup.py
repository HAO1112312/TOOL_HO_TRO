"""Windows startup integration with hidden Tk; no Odx, network or key injection."""
import gc
import ctypes
import json
import os
from contextlib import contextmanager, ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch


@unittest.skipUnless(os.name == "nt", "Windows desktop application")
class GuiStartupTests(unittest.TestCase):
    @contextmanager
    def invisible_layout_app(self):
        """Map at zero opacity without activation so Tk allocates real bounds."""
        import tkinter as tk
        from ctypes import wintypes
        import odx_gui as gui
        with tempfile.TemporaryDirectory(prefix="odx-gui-layout-") as temporary, ExitStack() as stack:
            Path(temporary, "autopilot_config.json").write_text(json.dumps({
                "batches": ["test-"], "current_batch": "test-", "sub_task": "01"}), encoding="utf-8")
            for replacement in (
                patch.object(gui.be, "TOOL_DIR", temporary),
                patch.object(gui.be, "set_task_box"), patch.object(gui.be, "set_clipboard"),
                patch.object(gui.be, "cleanup_all_odx"), patch.object(gui.be, "find_odx_window", return_value=0),
                patch.object(gui, "log_event"), patch.object(gui.subprocess, "Popen"),
                patch.object(gui.OdxAutoPilotGUI, "start_hotkey_listener"),
                patch.object(gui.OdxAutoPilotGUI, "start_system_health_checks"),
                patch.object(gui.OdxAutoPilotGUI, "run_check_update"),
                patch.object(gui.OdxAutoPilotGUI, "sync_settings_tick"),
            ):
                stack.enter_context(replacement)
            root, app = tk.Tk(), None
            root.withdraw()
            root.attributes("-alpha", 0)
            callbacks = []
            root.report_callback_exception = lambda *args: callbacks.append(args)
            try:
                app = gui.OdxAutoPilotGUI(root, initial_mode="host")
                root.update_idletasks()
                self.assertEqual(root.state(), "withdrawn")
                api = ctypes.WinDLL("user32", use_last_error=True)
                api.GetParent.argtypes = [wintypes.HWND]
                api.GetParent.restype = wintypes.HWND
                api.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                api.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
                api.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
                api.GetForegroundWindow.restype = wintypes.HWND
                window = api.GetParent(root.winfo_id()) or root.winfo_id()
                # Invisible test UI must neither take focus nor intercept clicks.
                style = api.GetWindowLongW(window, -20)
                api.SetWindowLongW(window, -20, style | 0x08000000 | 0x20 | 0x80)
                foreground = api.GetForegroundWindow()
                api.ShowWindow(window, 4)  # SW_SHOWNOACTIVATE
                self.assertEqual(api.GetForegroundWindow(), foreground)
                root.update()
                yield root, app
                self.assertNotEqual(api.GetForegroundWindow(), window)
                self.assertEqual(float(root.attributes("-alpha")), 0)
                self.assertEqual(callbacks, [])
            finally:
                if app is not None and app.is_running:
                    app.shutdown_now()
                elif app is None:
                    root.destroy()
                app, root = None, None
                gc.collect()

    def test_long_task_and_status_wrap_without_clipping_or_covering_timer(self):
        with self.invisible_layout_app() as (root, app):
            self.assertGreaterEqual(root.winfo_width(), min(1040, root.winfo_screenwidth() - 80))
            app.lbl_task_display.config(text="ĐÃ CÓ TRÊN MÁY: " + "capture-task-" * 24)
            app.lbl_host_status.config(text="Chờ Odx xử lý dữ liệu và xác nhận phòng. " * 10)
            heights = {}
            for width in (880, 1040):
                with self.subTest(width=width):
                    root.geometry(f"{width}x1000")
                    root.update()
                    root.update_idletasks()
                    warning, status, timer = app.lbl_task_display, app.lbl_host_status, app.lbl_timer
                    for label in (warning, status):
                        self.assertGreater(label.winfo_width(), 100)
                        self.assertLessEqual(label.winfo_x() + label.winfo_width(), label.master.winfo_width())
                        self.assertLessEqual(int(float(label.cget("wraplength"))), label.winfo_width())
                        self.assertGreater(label.winfo_reqheight(), 30)
                        self.assertGreaterEqual(label.winfo_height(), label.winfo_reqheight())
                    self.assertLess(status.winfo_x() + status.winfo_width(), timer.winfo_x())
                    self.assertGreaterEqual(timer.winfo_width(), timer.winfo_reqwidth())
                    self.assertLessEqual(timer.winfo_x() + timer.winfo_width(), timer.master.winfo_width())
                    self.assertLessEqual(app.btn_auto_fix.winfo_x() + app.btn_auto_fix.winfo_width(),
                                         app.btn_auto_fix.master.winfo_width())
                    heights[width] = status.winfo_height()
            self.assertGreater(heights[880], heights[1040])

    def test_three_choices_keep_their_full_height_in_initial_window(self):
        import tkinter as tk
        with self.invisible_layout_app() as (root, app):
            app.show_abort_choices()
            root.update()
            root.update_idletasks()
            panel = app.frame_host_choices
            self.assertGreaterEqual(panel.winfo_height(), panel.winfo_reqheight())
            self.assertLessEqual(panel.winfo_rooty() + panel.winfo_height(),
                                 root.winfo_rooty() + root.winfo_height())
            row = panel.winfo_children()[1]
            choices = [widget for widget in row.winfo_children() if isinstance(widget, tk.Button)]
            self.assertEqual(len(choices), 3)
            for button in choices:
                self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
                self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
                self.assertLessEqual(button.winfo_x() + button.winfo_width(), row.winfo_width())

    def test_game_and_action_preferences_fit_host_controls_at_supported_widths(self):
        with self.invisible_layout_app() as (root, app):
            for width in (880, 1040):
                with self.subTest(width=width):
                    root.geometry(f"{width}x1000")
                    root.update()
                    root.update_idletasks()
                    for widget in (app.combo_game, app.check_test_mode, app.check_action_prompt):
                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth())
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), widget.master.winfo_width())
                    self.assertEqual(tuple(app.combo_game.cget("values")), ("Arma 3", "Arma Reforger"))

    def test_real_hidden_tk_overlay_constructs_and_shuts_down(self):
        import tkinter as tk
        import odx_gui as gui
        callbacks = []
        with tempfile.TemporaryDirectory(prefix="odx-gui-startup-") as temporary:
            config = Path(temporary) / "autopilot_config.json"
            config.write_text(json.dumps({"batches": ["test-"], "current_batch": "test-", "sub_task": "01"}), encoding="utf-8")
            with patch.object(gui.be, "TOOL_DIR", temporary), patch.object(gui.be, "set_task_box") as task_write, patch.object(gui.be, "set_clipboard"), patch.object(gui.be, "cleanup_all_odx"), patch.object(gui.be, "find_odx_window", return_value=0), patch.object(gui, "log_event"), patch.object(gui.OdxAutoPilotGUI, "start_hotkey_listener"), patch.object(gui.OdxAutoPilotGUI, "start_system_health_checks"), patch.object(gui.OdxAutoPilotGUI, "run_check_update"), patch.object(gui.subprocess, "Popen") as launch:
                root = tk.Tk()
                root.withdraw()
                root.report_callback_exception = lambda *args: callbacks.append(args)
                app = None
                try:
                    app = gui.OdxAutoPilotGUI(root, initial_mode="host")
                    root.update_idletasks()
                    self.assertEqual(app.active_task_id, "test-01")
                    self.assertEqual(root.state(), "withdrawn")
                    self.assertIs(app.overlay.root, root)
                    action_buttons = [widget for widget in app.btn_host_start.master.winfo_children()
                                      if isinstance(widget, tk.Button)]
                    self.assertEqual(action_buttons, [app.btn_host_start, app.btn_host_emergency])
                    for old_name in ("btn_host_stop", "btn_host_abort", "btn_host_cancel"):
                        self.assertFalse(hasattr(app, old_name))
                    app.show_abort_choices()
                    root.update_idletasks()
                    self.assertEqual(app.frame_host_choices.winfo_manager(), "pack")
                    choice_row = app.frame_host_choices.winfo_children()[1]
                    choices = [widget for widget in choice_row.winfo_children() if isinstance(widget, tk.Button)]
                    self.assertEqual(len(choices), 3)
                    with patch.object(app, "host_choose_option") as choose:
                        for button, expected in zip(choices, ("FIX", "NEXT", "CANCEL")):
                            button.invoke()
                            self.assertEqual(choose.call_args.args, (expected,))
                    app.abort_state = None
                    app.hide_host_choices()
                    # Exercise key routing on state created by the real constructor.
                    app.odx_opened = True
                    with patch.object(app, "host_trigger_start") as start:
                        app.btn_host_start.config(state="normal")
                        app.btn_host_start.invoke()
                        start.assert_called_once_with()
                    app.is_recording = True
                    app.btn_host_emergency.config(state="normal")
                    with patch.object(app, "request_host_stop") as stop:
                        app.handle_host_hotkey(0x7A)
                        stop.assert_not_called()
                        app.btn_host_emergency.invoke()
                        stop.assert_called_once_with("emergency")
                    app.is_recording = False
                    app.odx_opened = False
                    app.switch_mode("AUDIT")
                    self.assertEqual(app.current_mode, "HOST")
                    self.assertEqual(app.view_mode, "AUDIT")
                    root.after(100, app.shutdown_now)
                    root.mainloop()
                    self.assertFalse(app.is_running)
                    self.assertEqual(callbacks, [])
                    launch.assert_not_called()
                    task_write.assert_not_called()
                finally:
                    if app is None or app.is_running:
                        root.destroy()
                    # Tk widgets and bound callbacks form cycles. Dispose them
                    # on the creating thread before later Ready workers can
                    # trigger Python's cyclic GC in a background thread.
                    app = None
                    root = None
                    gc.collect()

    def test_duplicate_instance_never_installs_job_or_hotkeys(self):
        import odx_gui as gui
        guard = Mock()
        guard.acquire.return_value = False
        root = Mock()
        with patch("odx_instance.SingleInstance", return_value=guard), patch.object(gui.tk, "Tk", return_value=root), patch.object(gui.messagebox, "showinfo") as message, patch.object(gui.be, "setup_process_protection") as protection, patch.object(gui, "OdxAutoPilotGUI") as construct:
            self.assertEqual(gui.main(), 1)
        construct.assert_not_called()
        protection.assert_not_called()
        message.assert_called_once()
        root.destroy.assert_called_once()

    def test_startup_failure_releases_single_instance_lock(self):
        import odx_gui as gui
        guard = Mock()
        guard.acquire.return_value = True
        with patch("odx_instance.SingleInstance", return_value=guard), patch.object(gui.tk, "Tk", side_effect=RuntimeError("Tk unavailable")), patch.object(gui.be, "setup_process_protection"), self.assertRaisesRegex(RuntimeError, "Tk unavailable"):
            gui.main()
        guard.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
