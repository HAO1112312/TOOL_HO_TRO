"""
ODX CAPTURE AUTO-PILOT SUITE - MODERN UNIFIED DESKTOP GUI (v3.0)
- Giao diện UX/UI hiện đại, nhỏ gọn, trực quan, dễ thao tác.
- Tích hợp đầy đủ 100% chức năng của cả 2 chế độ MÁY CHỦ (HOST) và MÁY CON (PLAYER).
- Kết nối chặt chẽ với Backend Engine: Quản lý tiến trình, Windows Job Object, NTP, Roster, Auto-Heal & Validate.
- Hỗ trợ phím nóng toàn cục trong game F10 / F11 / F8 không cần Alt-Tab.
- Tích hợp OSD HUD tàng hình trước video.
"""

import sys
import os
import time
import json
import subprocess
import random
import re
import threading
import queue
import ctypes
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

# Import Backend Engine
import odx_backend as be
from odx_overlay import OdxOverlay

try:
    from odx_logger import log_event, export_debug_logs
except Exception:
    def log_event(*args, **kwargs): pass
    def export_debug_logs(*args, **kwargs): return ""

try:
    from odx_updater import check_for_updates_async, download_and_apply_update, get_current_version_info
except Exception:
    def check_for_updates_async(*args, **kwargs): pass
    def download_and_apply_update(*args, **kwargs): return False, ""
    def get_current_version_info(): return {"version": "3.1.0"}

# Win32 Constants
user32 = ctypes.windll.user32

class ModernTheme:
    BG_DARK = "#0B0F19"
    BG_CARD = "#161F30"
    BG_CARD_LIGHT = "#1E293B"
    BORDER = "#334155"
    BORDER_LIGHT = "#475569"
    TEXT_MAIN = "#F8FAFC"
    TEXT_MUTED = "#94A3B8"
    ACCENT_GREEN = "#10B981"
    ACCENT_GREEN_HOVER = "#059669"
    ACCENT_BLUE = "#3B82F6"
    ACCENT_BLUE_HOVER = "#2563EB"
    ACCENT_RED = "#EF4444"
    ACCENT_RED_HOVER = "#DC2626"
    ACCENT_AMBER = "#F59E0B"
    CONSOLE_BG = "#030712"

class OdxAutoPilotGUI:
    def __init__(self, root, initial_mode=None):
        self.root = root
        self.root.title("ODX CAPTURE AUTO-PILOT STUDIO v3.0")
        self.root.geometry("880x760")
        self.root.minsize(820, 700)
        self.root.configure(bg=ModernTheme.BG_DARK)

        # State Variables
        self.current_mode = "HOST" if initial_mode == "host" else ("PLAYER" if initial_mode == "player" else "HOST")
        self.is_recording = False
        self.is_counting_down = False
        self.rec_start_time = 0
        self.target_duration = 240
        self.active_task_id = ""
        self.room_code = ""
        self.fix_counter = 0
        self.abort_state = None
        self.pending_fix_task = ""
        self.pending_next_task = ""
        self.original_task_before_abort = ""
        self.is_in_cooldown = False
        self.is_validating = False
        self.ignore_f10_until = 0
        self.last_start_trigger_time = 0
        self.last_stop_trigger_time = 0
        self.announced_all_ok = set()
        self.log_queue = queue.Queue()
        self.is_running = True
        self.odx_opened = False

        # Backend Components
        self.overlay = OdxOverlay()
        self.follower = be.LiveLogFollower()
        self.tracker = None
        self.player_proc = None

        # Load Saved Config & Progress
        self.cfg_file = os.path.join(be.TOOL_DIR, "autopilot_config.json")
        self.prog_file = os.path.join(be.TOOL_DIR, "autopilot_progress.json")
        self.config = self.load_config()

        self.batch_list = list(self.config.get("batches", ["batch1-ASRM", "batch2-ASRM", "asm-s221-", "AMRF-156-"]))
        self.current_batch = self.config.get("current_batch", self.batch_list[0] if self.batch_list else "batch1-ASRM")
        self.sub_task = str(self.config.get("sub_task", "01"))

        # Build UI
        self.create_widgets()

        # Start Background Listeners & Timers
        self.start_hotkey_listener()
        self.start_system_health_checks()
        self.poll_log_queue()
        self.update_live_timer()

        # Check for online updates in background (silent if no update)
        self.root.after(3500, lambda: self.run_check_update(silent_if_no_update=True))

        # Protocol cleanup on close [X]
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_window)

    def load_config(self):
        default_batches = ["batch1-ASRM", "batch2-ASRM", "asm-s221-", "AMRF-156-"]
        default_cfg = {
            "player_id": "hao",
            "player_name": "Hao",
            "host_id": "puer",
            "host_name": "Puer",
            "batches": default_batches,
            "current_batch": "batch1-ASRM",
            "sub_task": "01",
            "start_num": 1,
            "total_tasks": 15,
            "auto_increment": True
        }
        if os.path.exists(self.cfg_file):
            try:
                with open(self.cfg_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "batches" not in data:
                        old_p = data.get("prefix") or data.get("task_prefix")
                        if old_p and old_p not in default_batches:
                            default_batches.insert(0, old_p)
                        data["batches"] = default_batches
                    if "current_batch" not in data:
                        data["current_batch"] = data.get("prefix") or data.get("task_prefix") or default_batches[0]
                    if "sub_task" not in data:
                        s_idx = data.get("start_index") or data.get("start_num") or 1
                        digits = data.get("digits", 2)
                        data["sub_task"] = str(s_idx).zfill(digits)
                    if "auto_increment" not in data:
                        data["auto_increment"] = True
                    default_cfg.update(data)
            except Exception:
                pass
        return default_cfg

    def save_config(self):
        try:
            with open(self.cfg_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except Exception:
            pass

    def save_current_task_config(self, event=None):
        self.config["batches"] = self.batch_list
        if hasattr(self, "combo_batch"):
            self.config["current_batch"] = self.combo_batch.get().strip()
        if hasattr(self, "ent_subtask"):
            self.config["sub_task"] = self.ent_subtask.get().strip()
        if hasattr(self, "ent_start_num"):
            try:
                self.config["start_num"] = int(self.ent_start_num.get().strip())
            except Exception:
                pass
        if hasattr(self, "ent_total"):
            try:
                self.config["total_tasks"] = int(self.ent_total.get().strip())
            except Exception:
                pass
        if hasattr(self, "var_autoincrement"):
            self.config["auto_increment"] = self.var_autoincrement.get()
        self.save_config()

    # ==================================================================
    # UI CONSTRUCTION
    # ==================================================================
    def create_widgets(self):
        # 1. Top Header Bar
        header = tk.Frame(self.root, bg=ModernTheme.BG_CARD, height=64)
        header.pack(fill="x", padx=10, pady=(10, 6))

        h_left = tk.Frame(header, bg=ModernTheme.BG_CARD)
        h_left.pack(side="left", padx=14, pady=8)

        lbl_title = tk.Label(
            h_left, text="ODX CAPTURE AUTO-PILOT",
            font=("Segoe UI", 13, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD
        )
        lbl_title.pack(anchor="w")
        lbl_sub = tk.Label(
            h_left, text="Hệ thống Điều khiển & Đồng bộ 4 máy Chuyên nghiệp (v3.0)",
            font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD
        )
        lbl_sub.pack(anchor="w")

        # Health Badges (Admin, NTP, Game)
        h_right = tk.Frame(header, bg=ModernTheme.BG_CARD)
        h_right.pack(side="right", padx=14, pady=8)

        self.badge_admin = tk.Label(h_right, text="Admin: [..]", font=("Segoe UI", 9, "bold"), fg="#CBD5E1", bg="#334155", padx=8, pady=2)
        self.badge_admin.pack(side="left", padx=3)

        self.badge_ntp = tk.Label(h_right, text="NTP: [..]", font=("Segoe UI", 9, "bold"), fg="#CBD5E1", bg="#334155", padx=8, pady=2)
        self.badge_ntp.pack(side="left", padx=3)

        self.badge_game = tk.Label(h_right, text="Game: [..]", font=("Segoe UI", 9, "bold"), fg="#CBD5E1", bg="#334155", padx=8, pady=2)
        self.badge_game.pack(side="left", padx=3)

        self.badge_odx = tk.Label(h_right, text="Odx: [..]", font=("Segoe UI", 9, "bold"), fg="#CBD5E1", bg="#334155", padx=8, pady=2)
        self.badge_odx.pack(side="left", padx=3)

        # 2. Mode Selector Bar (Host / Player / Audit)
        nav_bar = tk.Frame(self.root, bg=ModernTheme.BG_DARK)
        nav_bar.pack(fill="x", padx=10, pady=2)

        self.btn_nav_host = tk.Button(
            nav_bar, text="👑 CHẾ ĐỘ MÁY CHỦ (HOST)", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", padx=16, pady=6,
            command=lambda: self.switch_mode("HOST")
        )
        self.btn_nav_host.pack(side="left", padx=(0, 6))

        self.btn_nav_player = tk.Button(
            nav_bar, text="🎮 CHẾ ĐỘ MÁY CON (PLAYER)", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", padx=16, pady=6,
            command=lambda: self.switch_mode("PLAYER")
        )
        self.btn_nav_player.pack(side="left", padx=6)

        self.btn_nav_audit = tk.Button(
            nav_bar, text="🔍 KIỂM ĐỊNH (AUDIT 8 FILE)", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", padx=16, pady=6,
            command=lambda: self.switch_mode("AUDIT")
        )
        self.btn_nav_audit.pack(side="left", padx=6)

        # 3. Bottom Quick Action Bar (Packed to BOTTOM first to guarantee 100% visibility)
        bottom_bar = tk.Frame(self.root, bg=ModernTheme.BG_DARK)
        bottom_bar.pack(side="bottom", fill="x", padx=10, pady=(2, 8))

        btn_clean = tk.Button(
            bottom_bar, text="⚡ Dọn sạch Odx ngầm & Giải phóng F10", font=("Segoe UI", 9, "bold"),
            bg="#334155", fg="#F8FAFC", relief="flat", cursor="hand2", padx=10, pady=4,
            command=self.run_clean_all
        )
        btn_clean.pack(side="left", padx=4)

        btn_sync = tk.Button(
            bottom_bar, text="🔄 Đồng bộ NTP", font=("Segoe UI", 9),
            bg="#1E293B", fg="#CBD5E1", relief="flat", cursor="hand2", padx=8, pady=4,
            command=self.run_sync_ntp
        )
        btn_sync.pack(side="left", padx=4)

        btn_open_captures = tk.Button(
            bottom_bar, text="📁 Mở Captures", font=("Segoe UI", 9),
            bg="#1E293B", fg="#CBD5E1", relief="flat", cursor="hand2", padx=6, pady=4,
            command=self.open_captures_folder
        )
        btn_open_captures.pack(side="left", padx=3)

        btn_export_logs = tk.Button(
            bottom_bar, text="📦 Xuất Log Debug", font=("Segoe UI", 9),
            bg="#1E293B", fg="#38BDF8", relief="flat", cursor="hand2", padx=6, pady=4,
            command=self.run_export_logs
        )
        btn_export_logs.pack(side="left", padx=3)

        btn_update = tk.Button(
            bottom_bar, text="🚀 Cập Nhật Online", font=("Segoe UI", 9, "bold"),
            bg="#064E3B", fg="#6EE7B7", relief="flat", cursor="hand2", padx=8, pady=4,
            command=lambda: self.run_check_update(silent_if_no_update=False)
        )
        btn_update.pack(side="right", padx=4)

        # 4. Console Log Widget (Bottom Card, absorbs vertical expansion)
        log_card = tk.Frame(self.root, bg=ModernTheme.BG_CARD)
        log_card.pack(side="bottom", fill="both", expand=True, padx=10, pady=4)

        log_header = tk.Frame(log_card, bg=ModernTheme.BG_CARD)
        log_header.pack(fill="x", padx=10, pady=(6, 2))
        tk.Label(log_header, text="NHẬT KÝ HOẠT ĐỘNG REAL-TIME", font=("Segoe UI", 9, "bold"), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left")
        
        btn_clr_log = tk.Button(
            log_header, text="Xóa Log", font=("Segoe UI", 8), bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MUTED,
            relief="flat", cursor="hand2", padx=6, pady=1, command=self.clear_log
        )
        btn_clr_log.pack(side="right")

        self.txt_log = tk.Text(
            log_card, bg=ModernTheme.CONSOLE_BG, fg="#E2E8F0", font=("Consolas", 9),
            relief="flat", height=7, wrap="word", padx=8, pady=6
        )
        self.txt_log.pack(fill="both", expand=True, padx=8, pady=(0, 6))

        # 5. Main Content Container (Contains active tab, fixed natural height)
        self.container = tk.Frame(self.root, bg=ModernTheme.BG_DARK)
        self.container.pack(side="top", fill="x", padx=10, pady=4)

        # Construct Tabs
        self.tab_host = tk.Frame(self.container, bg=ModernTheme.BG_DARK)
        self.tab_player = tk.Frame(self.container, bg=ModernTheme.BG_DARK)
        self.tab_audit = tk.Frame(self.container, bg=ModernTheme.BG_DARK)

        self.build_host_tab()
        self.build_player_tab()
        self.build_audit_tab()

        # Initial Mode Activation
        self.switch_mode(self.current_mode)

    # ------------------------------------------------------------------
    # TAB: HOST CONTROLS
    # ------------------------------------------------------------------
    def build_host_tab(self):
        # 1. Room Status Card
        card_room = tk.Frame(self.tab_host, bg=ModernTheme.BG_CARD)
        card_room.pack(fill="x", pady=4)

        r_top = tk.Frame(card_room, bg=ModernTheme.BG_CARD)
        r_top.pack(fill="x", padx=12, pady=8)

        tk.Label(r_top, text="MÃ PHÒNG (ROOM):", font=("Segoe UI", 10, "bold"), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left")
        
        self.ent_host_room = tk.Entry(
            r_top, font=("Consolas", 13, "bold"), width=8,
            bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.ACCENT_GREEN,
            relief="flat", justify="center"
        )
        self.ent_host_room.insert(0, self.room_code if self.room_code else "")
        self.ent_host_room.pack(side="left", padx=6)
        self.ent_host_room.bind("<Return>", lambda e: self.host_apply_room_code())
        self.setup_entry_clipboard(self.ent_host_room, is_room_code=True)

        btn_apply_room = tk.Button(
            r_top, text="Áp dụng", font=("Segoe UI", 9, "bold"),
            bg="#065F46", fg="#A7F3D0", activebackground="#047857",
            relief="flat", cursor="hand2", padx=8, pady=2, command=self.host_apply_room_code
        )
        btn_apply_room.pack(side="left", padx=2)

        btn_copy_room = tk.Button(
            r_top, text="📋 Copy Mã", font=("Segoe UI", 9),
            bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN,
            relief="flat", cursor="hand2", padx=8, pady=2, command=self.copy_room_code
        )
        btn_copy_room.pack(side="left", padx=4)

        self.btn_open_odx = tk.Button(
            r_top, text="🖥 Mở Odx Capture (Tạo Room)", font=("Segoe UI", 9, "bold"),
            bg=ModernTheme.ACCENT_BLUE, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_BLUE_HOVER,
            relief="flat", cursor="hand2", padx=10, pady=3, command=self.host_open_odx
        )
        self.btn_open_odx.pack(side="right")

        # Roster Slot Preview (4 Slots)
        self.roster_frame = tk.Frame(card_room, bg=ModernTheme.BG_CARD_LIGHT)
        self.roster_frame.pack(fill="x", padx=12, pady=(0, 10))

        self.slot_labels = []
        for i in range(4):
            lbl = tk.Label(
                self.roster_frame, text=f"Slot {i+1}: Đang chờ...",
                font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD_LIGHT, padx=8, pady=4
            )
            lbl.pack(side="left", expand=True, fill="x", padx=2, pady=2)
            self.slot_labels.append(lbl)

        # 2. Task & Batch Configuration Card
        card_task = tk.Frame(self.tab_host, bg=ModernTheme.BG_CARD)
        card_task.pack(fill="x", pady=4)

        # Row 1: Batch management
        t_row1 = tk.Frame(card_task, bg=ModernTheme.BG_CARD)
        t_row1.pack(fill="x", padx=12, pady=(8, 4))

        tk.Label(t_row1, text="Tên Batch:", font=("Segoe UI", 9, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD).pack(side="left")

        self.combo_batch = ttk.Combobox(
            t_row1, values=self.batch_list, font=("Consolas", 10), width=18, state="normal"
        )
        if self.current_batch in self.batch_list:
            self.combo_batch.set(self.current_batch)
        elif self.batch_list:
            self.combo_batch.set(self.batch_list[0])
        self.combo_batch.pack(side="left", padx=6)
        self.combo_batch.bind("<<ComboboxSelected>>", self.on_batch_selected)
        self.combo_batch.bind("<KeyRelease>", self.update_task_id_preview)

        btn_add_b = tk.Button(
            t_row1, text="➕ Thêm Batch", font=("Segoe UI", 8, "bold"),
            bg="#065F46", fg="#A7F3D0", activebackground="#047857",
            relief="flat", cursor="hand2", padx=8, pady=2, command=self.add_batch_dialog
        )
        btn_add_b.pack(side="left", padx=3)

        btn_del_b = tk.Button(
            t_row1, text="✖ Xóa Batch", font=("Segoe UI", 8),
            bg="#334155", fg="#CBD5E1", activebackground="#475569",
            relief="flat", cursor="hand2", padx=8, pady=2, command=self.delete_current_batch
        )
        btn_del_b.pack(side="left", padx=3)

        self.var_autoincrement = tk.BooleanVar(value=self.config.get("auto_increment", True))
        chk_auto = tk.Checkbutton(
            t_row1, text="Tự động tăng số", variable=self.var_autoincrement,
            font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD,
            selectcolor=ModernTheme.BG_CARD, activebackground=ModernTheme.BG_CARD,
            command=self.save_current_task_config
        )
        chk_auto.pack(side="right", padx=6)

        # Row 2: Sub-task & Indexing
        t_row2 = tk.Frame(card_task, bg=ModernTheme.BG_CARD)
        t_row2.pack(fill="x", padx=12, pady=(4, 8))

        tk.Label(t_row2, text="Task con:", font=("Segoe UI", 9, "bold"), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left")
        self.ent_subtask = tk.Entry(t_row2, font=("Consolas", 10), width=9, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_subtask.insert(0, str(self.config.get("sub_task", "01")))
        self.ent_subtask.pack(side="left", padx=4)
        self.ent_subtask.bind("<KeyRelease>", self.update_task_id_preview)
        self.setup_entry_clipboard(self.ent_subtask)

        tk.Label(t_row2, text="Số:", font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left", padx=(6, 2))
        self.ent_start_num = tk.Entry(t_row2, font=("Consolas", 10), width=4, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_start_num.insert(0, str(self.config.get("start_num", 1)))
        self.ent_start_num.pack(side="left", padx=3)
        self.ent_start_num.bind("<KeyRelease>", self.update_task_id_preview)

        tk.Label(t_row2, text="Tổng:", font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left", padx=(6, 2))
        self.ent_total = tk.Entry(t_row2, font=("Consolas", 10), width=4, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_total.insert(0, str(self.config.get("total_tasks", 15)))
        self.ent_total.pack(side="left", padx=3)
        self.ent_total.bind("<KeyRelease>", self.save_current_task_config)

        tk.Label(t_row2, text="Mã Task đầy đủ:", font=("Segoe UI", 9, "bold"), fg=ModernTheme.ACCENT_GREEN, bg=ModernTheme.BG_CARD).pack(side="left", padx=(8, 2))
        self.ent_full_task = tk.Entry(t_row2, font=("Consolas", 10, "bold"), width=15, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.ACCENT_GREEN, relief="flat")
        self.ent_full_task.pack(side="left", padx=3)
        self.ent_full_task.bind("<KeyRelease>", self.on_full_task_manual_edit)
        self.setup_entry_clipboard(self.ent_full_task)

        self.btn_inject_task = tk.Button(
            t_row2, text="⚡ Điền vào Odx", font=("Segoe UI", 8, "bold"),
            bg="#2563EB", fg="#FFFFFF", activebackground="#1D4ED8",
            relief="flat", cursor="hand2", padx=6, pady=1, state="disabled", command=self.btn_inject_task_id
        )
        self.btn_inject_task.pack(side="left", padx=3)

        self.btn_auto_fix = tk.Button(
            t_row2, text="+ Sửa lỗi (-fix)", font=("Segoe UI", 8, "bold"),
            bg="#D97706", fg="#FFFFFF", activebackground="#B45309",
            relief="flat", cursor="hand2", padx=6, pady=1, state="disabled", command=self.btn_auto_add_fix
        )
        self.btn_auto_fix.pack(side="left", padx=3)

        self.lbl_task_display = tk.Label(t_row2, text="", font=("Consolas", 9, "bold"), fg=ModernTheme.ACCENT_GREEN, bg=ModernTheme.BG_CARD)
        self.lbl_task_display.pack(side="right", padx=6)

        # Initial preview
        self.update_task_id_preview()

        # 3. Action Buttons & Big Status Display
        card_action = tk.Frame(self.tab_host, bg=ModernTheme.BG_CARD)
        card_action.pack(fill="x", pady=4)

        act_top = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        act_top.pack(fill="x", padx=12, pady=8)

        self.lbl_host_status = tk.Label(
            act_top, text="⚠️ BƯỚC 1: HÃY BẤM NÚT [MỞ ODX CAPTURE (TẠO ROOM)] TRƯỚC TIÊN!",
            font=("Segoe UI", 11, "bold"), fg=ModernTheme.ACCENT_AMBER, bg=ModernTheme.BG_CARD
        )
        self.lbl_host_status.pack(side="left")

        self.lbl_timer = tk.Label(
            act_top, text="00:00 / 04:00 (±5s)",
            font=("Consolas", 12, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD
        )
        self.lbl_timer.pack(side="right")

        act_btns = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        act_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_host_start = tk.Button(
            act_btns, text="▶ BẮT ĐẦU QUAY (F10)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_GREEN, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_GREEN_HOVER,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=self.host_trigger_start
        )
        self.btn_host_start.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_host_stop = tk.Button(
            act_btns, text="⏹ DỪNG & LƯU", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_BLUE, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_BLUE_HOVER,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=self.host_trigger_stop
        )
        self.btn_host_stop.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_host_abort = tk.Button(
            act_btns, text="⚠ HỦY KHẨN CẤP (F11)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_RED, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_RED_HOVER,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=self.host_trigger_abort
        )
        self.btn_host_abort.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_host_cancel = tk.Button(
            act_btns, text="🔄 HỦY / VỀ GỐC (F9)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, activebackground=ModernTheme.BG_CARD,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=self.host_cancel_and_reset
        )
        self.btn_host_cancel.pack(side="left", expand=True, fill="x", padx=3)

    # ------------------------------------------------------------------
    # TAB: PLAYER CONTROLS
    # ------------------------------------------------------------------
    def build_player_tab(self):
        card_p_input = tk.Frame(self.tab_player, bg=ModernTheme.BG_CARD)
        card_p_input.pack(fill="x", pady=4)

        p_row1 = tk.Frame(card_p_input, bg=ModernTheme.BG_CARD)
        p_row1.pack(fill="x", padx=12, pady=(10, 6))

        tk.Label(p_row1, text="MÃ PHÒNG (6 KÝ TỰ):", font=("Segoe UI", 10, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD).pack(side="left")
        self.ent_p_room = tk.Entry(p_row1, font=("Consolas", 13, "bold"), width=10, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.ACCENT_GREEN, relief="flat", justify="center")
        self.ent_p_room.pack(side="left", padx=8)
        self.setup_entry_clipboard(self.ent_p_room, is_room_code=True)

        btn_paste = tk.Button(p_row1, text="Dán Mã", font=("Segoe UI", 9), bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat", cursor="hand2", padx=6, command=self.player_paste_code)
        btn_paste.pack(side="left", padx=4)

        self.btn_p_connect = tk.Button(
            p_row1, text="🚀 KẾT NỐI VÀO PHÒNG (TỰ ĐỘNG 100%)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_GREEN, fg="#FFFFFF", relief="flat", cursor="hand2", padx=16, pady=5,
            command=self.player_start_connection
        )
        self.btn_p_connect.pack(side="right")

        p_row2 = tk.Frame(card_p_input, bg=ModernTheme.BG_CARD)
        p_row2.pack(fill="x", padx=12, pady=(0, 10))

        tk.Label(p_row2, text="Player ID:", font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left")
        self.ent_p_id = tk.Entry(p_row2, font=("Consolas", 10), width=12, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_p_id.insert(0, self.config.get("player_id", "hao"))
        self.ent_p_id.pack(side="left", padx=6)
        self.ent_p_id.bind("<KeyRelease>", lambda e: self.on_player_info_change())
        self.ent_p_id.bind("<FocusOut>", lambda e: self.on_player_info_change())
        self.setup_entry_clipboard(self.ent_p_id)

        tk.Label(p_row2, text="Tên hiển thị:", font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD).pack(side="left", padx=(10, 2))
        self.ent_p_name = tk.Entry(p_row2, font=("Consolas", 10), width=14, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_p_name.insert(0, self.config.get("player_name", "Hao"))
        self.ent_p_name.pack(side="left", padx=6)
        self.ent_p_name.bind("<KeyRelease>", lambda e: self.on_player_info_change())
        self.ent_p_name.bind("<FocusOut>", lambda e: self.on_player_info_change())
        self.setup_entry_clipboard(self.ent_p_name)

        btn_apply_p_info = tk.Button(
            p_row2, text="⚡ Điền vào Odx", font=("Segoe UI", 8, "bold"),
            bg="#2563EB", fg="#FFFFFF", activebackground="#1D4ED8",
            relief="flat", cursor="hand2", padx=6, pady=1, command=self.player_apply_info_to_odx
        )
        btn_apply_p_info.pack(side="left", padx=8)

        # Status Banner for Player
        card_p_banner = tk.Frame(self.tab_player, bg=ModernTheme.BG_CARD)
        card_p_banner.pack(fill="x", pady=4)

        b_frame = tk.Frame(card_p_banner, bg=ModernTheme.BG_CARD)
        b_frame.pack(fill="x", padx=12, pady=12)

        self.lbl_p_status = tk.Label(
            b_frame, text="CHƯA KẾT NỐI - HÃY NHẬP MÃ PHÒNG VÀ BẤM KẾT NỐI",
            font=("Segoe UI", 11, "bold"), fg=ModernTheme.ACCENT_AMBER, bg=ModernTheme.BG_CARD
        )
        self.lbl_p_status.pack(anchor="w")

        self.lbl_p_sub = tk.Label(
            b_frame, text="* Tool sẽ tự động mở giao diện Odx Capture, kết nối vào phòng và tự động Auto-Ready 100%.",
            font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD
        )
        self.lbl_p_sub.pack(anchor="w", pady=(4, 0))

        # Player Roster View
        self.p_roster_frame = tk.Frame(card_p_banner, bg=ModernTheme.BG_CARD_LIGHT)
        self.p_roster_frame.pack(fill="x", padx=12, pady=(0, 10))

        self.p_slot_labels = []
        for i in range(4):
            lbl = tk.Label(
                self.p_roster_frame, text=f"Slot {i+1}: Chờ...",
                font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD_LIGHT, padx=8, pady=4
            )
            lbl.pack(side="left", expand=True, fill="x", padx=2, pady=2)
            self.p_slot_labels.append(lbl)

    # ------------------------------------------------------------------
    # TAB: AUDIT & CHECK PASS
    # ------------------------------------------------------------------
    def build_audit_tab(self):
        card_audit = tk.Frame(self.tab_audit, bg=ModernTheme.BG_CARD)
        card_audit.pack(fill="x", pady=4)

        a_top = tk.Frame(card_audit, bg=ModernTheme.BG_CARD)
        a_top.pack(fill="x", padx=12, pady=10)

        tk.Label(a_top, text="Chọn Thư mục Capture:", font=("Segoe UI", 10, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD).pack(side="left")
        self.ent_audit_path = tk.Entry(a_top, font=("Consolas", 10), width=40, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_audit_path.pack(side="left", padx=8)

        btn_browse = tk.Button(a_top, text="📁 Chọn...", font=("Segoe UI", 9), bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat", cursor="hand2", padx=8, command=self.browse_audit_folder)
        btn_browse.pack(side="left", padx=4)

        btn_run_val = tk.Button(
            a_top, text="🔍 KIỂM ĐỊNH & AUTO-HEAL", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_BLUE, fg="#FFFFFF", relief="flat", cursor="hand2", padx=14, pady=4,
            command=self.run_manual_audit
        )
        btn_run_val.pack(side="right")

    # ==================================================================
    # NAVIGATION & MODE SWITCHING
    # ==================================================================
    def switch_mode(self, mode):
        self.current_mode = mode
        self.tab_host.pack_forget()
        self.tab_player.pack_forget()
        self.tab_audit.pack_forget()

        # Reset nav buttons style
        self.btn_nav_host.config(bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MUTED)
        self.btn_nav_player.config(bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MUTED)
        self.btn_nav_audit.config(bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MUTED)

        if mode == "HOST":
            self.tab_host.pack(fill="both", expand=True)
            self.btn_nav_host.config(bg=ModernTheme.ACCENT_GREEN, fg="#FFFFFF")
            self.log("Đã chuyển sang chế độ MÁY CHỦ (HOST).", "MODE")
        elif mode == "PLAYER":
            self.tab_player.pack(fill="both", expand=True)
            self.btn_nav_player.config(bg=ModernTheme.ACCENT_GREEN, fg="#FFFFFF")
            self.log("Đã chuyển sang chế độ MÁY CON (PLAYER).", "MODE")
        elif mode == "AUDIT":
            self.tab_audit.pack(fill="both", expand=True)
            self.btn_nav_audit.config(bg=ModernTheme.ACCENT_BLUE, fg="#FFFFFF")
            self.log("Đã chuyển sang tab KIỂM ĐỊNH TẬP TIN.", "MODE")

    # ==================================================================
    # LOGGING & CONSOLE OUTPUT
    # ==================================================================
    def log(self, msg, tag="AUTO"):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_queue.put(f"[{now}][{tag}] {msg}")
        try:
            log_event(tag, level="INFO", source="UI", message=msg)
        except Exception:
            pass

    def poll_log_queue(self):
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.txt_log.insert("end", line + "\n")
            self.txt_log.see("end")
        if self.is_running:
            self.root.after(100, self.poll_log_queue)

    def clear_log(self):
        self.txt_log.delete("1.0", "end")

    # ==================================================================
    # SYSTEM HEALTH BACKGROUND CHECKS
    # ==================================================================
    def start_system_health_checks(self):
        def _check_loop():
            # Check Odx Engine Version & Auto-detect highest version
            try:
                odx_ver = be.get_odx_version_display()
                if "0.3.5" in odx_ver:
                    self.badge_odx.config(text=f"Odx: [{odx_ver} ✓]", bg="#065F46", fg="#A7F3D0")
                elif "0.3.4" in odx_ver:
                    self.badge_odx.config(text=f"Odx: [{odx_ver}]", bg="#1E3A8A", fg="#93C5FD")
                else:
                    self.badge_odx.config(text=f"Odx: [{odx_ver} ⚠️ Cũ]", bg="#991B1B", fg="#FECACA")
                self.log(f"Đã kết nối Odx Engine ({odx_ver}) tại: {be.ODX_ROOT}", "SYSTEM")
            except Exception:
                pass

            # Check Admin
            adm = be.is_admin()
            try:
                if adm:
                    self.badge_admin.config(text="Admin: [✓ OK]", bg="#065F46", fg="#A7F3D0")
                else:
                    self.badge_admin.config(text="Admin: [! CẦN]", bg="#991B1B", fg="#FECACA")
            except Exception:
                pass

            # Initial NTP Sync
            ok_ntp, msg_ntp = be.sync_system_clock()
            try:
                if ok_ntp:
                    self.badge_ntp.config(text="NTP: [✓ OK]", bg="#065F46", fg="#A7F3D0")
                    self.log(msg_ntp, "NTP")
                else:
                    self.badge_ntp.config(text="NTP: [! LỆCH]", bg="#9A3412", fg="#FED7AA")
            except Exception:
                pass

            while self.is_running:
                gm = be.is_game_running("ArmaReforgerSteam")
                try:
                    if gm:
                        self.badge_game.config(text="Game: [✓ ĐANG CHẠY]", bg="#065F46", fg="#A7F3D0")
                    else:
                        self.badge_game.config(text="Game: [! CHƯA MỞ]", bg="#334155", fg="#CBD5E1")
                except Exception:
                    pass

                # Tự động phát hiện nếu Odx Capture bị tắt bên ngoài
                if getattr(self, "odx_opened", False) and self.current_mode == "HOST":
                    if time.time() - getattr(self, "odx_launch_time", 0) > 6:
                        if not be.find_odx_window():
                            self.root.after(0, self.reset_host_to_unopened_state)
                            self.log("Phát hiện Odx Capture đã tắt. Đã khóa lại thao tác và đưa về trạng thái chờ.", "WARN")

                time.sleep(3)

        t = threading.Thread(target=_check_loop, daemon=True)
        t.start()

    # ==================================================================
    # HOST CONTROLLER WORKFLOW
    # ==================================================================
    def reset_batch_params_to_default(self):
        """Reset toàn bộ thông số subtask, start_num, total về mặc định ban đầu (01, 1, 15)"""
        if hasattr(self, "ent_subtask"):
            self.ent_subtask.delete(0, "end")
            self.ent_subtask.insert(0, "01")
        if hasattr(self, "ent_start_num"):
            self.ent_start_num.delete(0, "end")
            self.ent_start_num.insert(0, "1")
        if hasattr(self, "ent_total"):
            self.ent_total.delete(0, "end")
            self.ent_total.insert(0, "15")
        if hasattr(self, "var_auto_inc"):
            self.var_auto_inc.set(True)

        self.sub_task = "01"
        self.start_num = 1
        self.total_tasks = 15
        self.fix_counter = 0
        self.abort_state = None
        self.pending_fix_task = ""
        self.pending_next_task = ""

    def on_batch_selected(self, event=None):
        sel = self.combo_batch.get().strip()
        if sel != self.current_batch:
            self.current_batch = sel
            self.reset_batch_params_to_default()
            self.save_current_task_config()
            self.update_task_id_preview()
            self.log(f"Đã chuyển sang Batch: [{sel}] (đặt lại về Task con: 01, Số: 1)", "CONFIG")
        else:
            self.update_task_id_preview()

    def add_batch_dialog(self):
        name = simpledialog.askstring("Thêm Batch Mới", "Nhập tên Batch mới (Ví dụ: batch3-ASRM hoặc asm-s222-):", parent=self.root)
        if name and name.strip():
            name = name.strip()
            if name not in self.batch_list:
                self.batch_list.append(name)
                self.combo_batch["values"] = self.batch_list
            self.combo_batch.set(name)
            self.current_batch = name

            # Reset ngay lập tức toàn bộ thông số về mặc định: Task con 01, Số 1, Tổng 15
            self.reset_batch_params_to_default()

            self.save_current_task_config()
            self.update_task_id_preview()
            self.log(f"★ Đã thêm Batch mới [{name}] & TỰ ĐỘNG RESET toàn bộ thông số về mặc định: Task con: 01, Số: 1, Tổng: 15!", "CONFIG")

    def delete_current_batch(self):
        cur = self.combo_batch.get().strip()
        if not cur:
            return
        if len(self.batch_list) <= 1:
            messagebox.showwarning("Không thể xóa", "Phải giữ lại ít nhất 1 Batch trong danh sách!")
            return

        confirm = messagebox.askyesno("Xác nhận xóa", f"Bạn có chắc muốn xóa Batch '{cur}' khỏi danh sách?")
        if confirm:
            if cur in self.batch_list:
                self.batch_list.remove(cur)
            new_val = self.batch_list[0]
            self.combo_batch["values"] = self.batch_list
            self.combo_batch.set(new_val)
            self.current_batch = new_val
            self.save_current_task_config()
            self.update_task_id_preview()
            self.log(f"Đã xóa Batch: [{cur}], chuyển sang: [{new_val}]", "CONFIG")

    def update_task_id_preview(self, event=None):
        batch = self.combo_batch.get().strip() if hasattr(self, "combo_batch") else self.current_batch
        subtask = self.ent_subtask.get().strip() if hasattr(self, "ent_subtask") else self.sub_task

        if not subtask:
            try:
                num = int(self.ent_start_num.get().strip()) if hasattr(self, "ent_start_num") else 1
                subtask = str(num).zfill(2)
            except Exception:
                subtask = "01"

        if not batch:
            tid = subtask
        elif batch.endswith("-") or batch.endswith("_"):
            tid = f"{batch}{subtask}"
        else:
            tid = f"{batch}-{subtask}"

        self.active_task_id = tid
        if hasattr(self, "ent_full_task"):
            cur_txt = self.ent_full_task.get().strip()
            if self.root.focus_get() != self.ent_full_task and cur_txt != tid:
                self.ent_full_task.delete(0, "end")
                self.ent_full_task.insert(0, tid)

        # Kiem tra xem task_id nay da tung duoc quay va co du lieu chua
        exists, exist_path = be.is_task_already_recorded(tid)
        if hasattr(self, "lbl_task_display"):
            if exists:
                self.lbl_task_display.config(text=f"⚠️ [ĐÃ CÓ TRÊN MÁY]: {tid}!", fg="#EF4444")
                if hasattr(self, "ent_full_task"):
                    self.ent_full_task.config(fg="#EF4444")
            else:
                self.lbl_task_display.config(text=f"[✓ Task mới: {tid}]", fg="#10B981")
                if hasattr(self, "ent_full_task"):
                    self.ent_full_task.config(fg="#10B981")

        self.save_current_task_config()
        # Tu dong cap nhat vao Odx Capture va config
        be.set_task_box(tid)

    def on_full_task_manual_edit(self, event=None):
        custom_id = self.ent_full_task.get().strip()
        if custom_id:
            self.active_task_id = custom_id
            be.set_clipboard(custom_id)
            be.set_task_box(custom_id)
            exists, exist_path = be.is_task_already_recorded(custom_id)
            if hasattr(self, "lbl_task_display"):
                if exists:
                    self.lbl_task_display.config(text=f"⚠️ [ĐÃ CÓ TRÊN MÁY]: {custom_id}!", fg="#EF4444")
                    self.ent_full_task.config(fg="#EF4444")
                else:
                    self.lbl_task_display.config(text=f"[✓ Task mới: {custom_id}]", fg="#10B981")
                    self.ent_full_task.config(fg="#10B981")

    def btn_auto_add_fix(self):
        if not getattr(self, "odx_opened", False):
            messagebox.showwarning(
                "Chưa mở Odx Capture",
                "Bạn phải nhấn nút '[🖥 Mở Odx Capture (Tạo Room)]' trước tiên!\n\nNếu chưa mở Odx Capture, các thao tác sau đó sẽ bị khóa hoàn toàn."
            )
            return
        cur_id = self.ent_full_task.get().strip() if hasattr(self, "ent_full_task") else self.active_task_id
        next_fix = be.get_next_available_fix_task_id(cur_id or self.active_task_id)
        self.active_task_id = next_fix
        if hasattr(self, "ent_full_task"):
            self.ent_full_task.delete(0, "end")
            self.ent_full_task.insert(0, next_fix)
        be.set_clipboard(next_fix)
        be.set_task_box(next_fix)
        self.update_task_id_preview()
        self.log(f"Đã tự động tạo mã Task Sửa Lỗi an toàn (tránh trùng): [{next_fix}]!", "FIX")

    def btn_inject_task_id(self):
        if not getattr(self, "odx_opened", False):
            messagebox.showwarning(
                "Chưa mở Odx Capture",
                "Bạn phải nhấn nút '[🖥 Mở Odx Capture (Tạo Room)]' trước tiên!\n\nNếu chưa mở Odx Capture, các thao tác sau đó sẽ bị khóa hoàn toàn."
            )
            return
        self.update_task_id_preview()
        be.set_task_box(self.active_task_id)
        exists, exist_path = be.is_task_already_recorded(self.active_task_id)
        if exists:
            self.log(f"⚠️ CẢNH BÁO: Task ID [{self.active_task_id}] ĐÃ TỒN TẠI TRÊN MÁY! Có thể bị Odx từ chối hoặc đè file.", "WARN")
            messagebox.showwarning(
                "Cảnh báo: Trùng mã Task",
                f"Task ID [{self.active_task_id}] ĐÃ CÓ DỮ LIỆU TRÊN MÁY!\n({exist_path})\n\n"
                f"Nếu Odx báo lỗi kẹt, hãy bấm nút '+ Sửa lỗi (-fix)' để tự động tạo đuôi sửa lỗi (ví dụ: {self.active_task_id}-fix1)."
            )
        else:
            self.log(f"Đã tự động gửi Task ID: [{self.active_task_id}] vào Odx Capture & Clipboard!", "TASK")
            messagebox.showinfo("Đã điền Task ID", f"Đã gửi mã Task: [{self.active_task_id}]\nvào Odx Capture và bộ nhớ tạm (Clipboard)!\n\nNếu ô Task ID trên Odx chưa cập nhật, bạn chỉ cần bấm chuột vào ô Task ID trên Odx rồi bấm Ctrl+V.")

    def increment_task_number(self):
        if not self.var_autoincrement.get():
            return

        # 1. Increment subtask if it ends with numbers
        sub = self.ent_subtask.get().strip()
        m = re.search(r'(\d+)$', sub)
        if m:
            digits = m.group(1)
            next_val = int(digits) + 1
            new_digits = str(next_val).zfill(len(digits))
            new_sub = sub[:m.start(1)] + new_digits
            self.ent_subtask.delete(0, "end")
            self.ent_subtask.insert(0, new_sub)

        # 2. Increment start_num
        try:
            c_num = int(self.ent_start_num.get().strip())
            self.ent_start_num.delete(0, "end")
            self.ent_start_num.insert(0, str(c_num + 1))
        except Exception:
            pass

        self.update_task_id_preview()
        self.save_current_task_config()
        self.log(f"Đã tự động tăng sang task tiếp theo: [{self.active_task_id}]", "AUTO")

    def host_apply_room_code(self):
        rcode = self.ent_host_room.get().strip().upper()
        if len(rcode) == 6 and rcode.isalnum():
            self.room_code = rcode
            be.set_clipboard(self.room_code)
            self.log(f"ĐÃ ÁP DỤNG MÃ PHÒNG HOST: [{self.room_code}]! Đã tự động copy vào Clipboard.", "HOST")
            self.start_host_room_tracking(self.room_code)
        else:
            messagebox.showwarning("Mã không hợp lệ", "Mã phòng phải gồm đúng 6 ký tự chữ/số (Ví dụ: UNJQEV)!")

    def copy_room_code(self):
        code = self.ent_host_room.get().strip().upper() if hasattr(self, "ent_host_room") else self.room_code
        if code and code != "ĐANG TẠO...":
            be.set_clipboard(code)
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(code)
            except Exception:
                pass
            self.log(f"Đã sao chép mã phòng [{code}] vào Clipboard.", "HOST")
            messagebox.showinfo("Đã sao chép", f"Mã phòng: {code}\nHãy gửi cho 3 thành viên còn lại!")

    def unlock_host_controls(self):
        """Mở khóa toàn bộ thao tác sau khi người dùng đã bấm Mở Odx Capture"""
        self.odx_opened = True
        self.odx_launch_time = time.time()
        if hasattr(self, "btn_open_odx"):
            self.btn_open_odx.config(
                text="✓ Đã Mở Odx (Tạo Room)", bg="#059669", activebackground="#047857"
            )
        if hasattr(self, "btn_host_start"):
            self.btn_host_start.config(state="normal", bg="#059669", fg="#FFFFFF")
        if hasattr(self, "btn_host_stop"):
            self.btn_host_stop.config(state="disabled", bg="#334155", fg="#64748B")
        if hasattr(self, "btn_host_abort"):
            self.btn_host_abort.config(state="normal", bg="#DC2626", fg="#FFFFFF")
        if hasattr(self, "btn_host_cancel"):
            self.btn_host_cancel.config(state="normal", bg="#334155", fg="#CBD5E1")
        if hasattr(self, "btn_inject_task"):
            self.btn_inject_task.config(state="normal", bg="#2563EB", fg="#FFFFFF")
        if hasattr(self, "btn_auto_fix"):
            self.btn_auto_fix.config(state="normal", bg="#D97706", fg="#FFFFFF")
        if hasattr(self, "lbl_host_status"):
            self.lbl_host_status.config(
                text=f"TRẠNG THÁI: [✓ SẴN SÀNG] BẤM F10 ĐỂ QUAY TASK [{self.active_task_id}]",
                fg=ModernTheme.ACCENT_GREEN
            )
        self.log("★ [ĐÃ MỞ ODX CAPTURE] Đã mở khóa toàn bộ thao tác điều khiển & phím F10!", "SUCCESS")

    def reset_host_to_unopened_state(self):
        """Khóa toàn bộ thao tác, reset nút Mở Odx, xóa mã phòng và slot khi Odx bị tắt/dọn sạch"""
        self.odx_opened = False
        self.room_code = ""
        self.is_recording = False
        self.is_counting_down = False
        self.is_in_cooldown = False
        self.rec_start_time = 0
        self.abort_state = None

        if hasattr(self, "btn_open_odx"):
            self.btn_open_odx.config(
                text="🖥 Mở Odx Capture (Tạo Room)",
                bg=ModernTheme.ACCENT_BLUE,
                activebackground=ModernTheme.ACCENT_BLUE_HOVER,
                state="normal"
            )
        if hasattr(self, "ent_host_room"):
            self.ent_host_room.delete(0, "end")

        if hasattr(self, "btn_host_start"):
            self.btn_host_start.config(state="disabled", bg="#1E293B", fg="#64748B")
        if hasattr(self, "btn_host_stop"):
            self.btn_host_stop.config(state="disabled", bg="#1E293B", fg="#64748B")
        if hasattr(self, "btn_host_abort"):
            self.btn_host_abort.config(state="disabled", bg="#1E293B", fg="#64748B")
        if hasattr(self, "btn_host_cancel"):
            self.btn_host_cancel.config(state="disabled", bg="#1E293B", fg="#64748B")
        if hasattr(self, "btn_inject_task"):
            self.btn_inject_task.config(state="disabled", bg="#1E293B", fg="#64748B")
        if hasattr(self, "btn_auto_fix"):
            self.btn_auto_fix.config(state="disabled", bg="#1E293B", fg="#64748B")

        if hasattr(self, "lbl_host_status"):
            self.lbl_host_status.config(
                text="⚠️ BƯỚC 1: HÃY BẤM NÚT [MỞ ODX CAPTURE (TẠO ROOM)] TRƯỚC TIÊN!",
                fg=ModernTheme.ACCENT_AMBER
            )
        if hasattr(self, "lbl_timer"):
            self.lbl_timer.config(text="00:00 / 04:00 (±5s)")

        if hasattr(self, "slot_labels"):
            for i, lbl in enumerate(self.slot_labels):
                lbl.config(text=f"Slot {i+1}: Trống...", fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD_LIGHT)

        # Reset ca ben Player tab neu dang ton tai
        if hasattr(self, "btn_p_connect"):
            self.btn_p_connect.config(text="🚀 KẾT NỐI VÀO PHÒNG (TỰ ĐỘNG 100%)", bg="#059669", state="normal")
        if hasattr(self, "lbl_p_status"):
            self.lbl_p_status.config(text="CHƯA KẾT NỐI - HÃY NHẬP MÃ PHÒNG VÀ BẤM KẾT NỐI", fg=ModernTheme.ACCENT_AMBER)
        if hasattr(self, "p_slot_labels"):
            for i, lbl in enumerate(self.p_slot_labels):
                lbl.config(text=f"Slot {i+1}: Chờ...", fg=ModernTheme.TEXT_MUTED)

        if self.tracker:
            self.tracker.stop()
            self.tracker = None

        if self.overlay:
            self.overlay.clear()

    def host_open_odx(self):
        self.unlock_host_controls()
        self.update_task_id_preview()
        be.cleanup_all_odx(kill_gui=True)
        time.sleep(0.5)

        # Reset follower to file end so it NEVER picks old room codes!
        self.follower.reset_to_end()
        self.room_code = ""
        if hasattr(self, "ent_host_room"):
            self.ent_host_room.delete(0, "end")
            self.ent_host_room.insert(0, "Tạo room...")

        # Inject task id into config before starting Odx Capture
        be.set_task_box(self.active_task_id)

        exe_path = os.path.join(be.ODX_ROOT, "Odx Capture.exe")
        if not os.path.exists(exe_path):
            alt_exe = os.path.join(be.ODX_ROOT, "bin", "odx-app.exe")
            if os.path.exists(alt_exe):
                exe_path = alt_exe
            else:
                messagebox.showerror("Lỗi", f"Không tìm thấy Odx Capture.exe tại: {be.ODX_ROOT}")
                return

        odx_hwnd = be.find_odx_window()
        if not odx_hwnd:
            self.log(f"Đang mở giao diện Odx Capture ({os.path.basename(exe_path)})...", "HOST")
            try:
                subprocess.Popen([exe_path], cwd=be.ODX_ROOT)
            except Exception as e:
                self.log(f"Lỗi khi khởi chạy Odx: {e}", "ERROR")
                messagebox.showerror("Lỗi khởi chạy", f"Không thể mở Odx Capture: {e}")
                return
        else:
            self.log("Odx Capture đã mở sẵn, đang tiến hành tự động tạo phòng...", "HOST")

        # Tu dong bam [Tao room] qua UI Automation
        be.host_auto_create_room(on_status=self.log)
        
        # Start following room code
        def _wait_room():
            self.log("Đang chờ bạn bấm [Tạo room] trên cửa sổ Odx Capture...", "HOST")
            w_start = time.time()
            while time.time() - w_start < 90 and self.is_running:
                self.follower.check_new_lines()
                if self.follower.room_code:
                    self.room_code = self.follower.room_code
                    if hasattr(self, "ent_host_room"):
                        self.ent_host_room.delete(0, "end")
                        self.ent_host_room.insert(0, self.room_code)
                    be.set_clipboard(self.room_code)
                    self.log(f"ĐÃ TỰ ĐỘNG NHẬN DIỆN MÃ PHÒNG: [{self.room_code}]! Đã copy vào Clipboard.", "HOST")
                    # Re-inject task box when room is ready
                    be.set_task_box(self.active_task_id)
                    self.start_host_room_tracking(self.room_code)
                    break
                time.sleep(0.5)

        threading.Thread(target=_wait_room, daemon=True).start()

    def start_host_room_tracking(self, room_code):
        if self.tracker:
            self.tracker.stop()

        def _on_roster_update(rc, host_id, members):
            def _update():
                try:
                    ready_cnt = sum(1 for idx, m in enumerate(members) if m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id)))
                    all_ready = (len(members) >= 4 and ready_cnt >= 4)

                    # Update UI labels
                    for idx in range(4):
                        if idx < len(members):
                            m = members[idx]
                            pname = str(m.get("name") or m.get("player_id"))
                            is_h = (str(m.get("player_id")) == str(host_id) or idx == 0)
                            tag = "[H] " if is_h else ""
                            st = "✓ SẴN SÀNG" if (is_h or m.get("ready")) else ".. Đang chờ"
                            col = ModernTheme.ACCENT_GREEN if (is_h or m.get("ready")) else ModernTheme.ACCENT_AMBER
                            self.slot_labels[idx].config(text=f"{tag}{pname}: {st}", fg=col)
                        else:
                            self.slot_labels[idx].config(text=f"Slot {idx+1}: Trống...", fg=ModernTheme.TEXT_MUTED)

                    if all_ready:
                        self.lbl_host_status.config(text="TRẠNG THÁI: [✓ ĐỦ 4/4 NGƯỜI SẴN SÀNG 100%] -> BẤM F10 ĐỂ BẮT ĐẦU!", fg=ModernTheme.ACCENT_GREEN)
                    else:
                        self.lbl_host_status.config(text=f"TRẠNG THÁI: Đang chờ thành viên ({ready_cnt}/4 sẵn sàng)...", fg=ModernTheme.ACCENT_AMBER)
                except Exception:
                    pass
            try:
                self.root.after(0, _update)
            except Exception:
                pass

        self.tracker = be.RoomTracker(room_code, overlay=self.overlay, on_change_callback=_on_roster_update)
        self.tracker.start()

    def host_trigger_start(self, from_hotkey=False):
        if not getattr(self, "odx_opened", False):
            self.log("⚠️ BỊ CHẶN: Bạn phải bấm nút '[🖥 Mở Odx Capture (Tạo Room)]' trước tiên!", "WARN")
            self.overlay.show_roster_hud("⚠️ HÃY BẤM [MỞ ODX CAPTURE] TRƯỚC TIÊN!", fg_color="#EF4444")
            if not from_hotkey:
                messagebox.showwarning(
                    "Chưa mở Odx Capture",
                    "Bạn phải nhấn nút '[🖥 Mở Odx Capture (Tạo Room)]' trước tiên!\n\nNếu chưa mở Odx Capture, các thao tác sau đó sẽ bị khóa hoàn toàn."
                )
            return

        if self.is_recording or self.is_in_cooldown or self.is_counting_down or getattr(self, "is_validating", False):
            return

        # -------------------------------------------------------------
        # KIỂM TRA TRẠNG THÁI SẴN SÀNG CỦA CẢ 4 THÀNH VIÊN TRONG PHÒNG
        # -------------------------------------------------------------
        if self.tracker and getattr(self, "odx_opened", False):
            room_data = self.tracker.latest_data
            if room_data and "members" in room_data:
                members = room_data.get("members", [])
                host_id = room_data.get("host")
                unready_names = []
                for idx, m in enumerate(members):
                    pid = str(m.get("player_id", ""))
                    name = str(m.get("name") or pid)
                    is_h = (pid == str(host_id)) or (idx == 0 and not host_id)
                    if not is_h and not m.get("ready"):
                        unready_names.append(name)

                # 1. Cảnh báo chưa đủ 4 thành viên trong phòng
                if len(members) < 4:
                    missing_cnt = 4 - len(members)
                    self.log(f"⚠️ [CHƯA ĐỦ THÀNH VIÊN]: Phòng mới có {len(members)}/4 người (Thiếu {missing_cnt} người)! Chưa thể bắt đầu round.", "WARN")
                    self.overlay.show_roster_hud(f"⚠️ [CHƯA ĐỦ 4 NGƯỜI]: Phòng mới có {len(members)}/4 người! Chờ đủ 4 người.", fg_color="#EF4444")
                    self.lbl_host_status.config(text=f"⚠️ CHƯA ĐỦ 4 NGƯỜI ({len(members)}/4). Chờ thêm {missing_cnt} người...", fg=ModernTheme.ACCENT_RED)
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    except Exception:
                        pass
                    return

                # 2. Cảnh báo có thành viên chưa bấm Sẵn sàng
                if unready_names:
                    names_str = ", ".join(unready_names)
                    self.log(f"⚠️ [CHƯA SẴN SÀNG]: Đang chờ thành viên [{names_str}] bật Sẵn sàng (Đang Chờ)! Chưa thể bắt đầu round.", "WARN")
                    self.overlay.show_roster_hud(f"⚠️ [CHƯA SẴN SÀNG]: Đang chờ [{names_str}]! Bấm F10 khi tất cả Ready.", fg_color="#EF4444")
                    self.lbl_host_status.config(text=f"⚠️ CHƯA SẴN SÀNG: Đang chờ [{names_str}]...", fg=ModernTheme.ACCENT_RED)
                    try:
                        import winsound
                        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                    except Exception:
                        pass
                    return

        now = time.time()
        if now - getattr(self, "last_start_trigger_time", 0) < 2.0:
            return
        self.last_start_trigger_time = now
        self.ignore_f10_until = now + 2.0

        # Reset follower flags for the new round so old results never leak
        self.follower.last_kept_path = None
        self.follower.last_all_ok = None
        self.follower.last_round_result = None

        self.abort_state = None
        self.update_task_id_preview()
        self.original_task_before_abort = re.sub(r"-fix\d+$", "", self.active_task_id)

        # Luon luon kiem tra trung ma task truoc khi bat dau - Chan tuyet doi khong cho quay de
        exists, exist_path = be.is_task_already_recorded(self.active_task_id)
        if exists:
            suggest_fix = be.get_next_available_fix_task_id(self.active_task_id)
            base_id = re.sub(r"-fix\d+$", "", self.active_task_id)
            self.original_task_before_abort = base_id
            self.pending_fix_task = suggest_fix

            m = re.search(r'(\d+)$', base_id)
            if m:
                digits = m.group(1)
                next_val = int(digits) + 1
                self.pending_next_task = base_id[:m.start(1)] + str(next_val).zfill(len(digits))
            else:
                self.pending_next_task = f"{base_id}-next"

            self.abort_state = "AWAITING_CHOICE"
            if self.tracker:
                self.tracker.pause_hud()

            msg_hud = f"⚠️ [TRÙNG MÃ] [F11: Fix {self.pending_fix_task}] | [F10: Mới {self.pending_next_task}] | [F9: HỦY VỀ BAN ĐẦU]"
            self.lbl_host_status.config(text=msg_hud, fg=ModernTheme.ACCENT_RED)
            self.overlay.show_roster_hud(msg_hud, fg_color="#EF4444")

            self.log("=" * 65, "WARN")
            self.log(f"⚠️ [CHẶN TRÙNG MÃ TASK]: Task [{self.active_task_id}] ĐÃ TỒN TẠI TRÊN MÁY!", "WARN")
            self.log(f"   Thư mục đã có: {exist_path}", "WARN")
            self.log(f"HÃY CHỌN 1 TRONG 3 THAO TÁC SONG SONG ĐỂ TIẾP TỤC:", "WARN")
            self.log(f"  ★ Bấm [F11]: Tự đổi sang Task Sửa Lỗi [{self.pending_fix_task}] & Quay ngay", "FIX")
            self.log(f"  ★ Bấm [F10]: Nhảy sang Task Mới [{self.pending_next_task}] & Quay ngay", "TASK")
            self.log(f"  ★ Bấm [F9] : HỦY BỎ & VỀ BAN ĐẦU", "CANCEL")
            self.log("=" * 65, "WARN")
            return

        # Thời gian đồng bộ chuẩn 100% giữa Host và Player: 4 phút ± 5s = 235s đến 245s
        self.target_duration = be.get_target_duration_for_task(self.active_task_id)
        m_tg, s_tg = divmod(self.target_duration, 60)
        target_str = f"{m_tg:02d}:{s_tg:02d}"

        be.set_task_box(self.active_task_id)

        # Trigger Odx via F10 ONLY IF user clicked the button on GUI.
        # If user pressed physical F10 on keyboard, Odx ALREADY received it directly!
        if not from_hotkey:
            hwnd = be.find_odx_window()
            be.send_toggle_hotkey(hwnd)

        # Tam dung HUD Roster de khong bi nhap nhay
        if self.tracker:
            self.tracker.pause_hud()

        self.btn_host_start.config(state="disabled")
        self.btn_host_stop.config(state="normal")
        self.is_counting_down = True
        self.is_recording = False
        self.rec_start_time = 0

        self.log(f"PHÁT LỆNH F10 TASK [{self.active_task_id}]: Odx đếm ngược 3s... Thời lượng mục tiêu ngẫu nhiên (4p ± 5s): {target_str} ({self.target_duration}s)", "START")
        self.lbl_host_status.config(text=f"⏳ [ODX ĐẾM NGƯỢC 3S]: Chuẩn bị quay [{self.active_task_id}]...", fg=ModernTheme.ACCENT_AMBER)
        self.lbl_timer.config(text=f"00:00 / {target_str} (Odx đếm: 3s)")

        def _do_countdown_and_start():
            for c in range(3, 0, -1):
                if not self.is_running or not self.is_counting_down:
                    return
                self.overlay.show_roster_hud(f"⏳ [ODX ĐẾM NGƯỢC]: {c}s... Chuẩn bị quay [{self.active_task_id}] (Mục tiêu: {target_str})", fg_color="#F59E0B")
                self.lbl_timer.config(text=f"00:00 / {target_str} (Odx đếm: {c}s)")
                self.lbl_host_status.config(text=f"⏳ [ODX ĐẾM NGƯỢC {c}S]: Chuẩn bị quay [{self.active_task_id}]...", fg=ModernTheme.ACCENT_AMBER)
                time.sleep(1)

            if not self.is_running or not self.is_counting_down:
                return

            self.is_counting_down = False
            self.is_recording = True
            self.rec_start_time = time.time()
            self.lbl_host_status.config(text=f"● ĐANG QUAY: [{self.active_task_id}] | Mục tiêu: {target_str}", fg=ModernTheme.ACCENT_GREEN)
            self.lbl_timer.config(text=f"00:00 / {target_str} | Còn: {target_str}")
            self.overlay.update_recording(self.active_task_id, 0, self.target_duration, blink=False, is_host=True)
            self.log(f"★ BẮT ĐẦU TÍNH GIỜ GHI HÌNH CHÍNH THỨC TASK [{self.active_task_id}]! Thời lượng mục tiêu chính xác: {target_str} ({self.target_duration}s)", "RECORD")

        threading.Thread(target=_do_countdown_and_start, daemon=True).start()

    def host_trigger_stop(self, from_hotkey=False):
        if not self.is_recording:
            return
        now = time.time()
        if now - getattr(self, "last_stop_trigger_time", 0) < 2.5:
            return
        self.last_stop_trigger_time = now
        self.ignore_f10_until = now + 2.5

        elapsed = time.time() - self.rec_start_time
        self.log(f"Đã phát lệnh DỪNG QUAY task [{self.active_task_id}] sau {int(elapsed)}s...", "STOP")
        self.is_recording = False
        self.is_counting_down = False
        self.rec_start_time = 0
        self.follower.current_recording = None
        self.btn_host_start.config(state="normal")
        self.btn_host_stop.config(state="disabled")
        self.lbl_timer.config(text="00:00 / 04:00 (±5s)")
        self.lbl_host_status.config(text="ĐANG DỪNG VÀ ĐÓNG GÓI...", fg=ModernTheme.ACCENT_AMBER)
        self.overlay.clear()

        # Bat lai HUD Roster sau khi dung quay
        if self.tracker:
            self.tracker.resume_hud()

        # Only simulate F10 if user clicked GUI button or stopped automatically by timer.
        if not from_hotkey:
            hwnd = be.find_odx_window()
            be.stop_recording_verified(self.follower, self.active_task_id, hwnd=hwnd)
        else:
            time.sleep(0.5)

        self.overlay.clear()

        # Run validation in background if not already validating
        if not getattr(self, "is_validating", False):
            threading.Thread(target=self.run_post_capture_validation, args=(self.active_task_id,), daemon=True).start()

    def host_cancel_and_reset(self):
        """Hủy bỏ toàn bộ lựa chọn, quay về trạng thái sẵn sàng ban đầu"""
        self.is_recording = False
        self.is_counting_down = False
        self.is_in_cooldown = False
        self.rec_start_time = 0
        self.abort_state = None
        self.btn_host_start.config(state="normal")
        self.btn_host_stop.config(state="disabled")
        self.lbl_timer.config(text="00:00 / 04:00 (±5s)")

        # Lấy lại mã task gốc ban đầu
        orig_task = getattr(self, "original_task_before_abort", None)
        if not orig_task:
            orig_task = getattr(self, "active_task_id", "")
        base_id = re.sub(r"-fix\d+$", "", orig_task)
        if not base_id:
            base_id = f"{self.config.get('batch_prefix', 'asm-s221-')}01"

        self.active_task_id = base_id

        # Cập nhật ô nhập subtask và start_num trên GUI
        m = re.search(r'(\d+)$', base_id)
        if m and hasattr(self, "ent_subtask"):
            self.ent_subtask.delete(0, "end")
            self.ent_subtask.insert(0, m.group(1))
        if m and hasattr(self, "ent_start_num"):
            try:
                self.ent_start_num.delete(0, "end")
                self.ent_start_num.insert(0, str(int(m.group(1))))
            except Exception:
                pass

        if hasattr(self, "ent_full_task"):
            self.ent_full_task.delete(0, "end")
            self.ent_full_task.insert(0, base_id)
        if hasattr(self, "lbl_task_display"):
            self.lbl_task_display.config(text=f"[Task: {base_id}]")

        be.set_task_box(base_id)
        self.update_task_id_preview()

        if self.tracker:
            self.tracker.resume_hud()

        msg_status = f"TRẠNG THÁI: [✓ SẴN SÀNG] Task [{base_id}] | Bấm F10 để bắt đầu quay"
        self.lbl_host_status.config(text=msg_status, fg=ModernTheme.ACCENT_GREEN)
        self.log(f"[CANCEL] ĐÃ HỦY VÀ KHÔI PHỤC VỀ BAN ĐẦU! Task hiện tại: [{base_id}]. Bấm F10 để bắt đầu quay.", "CANCEL")
        self.overlay.show_roster_hud(f"🔄 [ĐÃ HỦY VỀ BAN ĐẦU]: [{base_id}] | Bấm F10 để bắt đầu!", fg_color="#10B981")

    def host_choose_option(self, choice):
        """Xử lý trực tiếp 1 trong 3 lựa chọn song song (F10: FIX | F11: NEXT | F9: CANCEL)"""
        if self.abort_state != "AWAITING_CHOICE":
            return

        base_id = getattr(self, "original_task_before_abort", None) or re.sub(r"-fix\d+$", "", self.active_task_id)

        if choice == "FIX":
            self.fix_counter += 1
            fix_id = getattr(self, "pending_fix_task", f"{base_id}-fix{self.fix_counter}")
            self.abort_state = None
            self.active_task_id = fix_id
            if hasattr(self, "ent_full_task"):
                self.ent_full_task.delete(0, "end")
                self.ent_full_task.insert(0, fix_id)
            if hasattr(self, "lbl_task_display"):
                self.lbl_task_display.config(text=f"[Task: {fix_id}]")
            be.set_task_box(fix_id)
            self.update_task_id_preview()
            self.log(f"★ BẠN ĐÃ CHỌN [F11: QUAY TASK FIX] -> [{fix_id}]! Đang bắt đầu ghi hình...", "RECORD")
            self.host_trigger_start(from_hotkey=True)

        elif choice == "NEXT":
            next_id = getattr(self, "pending_next_task", "")
            if not next_id:
                m = re.search(r'(\d+)$', base_id)
                if m:
                    digits = m.group(1)
                    next_val = int(digits) + 1
                    next_id = base_id[:m.start(1)] + str(next_val).zfill(len(digits))
                else:
                    next_id = f"{base_id}-next"

            self.abort_state = None
            self.active_task_id = next_id

            m = re.search(r'(\d+)$', next_id)
            if m and hasattr(self, "ent_subtask"):
                self.ent_subtask.delete(0, "end")
                self.ent_subtask.insert(0, m.group(1))
            if m and hasattr(self, "ent_start_num"):
                try:
                    self.ent_start_num.delete(0, "end")
                    self.ent_start_num.insert(0, str(int(m.group(1))))
                except Exception:
                    pass

            if hasattr(self, "ent_full_task"):
                self.ent_full_task.delete(0, "end")
                self.ent_full_task.insert(0, next_id)
            if hasattr(self, "lbl_task_display"):
                self.lbl_task_display.config(text=f"[Task: {next_id}]")
            be.set_task_box(next_id)
            self.update_task_id_preview()
            self.log(f"★ BẠN ĐÃ CHỌN [F10: QUAY TASK MỚI] -> [{next_id}]! Đang bắt đầu ghi hình...", "RECORD")
            self.host_trigger_start(from_hotkey=True)

        elif choice == "CANCEL":
            self.host_cancel_and_reset()

    def host_trigger_abort(self):
        # 0. TRƯỜNG HỢP ĐANG TRONG 10S NGHỈ COOLDOWN -> NGẮT NGHỈ, DỪNG VÀ TẮT HUD
        if self.is_in_cooldown:
            self.is_in_cooldown = False
            self.overlay.clear()

        # 1. NẾU ĐANG GHI HÌNH HOẶC ĐẾM NGƯỢC -> GỬI LỆNH HỦY KHẨN CẤP F11 ĐẾN ODX
        if self.is_recording or self.is_counting_down or self.follower.current_recording:
            self.log(f"PHÁT LỆNH HỦY KHẨN CẤP TASK [{self.active_task_id}] (F11)!", "ABORT")
            self.is_recording = False
            self.is_counting_down = False
            self.rec_start_time = 0
            self.follower.current_recording = None
            self.follower.current_stopped = None
            self.btn_host_start.config(state="normal")
            self.btn_host_stop.config(state="disabled")
            self.lbl_timer.config(text="00:00 / 04:00 (±5s)")
            self.ignore_f10_until = time.time() + 2.5
            be.send_abort_hotkey()

        # 2. TẠM DỪNG HUD DANH SÁCH PHÒNG ĐỂ KHÔNG BỊ ĐÈ GIAO DIỆN LỰA CHỌN
        if self.tracker:
            self.tracker.pause_hud()

        # 3. TÍNH TOÁN CẢ 3 LỰA CHỌN
        base_id = re.sub(r"-fix\d+$", "", self.active_task_id or "")
        if not base_id:
            base_id = f"{self.config.get('batch_prefix', 'asm-s221-')}01"
        self.original_task_before_abort = base_id
        self.pending_fix_task = f"{base_id}-fix{self.fix_counter + 1}"

        m = re.search(r'(\d+)$', base_id)
        if m:
            digits = m.group(1)
            next_val = int(digits) + 1
            new_digits = str(next_val).zfill(len(digits))
            self.pending_next_task = base_id[:m.start(1)] + new_digits
        else:
            self.pending_next_task = f"{base_id}-next"

        # 4. KÍCH HOẠT TRẠNG THÁI CHỜ LỰA CHỌN VÀ HIỂN THỊ SONG SONG TRÊN THANH HUD & CONSOLE
        self.abort_state = "AWAITING_CHOICE"

        msg_hud = f"⚡ [F11: Fix {self.pending_fix_task}] | [F10: Mới {self.pending_next_task}] | [F9: HỦY VỀ BAN ĐẦU]"
        self.lbl_host_status.config(text=msg_hud, fg=ModernTheme.ACCENT_AMBER)
        self.overlay.show_roster_hud(msg_hud, fg_color="#F59E0B")

        self.log("=" * 65, "ABORT")
        self.log(f"★ HÃY CHỌN 1 TRONG 3 THAO TÁC SONG SONG:", "ABORT")
        self.log(f"  ★ Bấm [F11]: Quay Task Sửa Lỗi [{self.pending_fix_task}]", "FIX")
        self.log(f"  ★ Bấm [F10]: Quay Task Mới Tiếp Theo [{self.pending_next_task}]", "TASK")
        self.log(f"  ★ Bấm [F9] : HỦY BỎ & VỀ BAN ĐẦU [{base_id}]", "CANCEL")
        self.log("=" * 65, "ABORT")

    def run_post_capture_validation(self, task_id):
        if getattr(self, "is_validating", False):
            return
        self.is_validating = True
        try:
            self.log(f"Đang chờ Odx hoàn tất ghi đĩa và đóng gói ({task_id})...", "WAIT")
            time.sleep(3)

            # Thư mục kiểm định PHẢI LUÔN LUÔN gắn chặt với task_id vừa quay
            target_folder = os.path.join(be.ODX_ROOT, "data", "captures", task_id)
            w = time.time()
            while time.time() - w < 8 and self.is_running:
                if os.path.exists(target_folder) and os.path.exists(os.path.join(target_folder, "events.jsonl")):
                    break
                time.sleep(0.5)

            if not os.path.exists(target_folder):
                last_p = getattr(self.follower, "last_kept_path", None)
                if last_p and os.path.exists(last_p) and task_id in os.path.basename(last_p):
                    target_folder = last_p

            if target_folder and os.path.exists(target_folder):
                # Wait thêm chút nếu events.jsonl chưa ghi xong
                w_ev = time.time()
                while time.time() - w_ev < 5 and self.is_running:
                    if os.path.exists(os.path.join(target_folder, "events.jsonl")):
                        break
                    time.sleep(0.5)

                self.log(f"TIẾN HÀNH KIỂM ĐỊNH CHUẨN ADMIN CHO: [{os.path.basename(target_folder)}]", "VALIDATE")
                is_pass = be.validate_and_inspect_capture(target_folder, is_room_mode=True)

                # CHỜ VÀ XÁC NHẬN TÍN HIỆU ALL OK TỪ SERVER ORCHESTRATOR
                self.log("Đang chờ xác nhận tín hiệu [ALL OK] từ hệ thống (cả 4 máy thành viên lưu thành công)...", "WAIT")
                self.lbl_host_status.config(text="ĐANG CHỜ TÍN HIỆU [ALL OK] TỪ 4 MÁY...", fg=ModernTheme.ACCENT_AMBER)
                self.overlay.show_roster_hud("⏳ Đang chờ xác nhận [ALL OK] từ 4 máy...", fg_color="#F59E0B")

                all_ok_confirmed = False
                round_error_msg = None
                w_all_ok = time.time()
                while time.time() - w_all_ok < 15 and self.is_running:
                    self.follower.check_new_lines()
                    # Kiểm tra all ok
                    if getattr(self.follower, "last_all_ok", None) == task_id or (self.follower.last_round_result and self.follower.last_round_result[0] == task_id and "all ok" in self.follower.last_round_result[1].lower()):
                        all_ok_confirmed = True
                        break
                    # Kiểm tra máy chủ báo lỗi thành viên
                    if self.follower.last_round_result and self.follower.last_round_result[0] == task_id:
                        r_text = self.follower.last_round_result[1]
                        if "record again" in r_text.lower() or "failed" in r_text.lower() or "not done" in r_text.lower():
                            round_error_msg = r_text
                            break
                    time.sleep(0.5)

                if round_error_msg:
                    self.log(f"⚠️ [MÁY CHỦ BÁO LỖI] Task [{task_id}]: {round_error_msg}!", "ERROR")
                    self.lbl_host_status.config(text=f"[!] PHÒNG CÓ LỖI: {round_error_msg}", fg=ModernTheme.ACCENT_RED)
                    self.overlay.show_roster_hud(f"⚠️ LỖI THÀNH VIÊN: {round_error_msg}", fg_color="#EF4444")

                if all_ok_confirmed:
                    self.announced_all_ok.add(task_id)
                    self.log("=" * 65, "SUCCESS")
                    self.log(f"★ [✓ ALL OK 100%] TẤT CẢ 4 THÀNH VIÊN ĐÃ ĐỒNG BỘ VÀ LƯU DỮ LIỆU THÀNH CÔNG! ★", "SUCCESS")
                    self.log("=" * 65, "SUCCESS")
                    self.overlay.show_roster_hud(f"★ [✓ ALL OK] CẢ 4 THÀNH VIÊN THÀNH CÔNG 100%! ★", fg_color="#10B981")
                    self.lbl_host_status.config(text=f"[✓ ALL OK 100%] CẢ 4 MÁY THÀNH CÔNG: {os.path.basename(target_folder)}", fg=ModernTheme.ACCENT_GREEN)
                elif is_pass and not round_error_msg:
                    self.log(f"===> TASK [{os.path.basename(target_folder)}] ĐẠT CHUẨN ADMIN 100% [PASS CỤC BỘ]!", "SUCCESS")
                    self.lbl_host_status.config(text=f"[✓ PASS 100%] ĐÃ HOÀN TẤT: {os.path.basename(target_folder)}", fg=ModernTheme.ACCENT_GREEN)

                if (is_pass or all_ok_confirmed) and not round_error_msg:
                    if self.is_recording:
                        self.log(f"Kiểm định hoàn tất! Hiện bạn đang ghi hình task [{self.active_task_id}].", "VALIDATE")
                        return

                    # Auto increment task number & subtask
                    self.increment_task_number()
                    # Inject new task id into Odx Capture and Clipboard
                    be.set_task_box(self.active_task_id)

                    # NGHỈ 10S COOLDOWN
                    self.is_in_cooldown = True
                    self.log("BẮT ĐẦU NGHỈ GIẢI LAO 10 GIÂY TRƯỚC KHI CHO PHÉP QUAY TIẾP...", "COOLDOWN")
                    for cd in range(10, 0, -1):
                        if not self.is_running or self.is_recording or not self.is_in_cooldown:
                            break
                        self.overlay.show_roster_hud(f"⏳ [ALL OK - NGHỈ GIẢI LAO]: Còn {cd}s | Nghỉ ngơi trước task tiếp...", fg_color="#F59E0B")
                        self.lbl_host_status.config(text=f"[✓ ALL OK] ĐANG NGHỈ GIẢI LAO: Còn {cd}s... (Chuẩn bị: {self.active_task_id})", fg=ModernTheme.ACCENT_AMBER)
                        time.sleep(1)
                    
                    self.is_in_cooldown = False
                    if not self.is_recording:
                        self.lbl_timer.config(text="00:00 / 04:00 (±5s)")
                        self.overlay.show_roster_hud(f"[{self.active_task_id}] [✓ SẴN SÀNG] BẤM F10 ĐỂ BẮT ĐẦU QUAY!", fg_color="#10B981")
                        self.lbl_host_status.config(text=f"TRẠNG THÁI: [✓ SẴN SÀNG] BẤM F10 ĐỂ QUAY TASK [{self.active_task_id}]", fg=ModernTheme.ACCENT_GREEN)
                        self.log(f"ĐÃ HẾT 10S NGHỈ! Mã task mới [{self.active_task_id}] đã sẵn sàng. BẤM F10 KHI BẠN MUỐN BẮT ĐẦU.", "READY")
                        time.sleep(3)
                        if not self.is_recording:
                            self.overlay.clear()
                else:
                    self.log(f"[!] CẢNH BÁO: Task [{os.path.basename(target_folder)}] chưa đạt chuẩn đầy đủ hoặc có máy thành viên bị lỗi!", "WARN")
                    self.lbl_host_status.config(text=f"[!] CẢNH BÁO KIỂM ĐỊNH: {os.path.basename(target_folder)}", fg=ModernTheme.ACCENT_AMBER)
                    self.lbl_timer.config(text="00:00 / 04:00 (±5s)")
            else:
                self.log(f"[!] Không tìm thấy thư mục lưu dữ liệu cho task [{task_id}]!", "WARN")
                self.lbl_host_status.config(text=f"[!] KHÔNG TÌM THẤY DỮ LIỆU: {task_id}", fg=ModernTheme.ACCENT_AMBER)
                self.lbl_timer.config(text="00:00 / 04:00 (±5s)")
        finally:
            self.is_validating = False

    # ------------------------------------------------------------------
    # PLAYER CONTROLLER WORKFLOW & CLIPBOARD HELPERS
    # ------------------------------------------------------------------
    def setup_entry_clipboard(self, entry, is_room_code=False):
        """Kích hoạt Ctrl+V, Ctrl+C, Ctrl+A và menu chuột phải (Dán/Sao chép/Cắt) cho ô nhập liệu."""
        menu = tk.Menu(entry, tearoff=0)

        def do_paste():
            txt = be.get_clipboard()
            if not txt:
                try:
                    txt = self.root.clipboard_get()
                except Exception:
                    txt = ""
            if not txt:
                return
            if is_room_code:
                import re
                m = re.search(r'[A-Za-z0-9]{6}', txt)
                paste_val = m.group(0).upper() if m else re.sub(r'[^A-Za-z0-9]', '', txt)[:6].upper()
                if paste_val:
                    entry.delete(0, "end")
                    entry.insert(0, paste_val)
                    if hasattr(self, "log"):
                        self.log(f"Đã dán mã phòng: [{paste_val}]", "PLAYER")
            else:
                try:
                    sel = entry.selection_get()
                    if sel:
                        entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
                except Exception:
                    pass
                entry.insert(tk.INSERT, txt)

        def do_copy():
            try:
                sel = entry.selection_get()
                if sel:
                    be.set_clipboard(sel)
                    self.root.clipboard_clear()
                    self.root.clipboard_append(sel)
            except Exception:
                val = entry.get()
                if val:
                    be.set_clipboard(val)
                    self.root.clipboard_clear()
                    self.root.clipboard_append(val)

        def do_cut():
            do_copy()
            try:
                entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
            except Exception:
                entry.delete(0, "end")

        def do_select_all():
            entry.select_range(0, "end")
            entry.icursor("end")

        menu.add_command(label="📋 Dán (Paste)", command=do_paste)
        menu.add_command(label="📄 Sao chép (Copy)", command=do_copy)
        menu.add_command(label="✂ Cắt (Cut)", command=do_cut)
        menu.add_separator()
        menu.add_command(label="✔ Chọn tất cả (Select All)", command=do_select_all)

        def show_menu(event):
            menu.tk_popup(event.x_root, event.y_root)

        entry.bind("<Button-3>", show_menu)
        entry.bind("<Control-v>", lambda e: (do_paste(), "break")[1])
        entry.bind("<Control-V>", lambda e: (do_paste(), "break")[1])
        entry.bind("<Control-a>", lambda e: (do_select_all(), "break")[1])
        entry.bind("<Control-A>", lambda e: (do_select_all(), "break")[1])

    def player_paste_code(self):
        try:
            txt = be.get_clipboard()
            if not txt:
                try:
                    txt = self.root.clipboard_get()
                except Exception:
                    txt = ""
            txt = (txt or "").strip().upper()
            import re
            m = re.search(r'[A-Z0-9]{6}', txt)
            if m:
                code = m.group(0)
                self.ent_p_room.delete(0, "end")
                self.ent_p_room.insert(0, code)
                self.log(f"Đã dán mã phòng: [{code}]", "PLAYER")
            elif txt:
                cleaned = re.sub(r'[^A-Z0-9]', '', txt)
                if cleaned:
                    code = cleaned[:6]
                    self.ent_p_room.delete(0, "end")
                    self.ent_p_room.insert(0, code)
                    self.log(f"Đã dán mã phòng: [{code}]", "PLAYER")
                else:
                    self.log(f"Bộ nhớ tạm không chứa mã phòng hợp lệ: '{txt[:20]}'", "WARN")
            else:
                self.log("Bộ nhớ tạm (Clipboard) đang trống!", "WARN")
        except Exception as e:
            self.log(f"Lỗi khi dán mã từ Clipboard: {e}", "WARN")

    def on_player_info_change(self):
        pid = self.ent_p_id.get().strip() if hasattr(self, "ent_p_id") else ""
        pname = self.ent_p_name.get().strip() if hasattr(self, "ent_p_name") else ""
        self.config["player_id"] = pid
        self.config["player_name"] = pname
        self.save_config()
        be.set_player_info(pid, pname)

    def player_apply_info_to_odx(self):
        pid = self.ent_p_id.get().strip() if hasattr(self, "ent_p_id") else ""
        pname = self.ent_p_name.get().strip() if hasattr(self, "ent_p_name") else ""
        self.config["player_id"] = pid
        self.config["player_name"] = pname
        self.save_config()
        be.set_player_info(pid, pname)
        self.log(f"Đã cập nhật Mã người chơi [{pid}] & Tên [{pname}] vào Odx Capture [OK]!", "PLAYER")
        messagebox.showinfo("Đã điền", f"Đã cập nhật vào Odx Capture:\n- Mã người chơi: {pid}\n- Tên người chơi: {pname}")

    def player_start_connection(self):
        rcode = self.ent_p_room.get().strip().upper()
        if len(rcode) != 6 or not rcode.isalnum():
            messagebox.showwarning("Mã không hợp lệ", "Mã phòng phải gồm đúng 6 ký tự chữ/số do Host gửi!")
            return

        # Kiem tra tren Orchestrator xem ma phong co ton tai khong
        r_chk = be.fetch_room_status(rcode)
        if isinstance(r_chk, dict) and r_chk.get("error") == "NOT_FOUND":
            messagebox.showerror(
                "Mã phòng không tồn tại",
                f"Mã phòng [{rcode}] KHÔNG TỒN TẠI trên hệ thống Odx!\n\n"
                "- Có thể Host chưa bấm 'Tạo room'.\n"
                "- Hoặc bạn gõ nhầm mã phòng.\n\n"
                "Vui lòng kiểm tra lại mã Host gửi trước khi kết nối."
            )
            self.log(f"LỖI: Mã phòng [{rcode}] KHÔNG TỒN TẠI trên máy chủ Odx (Host chưa tạo hoặc sai mã).", "ERROR")
            return

        pid = self.ent_p_id.get().strip() or "2312"
        pname = self.ent_p_name.get().strip() or "Hao"

        self.config["player_id"] = pid
        self.config["player_name"] = pname
        self.save_config()

        # Luon dong bo player info vao Odx Capture truoc
        be.set_player_info(pid, pname)

        # 1. Ghi cau hinh truoc vao %APPDATA%\Odx\odx-app.json de neu Odx moi khoi dong se nhan ngay
        appdata_json = os.path.expandvars(r"%APPDATA%\Odx\odx-app.json")
        try:
            cfg_data = {}
            if os.path.exists(appdata_json):
                with open(appdata_json, "r", encoding="utf-8") as f:
                    cfg_data = json.load(f)
            cfg_data["PlayerId"] = pid
            cfg_data["PlayerName"] = pname
            os.makedirs(os.path.dirname(appdata_json), exist_ok=True)
            with open(appdata_json, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f, indent=2)
        except Exception:
            pass

        # 2. Copy ma phong vao Clipboard ngay lap tuc
        be.set_clipboard(rcode)

        self.log(f"MÁY CON: Đang kết nối vào phòng [{rcode}]...", "PLAYER")
        self.lbl_p_status.config(text=f"ĐANG KẾT NỐI PHÒNG: [{rcode}] (AUTO-READY)...", fg=ModernTheme.ACCENT_AMBER)

        # 3. Kiem tra xem Odx Capture da chay chua, neu chua thi mo
        odx_hwnd = be.find_odx_window()
        if not odx_hwnd:
            exe_path = os.path.join(be.ODX_ROOT, "Odx Capture.exe")
            if not os.path.exists(exe_path):
                alt_exe = os.path.join(be.ODX_ROOT, "bin", "odx-app.exe")
                if os.path.exists(alt_exe):
                    exe_path = alt_exe
                else:
                    messagebox.showerror("Lỗi", f"Không tìm thấy Odx Capture tại: {be.ODX_ROOT}")
                    return
            self.log(f"Đang mở giao diện Odx Capture ({os.path.basename(exe_path)})...", "PLAYER")
            try:
                self.player_proc = subprocess.Popen([exe_path], cwd=be.ODX_ROOT)
                time.sleep(1.5)
            except Exception as e:
                self.log(f"Lỗi khởi chạy Odx: {e}", "ERROR")

        # 4. Kich hoat luong UI Automation tu dong dien va bam San sang
        def _on_p_status(msg, tag):
            self.log(msg, tag)
            if tag == "SUCCESS":
                self.lbl_p_status.config(text=f"[✓ ĐÃ SẴN SÀNG] PHÒNG [{rcode}] - ĐANG CHỜ HOST BẮT ĐẦU...", fg=ModernTheme.ACCENT_GREEN)
                if hasattr(self, "btn_p_connect"):
                    self.btn_p_connect.config(text="✓ ĐÃ KẾT NỐI PHÒNG (ĐỒNG BỘ 100%)", bg="#059669")
            elif tag == "ERROR":
                self.lbl_p_status.config(text=f"[!] {msg}", fg=ModernTheme.ACCENT_RED)
            elif tag == "WARN":
                self.lbl_p_status.config(text=f"[!] {msg}", fg=ModernTheme.ACCENT_AMBER)

        be.player_auto_join_and_ready(rcode, pid, pname, on_status=_on_p_status)

        # Start tracking room for Player
        def _on_p_roster(rc, host_id, members):
            def _update():
                try:
                    for idx in range(4):
                        if idx < len(members):
                            m = members[idx]
                            p_n = str(m.get("name") or m.get("player_id"))
                            is_h = (str(m.get("player_id")) == str(host_id) or idx == 0)
                            tag = "[H] " if is_h else ""
                            st = "✓ Sẵn sàng" if (is_h or m.get("ready")) else ".. Chờ"
                            col = ModernTheme.ACCENT_GREEN if (is_h or m.get("ready")) else ModernTheme.ACCENT_AMBER
                            self.p_slot_labels[idx].config(text=f"{tag}{p_n}: {st}", fg=col)
                        else:
                            self.p_slot_labels[idx].config(text=f"Slot {idx+1}: Trống...", fg=ModernTheme.TEXT_MUTED)
                except Exception:
                    pass
            try:
                self.root.after(0, _update)
            except Exception:
                pass

        if self.tracker:
            self.tracker.stop()
        self.tracker = be.RoomTracker(rcode, overlay=self.overlay, on_change_callback=_on_p_roster)
        self.tracker.start()

        # Follow log for recording start/stop on Player
        def _player_follow_loop():
            recording = False
            r_start = 0
            curr_task = ""
            self.p_counting_down = False
            self.is_player_recording = False

            def _task_match(a, b):
                if not a or not b:
                    return True
                return str(a).strip().lower() == str(b).strip().lower()

            while self.is_running:
                self.follower.check_new_lines()

                # Đồng bộ task từ orchestrator nếu có
                if self.tracker and self.tracker.latest_data:
                    cur_sess = self.tracker.latest_data.get("current_session")
                    if cur_sess and cur_sess != curr_task and not recording and not self.p_counting_down:
                        curr_task = cur_sess
                        be.set_task_box(curr_task)

                # Player detects armed (Odx countdown 3s)
                if (self.follower.last_armed or self.follower.current_recording) and not recording and not self.p_counting_down:
                    self.p_counting_down = True
                    new_t = self.follower.last_armed or self.follower.current_recording
                    if self.tracker and self.tracker.latest_data and self.tracker.latest_data.get("current_session"):
                        new_t = self.tracker.latest_data.get("current_session")
                    curr_task = new_t
                    be.set_task_box(curr_task)

                    target_sec = be.get_target_duration_for_task(curr_task)
                    m_tg, s_tg = divmod(target_sec, 60)
                    target_str = f"{m_tg:02d}:{s_tg:02d}"

                    if self.tracker:
                        self.tracker.pause_hud()

                    self.log(f"★ MÁY CON: Nhận tín hiệu BẮT ĐẦU TASK [{curr_task}]! Odx đếm ngược 3s... (Mục tiêu: {target_str})", "START")

                    def _player_countdown_worker(t_name, t_sec, t_str):
                        for c in range(3, 0, -1):
                            if not self.is_running or not self.p_counting_down:
                                return
                            self.overlay.show_roster_hud(f"⏳ [ODX ĐẾM NGƯỢC]: {c}s... Chuẩn bị quay [{t_name}] (Mục tiêu: {t_str})", fg_color="#F59E0B")
                            self.lbl_p_status.config(text=f"⏳ [ODX ĐẾM NGƯỢC {c}S]: Chuẩn bị quay [{t_name}] (Mục tiêu: {t_str})...", fg=ModernTheme.ACCENT_AMBER)
                            time.sleep(1.0)

                        if not self.is_running or not self.p_counting_down:
                            return

                        nonlocal recording, r_start
                        self.p_counting_down = False
                        recording = True
                        self.is_player_recording = True
                        r_start = time.time()

                        # Xóa sạch cờ cũ để tránh dừng nhầm
                        self.follower.current_stopped = None
                        self.follower.last_round_result = None
                        self.follower.last_armed = None

                        self.log(f"★ MÁY CON: BẮT ĐẦU TÍNH GIỜ GHI HÌNH CHÍNH THỨC TASK [{t_name}]! (Mục tiêu chuẩn xác: {t_str})", "RECORD")
                        self.lbl_p_status.config(text=f"● ĐANG ĐỒNG BỘ GHI HÌNH: [{t_name}] | Mục tiêu: {t_str}", fg=ModernTheme.ACCENT_GREEN)
                        self.overlay.update_recording(t_name, 0, t_sec, blink=False, is_host=False)

                    threading.Thread(target=_player_countdown_worker, args=(curr_task, target_sec, target_str), daemon=True).start()

                if recording:
                    el = time.time() - r_start
                    target_sec = be.get_target_duration_for_task(curr_task)
                    self.overlay.update_recording(curr_task, el, target_sec, blink=(int(el) % 2 == 0), is_host=False)

                    # Chỉ chấp nhận lệnh dừng khi đã ghi hình ít nhất 3 giây
                    stop_triggered = False
                    if el >= 3:
                        if self.follower.current_stopped:
                            stop_task, _ = self.follower.current_stopped
                            if _task_match(stop_task, curr_task):
                                stop_triggered = True
                        elif self.follower.last_round_result:
                            r_task, _ = self.follower.last_round_result
                            if _task_match(r_task, curr_task):
                                stop_triggered = True
                        elif getattr(self.follower, "last_all_ok", None):
                            if _task_match(self.follower.last_all_ok, curr_task):
                                stop_triggered = True
                        elif el >= 15 and self.tracker and self.tracker.latest_data:
                            # Kiểm tra trạng thái member trong room từ orchestrator
                            my_pid = str(self.config.get("player_id", "")).strip()
                            members = self.tracker.latest_data.get("members", [])
                            for m in members:
                                if str(m.get("player_id", "")).strip() == my_pid:
                                    if m.get("state") == "idle" and self.tracker.latest_data.get("current_session") is None:
                                        stop_triggered = True
                                    break
                        elif el >= (target_sec + 4): # Hard safety timeout
                            stop_triggered = True

                    if stop_triggered:
                        recording = False
                        self.is_player_recording = False
                        self.p_counting_down = False

                        # Xóa sạch trạng thái để triệt tiêu hoàn toàn vòng lặp start-stop
                        self.follower.current_recording = None
                        self.follower.current_stopped = None
                        self.follower.last_round_result = None
                        self.follower.last_armed = None

                        self.overlay.clear()
                        if self.tracker:
                            self.tracker.resume_hud()
                        self.log(f"MÁY CON ĐÃ NHẬN LỆNH DỪNG TASK [{curr_task}]: Kiểm định dữ liệu...", "STOP")

                        # Xử lý hậu kiểm định và tự động Sẵn sàng cho round kế tiếp
                        def _post_round_worker(done_task):
                            time.sleep(2)
                            target_f = getattr(self.follower, "last_kept_path", None)
                            if not target_f or not os.path.exists(target_f):
                                target_f = os.path.join(be.ODX_ROOT, "data", "captures", done_task)
                            if target_f and os.path.exists(target_f):
                                be.validate_and_inspect_capture(target_f, is_room_mode=True)

                            # Chờ tín hiệu ALL OK
                            p_ok = False
                            w_p = time.time()
                            while time.time() - w_p < 8 and self.is_running:
                                self.follower.check_new_lines()
                                all_ok_flag = getattr(self.follower, "last_all_ok", None)
                                if all_ok_flag and _task_match(all_ok_flag, done_task):
                                    p_ok = True
                                    break
                                if self.follower.last_round_result and "all ok" in str(self.follower.last_round_result[1]).lower():
                                    p_ok = True
                                    break
                                time.sleep(0.5)

                            if p_ok:
                                self.log(f"★ [✓ ALL OK 100%] Hệ thống xác nhận cả 4 máy đã hoàn tất task [{done_task}]!", "SUCCESS")
                                self.lbl_p_status.config(text=f"[✓ ALL OK 100%] CẢ 4 MÁY THÀNH CÔNG [{done_task}] - ĐANG CHỜ HOST...", fg=ModernTheme.ACCENT_GREEN)
                                self.overlay.show_roster_hud(f"★ [✓ ALL OK] CẢ 4 MÁY THÀNH CÔNG [{done_task}] ★", fg_color="#10B981")
                            else:
                                self.lbl_p_status.config(text=f"ĐÃ HOÀN TẤT TASK [{done_task}] - ĐANG CHỜ HOST...", fg=ModernTheme.ACCENT_GREEN)

                            # TỰ ĐỘNG BẬT SẴN SÀNG CHO ROUND TIẾP THEO
                            time.sleep(1.5)
                            self.auto_ready_player_next_round()

                        threading.Thread(target=_post_round_worker, args=(curr_task,), daemon=True).start()

                time.sleep(0.2)

        threading.Thread(target=_player_follow_loop, daemon=True).start()

        # Luồng ngầm tự động bật Sẵn sàng liên tục khi ở bước nghỉ / rảnh rỗi trong phòng
        def _player_auto_ready_daemon():
            last_ready_check = 0
            while self.is_running:
                try:
                    if self.current_mode == "PLAYER":
                        if not getattr(self, "is_player_recording", False) and not getattr(self, "p_counting_down", False):
                            rcode = self.ent_p_room.get().strip().upper() if hasattr(self, "ent_p_room") else ""
                            if rcode and be.find_odx_window():
                                now = time.time()
                                should_ready = False
                                if self.tracker and self.tracker.latest_data:
                                    members = self.tracker.latest_data.get("members", [])
                                    my_pid = str(self.config.get("player_id", "")).strip()
                                    for m in members:
                                        if str(m.get("player_id", "")).strip() == my_pid:
                                            if not m.get("ready") and self.tracker.latest_data.get("current_session") is None:
                                                should_ready = True
                                            break
                                    else:
                                        if len(members) > 0 and self.tracker.latest_data.get("current_session") is None:
                                            should_ready = True

                                if should_ready and (now - last_ready_check > 2.5):
                                    last_ready_check = now
                                    self.auto_ready_player_next_round()
                except Exception:
                    pass
                time.sleep(1.2)

        threading.Thread(target=_player_auto_ready_daemon, daemon=True).start()

    def auto_ready_player_next_round(self):
        """Tự động kiểm tra và bật Sẵn sàng cho Máy con ở các round tiếp theo"""
        def _ready_worker():
            rcode = self.ent_p_room.get().strip().upper() if hasattr(self, "ent_p_room") else ""
            if not rcode:
                return
            be.player_ensure_ready_in_room(rcode, on_status=lambda msg, tag: self.log(msg, tag))
        threading.Thread(target=_ready_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # TAB: MANUAL AUDIT
    # ------------------------------------------------------------------
    def browse_audit_folder(self):
        c_dir = os.path.join(be.ODX_ROOT, "data", "captures")
        f = filedialog.askdirectory(initialdir=c_dir if os.path.exists(c_dir) else be.ODX_ROOT, title="Chọn thư mục Capture cần kiểm định")
        if f:
            self.ent_audit_path.delete(0, "end")
            self.ent_audit_path.insert(0, f)

    def run_manual_audit(self):
        p = self.ent_audit_path.get().strip()
        if not p or not os.path.exists(p):
            messagebox.showwarning("Thư mục không tồn tại", "Vui lòng chọn thư mục capture hợp lệ!")
            return
        self.log(f"Bắt đầu kiểm định toàn diện thư mục: [{os.path.basename(p)}]", "AUDIT")
        threading.Thread(target=lambda: be.validate_and_inspect_capture(p, is_room_mode=True), daemon=True).start()

    # ------------------------------------------------------------------
    # GLOBAL HOTKEYS & TIMER UPDATES
    # ------------------------------------------------------------------
    def start_hotkey_listener(self):
        def _hotkey_loop():
            while self.is_running:
                # F10: Start / Stop (VK_F10: 0x79)
                if user32.GetAsyncKeyState(0x79) & 0x8000:
                    while user32.GetAsyncKeyState(0x79) & 0x8000:
                        time.sleep(0.05)
                    time.sleep(0.1)

                    if time.time() < getattr(self, "ignore_f10_until", 0):
                        continue

                    if self.current_mode == "HOST":
                        if not getattr(self, "odx_opened", False):
                            self.log("⚠️ BỊ CHẶN: Bạn phải bấm nút '[🖥 Mở Odx Capture (Tạo Room)]' trên giao diện trước tiên!", "WARN")
                            self.overlay.show_roster_hud("⚠️ HÃY BẤM [MỞ ODX CAPTURE] TRƯỚC TIÊN!", fg_color="#EF4444")
                            continue
                        if self.abort_state == "AWAITING_CHOICE":
                            self.root.after(0, lambda: self.host_choose_option("NEXT"))
                        elif not self.is_recording and not self.is_counting_down and not getattr(self, "is_validating", False):
                            if self.is_in_cooldown:
                                self.log("Đang trong thời gian nghỉ 10 giây giữa các task, vui lòng chờ...", "WAIT")
                            else:
                                # from_hotkey=True ensures tool DOES NOT simulate another duplicate F10!
                                self.root.after(0, lambda: self.host_trigger_start(from_hotkey=True))
                        # Note: Chức năng dừng & lưu bằng F10 đã bị xóa bỏ theo yêu cầu.
                        # Khi đang quay, phím F10 sẽ không dừng để tránh bấm nhầm làm hỏng task.

                # F11: Abort / Chuyển task (VK_F11: 0x7A) - CHỈ DÀNH RIÊNG CHO HOST
                if user32.GetAsyncKeyState(0x7A) & 0x8000:
                    while user32.GetAsyncKeyState(0x7A) & 0x8000:
                        time.sleep(0.02)
                    time.sleep(0.05)
                    if self.current_mode == "HOST":
                        if not getattr(self, "odx_opened", False):
                            self.log("⚠️ BỊ CHẶN: Bạn phải bấm nút '[🖥 Mở Odx Capture (Tạo Room)]' trên giao diện trước tiên!", "WARN")
                            self.overlay.show_roster_hud("⚠️ HÃY BẤM [MỞ ODX CAPTURE] TRƯỚC TIÊN!", fg_color="#EF4444")
                            continue
                        if self.abort_state == "AWAITING_CHOICE":
                            self.root.after(0, lambda: self.host_choose_option("FIX"))
                        else:
                            self.root.after(0, self.host_trigger_abort)

                # F9: HỦY VỀ BAN ĐẦU (VK_F9: 0x78) - CHỈ DÀNH RIÊNG CHO HOST
                if user32.GetAsyncKeyState(0x78) & 0x8000:
                    while user32.GetAsyncKeyState(0x78) & 0x8000:
                        time.sleep(0.02)
                    time.sleep(0.05)
                    if self.current_mode == "HOST":
                        if not getattr(self, "odx_opened", False):
                            self.log("⚠️ BỊ CHẶN: Bạn phải bấm nút '[🖥 Mở Odx Capture (Tạo Room)]' trên giao diện trước tiên!", "WARN")
                            self.overlay.show_roster_hud("⚠️ HÃY BẤM [MỞ ODX CAPTURE] TRƯỚC TIÊN!", fg_color="#EF4444")
                            continue
                        if self.abort_state == "AWAITING_CHOICE":
                            self.root.after(0, lambda: self.host_choose_option("CANCEL"))
                        else:
                            self.root.after(0, self.host_cancel_and_reset)

                # F8: Hủy về ban đầu (VK_F8: 0x77) - CHỈ DÀNH RIÊNG CHO HOST
                if user32.GetAsyncKeyState(0x77) & 0x8000:
                    while user32.GetAsyncKeyState(0x77) & 0x8000:
                        time.sleep(0.02)
                    time.sleep(0.05)
                    if self.current_mode == "HOST":
                        if not getattr(self, "odx_opened", False):
                            self.log("⚠️ BỊ CHẶN: Bạn phải bấm nút '[🖥 Mở Odx Capture (Tạo Room)]' trên giao diện trước tiên!", "WARN")
                            self.overlay.show_roster_hud("⚠️ HÃY BẤM [MỞ ODX CAPTURE] TRƯỚC TIÊN!", fg_color="#EF4444")
                            continue
                        if self.abort_state == "AWAITING_CHOICE":
                            self.root.after(0, lambda: self.host_choose_option("CANCEL"))
                        else:
                            self.root.after(0, self.host_cancel_and_reset)

                # ------------------------------------------------------
                # PHÁT HIỆN BẤM NÚT SƯỜN CHUỘT (VK_XBUTTON1: 0x05, VK_XBUTTON2: 0x06)
                # ------------------------------------------------------
                s1 = bool(user32.GetAsyncKeyState(0x05) & 0x8000)
                s2 = bool(user32.GetAsyncKeyState(0x06) & 0x8000)
                if s1 or s2:
                    now = time.time()
                    if now - getattr(self, "last_side_warn_time", 0) > 1.2:
                        self.last_side_warn_time = now
                        btn_name = "XBUTTON 1 (Sườn Dưới)" if s1 else "XBUTTON 2 (Sườn Trên)"
                        self.root.after(0, lambda b=btn_name: self.on_side_mouse_button_pressed(b))

                time.sleep(0.015)

        t = threading.Thread(target=_hotkey_loop, daemon=True)
        t.start()

    def on_side_mouse_button_pressed(self, btn_name):
        """Xử lý và phát cảnh báo âm thanh + OSD HUD khi phát hiện bấm nút sườn chuột"""
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass

        if self.is_recording:
            self.log(f"🚨 [CẢNH BÁO VI PHẠM ROLE]: Phát hiện bạn vừa bấm NÚT SƯỜN CHUỘT [{btn_name}]! Quy định: CHỈ được dùng Chuột Trái, Chuột Phải và Con lăn.", "WARN")
            self.mouse_warning_until = time.time() + 4.0
            self.mouse_warning_text = f"🚨 VI PHAM ROLE: VUA BAM NUT SUON [{btn_name}]! (CHI DUNG TRAI/PHAI/CON LAN)"
            self.overlay.show_roster_hud(self.mouse_warning_text, fg_color="#EF4444")
            if hasattr(self, "lbl_host_status"):
                self.lbl_host_status.config(
                    text=f"🚨 VI PHẠM ROLE: Vừa bấm nút sườn chuột [{btn_name}]! (Không dùng nút phụ)",
                    fg=ModernTheme.ACCENT_RED
                )
        else:
            self.log(f"⚠️ [TEST CHUỘT]: Đã phát hiện bấm nút sườn chuột [{btn_name}]. Khi quay, nút này BỊ CẤM sử dụng!", "WARN")
            self.overlay.show_roster_hud(f"⚠️ [TEST CHUỘT]: PHAT HIEN NUT SUON [{btn_name}] (CAM DUNG KHI QUAY)", fg_color="#F59E0B")

    def update_live_timer(self):
        # Sync with Odx log if Odx started by physical F10 directly
        self.follower.check_new_lines()

        # Check if Odx rejected recording because task id is still being recorded or saved
        if getattr(self.follower, "task_in_use_error", None):
            err_msg = self.follower.task_in_use_error
            self.follower.task_in_use_error = None
            self.is_recording = False
            self.is_counting_down = False
            self.rec_start_time = 0
            self.btn_host_start.config(state="normal")
            self.btn_host_stop.config(state="disabled")
            self.lbl_timer.config(text="00:00 / 04:00 (±5s)")

            base_id = re.sub(r"-fix\d+$", "", self.active_task_id)
            self.original_task_before_abort = base_id
            self.pending_fix_task = f"{base_id}-fix{self.fix_counter + 1}"
            m = re.search(r'(\d+)$', base_id)
            if m:
                digits = m.group(1)
                next_val = int(digits) + 1
                self.pending_next_task = base_id[:m.start(1)] + str(next_val).zfill(len(digits))
            else:
                self.pending_next_task = f"{base_id}-next"

            self.abort_state = "AWAITING_CHOICE"
            msg_hud = f"⚡ [F11: Fix {self.pending_fix_task}] | [F10: Mới {self.pending_next_task}] | [F9: HỦY VỀ BAN ĐẦU]"
            self.lbl_host_status.config(text=msg_hud, fg=ModernTheme.ACCENT_AMBER)
            self.overlay.show_roster_hud(msg_hud, fg_color="#F59E0B")

            self.log("=" * 65, "WARN")
            self.log(f"⚠️ [ODX TỪ CHỐI DO TRÙNG MÃ TASK]: {err_msg}", "ERROR")
            self.log(f"HÃY CHỌN 1 TRONG 3 THAO TÁC SONG SONG:", "WARN")
            self.log(f"  ★ Bấm [F11]: Quay Task Sửa Lỗi [{self.pending_fix_task}]", "FIX")
            self.log(f"  ★ Bấm [F10]: Quay Task Mới Tiếp Theo [{self.pending_next_task}]", "TASK")
            self.log(f"  ★ Bấm [F9] : HỦY BỎ & VỀ BAN ĐẦU [{base_id}]", "CANCEL")
            self.log("=" * 65, "WARN")

        if self.follower.current_recording and not self.is_recording and self.current_mode == "HOST":
            if not self.is_counting_down:
                self.is_recording = True
                self.rec_start_time = time.time()
                self.active_task_id = self.follower.current_recording
                self.target_duration = be.get_target_duration_for_task(self.active_task_id)
                if self.tracker:
                    self.tracker.pause_hud()
                self.btn_host_start.config(state="disabled")
                self.btn_host_stop.config(state="normal")
                m_tg, s_tg = divmod(self.target_duration, 60)
                self.lbl_host_status.config(text=f"● ĐANG QUAY: [{self.active_task_id}] | Mục tiêu: {m_tg:02d}:{s_tg:02d}", fg=ModernTheme.ACCENT_GREEN)
                self.log(f"Odx Capture đã xác nhận bắt đầu ghi hình task [{self.active_task_id}]! Thời lượng mục tiêu: {m_tg:02d}:{s_tg:02d} ({self.target_duration}s)", "RECORD")

        # Sync with Odx log if Odx stopped by physical F10 / F11 directly in game
        if self.follower.current_stopped and self.is_recording and self.current_mode == "HOST":
            if self.tracker:
                self.tracker.resume_hud()
            stop_task, stop_reason = self.follower.current_stopped
            self.follower.current_stopped = None
            if "cancel" in str(stop_reason).lower():
                self.host_trigger_abort()
            else:
                self.host_trigger_stop(from_hotkey=True)

        # Announce ALL OK if detected in background log
        if getattr(self.follower, "last_all_ok", None):
            ok_t = self.follower.last_all_ok
            if ok_t not in self.announced_all_ok:
                self.announced_all_ok.add(ok_t)
                self.log(f"★ [✓ ALL OK 100%] Hệ thống Odx xác nhận Task [{ok_t}]: ALL OK (Cả 4 máy thành công)!", "SUCCESS")

        if self.is_recording and self.rec_start_time > 0:
            elapsed = time.time() - self.rec_start_time
            m_el, s_el = divmod(int(elapsed), 60)
            rem = max(0, int(self.target_duration - elapsed))
            m_rem, s_rem = divmod(rem, 60)
            m_tg, s_tg = divmod(self.target_duration, 60)
            self.lbl_timer.config(text=f"{m_el:02d}:{s_el:02d} / {m_tg:02d}:{s_tg:02d} | Còn: {m_rem:02d}:{s_rem:02d}")

            # Update HUD
            if time.time() < getattr(self, "mouse_warning_until", 0):
                blink_w = (int(elapsed * 4) % 2 == 0)
                fg_w = "#EF4444" if blink_w else "#FFFFFF"
                self.overlay.show_roster_hud(getattr(self, "mouse_warning_text", "🚨 VI PHAM NUT SUON CHUOT!"), fg_color=fg_w)
            else:
                self.overlay.update_recording(self.active_task_id, elapsed, self.target_duration, blink=(int(elapsed) % 2 == 0), is_host=True)

            # Auto stop when reaching target duration
            if elapsed >= self.target_duration:
                self.host_trigger_stop(from_hotkey=False)
        else:
            if not self.is_counting_down and not self.is_in_cooldown:
                self.lbl_timer.config(text="00:00 / 04:00 (±5s)")

        if self.is_running:
            self.root.after(200, self.update_live_timer)

    # ------------------------------------------------------------------
    # UTILITY ACTIONS & SAFE WINDOW EXIT
    # ------------------------------------------------------------------
    def run_clean_all(self):
        self.log("Đang quét sạch mọi tiến trình Odx ngầm và giải phóng phím F10...", "CLEAN")
        be.cleanup_all_odx(kill_gui=True)
        self.reset_host_to_unopened_state()
        self.log("ĐÃ GIẢI PHÓNG HOÀN TOÀN MỌI TIẾN TRÌNH ODX VÀ PHÍM F10 [OK]!", "CLEAN")
        messagebox.showinfo(
            "Đã dọn sạch",
            "Đã dọn sạch mọi tiến trình Odx ngầm!\n"
            "Nút 'Mở Odx Capture (Tạo Room)' đã đóng lại, mã phòng đã xóa và các nút thao tác đã được khóa an toàn."
        )

    def run_sync_ntp(self):
        ok, msg = be.sync_system_clock()
        self.log(msg, "NTP")
        if ok:
            messagebox.showinfo("NTP", "Đã đồng bộ thời gian Windows với máy chủ NTP thành công!")
        else:
            messagebox.showwarning("NTP", "Không thể đồng bộ tự động. Vui lòng chạy tool với quyền Administrator!")

    def open_captures_folder(self):
        c_dir = os.path.join(be.ODX_ROOT, "data", "captures")
        if not os.path.exists(c_dir):
            os.makedirs(c_dir, exist_ok=True)
        os.startfile(c_dir)

    def run_export_logs(self):
        """Gom toan bo log JSONL, thong tin telemetry va cau hinh thanh 1 file ZIP de gui debug"""
        try:
            pid = self.ent_player_id.get().strip() if hasattr(self, 'ent_player_id') else ""
            pname = self.ent_player_name.get().strip() if hasattr(self, 'ent_player_name') else ""
            zip_path = export_debug_logs(player_id=pid, player_name=pname)
            if zip_path and os.path.exists(zip_path):
                fn = os.path.basename(zip_path)
                self.log(f"[✓ EXPORT LOGS] Đã xuất gói log kiểm định: {fn}", "SUCCESS")
                messagebox.showinfo(
                    "Xuất Gói Log Thành Công",
                    f"Đã đóng gói toàn bộ log hoạt động, số liệu trễ và cấu hình vào file:\n\n{zip_path}\n\nThư mục sẽ được mở tự động để bạn gửi file phân tích."
                )
                try:
                    subprocess.Popen(f'explorer.exe /select,"{zip_path}"')
                except Exception:
                    pass
            else:
                messagebox.showwarning("Cảnh báo", "Không tạo được gói log kiểm định.")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Lỗi khi xuất gói log: {e}")

    def run_check_update(self, silent_if_no_update=False):
        """Kiem tra va tai ban cap nhat truc tuyen tu GitHub"""
        def _on_result(has_update, remote_info, err_msg):
            if err_msg:
                if not silent_if_no_update:
                    self.root.after(0, lambda: messagebox.showwarning("Kiểm tra cập nhật", f"Không thể kiểm tra cập nhật:\n{err_msg}"))
                return

            cur_v = get_current_version_info().get("version", "3.1.0")
            if has_update and remote_info:
                rem_v = remote_info.get("version", "Mới")
                notes = remote_info.get("changelog", "Bản cập nhật tối ưu hiệu năng và sửa lỗi.")
                dl_url = remote_info.get("download_url", "")

                def _ask_user():
                    msg = (
                        f"Phát hiện bản cập nhật mới: v{rem_v} (Bản hiện tại: v{cur_v})\n\n"
                        f"Nội dung thay đổi:\n{notes}\n\n"
                        "Bạn có muốn tải và cài đặt bản cập nhật này ngay không?"
                    )
                    if messagebox.askyesno("🚀 Phát Hiện Bản Cập Nhật Mới", msg):
                        self.log(f"[UPDATE] Đang tải bản cập nhật v{rem_v}...", "INFO")
                        def _prog(p_msg, pct):
                            self.log(f"[UPDATE] {p_msg}", "INFO")
                        ok, err = download_and_apply_update(dl_url, on_progress=_prog)
                        if ok:
                            self.log("[UPDATE] Đang cài đặt và khởi động lại Tool...", "SUCCESS")
                            self.root.after(1200, self.root.destroy)
                        else:
                            messagebox.showerror("Lỗi Cập Nhật", f"Không thể hoàn tất cập nhật:\n{err}")

                self.root.after(0, _ask_user)
            else:
                if not silent_if_no_update:
                    self.root.after(0, lambda: messagebox.showinfo("Cập Nhật", f"Bạn đang sử dụng phiên bản mới nhất (v{cur_v})."))

        check_for_updates_async(_on_result)

    def on_close_window(self):
        """KHI BẤM DẤU [X] ĐỂ TẮT GIAO DIỆN -> KILL SẠCH 100% MỌI TIẾN TRÌNH CON."""
        self.is_running = False
        if self.tracker:
            self.tracker.stop()
        if self.overlay:
            self.overlay.close()
        be.cleanup_all_odx(kill_gui=True)
        self.root.destroy()
        sys.exit(0)

def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    initial_mode = None
    if "--host" in sys.argv:
        initial_mode = "host"
    elif "--player" in sys.argv:
        initial_mode = "player"

    root = tk.Tk()
    app = OdxAutoPilotGUI(root, initial_mode=initial_mode)
    root.mainloop()

if __name__ == "__main__":
    main()

