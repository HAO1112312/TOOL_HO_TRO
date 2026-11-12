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

class OdxOverlay:
    def __init__(self):
        self.cmd_queue = queue.Queue()
        self.ready_event = threading.Event()
        self.thread = threading.Thread(target=self._run_ui, daemon=True)
        self.thread.start()
        self.ready_event.wait(timeout=5)

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

    def _run_ui(self):
        self.root = tk.Tk()
        self.root.withdraw() # Ẩn root hoàn toàn, không tạo cửa sổ toàn màn hình

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

        # Command loop
        def poll_commands():
            while not self.cmd_queue.empty():
                try:
                    cmd, args = self.cmd_queue.get_nowait()
                    if cmd == "SHOW_CD":
                        num_txt, sub_txt, color = args
                        self.cd_num_lbl.config(text=num_txt, fg=color)
                        self.cd_sub_lbl.config(text=sub_txt)
                        self.cd_win.deiconify()
                    elif cmd == "HIDE_CD":
                        self.cd_win.withdraw()
                    elif cmd == "SHOW_HUD":
                        text, fg_col = args
                        self.hud_lbl.config(text=text, fg=fg_col)
                        calc_w = max(950, min(screen_w - 40, int(len(text) * 11.5) + 60))
                        calc_x = (screen_w - calc_w) // 2
                        self.hud_win.geometry(f"{calc_w}x{hud_h}+{calc_x}+{hud_y}")
                        self.hud_win.deiconify()
                    elif cmd == "HIDE_HUD":
                        self.hud_win.withdraw()
                    elif cmd == "SHOW_BANNER":
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
                        self.root.destroy()
                        return
                except Exception:
                    pass
            self.root.after(40, poll_commands)

        self.root.after(40, poll_commands)
        self.root.mainloop()

    # Public API
    def countdown(self, seconds=3, subtext="Chuan bi quay..."):
        self.cmd_queue.put(("HIDE_HUD", ()))
        self.cmd_queue.put(("HIDE_BANNER", ()))
        
        for i in range(seconds, 0, -1):
            self.cmd_queue.put(("SHOW_CD", (str(i), subtext, "#EF4444")))
            time.sleep(1)

        self.cmd_queue.put(("SHOW_CD", ("[*] BAT DAU!", "Dang ghi hinh dong bo...", "#10B981")))
        time.sleep(1)
        self.cmd_queue.put(("HIDE_CD", ()))

    def update_recording(self, task_id, elapsed_sec, target_sec=240, blink=False, is_host=True):
        m_el, s_el = divmod(int(elapsed_sec), 60)
        m_tg, s_tg = divmod(int(target_sec), 60)
        rem = max(0, int(target_sec - elapsed_sec))
        m_rem, s_rem = divmod(rem, 60)

        dot = "● [REC]" if not blink else "○ [REC]"
        
        if elapsed_sec >= target_sec:
            stop_action = "TU DONG DUNG & LUU" if is_host else "CHO HOST DUNG"
            hud_text = f"🟢 [DA DU GIO] [{task_id}] | {m_el:02d}:{s_el:02d} / {m_tg:02d}:{s_tg:02d} | {stop_action} (Tu dung sau {rem}s)"
            fg = "#34D399"
        elif rem <= 10:
            hud_text = f"🟡 [SAP DU GIO] [{task_id}] | {m_el:02d}:{s_el:02d} / {m_tg:02d}:{s_tg:02d} | Con {rem}s (±5s) | [TU DONG LUU]"
            fg = "#FBBF24"
        else:
            stop_hint = "[F11: HUY KHAN CAP]" if is_host else "[DONG BO THEO HOST]"
            hud_text = f"{dot} [{task_id}] | {m_el:02d}:{s_el:02d} / {m_tg:02d}:{s_tg:02d} | Con: {m_rem:02d}:{s_rem:02d} | {stop_hint}"
            fg = "#F9FAFB" if not blink else "#FCA5A5"
        self.cmd_queue.put(("SHOW_HUD", (hud_text, fg)))

    def notify_cancelled(self, task_id, next_fix_id, display_sec=4):
        self.cmd_queue.put(("HIDE_HUD", ()))
        t1 = f"[!] DA DUNG TASK [{task_id}] (CO LOI)"
        t2 = f"[>] CHUYEN SANG TASK SUA LOI: [{next_fix_id}]"
        self.cmd_queue.put(("SHOW_BANNER", (t1, t2, "#7C2D12", "#FED7AA")))
        time.sleep(display_sec)
        self.cmd_queue.put(("HIDE_BANNER", ()))

    def notify_completed(self, task_id, next_task_id, cooldown_sec=10):
        self.cmd_queue.put(("HIDE_HUD", ()))
        for cd in range(cooldown_sec, 0, -1):
            t1 = f"[OK] HOAN THANH TASK: [{task_id}]"
            t2 = f"[>] Chuan bi bat dau: [{next_task_id}] (sau {cd}s...)"
            self.cmd_queue.put(("SHOW_BANNER", (t1, t2, "#064E3B", "#A7F3D0")))
            time.sleep(1)
        self.cmd_queue.put(("HIDE_BANNER", ()))

    def show_roster_hud(self, hud_text, fg_color="#A7F3D0"):
        self.cmd_queue.put(("SHOW_HUD", (hud_text, fg_color)))

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
