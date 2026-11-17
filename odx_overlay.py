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
import threading
import queue
import ctypes
import tkinter as tk

# Win32 Constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x00000011

DEFAULT_PREFERENCES = {
    "hud_enabled": True, "hud_show_timer": True, "hud_show_task": True,
    "hud_show_roster": True, "hud_scale": 1.0, "hud_opacity": .95,
    "hud_language": "vi", "test_mode": False,
}

class OdxOverlay:
    def __init__(self, root=None):
        self.cmd_queue = queue.Queue()
        self._preferences = dict(DEFAULT_PREFERENCES)
        self._preferences_lock = threading.RLock()
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
                    elif cmd == "CLEAR_ALL":
                        self.cd_win.withdraw()
                        self.hud_win.withdraw()
                        self.banner_win.withdraw()
                    elif cmd == "QUIT":
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
        for key in ("hud_enabled", "hud_show_timer", "hud_show_task", "hud_show_roster", "test_mode"):
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
            self._preferences = clean
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
        self.cmd_queue.put(("HIDE_HUD", ()))

    def clear(self):
        self.cmd_queue.put(("CLEAR_ALL", ()))

    def close(self):
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
