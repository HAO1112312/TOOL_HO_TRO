"""
ODX CAPTURE: COMPACT FLOATING OVERLAY (KHÔNG CHE MÀN HÌNH)
- Kích thước siêu nhỏ gọn (chỉ 560x44 pixel ở sát mép trên), KHÔNG BAO GIỜ DÙNG FULLSCREEN
- Hoàn toàn loại bỏ nguy cơ bị đen màn hình!
- Tàng hình tuyệt đối trước video quay mp4 (WDA_EXCLUDEFROMCAPTURE = 0x11)
- Click-through: Chuột click xuyên qua (WS_EX_TRANSPARENT), không cướp focus của game
- Hoàn toàn im lặng 100%, không phát bất kỳ âm thanh hay tiếng beep nào để tránh tạp âm dataset
"""

import sys
import os
import time
from odx_timing import format_recording_clock, remaining_seconds
import math
import hashlib
import threading
import queue
import ctypes
from ctypes import wintypes
from collections import OrderedDict
import tkinter as tk

# Win32 Constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x00000011
ACTION_TRANSPARENT_COLOR = "#010203"
ACTION_PROMPT_TEXT = "THAY ĐỔI HÀNH ĐỘNG"

DEFAULT_PREFERENCES = {
    "hud_enabled": True, "hud_show_timer": True, "hud_show_task": True,
    "hud_show_roster": True, "hud_scale": 1.0, "hud_opacity": .95,
    "hud_language": "vi", "test_mode": False, "action_prompt_enabled": True,
}

class OdxOverlay:
    def __init__(self, root=None):
        self.cmd_queue = queue.Queue()
        self._preferences = dict(DEFAULT_PREFERENCES)
        self._preferences_lock = threading.RLock()
        self._action_lock = threading.RLock()
        self._action_generation = 0
        self._action_seen = OrderedDict()
        self._action_pending = False
        self._action_needs_hide = False
        self._action_task = None
        self._overlay_closed = False
        self.ready_event = threading.Event()
        self.thread = None
        self._owns_root = root is None
        if root is None:
            self.thread = threading.Thread(target=self._run_ui, daemon=True)
            self.thread.start()
            self.ready_event.wait(timeout=5)
        else:
            self._run_ui(root)

    def _apply_stealth(self, win):
        """Áp dụng tàng hình trước video và click-through cho cửa sổ nhỏ."""
        win.update_idletasks()
        w_id = win.winfo_id()
        p_id = ctypes.windll.user32.GetParent(w_id)
        hwnd = p_id if p_id != 0 else w_id
        
        try:
            # 1. Tàng hình tuyệt đối: Không xuất hiện trong video quay
            ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            # 2. Click-through: Chuột bấm xuyên qua, không chiếm chuột game
            ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(
                hwnd, 
                GWL_EXSTYLE, 
                ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            )
        except Exception:
            pass

    def _run_ui(self, root=None):
        self.root = root if root is not None else tk.Tk()
        if self._owns_root:
            self.root.withdraw()

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        # ==========================================
        # 1. Cửa sổ Đếm ngược (Nhỏ ở tâm màn hình: 340x130)
        # ==========================================
        self.cd_win = tk.Toplevel(self.root)
        self.cd_win.overrideredirect(True)
        self.cd_win.attributes("-topmost", True)
        self.cd_win.attributes("-alpha", 0.92)
        self.cd_win.config(bg="#0f172a") # Slate 900
        cd_w, cd_h = 340, 130
        cd_x = (screen_w - cd_w) // 2
        cd_y = (screen_h - cd_h) // 2 - 60
        self.cd_win.geometry(f"{cd_w}x{cd_h}+{cd_x}+{cd_y}")

        self.cd_num_lbl = tk.Label(
            self.cd_win, text="", font=("Consolas", 48, "bold"),
            fg="#ef4444", bg="#0f172a"
        )
        self.cd_num_lbl.pack(pady=(10, 0))
        self.cd_sub_lbl = tk.Label(
            self.cd_win, text="", font=("Arial", 11, "bold"),
            fg="#cbd5e1", bg="#0f172a"
        )
        self.cd_sub_lbl.pack(pady=(2, 10))
        self._apply_stealth(self.cd_win)
        self.cd_win.withdraw() # Ẩn lúc khởi động

        # ==========================================
        # 2. Cửa sổ HUD Quay & Roster (820x46 pixel ở mép trên cùng)
        # ==========================================
        self.hud_win = tk.Toplevel(self.root)
        self.hud_win.overrideredirect(True)
        self.hud_win.attributes("-topmost", True)
        self.hud_win.attributes("-alpha", 0.92)
        self.hud_win.config(bg="#111827") # Dark Gray
        hud_w, hud_h = 1050, 46
        hud_x = (screen_w - hud_w) // 2
        hud_y = 12
        self.hud_win.geometry(f"{hud_w}x{hud_h}+{hud_x}+{hud_y}")

        self.hud_lbl = tk.Label(
            self.hud_win, text="", font=("Consolas", 11, "bold"),
            fg="#f9fafb", bg="#111827"
        )
        self.hud_lbl.pack(expand=True, fill="both")
        self._apply_stealth(self.hud_win)
        self.hud_win.withdraw()

        # ==========================================
        # 3. Cửa sổ Banner Thông báo (620x70 pixel ở phía trên)
        # ==========================================
        self.banner_win = tk.Toplevel(self.root)
        self.banner_win.overrideredirect(True)
        self.banner_win.attributes("-topmost", True)
        self.banner_win.attributes("-alpha", 0.95)
        self.banner_win.config(bg="#064e3b") # Emerald dark
        b_w, b_h = 640, 75
        b_x = (screen_w - b_w) // 2
        b_y = 70
        self.banner_win.geometry(f"{b_w}x{b_h}+{b_x}+{b_y}")

        self.banner_l1 = tk.Label(
            self.banner_win, text="", font=("Arial", 14, "bold"),
            fg="#a7f3d0", bg="#064e3b"
        )
        self.banner_l1.pack(pady=(8, 0))
        self.banner_l2 = tk.Label(
            self.banner_win, text="", font=("Arial", 11),
            fg="#ffffff", bg="#064e3b"
        )
        self.banner_l2.pack(pady=(2, 8))
        self._apply_stealth(self.banner_win)
        self.banner_win.withdraw()

        # Only outlined text is visible; the color-keyed area shows gameplay.
        self.action_win = tk.Toplevel(self.root)
        self.action_win.withdraw()
        self.action_win.overrideredirect(True)
        self.action_win.attributes("-topmost", True)
        self.action_win.config(bg=ACTION_TRANSPARENT_COLOR, borderwidth=0)
        self.action_win.attributes("-transparentcolor", ACTION_TRANSPARENT_COLOR)
        self.action_canvas = tk.Canvas(self.action_win, bg=ACTION_TRANSPARENT_COLOR,
                                       borderwidth=0, highlightthickness=0)
        self.action_canvas.pack(expand=True, fill="both")
        for index in range(8):
            self.action_canvas.create_text(0, 0, text=ACTION_PROMPT_TEXT, fill="#000000",
                                           anchor="center", justify="center",
                                           tags=("action_label", f"action_outline_{index}"))
        self.action_canvas.create_text(0, 0, text=ACTION_PROMPT_TEXT, fill="#FFE066",
                                       anchor="center", justify="center",
                                       tags=("action_label", "action_main"))
        for index in range(8):
            self.action_canvas.create_text(0, 0, text="", fill="#000000", anchor="center",
                                           justify="center", tags=("task_label", f"task_outline_{index}"))
        self.action_canvas.create_text(0, 0, text="", fill="#ffffff", anchor="center",
                                       justify="center", tags=("task_label", "task_main"))
        self._action_hide_after = None
        self._apply_stealth(self.action_win)
        self.action_win.withdraw()

        # Member errors have their own window so capture ticks and action cues
        # cannot overwrite them. Full native details remain in the Tool log.
        self.room_error_win = tk.Toplevel(self.root)
        self.room_error_win.withdraw()
        self.room_error_win.overrideredirect(True)
        self.room_error_win.attributes("-topmost", True)
        self.room_error_win.config(bg="#7f1d1d")
        self.room_error_title = tk.Label(self.room_error_win, text="", font=("Arial", 14, "bold"),
                                         fg="#fef2f2", bg="#7f1d1d", justify="center")
        self.room_error_title.pack(fill="x", padx=16, pady=(8, 0))
        self.room_error_text = tk.Label(self.room_error_win, text="", font=("Arial", 12, "bold"),
                                        fg="#fee2e2", bg="#7f1d1d", justify="center")
        self.room_error_text.pack(expand=True, fill="both", padx=16, pady=(2, 8))
        self._room_error_hide_after = None
        self._apply_stealth(self.room_error_win)
        self.room_error_win.withdraw()

        self.ready_event.set()
        ui_preferences = dict(DEFAULT_PREFERENCES)
        last_hud_kind = None
        self._apply_window_preferences(ui_preferences, screen_w, screen_h)

        # Command loop
        def poll_commands():
            nonlocal ui_preferences, last_hud_kind
            while not self.cmd_queue.empty():
                try:
                    cmd, args = self.cmd_queue.get_nowait()
                    if cmd == "PREFERENCES":
                        ui_preferences = dict(args[0])
                        self._apply_window_preferences(ui_preferences, screen_w, screen_h)
                        if last_hud_kind == "SHOW_ROSTER" and not ui_preferences["hud_show_roster"]:
                            self.hud_win.withdraw()
                    elif cmd == "SHOW_CD" and ui_preferences["hud_enabled"]:
                        num_txt, sub_txt, color = args
                        self.cd_num_lbl.config(text=num_txt, fg=color)
                        self.cd_sub_lbl.config(text=sub_txt)
                        self.cd_win.deiconify()
                    elif cmd == "HIDE_CD":
                        self.cd_win.withdraw()
                    elif (cmd in ("SHOW_HUD", "SHOW_ROSTER") and ui_preferences["hud_enabled"]
                          and (cmd != "SHOW_ROSTER" or ui_preferences["hud_show_roster"])):
                        text, fg_col = args
                        last_hud_kind = cmd
                        self.hud_lbl.config(text=text, fg=fg_col)
                        scale = ui_preferences["hud_scale"]
                        available = max(1, screen_w - 40)
                        calc_w = min(available, max(int(500 * scale), int(len(text) * 11.5 * scale) + 60))
                        calc_x = (screen_w - calc_w) // 2
                        self.hud_win.geometry(f"{calc_w}x{max(24, int(hud_h * scale))}+{calc_x}+{hud_y}")
                        self.hud_win.deiconify()
                    elif cmd == "HIDE_HUD":
                        self.hud_win.withdraw()
                    elif cmd == "SHOW_BANNER" and ui_preferences["hud_enabled"]:
                        t1, t2, bg_col, fg_col = args
                        self.banner_win.config(bg=bg_col)
                        self.banner_l1.config(text=t1, bg=bg_col, fg=fg_col)
                        self.banner_l2.config(text=t2, bg=bg_col)
                        self.banner_win.deiconify()
                    elif cmd == "HIDE_BANNER":
                        self.banner_win.withdraw()
                    elif cmd == "SHOW_ACTION":
                        self._show_action_ui(*args, preferences=ui_preferences)
                    elif cmd == "HIDE_ACTION":
                        self._hide_action_ui(*args)
                    elif cmd == "SHOW_ROOM_ERROR":
                        self._show_room_error_ui(*args, preferences=ui_preferences)
                    elif cmd == "HIDE_ROOM_ERROR":
                        self._hide_room_error_ui(*args)
                    elif cmd == "CLEAR_ALL":
                        self.cd_win.withdraw()
                        self.hud_win.withdraw()
                        self.banner_win.withdraw()
                        self._hide_action_ui(args[0] if args else None)
                    elif cmd == "QUIT":
                        self._hide_action_ui()
                        self._hide_room_error_ui()
                        self.action_win.destroy()
                        self.room_error_win.destroy()
                        self.cd_win.destroy()
                        self.hud_win.destroy()
                        self.banner_win.destroy()
                        if self._owns_root:
                            self.root.destroy()
                        return
                except Exception:
                    pass
            self.root.after(40, poll_commands)

        self.root.after(40, poll_commands)
        if self._owns_root:
            self.root.mainloop()

    # Public API
    def set_preferences(self, settings):
        """Queue display changes; callers never touch Tk from worker threads."""
        if not hasattr(self, "_preferences_lock"):
            self._preferences_lock = threading.RLock()
        with self._preferences_lock:
            return self._set_preferences(settings)

    def _set_preferences(self, settings):
        if not isinstance(settings, dict):
            return False
        previous = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        clean = dict(previous)
        for key in ("hud_enabled", "hud_show_timer", "hud_show_task", "hud_show_roster", "test_mode", "action_prompt_enabled"):
            if type(settings.get(key)) is bool:
                clean[key] = settings[key]
        for key, minimum, maximum in (("hud_scale", .5, 2.0), ("hud_opacity", .2, 1.0)):
            value = settings.get(key)
            if type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum:
                clean[key] = float(value)
        if settings.get("hud_language") in ("vi", "en"):
            clean["hud_language"] = settings["hud_language"]
        if clean != previous:
            # Replace the snapshot instead of mutating data read by render workers.
            with self._action_state():
                self._preferences = clean
                if not clean["hud_enabled"] or not clean.get("action_prompt_enabled", True):
                    self._invalidate_action()
                if not clean["hud_enabled"]:
                    self._invalidate_room_error()
                self.cmd_queue.put(("PREFERENCES", (dict(clean),)))
            return True
        return False

    def _apply_window_preferences(self, preferences, screen_w, screen_h):
        """Apply queued preferences exclusively on the overlay's Tk thread."""
        scale = preferences["hud_scale"]
        for window in (self.cd_win, self.hud_win, self.banner_win):
            window.attributes("-alpha", preferences["hud_opacity"])
            if not preferences["hud_enabled"]:
                window.withdraw()
        for label, family, size in ((self.hud_lbl, "Consolas", 11),
                                    (self.cd_num_lbl, "Consolas", 48),
                                    (self.cd_sub_lbl, "Arial", 11),
                                    (self.banner_l1, "Arial", 14),
                                    (self.banner_l2, "Arial", 11)):
            label.config(font=(family, max(6, round(size * scale)), "bold"))
        available = max(1, screen_w - 40)
        text = self.hud_lbl.cget("text")
        width = min(available, max(round(500 * scale), int(len(text) * 11.5 * scale) + 60))
        self.hud_win.geometry(f"{width}x{max(24, round(46 * scale))}+{(screen_w - width) // 2}+12")
        width, height = min(max(1, screen_w - 40), round(340 * scale)), round(130 * scale)
        self.cd_win.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{max(0, (screen_h - height) // 2 - 60)}")
        width, height = min(max(1, screen_w - 40), round(640 * scale)), round(75 * scale)
        self.banner_win.geometry(f"{width}x{height}+{(screen_w - width) // 2}+70")
        action_window = getattr(self, "action_win", None)
        if action_window is not None:
            action_window.attributes("-alpha", preferences["hud_opacity"])
            self._position_action_window(preferences, screen_w, screen_h)
            if not preferences["hud_enabled"] or not preferences.get("action_prompt_enabled", True):
                self._hide_action_ui()
        error_window = getattr(self, "room_error_win", None)
        if error_window is not None:
            error_window.attributes("-alpha", preferences["hud_opacity"])
            self._position_room_error_window(preferences, screen_w, screen_h)
            if not preferences["hud_enabled"]:
                self._hide_room_error_ui()

    def _action_state(self):
        # Public overlay helpers are also used with lightweight __new__ fixtures.
        if not hasattr(self, "_action_lock"):
            self._action_lock = threading.RLock()
            self._action_generation = 0
            self._action_seen = OrderedDict()
            self._action_pending = False
            self._action_needs_hide = False
            self._action_task = None
            self._overlay_closed = False
        return self._action_lock

    def _invalidate_action(self, enqueue=False):
        with self._action_state():
            pending = self._action_pending or self._action_needs_hide
            self._action_generation += 1
            self._action_pending = False
            self._action_task = None
            generation = self._action_generation
            if enqueue and pending:
                self.cmd_queue.put(("HIDE_ACTION", (generation,)))
            return generation

    @staticmethod
    def _fit_action_bounds(work_area, scale):
        left, top, right, bottom = work_area
        available_w, available_h = max(1, right - left), max(1, bottom - top)
        width = min(max(1, available_w - 40), max(300, round(880 * scale)))
        height = min(max(1, available_h - 40), max(100, round(190 * scale)))
        return width, height, left + (available_w - width) // 2, top + (available_h - height) // 2

    def _action_work_area(self, screen_w, screen_h):
        try:
            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                if rect.right > rect.left and rect.bottom > rect.top:
                    return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError, ValueError):
            pass
        return 0, 0, screen_w, screen_h

    def _position_action_window(self, preferences, screen_w, screen_h, task_id=None):
        scale = preferences["hud_scale"]
        width, height, x, y = self._fit_action_bounds(self._action_work_area(screen_w, screen_h), scale)
        self.action_win.geometry(f"{width}x{height}{x:+d}{y:+d}")
        title_size = max(18, min(round(36 * scale), max(18, width // 20)))
        self.action_canvas.config(width=width, height=height)
        self.action_canvas.itemconfigure("action_label", text=ACTION_PROMPT_TEXT,
                                         font=("Arial", title_size, "bold"), width=max(1, width - 40))
        task_id = task_id if task_id is not None else getattr(self, "_action_task", None)
        task_text = f"Task: {task_id}" if preferences["hud_show_task"] and task_id else ""
        self.action_canvas.itemconfigure("task_label", text=task_text,
                                         font=("Arial", max(10, min(round(14 * scale), title_size // 2)), "bold"),
                                         width=max(1, width - 40))
        title_y = height / 2 - round(16 * scale) if task_text else height / 2
        task_y = height / 2 + round(40 * scale)
        outline = max(1, round(2 * scale))
        for index, (dx, dy) in enumerate(((-1, -1), (0, -1), (1, -1), (-1, 0),
                                         (1, 0), (-1, 1), (0, 1), (1, 1))):
            self.action_canvas.coords(f"action_outline_{index}", width / 2 + dx * outline,
                                       title_y + dy * outline)
            self.action_canvas.coords(f"task_outline_{index}", width / 2 + dx, task_y + dy)
        self.action_canvas.coords("action_main", width / 2, title_y)
        self.action_canvas.coords("task_main", width / 2, task_y)

    def _hide_action_ui(self, generation=None):
        """Run only on the Tk thread; a stale timer cannot hide a later cue."""
        with self._action_state():
            if generation is not None and generation != self._action_generation:
                return False
            after = getattr(self, "_action_hide_after", None)
            self._action_hide_after = None
            self._action_pending = False
            self._action_needs_hide = False
            self._action_task = None
        if after is not None:
            try:
                self.root.after_cancel(after)
            except (tk.TclError, RuntimeError):
                pass
        window = getattr(self, "action_win", None)
        if window is not None:
            window.withdraw()
        return True

    def _show_action_ui(self, task_id, duration_ms, generation, preferences):
        with self._action_state():
            if generation != self._action_generation or self._overlay_closed:
                return False
            if not preferences["hud_enabled"] or not preferences.get("action_prompt_enabled", True):
                return False
        self._hide_action_ui(generation)
        self._position_action_window(preferences, self.root.winfo_screenwidth(), self.root.winfo_screenheight(), task_id)
        with self._action_state():
            if generation != self._action_generation or self._overlay_closed:
                return False
            self._action_pending, self._action_task, self._action_needs_hide = True, task_id, True
            # On Windows, wm state normal maps with SW_SHOWNOACTIVATE;
            # wm deiconify additionally requests raising/activation.
            self.action_win.state("normal")
            self._action_hide_after = self.root.after(duration_ms, lambda: self._hide_action_ui(generation))
        return True

    def show_action_change(self, task_id, duration_sec=5, cue_id=""):
        """Queue one silent centered prompt per round cue, without touching Tk."""
        preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        if not preferences["hud_enabled"] or not preferences.get("action_prompt_enabled", True):
            return False
        if not isinstance(task_id, str) or not task_id or len(task_id) > 128:
            return False
        if not isinstance(cue_id, str) or len(cue_id) > 256:
            return False
        if type(duration_sec) not in (int, float) or not math.isfinite(duration_sec) or duration_sec <= 0:
            return False
        duration_ms = max(100, min(30000, round(duration_sec * 1000)))
        with self._action_state():
            identity = task_id, cue_id
            current_preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
            if not current_preferences["hud_enabled"] or not current_preferences.get("action_prompt_enabled", True):
                return False
            if self._overlay_closed or identity in self._action_seen:
                return False
            self._action_seen[identity] = None
            if len(self._action_seen) > 128:
                self._action_seen.popitem(last=False)
            self._action_generation += 1
            generation = self._action_generation
            self._action_pending, self._action_task, self._action_needs_hide = True, task_id, True
            self.cmd_queue.put(("SHOW_ACTION", (task_id, duration_ms, generation)))
        return True

    def _action_capture_task(self, task_id):
        if getattr(self, "_action_task", None) not in (None, task_id):
            self._invalidate_action(enqueue=True)

    def _room_error_state(self):
        lock = self._action_state()
        with lock:
            if not hasattr(self, "_room_error_generation"):
                self._room_error_generation = 0
                self._room_error_seen = OrderedDict()
                self._room_error_needs_hide = False
        return lock

    def _invalidate_room_error(self, enqueue=False):
        with self._room_error_state():
            self._room_error_generation += 1
            generation = self._room_error_generation
            if enqueue and self._room_error_needs_hide:
                self.cmd_queue.put(("HIDE_ROOM_ERROR", (generation,)))
            return generation

    def _position_room_error_window(self, preferences, screen_w, screen_h):
        left, top, right, bottom = self._action_work_area(screen_w, screen_h)
        scale = preferences["hud_scale"]
        available_w, available_h = max(1, right - left), max(1, bottom - top)
        width = min(max(1, available_w - 40), max(300, round(940 * scale)))
        height = min(max(1, available_h - 40), max(64, min(round(140 * scale), available_h // 5)))
        x, y = left + (available_w - width) // 2, max(top, bottom - height - 20)
        self.room_error_win.geometry(f"{width}x{height}{x:+d}{y:+d}")
        body_size = max(9, min(round(12 * scale), max(9, width // 75), max(9, height // 8)))
        self.room_error_title.config(font=("Arial", max(10, body_size + 2), "bold"),
                                     wraplength=max(1, width - 32))
        self.room_error_text.config(font=("Arial", body_size, "bold"), wraplength=max(1, width - 32))

    def _hide_room_error_ui(self, generation=None):
        with self._room_error_state():
            if generation is not None and generation != self._room_error_generation:
                return False
            after = getattr(self, "_room_error_hide_after", None)
            self._room_error_hide_after = None
            self._room_error_needs_hide = False
        if after is not None:
            try:
                self.root.after_cancel(after)
            except (tk.TclError, RuntimeError):
                pass
        window = getattr(self, "room_error_win", None)
        if window is not None:
            window.withdraw()
        return True

    def _show_room_error_ui(self, message, duration_ms, generation, preferences):
        with self._room_error_state():
            if generation != self._room_error_generation or self._overlay_closed or not preferences["hud_enabled"]:
                return False
        self._hide_room_error_ui(generation)
        self._position_room_error_window(preferences, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        title = "MEMBER ERROR — SEE TOOL LOG" if preferences["hud_language"] == "en" else "LỖI THÀNH VIÊN — XEM LOG TOOL"
        if preferences.get("test_mode", False):
            title = "[TEST] " + title
        self.room_error_title.config(text=title)
        self.room_error_text.config(text=message)
        with self._room_error_state():
            if generation != self._room_error_generation or self._overlay_closed:
                return False
            self._room_error_needs_hide = True
            self.room_error_win.state("normal")
            self._room_error_hide_after = self.root.after(duration_ms, lambda: self._hide_room_error_ui(generation))
        return True

    def show_room_error(self, message, cue_id="", duration_sec=8):
        """Keep a changed member error visible while capture/action HUD updates."""
        if not isinstance(message, str) or not message.strip() or len(message) > 65536:
            return False
        if not isinstance(cue_id, str) or len(cue_id) > 65536:
            return False
        if type(duration_sec) not in (int, float) or not math.isfinite(duration_sec) or duration_sec <= 0:
            return False
        identity = hashlib.sha256((cue_id or message).encode("utf-8")).digest()
        text = " ".join(message.split())
        if len(text) > 360:
            text = text[:357] + "..."
        with self._room_error_state():
            if self._overlay_closed or not getattr(self, "_preferences", DEFAULT_PREFERENCES)["hud_enabled"]:
                return False
            if identity in self._room_error_seen:
                return False
            self._room_error_seen[identity] = None
            if len(self._room_error_seen) > 64:
                self._room_error_seen.popitem(last=False)
            self._room_error_generation += 1
            self._room_error_needs_hide = True
            self.cmd_queue.put(("SHOW_ROOM_ERROR", (text, max(3000, min(30000, round(duration_sec * 1000))),
                                                    self._room_error_generation)))
        return True

    def clear_room_error(self):
        """Clear only the member notice after room errors recover."""
        with self._room_error_state():
            self._room_error_seen.clear()
            self._invalidate_room_error(enqueue=True)

    def _queue_display(self, command, args):
        """Mark every test display without modifying the capture's task ID."""
        if getattr(self, "_preferences", DEFAULT_PREFERENCES).get("test_mode", False):
            values = list(args)
            text_index = 1 if command == "SHOW_CD" else 0
            if not values[text_index].startswith("[TEST]"):
                values[text_index] = "[TEST] " + values[text_index]
            args = tuple(values)
        self.cmd_queue.put((command, args))

    def countdown(self, seconds=3, subtext="Chuan bi quay..."):
        self._invalidate_action(enqueue=True)
        self.cmd_queue.put(("HIDE_HUD", ()))
        self.cmd_queue.put(("HIDE_BANNER", ()))
        
        for i in range(seconds, 0, -1):
            self._queue_display("SHOW_CD", (str(i), subtext, "#EF4444"))
            time.sleep(1)

        self._queue_display("SHOW_CD", ("[*] BAT DAU!", "Dang ghi hinh dong bo...", "#10B981"))
        time.sleep(1)
        self.cmd_queue.put(("HIDE_CD", ()))

    def _show_capture_hud(self, text, color):
        if not getattr(self, "_preferences", DEFAULT_PREFERENCES)["hud_enabled"]:
            self.clear()
            return
        # A new capture state replaces any previous round's countdown/banner.
        self.cmd_queue.put(("HIDE_CD", ()))
        self.cmd_queue.put(("HIDE_BANNER", ()))
        self._queue_display("SHOW_HUD", (text, color))

    def show_pending_recording(self, task_id, remaining_sec=None, is_host=True):
        """Display preparation without claiming that video is being recorded."""
        self._invalidate_action(enqueue=True)
        preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        english = preferences["hud_language"] == "en"
        if preferences["hud_show_timer"] and remaining_sec is not None and remaining_sec > 0:
            state = f"{'ODX COUNTDOWN' if english else 'ODX DEM NGUOC'}: {math.ceil(remaining_sec)}s"
        else:
            state = "WAITING FOR ODX CAPTURE" if english else "CHO ODX BAT DAU GHI"
        stop_hint = ("[F10: EMERGENCY STOP]" if english else "[F10: DUNG KHAN CAP]") if is_host else (
            "[FOLLOWING HOST]" if english else "[DONG BO THEO HOST]")
        task_text = f" [{task_id}]" if preferences["hud_show_task"] else ""
        self._show_capture_hud(f"[{'WAITING' if english else 'CHO QUAY'}]{task_text} | {state} | {stop_hint}", "#FBBF24")

    def update_recording(self, task_id, elapsed_sec, target_sec=240, blink=False, is_host=True):
        self._action_capture_task(task_id)
        if elapsed_sec < 0:
            self.show_pending_recording(task_id, -elapsed_sec, is_host=is_host)
            return
        rem = remaining_seconds(elapsed_sec, target_sec)
        m_rem, s_rem = divmod(rem, 60)
        preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        english = preferences["hud_language"] == "en"
        task_text = f" [{task_id}]" if preferences["hud_show_task"] else ""
        timer_text = f" | {format_recording_clock(elapsed_sec, target_sec)}" if preferences["hud_show_timer"] else ""
        dot = "● [REC]" if not blink else "○ [REC]"
        
        if elapsed_sec >= target_sec:
            stop_action = ("WAITING FOR ODX TO FINISH" if english else "CHO ODX KET THUC") if is_host else (
                "WAITING FOR HOST TO FINISH" if english else "CHO HOST KET THUC")
            hud_text = f"🟢 [{'TARGET REACHED' if english else 'DA DU GIO'}]{task_text}{timer_text} | {stop_action}"
            fg = "#34D399"
        elif rem <= 10:
            stop_hint = ("[F10: EMERGENCY STOP]" if english else "[F10: DUNG KHAN CAP]") if is_host else (
                "[FOLLOWING HOST]" if english else "[DONG BO THEO HOST]")
            remaining_text = f" | {'Remaining' if english else 'Con'} {rem}s" if preferences["hud_show_timer"] else ""
            hud_text = f"🟡 [{'NEAR TARGET' if english else 'SAP DU GIO'}]{task_text}{timer_text}{remaining_text} | {stop_hint}"
            fg = "#FBBF24"
        else:
            stop_hint = ("[F10: EMERGENCY STOP]" if english else "[F10: DUNG KHAN CAP]") if is_host else (
                "[FOLLOWING HOST]" if english else "[DONG BO THEO HOST]")
            remaining_text = f" | {'Remaining' if english else 'Con'}: {m_rem:02d}:{s_rem:02d}" if preferences["hud_show_timer"] else ""
            hud_text = f"{dot}{task_text}{timer_text}{remaining_text} | {stop_hint}"
            fg = "#F9FAFB" if not blink else "#FCA5A5"
        self._show_capture_hud(hud_text, fg)

    def notify_cancelled(self, task_id, next_fix_id, display_sec=4):
        self.cmd_queue.put(("HIDE_HUD", ()))
        t1 = f"[!] DA DUNG TASK [{task_id}] (CO LOI)"
        t2 = f"[>] CHUYEN SANG TASK SUA LOI: [{next_fix_id}]"
        self._queue_display("SHOW_BANNER", (t1, t2, "#7C2D12", "#FED7AA"))
        time.sleep(display_sec)
        self.cmd_queue.put(("HIDE_BANNER", ()))

    def notify_completed(self, task_id, next_task_id, cooldown_sec=10):
        self.cmd_queue.put(("HIDE_HUD", ()))
        for cd in range(cooldown_sec, 0, -1):
            t1 = f"[OK] HOAN THANH TASK: [{task_id}]"
            t2 = f"[>] Chuan bi bat dau: [{next_task_id}] (sau {cd}s...)"
            self._queue_display("SHOW_BANNER", (t1, t2, "#064E3B", "#A7F3D0"))
            time.sleep(1)
        self.cmd_queue.put(("HIDE_BANNER", ()))

    def show_roster_hud(self, hud_text, fg_color="#A7F3D0", roster=False):
        preferences = getattr(self, "_preferences", DEFAULT_PREFERENCES)
        if not preferences["hud_enabled"] or (roster and not preferences["hud_show_roster"]):
            return
        self._queue_display("SHOW_ROSTER" if roster else "SHOW_HUD", (hud_text, fg_color))

    def hide_hud(self):
        self._invalidate_action(enqueue=True)
        self.cmd_queue.put(("HIDE_HUD", ()))

    def clear(self):
        with self._action_state():
            generation = self._invalidate_action()
            self.cmd_queue.put(("CLEAR_ALL", (generation,)))
            self.clear_room_error()

    def close(self):
        with self._action_state():
            self._overlay_closed = True
            self._invalidate_action()
            self._invalidate_room_error()
            self.cmd_queue.put(("QUIT", ()))

if __name__ == "__main__":
    print("Testing compact overlay...")
    ov = OdxOverlay()
    ov.countdown(3, "Test đếm ngược...")
    for s in range(4):
        ov.update_recording("asm-s221-01", s, 240, blink=(s % 2 == 0))
        time.sleep(1)
    ov.notify_completed("asm-s221-01", "asm-s221-02", cooldown_sec=3)
    ov.clear()
    ov.close()
    print("Done!")
