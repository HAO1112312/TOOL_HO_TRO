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
import tempfile
import ctypes
from ctypes import wintypes
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

# Import Backend Engine
import odx_backend as be
from odx_overlay import OdxOverlay
from odx_sync import HostSettingsServer, PlayerSettingsClient
from odx_window import WindowPreferences
from odx_hotkeys import replace_keyboard_hook
from odx_timing import format_elapsed, format_recording_clock

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
        self.app_version = get_current_version_info().get("version", "3.1.3")
        self.root.title(f"ODX CAPTURE AUTO-PILOT STUDIO v{self.app_version}")
        self.root.geometry("1040x840")
        self.root.configure(bg=ModernTheme.BG_DARK)

        # State Variables
        self.current_mode = "HOST" if initial_mode == "host" else ("PLAYER" if initial_mode == "player" else "HOST")
        self.view_mode = self.current_mode
        self.is_recording = False
        self.is_counting_down = False
        self.rec_start_time = 0
        self._scheduled_start_at = None
        self._scheduled_start_task = ""
        self._stop_elapsed = None
        self._prepared_host_stop = None
        self._prepared_host_stop_key = None
        self._prepared_host_stop_attempted = False
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
        self.is_host_opening = False
        self._host_launch_generation = 0
        self._host_room_cancel_event = None
        self._host_process = None
        self.is_stopping = False
        self.is_updating = False
        self.is_player_recording = False
        self.p_counting_down = False
        self.task_id_override = None
        self._transition_token = 0
        self._player_connection_generation = 0
        self.player_connection_active = False
        self.is_connecting = False
        self._connection_operation_token = 0
        self._connection_cancel_event = None
        self._ready_inflight = False
        self._ready_operation_token = 0
        self._ready_cancel_event = None
        self._player_round_token = 0
        self._player_validation_token = None
        self._player_validation_failed = False
        self._player_failed_audit = None
        self._player_ready_after_round = None
        self.ui_queue = queue.Queue()
        self._ui_thread_id = threading.get_ident()
        self.hotkeys_ready = False
        self._keyboard_hook = None
        self._hook_thread_id = None
        self._hotkey_precedence_required = True
        self._hotkey_owner_identity = None
        self._hotkey_refresh_target = None
        self._hotkey_refresh_generation = 0
        self._hotkey_refresh_retry_at = 0
        self._closing = False
        self._action_lock = threading.RLock()
        self._start_command_sent = False
        self._stop_confirmation_failed = False
        self.capture_state_unknown = False
        self._pending_stop_action = None
        self._pending_stop_task = ""
        self._completed_validation_tokens = set()
        self._sync_required = True
        self._sync_server = None
        self._sync_client = None
        self._sync_key = None
        self._sync_polling = False
        self._shared_settings = {}
        self._shared_targets = {}
        self._round_start_epoch = None
        self._pending_sync_start_task = None
        self._sync_start_after = None
        self._sync_start_task = None
        self._sync_start_nonce = None
        self._room_test_mode = None
        self._round_test_mode = False

        # Backend Components
        self.overlay = OdxOverlay(self.root)
        self.follower = be.LiveLogFollower()
        self.tracker = None
        self.player_proc = None

        # Load Saved Config & Progress
        self.cfg_file = os.path.join(be.TOOL_DIR, "autopilot_config.json")
        self.prog_file = os.path.join(be.TOOL_DIR, "autopilot_progress.json")
        self.config = self.load_config()
        self.task_id_override = self.config.get("task_id_override") or None
        self.progress = self.load_progress()

        self.batch_list = list(self.config.get("batches", ["batch1-ASRM", "batch2-ASRM", "asm-s221-", "AMRF-156-"]))
        self.current_batch = self.config.get("current_batch", self.batch_list[0] if self.batch_list else "batch1-ASRM")
        self.sub_task = str(self.config.get("sub_task", "01"))

        # Build UI
        self.create_widgets()
        self.restore_progress_task()
        self.window_preferences = WindowPreferences(self.root, os.path.join(be.TOOL_DIR, "autopilot_ui.json"))
        self.window_preferences.restore()

        # Start Background Listeners & Timers
        self.start_hotkey_listener()
        self.start_system_health_checks()
        self.poll_log_queue()
        self.update_live_timer()
        self.root.after(200, self.sync_settings_tick)

        # Check for online updates in background (silent if no update)
        self.root.after(3500, lambda: self.run_check_update(silent_if_no_update=True))
        self.root.after(500, self.show_update_status)

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
            "auto_increment": True,
            "test_mode": False
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
            except Exception as exc:
                self.log(f"Không đọc được cấu hình; đang dùng mặc định: {exc}", "ERROR")
        return default_cfg

    def save_config(self):
        if self.operation_busy():
            return False
        temporary_path = None
        try:
            fd, temporary_path = tempfile.mkstemp(prefix=".autopilot-", suffix=".tmp", dir=be.TOOL_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, self.cfg_file)
            return True
        except Exception as exc:
            self.log(f"Không lưu được cấu hình: {exc}", "ERROR")
            return False
        finally:
            if temporary_path and os.path.exists(temporary_path):
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def save_current_task_config(self, event=None):
        if self.operation_busy():
            return
        self.config["batches"] = self.batch_list
        self.config["task_id_override"] = self.task_id_override
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

    def load_progress(self):
        try:
            if not os.path.isfile(self.prog_file):
                return {"schema": 1, "batches": {}}
            with open(self.prog_file, "r", encoding="utf-8") as stream:
                progress = json.load(stream)
            if not isinstance(progress.get("batches"), dict):
                raise ValueError("Nhật ký tiến độ không có danh sách batch hợp lệ")
            for state in progress["batches"].values():
                completed = state.get("completed", [])
                if not isinstance(completed, list) or any(not self.valid_task_id(task) for task in completed):
                    raise ValueError("Mã task trong nhật ký tiến độ không hợp lệ")
                state["completed"] = list(dict.fromkeys(re.sub(r"-fix\d+$", "", task) for task in completed))
            return progress
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            self.log(f"Không đọc được nhật ký tiến độ: {exc}", "ERROR")
            return {"schema": 1, "batches": {}}

    def batch_progress(self):
        batch = self.combo_batch.get().strip()
        return self.progress.get("batches", {}).get(batch, {})

    def configured_task_total(self):
        try:
            total = int(self.ent_total.get().strip())
            return total if total > 0 else None
        except (TypeError, ValueError):
            return None

    def batch_complete(self):
        if self.capture_test_mode():
            return False
        total = self.configured_task_total()
        return total is not None and len(self.batch_progress().get("completed", [])) >= total

    def completion_text(self):
        return f"BATCH HOÀN TẤT {len(self.batch_progress().get('completed', []))}/{self.configured_task_total()} TASK. Tăng Tổng hoặc chọn batch khác để tiếp tục."

    def record_task_completion(self, task_id):
        """Commit the completion and resumable next ID before updating the UI/config."""
        base = re.sub(r"-fix\d+$", "", task_id)
        batch = self.combo_batch.get().strip()
        progress = json.loads(json.dumps(self.progress))
        state = progress.setdefault("batches", {}).setdefault(batch, {"completed": []})
        if base in state["completed"]:
            return "duplicate", state.get("next_task")
        match = re.search(r"(\d+)$", base)
        next_task = (base[:match.start(1)] + str(int(match.group(1)) + 1).zfill(len(match.group(1)))
                     if match else base + "-next")
        state["completed"].append(base)
        state["last_completed"] = base
        state["suggested_next_task"] = next_task
        state["next_task"] = next_task if self.var_autoincrement.get() else None
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix=".progress-", suffix=".tmp", dir=be.TOOL_DIR)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(progress, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.prog_file)
            self.progress = progress
            return "saved", state["next_task"]
        except OSError as exc:
            self.log(f"Không lưu được tiến độ; giữ nguyên task để tránh bỏ sót: {exc}", "ERROR")
            return "failed", None
        finally:
            if temporary and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def restore_progress_task(self):
        if self.capture_test_mode():
            return
        state = self.batch_progress()
        if re.sub(r"-fix\d+$", "", self.active_task_id) in state.get("completed", []):
            next_task = state.get("next_task")
            if next_task and self.valid_task_id(next_task) and self.var_autoincrement.get():
                self.select_task_id(next_task)
                self.log(f"Khôi phục tiến độ đã lưu. Task tiếp: [{next_task}].", "PROGRESS")
            else:
                self.log(f"Task hiện tại đã hoàn tất. Task đề xuất: [{state.get('suggested_next_task', '')}].", "PROGRESS")
        if self.batch_complete():
            self.lbl_host_status.config(text=self.completion_text(), fg=ModernTheme.ACCENT_GREEN)

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
            h_left, text=f"Hệ thống Điều khiển & Đồng bộ 4 máy (v{self.app_version})",
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
            bottom_bar, text="↻ Làm mới kết nối", font=("Segoe UI", 9, "bold"),
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

        # Reserve the control panel before giving the remaining height to logs.
        log_card.pack_forget()
        log_card.pack(side="bottom", fill="both", expand=True, padx=10, pady=4)

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
        self.ent_full_task.pack(side="left", fill="x", expand=True, padx=3)
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

        self.lbl_task_display = tk.Label(card_task, text="", font=("Consolas", 9, "bold"),
                                          fg=ModernTheme.ACCENT_GREEN, bg=ModernTheme.BG_CARD,
                                          anchor="w", justify="left", wraplength=700)
        self.lbl_task_display.pack(fill="x", padx=12, pady=(0, 6))
        self.wrap_label_to_width(self.lbl_task_display)

        # Initial preview
        self.update_task_id_preview()

        # 3. Action Buttons & Big Status Display
        card_action = tk.Frame(self.tab_host, bg=ModernTheme.BG_CARD)
        card_action.pack(fill="x", pady=4)

        act_top = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        act_top.pack(fill="x", padx=12, pady=8)
        act_top.columnconfigure(0, weight=1)

        self.lbl_host_status = tk.Label(
            act_top, text="⚠️ BƯỚC 1: HÃY BẤM NÚT [MỞ ODX CAPTURE (TẠO ROOM)] TRƯỚC TIÊN!",
            font=("Segoe UI", 11, "bold"), fg=ModernTheme.ACCENT_AMBER, bg=ModernTheme.BG_CARD,
            anchor="w", justify="left", width=1, wraplength=650
        )
        self.lbl_host_status.grid(row=0, column=0, sticky="ew")
        self.wrap_label_to_width(self.lbl_host_status)

        self.lbl_timer = tk.Label(
            act_top, text="00:00 / 04:00 (±5s)",
            font=("Consolas", 12, "bold"), fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD
        )
        self.lbl_timer.grid(row=0, column=1, sticky="ne", padx=(12, 0))

        act_btns = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        act_btns.pack(fill="x", padx=12, pady=(0, 10))

        self.btn_host_start = tk.Button(
            act_btns, text="▶ BẮT ĐẦU QUAY (F10)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_GREEN, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_GREEN_HOVER,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=lambda: self.handle_host_hotkey(0x79)
        )
        self.btn_host_start.pack(side="left", expand=True, fill="x", padx=3)

        self.btn_host_emergency = tk.Button(
            act_btns, text="⚠ DỪNG KHẨN CẤP (F10)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_RED, fg="#FFFFFF", activebackground=ModernTheme.ACCENT_RED_HOVER,
            relief="flat", cursor="hand2", padx=8, pady=8, state="disabled", command=self.host_trigger_emergency_stop
        )
        self.btn_host_emergency.pack(side="left", expand=True, fill="x", padx=3)

        params = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        params.pack(fill="x", padx=15, pady=(0, 6))
        for name, label, key, default in (
            ("ent_round_seconds", "Thời lượng (s)", "round_duration_seconds", 240),
            ("ent_random_spread", "Random ± (s)", "random_offset_seconds", 5),
            ("ent_cooldown_seconds", "Nghỉ (s)", "cooldown_between_rounds_seconds", 12),
            ("ent_target_players", "Số máy", "target_players", 4),
        ):
            tk.Label(params, text=label, bg=ModernTheme.BG_CARD, fg=ModernTheme.TEXT_MUTED,
                     font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
            entry = tk.Entry(params, width=5, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
            entry.insert(0, str(self.config.get(key, default)))
            entry.pack(side="left", padx=(0, 10))
            entry.bind("<FocusOut>", lambda event: self.save_shared_controls())
            setattr(self, name, entry)
        self.var_test_mode = tk.BooleanVar(self.root, value=self.capture_test_mode())
        self.check_test_mode = tk.Checkbutton(
            card_action, text="TEST đồng bộ: quay toàn màn hình, không cần game", variable=self.var_test_mode,
            command=self.on_test_mode_changed, bg=ModernTheme.BG_CARD, fg=ModernTheme.ACCENT_AMBER,
            selectcolor=ModernTheme.BG_CARD_LIGHT, activebackground=ModernTheme.BG_CARD)
        self.check_test_mode.pack(anchor="w", padx=15, pady=(0, 4))
        self.lbl_host_sync = tk.Label(card_action, text="Đồng bộ: chờ tạo room", anchor="w",
                                      bg=ModernTheme.BG_CARD, fg=ModernTheme.TEXT_MUTED, font=("Segoe UI", 9))
        self.lbl_host_sync.pack(fill="x", padx=15, pady=(0, 6))
        self.wrap_label_to_width(self.lbl_host_sync)
        hud_row = tk.Frame(card_action, bg=ModernTheme.BG_CARD)
        hud_row.pack(fill="x", padx=15, pady=(0, 6))
        for name, label, key in (
            ("check_hud_enabled", "HUD", "hud_enabled"),
            ("check_hud_timer", "Thời gian", "hud_show_timer"),
            ("check_hud_task", "Mã task", "hud_show_task"),
            ("check_hud_roster", "Danh sách phòng", "hud_show_roster"),
        ):
            variable = tk.BooleanVar(self.root, value=bool(self.config.get(key, True)))
            setattr(self, "var_" + key, variable)
            checkbox = tk.Checkbutton(hud_row, text=label, variable=variable, command=self.save_shared_controls,
                                      bg=ModernTheme.BG_CARD, fg=ModernTheme.TEXT_MAIN,
                                      selectcolor=ModernTheme.BG_CARD_LIGHT, activebackground=ModernTheme.BG_CARD)
            checkbox.pack(side="left", padx=(0, 12))
            setattr(self, name, checkbox)

        self.frame_host_choices = tk.Frame(card_action, bg=ModernTheme.BG_CARD_LIGHT)
        tk.Label(
            self.frame_host_choices, text="CHỌN CÁCH QUAY TIẾP", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.ACCENT_AMBER
        ).pack(anchor="w", padx=8, pady=(6, 3))
        choice_row = tk.Frame(self.frame_host_choices, bg=ModernTheme.BG_CARD_LIGHT)
        choice_row.pack(fill="x", padx=5, pady=(0, 6))
        for label, choice, color in (
            ("CHỌN TASK -FIX (F11)", "FIX", ModernTheme.ACCENT_AMBER),
            ("CHỌN TASK TIẾP", "NEXT", ModernTheme.ACCENT_BLUE),
            ("CHỌN TASK GỐC (F9)", "CANCEL", ModernTheme.ACCENT_GREEN),
        ):
            tk.Button(
                choice_row, text=label, font=("Segoe UI", 9, "bold"),
                bg=color, fg=ModernTheme.TEXT_MAIN, relief="flat", padx=8, pady=7,
                command=lambda value=choice: self.host_choose_option(value)
            ).pack(side="left", fill="x", expand=True, padx=3)

        tk.Label(
            card_action, text="F11: Chọn mã task -fix   |   F10: Bắt đầu / Dừng quay",
            font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD
        ).pack(anchor="w", padx=15, pady=(0, 8))

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
        self.lbl_p_status.pack(fill="x", anchor="w")
        self.wrap_label_to_width(self.lbl_p_status)

        self.lbl_p_timer = tk.Label(
            b_frame, text="CHỜ HOST", font=("Consolas", 12, "bold"),
            fg=ModernTheme.TEXT_MAIN, bg=ModernTheme.BG_CARD
        )
        self.lbl_p_timer.pack(fill="x", anchor="w", pady=(4, 0))
        self.wrap_label_to_width(self.lbl_p_timer)
        sync_row = tk.Frame(b_frame, bg=ModernTheme.BG_CARD)
        sync_row.pack(fill="x", pady=(4, 0))
        tk.Label(sync_row, text="IP Host (để trống để tự tìm):", bg=ModernTheme.BG_CARD,
                 fg=ModernTheme.TEXT_MUTED, font=("Segoe UI", 9)).pack(side="left")
        self.ent_sync_host = tk.Entry(sync_row, width=24, bg=ModernTheme.BG_CARD_LIGHT, fg=ModernTheme.TEXT_MAIN, relief="flat")
        self.ent_sync_host.insert(0, self.config.get("sync_host_address", ""))
        self.ent_sync_host.pack(side="left", padx=5)
        self.lbl_player_sync = tk.Label(b_frame, text="Đồng bộ: chờ Host", bg=ModernTheme.BG_CARD,
                                        fg=ModernTheme.ACCENT_AMBER, font=("Segoe UI", 9))
        self.lbl_player_sync.pack(fill="x", anchor="w", pady=(3, 0))
        self.wrap_label_to_width(self.lbl_player_sync)

        self.lbl_p_sub = tk.Label(
            b_frame, text="* Tool sẽ tự động mở giao diện Odx Capture, kết nối vào phòng và tự động Auto-Ready 100%.",
            font=("Segoe UI", 9), fg=ModernTheme.TEXT_MUTED, bg=ModernTheme.BG_CARD
        )
        self.lbl_p_sub.pack(fill="x", anchor="w", pady=(4, 0))
        self.wrap_label_to_width(self.lbl_p_sub)

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
            a_top, text="🔍 KIỂM ĐỊNH (CHỈ ĐỌC)", font=("Segoe UI", 10, "bold"),
            bg=ModernTheme.ACCENT_BLUE, fg="#FFFFFF", relief="flat", cursor="hand2", padx=14, pady=4,
            command=self.run_manual_audit
        )
        btn_run_val.pack(side="right")

    # ==================================================================
    # NAVIGATION & MODE SWITCHING
    # ==================================================================
    def switch_mode(self, mode):
        if mode != self.view_mode and self.operation_busy():
            self.log("Hãy chờ quay/lưu dữ liệu hoàn tất trước khi chuyển chế độ.", "WARN")
            return
        changing_role = mode in ("HOST", "PLAYER") and mode != self.current_mode
        if changing_role and (self.odx_opened or self.player_connection_active):
            messagebox.showwarning("Phiên Odx đang mở", "Hãy đóng tool rồi mở lại với vai trò mới. Odx được giữ nguyên khi làm mới kết nối hoặc có lỗi.")
            return
        if changing_role and self.current_mode == "PLAYER":
            self.cancel_player_ready(cancel_connection=True)
            self._player_connection_generation += 1
            self.player_connection_active = False
            if self.tracker:
                self.tracker.stop()
                self.tracker = None
        if mode in ("HOST", "PLAYER"):
            self.current_mode = mode
        self.view_mode = mode
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
        while not self.ui_queue.empty():
            callback, args, kwargs = self.ui_queue.get_nowait()
            try:
                callback(*args, **kwargs)
            except Exception as exc:
                self.log(f"Lỗi cập nhật giao diện: {exc}", "ERROR")
            if not self.is_running:
                return
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.txt_log.insert("end", line + "\n")
            self.txt_log.see("end")
        if self.is_running:
            self.root.after(100, self.poll_log_queue)

    def post_ui(self, callback, *args, **kwargs):
        """Worker threads enqueue UI work; only the Tk thread executes it."""
        if self.is_running:
            if threading.get_ident() == self._ui_thread_id:
                callback(*args, **kwargs)
            else:
                self.ui_queue.put((callback, args, kwargs))

    @staticmethod
    def wrap_label_to_width(label):
        label.configure(anchor="w", justify="left", wraplength=650)
        def resized(event):
            if event.width > 1:
                width = max(1, event.width - 4)
                if int(float(label.cget("wraplength"))) != width:
                    label.configure(wraplength=width)
        label.bind("<Configure>", resized, add="+")

    def operation_busy(self):
        return any(getattr(self, name, False) for name in (
            "is_recording", "is_counting_down", "is_stopping", "is_validating",
            "is_updating", "is_player_recording", "p_counting_down", "capture_state_unknown",
            "is_connecting", "_ready_inflight", "is_host_opening"))

    def set_task_controls_enabled(self, enabled):
        for name in ("combo_batch", "ent_subtask", "ent_start_num", "ent_total",
                     "ent_full_task", "btn_inject_task", "btn_auto_fix", "ent_round_seconds",
                     "ent_random_spread", "ent_cooldown_seconds", "ent_target_players", "check_hud_enabled",
                     "check_hud_timer", "check_hud_task", "check_hud_roster"):
            widget = getattr(self, name, None)
            if widget:
                widget.config(state="normal" if enabled else "disabled")

    def hide_host_choices(self):
        panel = getattr(self, "frame_host_choices", None)
        if panel is not None:
            panel.pack_forget()

    def round_target_duration(self, task_id):
        shared = getattr(self, "_shared_settings", {})
        if self.current_mode == "PLAYER" and task_id in getattr(self, "_shared_targets", {}):
            return self._shared_targets[task_id]
        if self.current_mode == "PLAYER" and shared.get("task_id") == task_id and shared.get("target_duration"):
            return int(shared["target_duration"])
        import hashlib
        try:
            base = max(1, min(86400, int(self.config.get("round_duration_seconds", 240))))
            spread = max(0, min(base - 1, 300, int(self.config.get("random_offset_seconds", 5))))
        except (ValueError, TypeError):
            base, spread = 240, 5
        if not self.config.get("random_enabled", True) or not spread:
            return base
        clean = re.sub(r"[^a-zA-Z0-9_-]", "", str(task_id)).lower()
        value = int(hashlib.md5(clean.encode("utf-8")).hexdigest()[:8], 16)
        return base + value % (2 * spread + 1) - spread

    def save_shared_controls(self):
        if self.operation_busy():
            return
        try:
            values = self.read_shared_controls()
            self.config.update(values)
            self.save_config()
        except ValueError as exc:
            self.log(str(exc), "WARN")

    def read_shared_controls(self):
        values = {}
        for widget_name, key, default, minimum, maximum in (
            ("ent_round_seconds", "round_duration_seconds", 240, 1, 3600),
            ("ent_random_spread", "random_offset_seconds", 5, 0, 60),
            ("ent_cooldown_seconds", "cooldown_between_rounds_seconds", 12, 0, 600),
            ("ent_target_players", "target_players", 4, 1, 4),
        ):
            widget = getattr(self, widget_name, None)
            value = int(widget.get() if widget is not None else self.config.get(key, default))
            if not minimum <= value <= maximum:
                raise ValueError(f"{key}: nhập số từ {minimum} đến {maximum}.")
            values[key] = value
        if values["random_offset_seconds"] >= values["round_duration_seconds"]:
            raise ValueError("Khoảng random phải nhỏ hơn thời lượng quay.")
        for field in ("hud_enabled", "hud_show_timer", "hud_show_task", "hud_show_roster"):
            variable = getattr(self, "var_" + field, None)
            values[field] = bool(variable.get()) if variable is not None else bool(self.config.get(field, True))
        values["test_mode"] = self.capture_test_mode()
        return values

    def capture_test_mode(self):
        return self.config.get("test_mode", False) is True

    def on_test_mode_changed(self):
        if self.operation_busy() or self.room_code or self.player_connection_active:
            self.var_test_mode.set(self.capture_test_mode())
            self.log("Hãy đóng tool rồi mở lại để đổi chế độ TEST trước khi tạo room mới.", "WARN")
            return
        self.config["test_mode"] = bool(self.var_test_mode.get())
        self.overlay.set_preferences(self.config)
        self.save_config()
        self.update_task_id_preview()
        self.log("Đã bật TEST: quay màn hình chính, không cần game; không tính tiến độ sản xuất."
                 if self.capture_test_mode() else "Đã tắt TEST; quay game theo cấu hình thông thường.", "MODE")

    def configure_capture_mode_for_room(self, enabled, current):
        """Configure the owned native UI before its room snapshots the source."""
        mode = self.current_mode

        def on_status(message, tag="WAIT"):
            def display():
                if not current():
                    return
                label = self.lbl_host_status if mode == "HOST" else self.lbl_p_status
                label.config(text=message, fg=ModernTheme.ACCENT_AMBER)
                self.log(message, tag)
            if current():
                self.post_ui(display)

        on_status("ĐANG CHỜ ODX KHỞI TẠO GIAO DIỆN VÀ NGUỒN QUAY...")
        deadline = time.monotonic() + 15
        while current() and not self.owned_odx_window() and time.monotonic() < deadline:
            time.sleep(.1)
        if not current():
            raise RuntimeError("Phiên kết nối đã bị hủy.")
        if not self.owned_odx_window():
            raise RuntimeError("Chưa tìm thấy cửa sổ Odx do tool quản lý để chọn nguồn quay.")
        cancel_event = threading.Event()
        try:
            worker = be.set_capture_test_mode(enabled, timeout=30, cancel_event=cancel_event, on_status=on_status)
            deadline = time.monotonic() + 32
            if worker:
                worker.join(timeout=.1)
                while worker.is_alive():
                    if not current():
                        raise RuntimeError("Phiên kết nối đã bị hủy.")
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Hết thời gian chờ Odx khởi tạo nguồn quay; chưa tạo hoặc vào room.")
                    worker.join(timeout=.1)
            if not current():
                raise RuntimeError("Phiên kết nối đã bị hủy.")
            if not worker or not getattr(worker, "success", False):
                raise RuntimeError(getattr(worker, "error", "") or "Không xác nhận được nguồn quay màn hình trên Odx.")
        finally:
            cancel_event.set()

    def room_capture_mode_matches(self):
        configured = getattr(self, "_room_test_mode", None)
        return configured == self.capture_test_mode() if configured is not None else not self.capture_test_mode()

    def expected_players(self):
        return max(1, min(4, int(self.config.get("target_players", 4))))

    def refresh_tracker_settings(self):
        if self.tracker and getattr(self.tracker, "expected_players", None) != self.expected_players():
            self.tracker.expected_players = self.expected_players()
            self.tracker._force_hud_update = True

    def shared_phase(self):
        if self.capture_state_unknown:
            return "error"
        for flag, phase in (("is_stopping", "stopping"), ("is_validating", "validating"),
                            ("is_recording", "recording"), ("is_counting_down", "countdown"),
                            ("is_in_cooldown", "cooldown")):
            if getattr(self, flag, False):
                return phase
        if self._pending_sync_start_task:
            return "preparing"
        return "choices" if self.abort_state else "idle"

    def host_settings_snapshot(self):
        self.config.update(self.read_shared_controls())
        task = self.active_task_id
        return dict(
            **{key: self.config[key] for key in ("round_duration_seconds", "random_offset_seconds",
                "cooldown_between_rounds_seconds", "target_players")},
            random_enabled=bool(self.config.get("random_enabled", True)), random_algorithm="task-md5-v1",
            test_mode=self.capture_test_mode(),
            task_id=task, target_duration=self.round_target_duration(task), current_batch=self.combo_batch.get(),
            sub_task=self.ent_subtask.get(), start_num=int(self.ent_start_num.get()),
            total_tasks=int(self.ent_total.get()), auto_increment=bool(self.var_autoincrement.get()),
            phase=self.shared_phase(), round_start_epoch=self._round_start_epoch,
            start_request_id=getattr(self, "_sync_start_nonce", None),
            base_task_id=self.original_task_before_abort or task,
            pending_fix_task=self.pending_fix_task, pending_next_task=self.pending_next_task,
            app_version=self.app_version,
            **{key: self.config.get(key, default) for key, default in (
                ("hud_enabled", True), ("hud_show_timer", True), ("hud_show_task", True),
                ("hud_show_roster", True), ("hud_scale", 1.0), ("hud_opacity", .95), ("hud_language", "vi"))})

    def stop_settings_sync(self):
        server = getattr(self, "_sync_server", None)
        if server is not None:
            server.stop()
        self._sync_server = self._sync_client = None
        self._sync_key = None
        self._sync_polling = False
        self._shared_settings = {}
        self._shared_targets = {}
        self._sync_applied_revision = None
        self._sync_applied_instance = None

    def sync_settings_tick(self):
        if not self.is_running:
            return
        try:
            data = self.tracker.latest_data if self.tracker else None
            host_id = str(data.get("host") or "") if data else ""
            room = (self.room_code if self.current_mode == "HOST" else
                    getattr(self.tracker, "room_code", ""))
            address = self.ent_sync_host.get().strip() if self.current_mode == "PLAYER" else ""
            key = (self.current_mode, room, host_id, address)
            if room and host_id and self.valid_room_code(room):
                if key != self._sync_key:
                    self.stop_settings_sync()
                    if self.current_mode == "HOST":
                        self._sync_server = HostSettingsServer(room, host_id).start()
                    else:
                        self._sync_client = PlayerSettingsClient(room, host_id, host_address=address or None)
                        self.config["sync_host_address"] = address
                    self._sync_key = key
                if self.current_mode == "HOST":
                    revision = self._sync_server.publish(self.host_settings_snapshot())
                    self.refresh_tracker_settings()
                    self.overlay.set_preferences(self.config)
                    prefix = "[TEST màn hình] " if self.capture_test_mode() else ""
                    self.lbl_host_sync.config(text=f"{prefix}Đồng bộ Host: bản {revision} · cổng {self._sync_server.port}", fg=ModernTheme.ACCENT_GREEN)
                    pending = self._pending_sync_start_task
                    if pending and pending == self.active_task_id and not self.operation_busy():
                        members = data.get("members", [])
                        players = {str(m.get("player_id")) for m in members if str(m.get("player_id")) != host_id}
                        if (self.tracker.is_all_ready(expected=self.expected_players())
                                and players <= self._sync_server.applied_players(
                                    revision, after=getattr(self, "_sync_start_after", None))):
                            self.host_trigger_start()
                elif not self._sync_polling:
                    self._sync_polling = True
                    client, generation = self._sync_client, self._player_connection_generation
                    def fetch_settings():
                        envelope = client.fetch()
                        self.post_ui(self.finish_settings_fetch, key, generation, client, envelope)
                    threading.Thread(target=fetch_settings, daemon=True).start()
            elif self._sync_key is not None:
                self.stop_settings_sync()
        except (OSError, ValueError, TypeError) as exc:
            label = self.lbl_host_sync if self.current_mode == "HOST" else self.lbl_player_sync
            label.config(text=f"Chưa đồng bộ: {exc}", fg=ModernTheme.ACCENT_AMBER)
        self.root.after(1000, self.sync_settings_tick)

    def finish_settings_fetch(self, key, generation, client, envelope):
        if (key != self._sync_key or generation != self._player_connection_generation
                or self.current_mode != "PLAYER" or client is not self._sync_client):
            return
        self._sync_polling = False
        if client.last_error:
            self.lbl_player_sync.config(text="Chưa đồng bộ Host: kiểm tra IP Host và kết nối mạng.", fg=ModernTheme.ACCENT_AMBER)
            return
        if envelope is not None:
            settings = dict(envelope["settings"])
            if settings.get("test_mode", False) != bool(getattr(self, "_room_test_mode", False)):
                self._sync_applied_revision = self._sync_applied_instance = None
                self.lbl_player_sync.config(text="Chế độ quay khác Host; hãy đóng tool rồi mở lại để kết nối đúng chế độ.", fg=ModernTheme.ACCENT_RED)
                return
            self._shared_settings = settings
            task = settings.get("task_id")
            if task and settings.get("target_duration"):
                self._shared_targets[task] = int(settings["target_duration"])
                if len(self._shared_targets) > 128:
                    self._shared_targets.pop(next(iter(self._shared_targets)))
            for field in ("test_mode", "round_duration_seconds", "random_offset_seconds", "random_enabled", "target_players",
                          "cooldown_between_rounds_seconds", "hud_enabled", "hud_show_timer", "hud_show_task",
                          "hud_show_roster", "hud_scale", "hud_opacity", "hud_language"):
                if field in settings:
                    self.config[field] = settings[field]
            self.overlay.set_preferences(settings)
            self.refresh_tracker_settings()
            self._sync_applied_revision = envelope["revision"]
            self._sync_applied_instance = envelope["instance_id"]
            if not (self.is_player_recording or self.p_counting_down or self.is_validating):
                phase = settings.get("phase", "idle")
                duration = settings.get("target_duration", 240)
                self.lbl_p_timer.config(text=f"{task or 'CHỜ TASK'} · {duration}s · Host: {phase}")
                if phase == "choices":
                    self.lbl_p_status.config(text="HOST ĐANG CHỌN: -FIX / TASK TIẾP / QUAY LẠI BAN ĐẦU", fg=ModernTheme.ACCENT_AMBER)
        revision = getattr(self, "_sync_applied_revision", None)
        shared = self._shared_settings
        task = shared.get("task_id")
        active_local = task and (self.follower.current_recording == task or self.follower.last_armed == task)
        if task and shared.get("phase", "idle") in ("idle", "preparing", "countdown") and not active_local:
            exists, path = be.is_task_already_recorded(task)
            if exists:
                self.lbl_player_sync.config(text=f"CHẶN TASK [{task}]: đã có trên Player hoặc chưa kiểm tra được thư mục.", fg=ModernTheme.ACCENT_RED)
                # Completed-round readiness is separate from permission to
                # acknowledge/start this exact folder again.
                if envelope is not None:
                    self.root.after(0, self.auto_ready_player_next_round)
                return
        if revision is not None:
            player_id = str(self.config.get("player_id", ""))
            threading.Thread(target=client.acknowledge, args=(revision, player_id), daemon=True).start()
        prefix = "[TEST màn hình] " if self.capture_test_mode() else ""
        self.lbl_player_sync.config(text=prefix + "Đã đồng bộ cấu hình và thời lượng từ Host", fg=ModernTheme.ACCENT_GREEN)
        if envelope is not None:
            self.root.after(0, self.auto_ready_player_next_round)

    def synced_settings_ready(self, *, allow_finished_task=False):
        if not getattr(self, "_sync_required", False):
            return True
        client = self._sync_client
        fresh = bool(client and client.last_seen_at and time.monotonic() - client.last_seen_at < 6
                    and not client.last_error and self._shared_settings
                    and self._sync_applied_revision == client.current_revision
                    and self._sync_applied_instance == client.instance_id)
        task = self._shared_settings.get("task_id")
        mode_matches = (self._shared_settings.get("test_mode", False)
                        == bool(getattr(self, "_room_test_mode", False)))
        if not (fresh and mode_matches and task):
            return False
        if not be.is_task_already_recorded(task)[0]:
            return True
        return (allow_finished_task and getattr(self, "_player_ready_after_round", None)
                == (self._player_connection_generation, self._player_round_token, task))

    @staticmethod
    def valid_task_id(task_id):
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(task_id)))

    def owned_odx_window(self):
        if be.find_unmanaged_odx_window():
            return None
        hwnd = be.find_odx_window()
        return hwnd if hwnd and be.is_managed_odx_window(hwnd) else None

    def odx_gui_executable(self):
        # Own the actual GUI PID. The package bootstrap can spawn it and exit
        # before the job assignment, leaving a visible but unowned window.
        for relative in (("bin", "odx-app.exe"), ("Odx Capture.exe",)):
            executable = os.path.join(be.ODX_ROOT, *relative)
            if os.path.isfile(executable):
                return executable
        raise FileNotFoundError(f"Không tìm thấy Odx Capture tại: {be.ODX_ROOT}")

    def cancel_player_ready(self, cancel_connection=False):
        """Stop pending readiness work without pretending its worker has exited."""
        event = getattr(self, "_ready_cancel_event", None)
        if event is not None:
            event.set()
        if cancel_connection:
            event = getattr(self, "_connection_cancel_event", None)
            if event is not None:
                event.set()

    def player_ready_request_allowed(self, generation, round_token, player_id,
                                     operation_token, connecting=False):
        """A worker-safe guard: no widget reads and no dependency on window visibility."""
        if (not self.is_running or self.current_mode != "PLAYER"
                or generation != self._player_connection_generation
                or round_token != self._player_round_token):
            return False
        if any(getattr(self, name, False) for name in (
                "is_player_recording", "p_counting_down", "is_validating",
                "_player_validation_failed", "is_recording", "is_counting_down",
                "is_stopping", "is_updating", "capture_state_unknown", "is_host_opening")):
            return False
        if (getattr(self.follower, "current_recording", None)
                or getattr(self.follower, "last_armed", None)):
            return False
        if not self.synced_settings_ready(allow_finished_task=True):
            return False
        configured_id = str(self.config.get("player_id", player_id)).strip()
        if not player_id or configured_id != player_id:
            return False
        if connecting:
            return operation_token == self._connection_operation_token
        return (operation_token == self._ready_operation_token
                and not self.is_connecting)

    def select_task_id(self, task_id):
        self.cancel_prepared_host_stop()
        self.task_id_override = task_id
        self.active_task_id = task_id
        batch = self.combo_batch.get().strip()
        prefix = batch if batch.endswith(("-", "_")) else batch + "-"
        suffix = task_id[len(prefix):] if task_id.startswith(prefix) else ""
        match = re.fullmatch(r"(\d+)(?:-fix\d+)?", suffix)
        if match:
            self.ent_subtask.delete(0, "end")
            self.ent_subtask.insert(0, match.group(1))
            self.ent_start_num.delete(0, "end")
            self.ent_start_num.insert(0, str(int(match.group(1))))
        self.ent_full_task.delete(0, "end")
        self.ent_full_task.insert(0, task_id)
        self.update_task_id_preview()

    def clear_log(self):
        self.txt_log.delete("1.0", "end")

    # ==================================================================
    # SYSTEM HEALTH BACKGROUND CHECKS
    # ==================================================================
    def start_system_health_checks(self):
        def _check_loop():
            try:
                version = be.get_odx_version_display()
                self.post_ui(self.badge_odx.config, text=f"Odx: [{version}]", bg="#065F46", fg="#A7F3D0")
                self.log(f"Odx Engine ({version}): {be.ODX_ROOT}", "SYSTEM")
                admin = be.is_admin()
                self.post_ui(self.badge_admin.config, text="Admin: [OK]" if admin else "Admin: [CẦN]", bg="#065F46" if admin else "#991B1B")
                ok, message = be.sync_system_clock()
                self.post_ui(self.badge_ntp.config, text="NTP: [OK]" if ok else "NTP: [CHƯA XÁC NHẬN]")
                self.log(message, "NTP")
            except Exception as exc:
                self.log(f"Kiểm tra hệ thống: {exc}", "WARN")
            while self.is_running:
                game = be.is_game_running("ArmaReforgerSteam")
                self.post_ui(self.badge_game.config, text="Game: [ĐANG CHẠY]" if game else "Game: [CHƯA MỞ]")
                if self.odx_opened and self.current_mode == "HOST" and time.time() - getattr(self, "odx_launch_time", 0) > 10 and not be.find_odx_window():
                    if not self.operation_busy():
                        self.post_ui(self.reset_host_to_unopened_state)
                    else:
                        self.log("Không tìm thấy cửa sổ Odx khi đang xử lý; giữ trạng thái để tránh mất dữ liệu.", "WARN")
                time.sleep(3)
        threading.Thread(target=_check_loop, daemon=True).start()

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
        self.hide_host_choices()
        self.pending_fix_task = ""
        self.pending_next_task = ""

    def on_batch_selected(self, event=None):
        if self.operation_busy():
            return
        self.task_id_override = None
        sel = self.combo_batch.get().strip()
        if sel != self.current_batch:
            self.current_batch = sel
            self.reset_batch_params_to_default()
            self.save_current_task_config()
            self.update_task_id_preview()
            self.restore_progress_task()
            self.log(f"Đã chuyển sang Batch: [{sel}] (đặt lại về Task con: 01, Số: 1)", "CONFIG")
        else:
            self.update_task_id_preview()

    def add_batch_dialog(self):
        if self.operation_busy():
            return
        name = simpledialog.askstring("Thêm Batch Mới", "Nhập tên Batch mới (Ví dụ: batch3-ASRM hoặc asm-s222-):", parent=self.root)
        if name and name.strip():
            name = name.strip()
            if name not in self.batch_list:
                self.batch_list.append(name)
                self.combo_batch["values"] = self.batch_list
            self.combo_batch.set(name)
            self.current_batch = name
            self.task_id_override = None

            # Reset ngay lập tức toàn bộ thông số về mặc định: Task con 01, Số 1, Tổng 15
            self.reset_batch_params_to_default()

            self.save_current_task_config()
            self.update_task_id_preview()
            self.log(f"★ Đã thêm Batch mới [{name}] & TỰ ĐỘNG RESET toàn bộ thông số về mặc định: Task con: 01, Số: 1, Tổng: 15!", "CONFIG")

    def delete_current_batch(self):
        if self.operation_busy():
            return
        self.task_id_override = None
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
        if self.operation_busy():
            return
        if event is not None:
            self.task_id_override = None
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

        tid = self.task_id_override or tid
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
        if self.valid_task_id(tid) and self.odx_opened and self.current_mode == "HOST" and self.owned_odx_window():
            be.set_task_box(tid)

    def on_full_task_manual_edit(self, event=None):
        if self.operation_busy():
            return
        custom_id = self.ent_full_task.get().strip()
        if custom_id:
            self.task_id_override = custom_id
            self.active_task_id = custom_id
            if not self.valid_task_id(custom_id):
                self.lbl_task_display.config(text="Mã Task chứa ký tự không hợp lệ", fg="#EF4444")
                return
            be.set_clipboard(custom_id)
            if self.odx_opened and self.current_mode == "HOST" and self.owned_odx_window():
                be.set_task_box(custom_id)
            exists, exist_path = be.is_task_already_recorded(custom_id)
            if hasattr(self, "lbl_task_display"):
                if exists:
                    self.lbl_task_display.config(text=f"⚠️ [ĐÃ CÓ TRÊN MÁY]: {custom_id}!", fg="#EF4444")
                    self.ent_full_task.config(fg="#EF4444")
                else:
                    self.lbl_task_display.config(text=f"[✓ Task mới: {custom_id}]", fg="#10B981")
                    self.ent_full_task.config(fg="#10B981")
            self.save_current_task_config()

    def btn_auto_add_fix(self):
        if self.operation_busy():
            return
        if not getattr(self, "odx_opened", False):
            messagebox.showwarning(
                "Chưa mở Odx Capture",
                "Bạn phải nhấn nút '[🖥 Mở Odx Capture (Tạo Room)]' trước tiên!\n\nNếu chưa mở Odx Capture, các thao tác sau đó sẽ bị khóa hoàn toàn."
            )
            return
        cur_id = self.ent_full_task.get().strip() if hasattr(self, "ent_full_task") else self.active_task_id
        try:
            next_fix = be.get_next_available_fix_task_id(cur_id or self.active_task_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self.log(f"Chưa chọn được mã -fix: {exc}", "ERROR")
            self.lbl_host_status.config(text=f"CHƯA CHỌN ĐƯỢC TASK -FIX: {exc}", fg=ModernTheme.ACCENT_RED)
            return
        self.task_id_override = next_fix
        self.active_task_id = next_fix
        if hasattr(self, "ent_full_task"):
            self.ent_full_task.delete(0, "end")
            self.ent_full_task.insert(0, next_fix)
        be.set_clipboard(next_fix)
        self.update_task_id_preview()
        self.log(f"Đã tự động tạo mã Task Sửa Lỗi an toàn (tránh trùng): [{next_fix}]!", "FIX")

    def btn_inject_task_id(self):
        if self.operation_busy():
            return
        if not self.owned_odx_window():
            self.log("Không có cửa sổ Odx do tool quản lý để điền mã task.", "WARN")
            return
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
        self.task_id_override = None

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
        if self.operation_busy():
            return
        rcode = self.ent_host_room.get().strip().upper()
        if self.valid_room_code(rcode):
            self.room_code = rcode
            be.set_clipboard(self.room_code)
            self.log(f"ĐÃ ÁP DỤNG MÃ PHÒNG HOST: [{self.room_code}]! Đã tự động copy vào Clipboard.", "HOST")
            self.start_host_room_tracking(self.room_code)
        else:
            messagebox.showwarning("Mã không hợp lệ", "Mã phòng phải gồm đúng 6 ký tự chữ/số (Ví dụ: UNJQEV)!")

    def copy_room_code(self):
        code = self.ent_host_room.get().strip().upper() if hasattr(self, "ent_host_room") else self.room_code
        if self.valid_room_code(code):
            be.set_clipboard(code)
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(code)
            except Exception:
                pass
            self.log(f"Đã sao chép mã phòng [{code}] vào Clipboard.", "HOST")
            messagebox.showinfo("Đã sao chép", f"Mã phòng: {code}\nHãy gửi cho 3 thành viên còn lại!")
        else:
            self.log("Chưa có mã phòng được xác nhận; hãy chờ tool tạo room xong.", "WAIT")

    def unlock_host_controls(self):
        """Mở khóa điều khiển sau khi đã xác nhận mã phòng thật của Odx."""
        self.odx_opened = True
        self.odx_launch_time = time.time()
        if hasattr(self, "btn_open_odx"):
            self.btn_open_odx.config(
                text=f"✓ Room {self.room_code}", bg="#059669", activebackground="#047857", state="normal"
            )
        if hasattr(self, "btn_host_start"):
            self.btn_host_start.config(state="normal", bg="#059669", fg="#FFFFFF")
        if hasattr(self, "btn_host_emergency"):
            self.btn_host_emergency.config(state="disabled", bg="#DC2626", fg="#FFFFFF")
        if hasattr(self, "btn_inject_task"):
            self.btn_inject_task.config(state="normal", bg="#2563EB", fg="#FFFFFF")
        if hasattr(self, "btn_auto_fix"):
            self.btn_auto_fix.config(state="normal", bg="#D97706", fg="#FFFFFF")
        if hasattr(self, "lbl_host_status"):
            self.lbl_host_status.config(
                text=f"{'[TEST MÀN HÌNH] ' if self.capture_test_mode() else ''}ROOM [{self.room_code}] ĐÃ XÁC NHẬN - CHỜ {self.expected_players()} MÁY SẴN SÀNG",
                fg=ModernTheme.ACCENT_GREEN
            )
        self.log(f"Đã xác nhận phòng [{self.room_code}]. Chờ đủ {self.expected_players()} máy sẵn sàng trước khi quay.", "SUCCESS")

    def reset_host_to_unopened_state(self):
        """Khóa toàn bộ thao tác, reset nút Mở Odx, xóa mã phòng và slot khi Odx bị tắt/dọn sạch"""
        self.cancel_host_room_creation()
        self.cancel_player_ready(cancel_connection=True)
        self.stop_settings_sync()
        self.odx_opened = False
        self.room_code = ""
        self._room_test_mode = None
        self.is_recording = False
        self.is_counting_down = False
        self.is_in_cooldown = False
        self.rec_start_time = 0
        self.abort_state = None
        self.hide_host_choices()
        self._pending_stop_action = None
        self._pending_stop_task = ""
        self.capture_state_unknown = False
        self._pending_sync_start_task = None
        self._stop_confirmation_failed = False
        self._start_command_sent = False
        self._transition_token += 1
        self._player_connection_generation += 1
        self.player_connection_active = False
        self._ready_inflight = False

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
        if hasattr(self, "btn_host_emergency"):
            self.btn_host_emergency.config(state="disabled", bg="#1E293B", fg="#64748B")
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

    @staticmethod
    def valid_room_code(code):
        return bool(re.fullmatch(r"[A-Z0-9]{6}", str(code or "").strip()))

    def cancel_host_room_creation(self):
        event = getattr(self, "_host_room_cancel_event", None)
        if event:
            event.set()
        self._host_launch_generation = getattr(self, "_host_launch_generation", 0) + 1
        self.is_host_opening = False

    def host_open_odx(self):
        if self.operation_busy():
            return
        if self.room_code and not self.room_capture_mode_matches():
            self.log("Chế độ TEST đã đổi; hãy đóng tool rồi mở lại để tạo room mới.", "ERROR")
            return
        if not self.hotkeys_ready:
            messagebox.showerror("Chưa sẵn sàng", "Bộ bảo vệ phím nóng chưa hoạt động. Hãy đợi hoặc khởi động lại tool.")
            return
        existing = be.find_odx_window()
        if be.find_unmanaged_odx_window() or (existing and not be.is_managed_odx_window(existing)):
            messagebox.showwarning("Odx đang được mở bên ngoài", "Hãy đóng cửa sổ Odx đang mở rồi mở lại bằng tool. Tool sẽ không nhận điều khiển hoặc dừng phiên bên ngoài.")
            return
        self.follower.check_new_lines()
        if self.follower.current_recording or self.follower.last_armed:
            self.log("Odx đang quay hoặc đếm ngược; chưa tạo lại phòng.", "WAIT")
            return
        self.update_task_id_preview()
        if not existing:
            try:
                exe_path = self.odx_gui_executable()
            except FileNotFoundError as exc:
                messagebox.showerror("Lỗi", str(exc))
                return
        else:
            exe_path = None

        # Reuse a managed window on retry; never kill a room just because the
        # operator clicked this button again.
        self._host_launch_generation = getattr(self, "_host_launch_generation", 0) + 1
        self._room_test_mode = self.capture_test_mode()
        generation = self._host_launch_generation
        cancel_event = threading.Event()
        self._host_room_cancel_event = cancel_event
        self.is_host_opening = True
        self.odx_opened = False
        self.room_code = ""
        self.set_task_controls_enabled(False)
        for name in ("btn_host_start", "btn_host_emergency"):
            widget = getattr(self, name, None)
            if widget:
                widget.config(state="disabled")
        self.btn_open_odx.config(text="⏳ ĐANG MỞ / TẠO ROOM...", state="disabled", bg=ModernTheme.ACCENT_AMBER)
        self.ent_host_room.delete(0, "end")
        self.ent_host_room.insert(0, "ĐANG TẠO...")
        self.lbl_host_status.config(text="ĐANG TỰ ĐỘNG TẠO ROOM - CHƯA XÁC NHẬN MÃ PHÒNG", fg=ModernTheme.ACCENT_AMBER)
        if self.tracker:
            self.tracker.pause_hud()
        if not existing:
            self.follower.reset_to_end()
        else:
            # Keep the cursor, but never treat an earlier room event as proof
            # that this request completed. An existing room is read from the UI.
            self.follower.room_code = None
        try:
            if not existing:
                process = getattr(self, "_host_process", None)
                if process is None or process.poll() is not None:
                    ok, error = be.prepare_capture_mode_launch(self.capture_test_mode())
                    if not ok:
                        raise RuntimeError(error)
                    be.set_task_box(self.active_task_id)
                    self.log(f"Đang mở Odx Capture ({os.path.basename(exe_path)}) và tạo phòng...", "HOST")
                    self._host_process = be.register_managed_process(subprocess.Popen([exe_path], cwd=be.ODX_ROOT))
            else:
                self.log("Đang kiểm tra phòng hiện tại hoặc tự bấm [Tạo room] trên Odx...", "HOST")
            threading.Thread(target=self.wait_host_room_created, args=(generation, cancel_event), daemon=True).start()
        except Exception as exc:
            self.finish_host_room_creation(generation, "", f"Không khởi chạy được Odx/tạo phòng: {exc}")

    def host_room_creation_status(self, generation, message, tag):
        if generation != self._host_launch_generation or not self.is_host_opening:
            return
        self.log(message, tag)
        self.lbl_host_status.config(text=str(message), fg=ModernTheme.ACCENT_RED if tag == "ERROR" else ModernTheme.ACCENT_AMBER)

    def wait_host_room_created(self, generation, cancel_event):
        def current():
            return (self.is_running and generation == self._host_launch_generation
                    and not cancel_event.is_set())

        def room_code_supplier():
            if not current():
                return ""
            self.follower.check_new_lines()
            code = str(getattr(self.follower, "room_code", "") or "").strip().upper()
            return code if self.valid_room_code(code) else ""

        def on_status(message, tag):
            if current():
                self.post_ui(self.host_room_creation_status, generation, message, tag)

        code, error = "", "Không xác nhận được mã phòng. Có thể bấm nút tạo room để thử lại."
        try:
            if not current():
                return
            self.configure_capture_mode_for_room(bool(self._room_test_mode), current)
            worker = be.host_auto_create_room(on_status=on_status, cancel_event=cancel_event,
                                              room_code_supplier=room_code_supplier)
            if worker is None:
                raise RuntimeError("Không khởi chạy được luồng tự tạo phòng.")
            worker.join()
            if not current():
                return
            confirmed = getattr(worker, "room_code", "") if getattr(worker, "success", False) else ""
            code = str(confirmed or "").strip().upper()
            if not self.valid_room_code(code):
                code = ""
                error = str(getattr(worker, "error", "") or error)
            elif not self.owned_odx_window():
                code, error = "", "Cửa sổ Odx đã đóng hoặc quyền sở hữu thay đổi trước khi xác nhận phòng."
        except Exception as exc:
            error = str(exc)
        if current():
            self.post_ui(self.finish_host_room_creation, generation, code, error)

    def finish_host_room_creation(self, generation, code, error=""):
        if generation != self._host_launch_generation or not self.is_running:
            return
        event = getattr(self, "_host_room_cancel_event", None)
        if event and event.is_set():
            return
        self.is_host_opening = False
        if not self.valid_room_code(code):
            self.odx_opened = False
            self.room_code = ""
            self.ent_host_room.delete(0, "end")
            self.btn_open_odx.config(text="↻ THỬ TẠO ROOM LẠI", state="normal", bg=ModernTheme.ACCENT_BLUE)
            self.lbl_host_status.config(text=f"CHƯA TẠO ĐƯỢC ROOM: {error}", fg=ModernTheme.ACCENT_RED)
            if self.tracker:
                self.tracker.stop()
                self.tracker = None
            self.log(f"Tự tạo phòng chưa hoàn tất: {error}", "ERROR")
            return
        self.room_code = code
        self.ent_host_room.delete(0, "end")
        self.ent_host_room.insert(0, code)
        self.set_task_controls_enabled(True)
        self.unlock_host_controls()
        be.set_clipboard(code)
        be.set_task_box(self.active_task_id)
        self.log(f"Mã phòng [{code}] đã được xác nhận và sao chép vào Clipboard.", "HOST")
        self.ensure_host_hotkey_priority(force=True)
        self.start_host_room_tracking(code)

    def start_host_room_tracking(self, room_code):
        if self.tracker:
            self.tracker.stop()

        def _on_roster_update(rc, host_id, members):
            def _update():
                try:
                    ready_cnt = sum(1 for idx, m in enumerate(members) if not m.get("problem") and (m.get("ready") or (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id))))
                    expected = self.expected_players()
                    all_ready = (len(members) == expected and ready_cnt == expected)

                    # Update UI labels
                    for idx in range(4):
                        if idx < len(members):
                            m = members[idx]
                            pname = str(m.get("name") or m.get("player_id"))
                            is_h = (str(m.get("player_id")) == str(host_id) or (idx == 0 and not host_id))
                            tag = "[H] " if is_h else ""
                            ready = not m.get("problem") and (is_h or m.get("ready"))
                            st = "✓ SẴN SÀNG" if ready else str(m.get("problem") or ".. Đang chờ")
                            col = ModernTheme.ACCENT_GREEN if ready else ModernTheme.ACCENT_AMBER
                            self.slot_labels[idx].config(text=f"{tag}{pname}: {st}", fg=col)
                        else:
                            self.slot_labels[idx].config(text=f"Slot {idx+1}: Trống...", fg=ModernTheme.TEXT_MUTED)

                    if self.operation_busy() or self.is_in_cooldown or self.abort_state:
                        return
                    if all_ready:
                        self.lbl_host_status.config(text=f"ĐỦ {expected}/{expected} NGƯỜI SẴN SÀNG - F10 ĐỂ BẮT ĐẦU", fg=ModernTheme.ACCENT_GREEN)
                    else:
                        self.lbl_host_status.config(text=f"Đang chờ thành viên ({ready_cnt}/{expected} sẵn sàng)...", fg=ModernTheme.ACCENT_AMBER)
                except Exception:
                    pass
            try:
                self.post_ui(_update)
            except Exception:
                pass

        self.tracker = be.RoomTracker(room_code, overlay=self.overlay, on_change_callback=_on_roster_update,
                                      expected_players=self.expected_players())
        self.tracker.start()

    def host_trigger_start(self):
        if self.current_mode != "HOST" or not self.odx_opened:
            self.log("Hãy mở Odx Capture ở chế độ HOST trước khi bắt đầu.", "WARN")
            return
        if self.operation_busy() or self.is_in_cooldown:
            self.log("Đang quay, lưu hoặc nghỉ giữa các task; chưa thể bắt đầu.", "WAIT")
            return
        try:
            self.config.update(self.read_shared_controls())
        except (ValueError, TypeError) as exc:
            self.log(f"Thông số quay không hợp lệ: {exc}", "ERROR")
            return
        if self.configured_task_total() is None:
            messagebox.showwarning("Tổng task không hợp lệ", "Tổng số task phải là số nguyên lớn hơn 0.")
            return
        if self.batch_complete():
            self.lbl_host_status.config(text=self.completion_text(), fg=ModernTheme.ACCENT_GREEN)
            self.log(self.completion_text(), "PROGRESS")
            return
        if not self.hotkeys_ready:
            self.ensure_host_hotkey_priority()
            self.log("Bộ xử lý F10 đang khởi tạo hoặc chờ xác nhận; chưa phát lệnh quay.", "WAIT")
            return
        if not self.ensure_host_hotkey_priority():
            self.log("Đang xác nhận F10 đi qua Auto-Pilot; chưa phát lệnh quay.", "WAIT")
            return
        if be.find_unmanaged_odx_window():
            self.log("Một cửa sổ Odx bên ngoài tool có thể nhận phím nóng toàn cục. Hãy đóng cửa sổ đó trước khi quay.", "ERROR")
            return
        if not self.room_capture_mode_matches():
            self.log("Nguồn quay chưa được xác nhận; giữ nguyên Odx và thử Làm mới kết nối. Đổi chế độ cần đóng/mở lại tool.", "ERROR")
            return
        if not self.capture_test_mode() and not be.is_game_running(self.config.get("game_process", "ArmaReforgerSteam")):
            self.log("Game chưa chạy; chưa phát lệnh quay.", "WARN")
            return
        hwnd = be.find_odx_window()
        if not hwnd:
            self.log("Không tìm thấy cửa sổ Odx Capture; chưa phát lệnh quay.", "WARN")
            return
        if not be.is_managed_odx_window(hwnd):
            self.log("Cửa sổ Odx hiện tại không do tool khởi chạy; chưa nhận quyền điều khiển.", "ERROR")
            return
        room_data = self.tracker.latest_data if self.tracker else None
        if self.tracker and (self.tracker.last_error or time.monotonic() - self.tracker.last_success_at > 10):
            self.log(f"Trạng thái phòng chưa xác nhận: {self.tracker.last_error or 'dữ liệu đã cũ'}", "WARN")
            return
        if not room_data or not isinstance(room_data.get("members"), list):
            self.log("Chưa xác nhận trạng thái phòng; hãy chờ kết nối trước khi bắt đầu.", "WARN")
            return
        if room_data.get("current_session"):
            self.log("Round trước vẫn đang xử lý; hãy chờ phòng kết thúc trước khi bắt đầu task.", "WAIT")
            return
        members = room_data["members"]
        host_id = room_data.get("host")
        unready = [str(m.get("name") or m.get("player_id")) for idx, m in enumerate(members)
                   if not ((str(m.get("player_id")) == str(host_id)) or (idx == 0 and not host_id))
                   and not m.get("ready")]
        expected = self.expected_players()
        if len(members) != expected or unready or not self.tracker.is_all_ready(expected=expected):
            self.log(f"Chưa đủ {expected} người sẵn sàng. Đang chờ: {', '.join(unready) or 'thành viên'}.", "WARN")
            self.overlay.show_roster_hud(f"CHUA DU {expected}/{expected} SAN SANG - CHUA BAT DAU", fg_color="#EF4444")
            return
        self.update_task_id_preview()
        if not self.valid_task_id(self.active_task_id):
            messagebox.showwarning("Mã Task không hợp lệ", "Chỉ dùng chữ, số, dấu gạch dưới và gạch ngang (tối đa 128 ký tự).")
            return
        exists, path = be.is_task_already_recorded(self.active_task_id)
        if exists:
            self._pending_sync_start_task = None
            self._sync_start_after = None
            self.btn_host_emergency.config(state="disabled")
            self.original_task_before_abort = re.sub(r"-fix\d+$", "", self.active_task_id)
            self.show_abort_choices()
            self.log(f"CHẶN QUAY TRÙNG [{self.active_task_id}]: {path}. Chọn F11 để dùng mã -fix mới.", "WARN")
            return
        if getattr(self, "_sync_required", False):
            server = self._sync_server
            if server is None or server.room_code != self.room_code or server.host_id != str(host_id):
                self.log("Đang khởi tạo đồng bộ Host; hãy chờ trước khi bắt đầu.", "WAIT")
                return
            try:
                if getattr(self, "_sync_start_task", None) != self.active_task_id or getattr(self, "_sync_start_after", None) is None:
                    import secrets
                    self._sync_start_task = self.active_task_id
                    self._sync_start_after = time.monotonic()
                    self._sync_start_nonce = secrets.token_hex(16)
                revision = server.publish(self.host_settings_snapshot())
                waiting = {str(m.get("player_id")) for m in members
                           if str(m.get("player_id")) != str(host_id)} - server.applied_players(revision, after=self._sync_start_after)
            except (ValueError, OSError) as exc:
                self.log(f"Chưa đồng bộ cấu hình: {exc}", "ERROR")
                return
            if waiting:
                self._pending_sync_start_task = self.active_task_id
                self.btn_host_emergency.config(state="normal")
                self.lbl_host_status.config(text=f"CHỜ PLAYER ĐỒNG BỘ ĐỂ QUAY [{self.active_task_id}]", fg=ModernTheme.ACCENT_AMBER)
                self.log(f"Chờ Player áp dụng cấu hình bản {revision}: {', '.join(sorted(waiting))}.", "WAIT")
                return
        task_id = self.active_task_id
        self.original_task_before_abort = re.sub(r"-fix\d+$", "", task_id)
        self.target_duration = self.round_target_duration(task_id)
        self.rec_start_time = 0
        self._round_start_epoch = None
        self._scheduled_start_at = None
        self._scheduled_start_task = ""
        self._stop_elapsed = None
        self.follower.last_armed = None
        self.follower.current_stopped = None
        self.follower.last_round_result = None
        self.follower.last_all_ok = None
        self.follower.last_kept_path = None
        self.follower.task_in_use_error = None
        self.follower.id_conflict = None
        self.follower.clock_drift_error = None
        self.abort_state = None
        self.hide_host_choices()
        self._transition_token += 1
        token = self._transition_token
        self._start_command_sent = False
        self._pending_sync_start_task = None
        self._sync_start_after = None
        self._round_test_mode = self.capture_test_mode()
        self.is_counting_down = True
        self.set_task_controls_enabled(False)
        self.btn_host_start.config(state="disabled")
        self.btn_host_emergency.config(state="normal")
        if self.tracker:
            self.tracker.pause_hud()
        self.lbl_host_status.config(text=f"ĐANG CHỜ ODX XÁC NHẬN QUAY [{task_id}]...", fg=ModernTheme.ACCENT_AMBER)
        self.overlay.show_pending_recording(task_id)
        threading.Thread(target=self.prepare_host_start, args=(token, task_id), daemon=True).start()

    def prepare_host_start(self, token, task_id):
        may_have_started = False
        try:
            if not self.is_running or token != self._transition_token:
                return
            injection = be.set_task_box(task_id)
            if injection:
                injection.join(timeout=8)
            if not injection or injection.is_alive() or not getattr(injection, "success", False):
                raise RuntimeError("Chưa xác nhận ô Task ID trên Odx; không phát lệnh quay.")
            if not self.is_running or token != self._transition_token:
                return
            hwnd = self.owned_odx_window()
            if not hwnd or not be.is_managed_odx_window(hwnd) or not self.room_capture_mode_matches():
                raise RuntimeError("Odx hoặc nguồn quay thay đổi; không phát lệnh quay.")
            if not self.capture_test_mode() and not be.is_game_running(self.config.get("game_process", "ArmaReforgerSteam")):
                raise RuntimeError("Game hoặc Odx đã đóng; không phát lệnh quay.")
            if not self.tracker or not self.tracker.is_all_ready(expected=self.expected_players()):
                raise RuntimeError("Trạng thái phòng thay đổi trong lúc chuẩn bị; chưa phát lệnh quay.")
            if (self.tracker.latest_data or {}).get("current_session"):
                raise RuntimeError("Phòng vẫn đang xử lý round trước; chưa phát lệnh quay.")
            if not self.ensure_host_hotkey_priority():
                raise RuntimeError("Chưa xác nhận quyền xử lý F10 trước Odx; chưa phát lệnh quay.")
            with self._action_lock:
                if not self.is_running or token != self._transition_token:
                    return
                exists, path = be.is_task_already_recorded(task_id)
                if exists:
                    self.post_ui(self.finish_start_collision, token, task_id, path)
                    return
                self._start_command_sent = True
                may_have_started = True
                if not be.send_toggle_hotkey(hwnd):
                    raise RuntimeError("Không gửi được lệnh F10")
        except Exception as exc:
            self.post_ui(self.finish_start, token, task_id, False, str(exc), may_have_started)
            return
        self.watch_start(token, task_id)

    def finish_start_collision(self, token, task_id, path):
        if not self.is_running or token != self._transition_token:
            return
        self._transition_token += 1
        self.is_counting_down = False
        self._start_command_sent = False
        self._pending_sync_start_task = None
        self._sync_start_after = None
        self.rec_start_time = 0
        self._scheduled_start_at = None
        self._scheduled_start_task = ""
        self.capture_state_unknown = False
        self.set_task_controls_enabled(True)
        self.btn_host_start.config(state="normal")
        self.btn_host_emergency.config(state="disabled")
        self.show_abort_choices()
        self.log(f"CHẶN QUAY TRÙNG [{task_id}]: {path}. Chưa gửi F10; dữ liệu cũ được giữ nguyên.", "WARN")

    def watch_start(self, token, task_id):
        deadline = time.monotonic() + 25
        error = "Chưa xác định được mốc bắt đầu ghi thật của Odx."
        while self.is_running and token == self._transition_token and time.monotonic() < deadline:
            self.follower.check_new_lines()
            if self.follower.current_recording == task_id:
                started_at = be.get_recording_start_time(task_id)
                if started_at is not None:
                    self.post_ui(self.finish_start, token, task_id, True, "", True, started_at)
                    return
            if self.follower.current_recording and self.follower.current_recording != task_id:
                error = f"Odx đang quay mã khác: {self.follower.current_recording}"
                break
            stopped = self.follower.current_stopped
            if stopped and stopped[0] == task_id:
                self.post_ui(self.finish_start_stopped, token, task_id)
                return
            errors = [getattr(self.follower, name, None) for name in
                      ("task_in_use_error", "id_conflict", "clock_drift_error")]
            if any(errors):
                error = str(next(err for err in errors if err))
                break
            time.sleep(0.1)
        self.post_ui(self.finish_start, token, task_id, False, error)

    def finish_start_stopped(self, token, task_id):
        if token == self._transition_token and self.is_running:
            self.is_counting_down = False
            self.request_host_stop("emergency")

    def finish_start(self, token, task_id, success, error, may_have_started=True, started_at=None):
        if token != self._transition_token or not self.is_running:
            return
        self.is_counting_down = False
        if success:
            self.begin_confirmed_recording(task_id, started_at)
            return
        self.log(f"Không thể bắt đầu task [{task_id}]: {error}", "ERROR")
        self.lbl_host_status.config(text=f"KHÔNG BẮT ĐẦU: {error}", fg=ModernTheme.ACCENT_RED)
        if not may_have_started or (not self.follower.current_recording and not self.follower.last_armed and any(
                getattr(self.follower, name, None) for name in ("task_in_use_error", "id_conflict", "clock_drift_error"))):
            self.set_task_controls_enabled(True)
            self.btn_host_start.config(state="normal")
            self.btn_host_emergency.config(state="disabled")
            if self.tracker:
                self.tracker.resume_hud()
            return
        # Cancel an armed countdown too; do not leave an unobserved capture behind.
        self.capture_state_unknown = True
        self.request_host_stop("emergency")

    def begin_confirmed_recording(self, task_id, started_at=None):
        if self.is_recording:
            return
        self.cancel_prepared_host_stop()
        self._round_test_mode = bool(getattr(self, "_room_test_mode", False))
        anchor = time.monotonic() if started_at is None else float(started_at)
        self._round_start_epoch = time.time() + anchor - time.monotonic()
        self.active_task_id = task_id
        self.task_id_override = task_id
        self.target_duration = self.round_target_duration(task_id)
        if anchor > time.monotonic():
            self._scheduled_start_at = anchor
            self._scheduled_start_task = task_id
            self.is_counting_down = True
            self.rec_start_time = 0
            self.show_host_pending_clock(task_id, anchor)
            return
        self._scheduled_start_at = None
        self._scheduled_start_task = ""
        self.is_counting_down = False
        self.is_recording = True
        self.capture_state_unknown = False
        self.rec_start_time = anchor
        self.set_task_controls_enabled(False)
        self.btn_host_start.config(state="disabled")
        self.btn_host_emergency.config(state="normal")
        if self.tracker:
            self.tracker.pause_hud()
        self.lbl_host_status.config(text=f"● ĐANG QUAY [{task_id}] - F10: DỪNG KHẨN CẤP", fg=ModernTheme.ACCENT_GREEN)
        self.log(f"Odx đã xác nhận bắt đầu task [{task_id}].", "RECORD")

    def show_host_pending_clock(self, task_id, anchor=None):
        remaining = max(0, anchor - time.monotonic()) if anchor is not None else None
        self.lbl_timer.config(text="CHỜ GHI" if remaining is None else f"ĐẾM NGƯỢC {remaining:.1f}s")
        self.lbl_host_status.config(text=f"ODX ĐANG CHUẨN BỊ [{task_id}]", fg=ModernTheme.ACCENT_AMBER)
        self.overlay.show_pending_recording(task_id, remaining)

    def capture_elapsed(self):
        if self.rec_start_time <= 0:
            return 0.0
        stopped = getattr(self.follower, "current_stopped", None)
        stop_at = (getattr(self.follower, "current_stopped_at", None)
                   if stopped and stopped[0] == self.active_task_id else None)
        end = stop_at if stop_at is not None else time.monotonic()
        return max(0.0, end - self.rec_start_time)

    def cancel_prepared_host_stop(self):
        prepared = getattr(self, "_prepared_host_stop", None)
        self._prepared_host_stop = None
        self._prepared_host_stop_key = None
        self._prepared_host_stop_attempted = False
        if prepared is not None:
            try:
                prepared.cancel()
            except Exception:
                pass

    def prepare_host_stop_if_needed(self, elapsed):
        # Prepare native accessibility and server checks while recording still
        # has time left. Preparation never invokes End round or blocks F10.
        if (not self.is_recording or self.is_stopping
                or not max(0, self.target_duration - 8) <= elapsed < self.target_duration
                or getattr(self, "_prepared_host_stop_attempted", False)):
            return
        self._prepared_host_stop_attempted = True
        prepare = getattr(be, "prepare_recording_stop", None)
        if prepare is None:
            return
        task, token = self.active_task_id, self._transition_token
        hwnd = self.owned_odx_window()
        if not hwnd:
            return

        def current():
            return (self.is_running and self.current_mode == "HOST"
                    and self._transition_token == token and self.active_task_id == task
                    and self.is_recording and not self.is_stopping
                    and self.follower.current_recording == task)
        try:
            self._prepared_host_stop = prepare(self.follower, task, hwnd=hwnd,
                                              can_prepare=current)
            self._prepared_host_stop_key = (token, task)
        except Exception as exc:
            self.log(f"Chưa chuẩn bị được lệnh dừng [{task}]: {exc}; vẫn xác minh khi đủ giờ.", "WARN")

    def take_prepared_host_stop(self, task):
        prepared = getattr(self, "_prepared_host_stop", None)
        key = getattr(self, "_prepared_host_stop_key", None)
        if prepared is not None and key == (self._transition_token, task):
            try:
                if prepared.claim():
                    self._prepared_host_stop = None
                    self._prepared_host_stop_key = None
                    return prepared
            except Exception:
                pass
        self.cancel_prepared_host_stop()
        return None

    def host_trigger_emergency_stop(self):
        if self.current_mode != "HOST" or not self.odx_opened:
            return
        if self._pending_sync_start_task:
            self._pending_sync_start_task = None
            self._sync_start_after = None
            self.btn_host_emergency.config(state="disabled")
            self.log("Đã hủy yêu cầu quay đang chờ đồng bộ; chưa gửi F10.", "CANCEL")
            self.lbl_host_status.config(text="ĐÃ HỦY CHỜ QUAY - F10 ĐỂ BẮT ĐẦU", fg=ModernTheme.ACCENT_AMBER)
            return
        if self.is_stopping or self.is_validating or self.is_updating:
            self.log("Đang chờ Odx hoàn tất; không gửi thêm F10.", "WAIT")
            return
        if self.capture_state_unknown or self._stop_confirmation_failed:
            self.log("Chưa xác nhận trạng thái Odx; không gửi lại F10. Hãy kiểm tra Odx hoặc đóng tiến trình do tool quản lý.", "ERROR")
            return
        if self.is_recording or self.is_counting_down:
            self.is_in_cooldown = False
            action = "emergency" if self.is_counting_down or self.capture_elapsed() < self.target_duration else "validate"
            self.request_host_stop(action)

    def has_round_finished(self, task_id):
        result = self.follower.last_round_result
        # Local "done: kept" reports "ok" before the room finishes its round.
        return bool(result and result[0] == task_id and not self.follower.current_recording
                    and str(result[1]).strip().lower() not in ("", "ok"))

    def wait_capture_finished(self, task_id, timeout=30):
        deadline = time.monotonic() + timeout
        while self.is_running and time.monotonic() < deadline:
            self.follower.check_new_lines()
            if self.has_round_finished(task_id):
                return True
            if self.follower.current_recording and self.follower.current_recording != task_id:
                return False
            time.sleep(0.1)
        return False

    def request_host_stop(self, action):
        if self.is_stopping:
            return
        task_id = self.active_task_id
        prepared = self.take_prepared_host_stop(task_id)
        with self._action_lock:
            preparing_without_capture = self.is_counting_down and not self._start_command_sent and not self.follower.current_recording
            allow_armed = self._start_command_sent
            self._transition_token += 1
            token = self._transition_token
            self.is_stopping = True
            self.is_counting_down = False
            self._pending_stop_action = action
            self._pending_stop_task = task_id
            self._stop_elapsed = self.capture_elapsed()
            self._scheduled_start_at = None
            self._scheduled_start_task = ""
        if preparing_without_capture:
            self.finish_host_stop(token, task_id, True, action)
            return
        self.btn_host_start.config(state="disabled")
        self.btn_host_emergency.config(state="disabled")
        self.set_task_controls_enabled(False)
        stop_label = "ĐANG DỪNG KHẨN CẤP" if action == "emergency" else "ĐANG KẾT THÚC"
        self.lbl_host_status.config(text=f"{stop_label} [{task_id}] - CHỜ ODX...", fg=ModernTheme.ACCENT_AMBER)
        self.log(f"Yêu cầu kết thúc [{task_id}] trực tiếp trên cửa sổ Odx Host; chờ xác nhận dừng.", "WAIT")

        def on_status(message, tag="WAIT"):
            def display():
                if (self.is_running and token == self._transition_token and self.is_stopping
                        and self._pending_stop_task == task_id):
                    self.lbl_host_status.config(text=str(message), fg=ModernTheme.ACCENT_AMBER)
                    self.log(message, tag)
            self.post_ui(display)

        def _worker():
            try:
                if not self.is_running or token != self._transition_token:
                    return
                hwnd = self.owned_odx_window()
                if not hwnd:
                    ok = False
                else:
                    stop_options = {"prepared": prepared} if prepared is not None else {}
                    ok = be.stop_recording_verified(
                        self.follower, task_id, hwnd=hwnd, timeout=30, allow_armed=allow_armed,
                        can_stop=lambda: (self.is_running and token == self._transition_token
                                          and self.owned_odx_window() == hwnd), on_status=on_status,
                        **stop_options)
                if ok and action in ("emergency", "reset", "close"):
                    ok = self.wait_capture_finished(task_id)
                self.post_ui(self.finish_host_stop, token, task_id, bool(ok), action)
            except Exception as exc:
                self.log(f"Lỗi khi dừng Odx: {exc}", "ERROR")
                self.post_ui(self.finish_host_stop, token, task_id, False, action)
            finally:
                if prepared is not None:
                    prepared.cancel()

        threading.Thread(target=_worker, daemon=True).start()

    def finish_host_stop(self, token, task_id, success, action):
        if token != self._transition_token or not self.is_running:
            return
        self.is_stopping = False
        if not success:
            self._stop_confirmation_failed = True
            self.capture_state_unknown = True
            self._closing = False
            self.btn_host_emergency.config(state="disabled")
            self.lbl_host_status.config(text="CHƯA XÁC NHẬN KẾT THÚC - HÃY KIỂM TRA ODX", fg=ModernTheme.ACCENT_RED)
            self.log("Chưa xác nhận Odx kết thúc; giữ nguyên task và thời gian, không lặp lệnh có thể bật quay lại. Odx được giữ mở.", "ERROR")
            return
        self.cancel_prepared_host_stop()
        final_elapsed = self.capture_elapsed()
        self.is_recording = False
        self._stop_confirmation_failed = False
        self.is_counting_down = False
        self._start_command_sent = False
        self.capture_state_unknown = False
        self._round_start_epoch = None
        self._pending_stop_action = None
        self._pending_stop_task = ""
        self.rec_start_time = 0
        self._scheduled_start_at = None
        self._scheduled_start_task = ""
        self.follower.current_recording = None
        self.follower.current_stopped = None
        self.follower.last_armed = None
        self.overlay.clear()
        self.lbl_timer.config(text=format_recording_clock(final_elapsed, self.target_duration))
        self.btn_host_emergency.config(state="disabled")
        if self.tracker:
            self.tracker.resume_hud()
        if action == "close":
            self.shutdown_now()
            return
        if action == "reset":
            self.reset_host_task()
            return
        if action == "emergency":
            self.set_task_controls_enabled(True)
            self.btn_host_start.config(state="normal")
            self.show_abort_choices()
            return
        self.is_validating = True
        threading.Thread(target=self.run_post_capture_validation, args=(task_id, token), daemon=True).start()

    def host_cancel_and_reset(self):
        if self.is_stopping or self.is_validating or self.is_updating:
            self.log("Đang dừng/lưu dữ liệu; hãy chờ hoàn tất trước khi đặt lại.", "WAIT")
            return
        if self.is_recording or self.is_counting_down or getattr(self, "capture_state_unknown", False):
            if self.capture_state_unknown or self._stop_confirmation_failed:
                self.log("Chưa xác nhận trạng thái Odx; chưa thể đặt lại task hoặc gửi thêm F10.", "WAIT")
                return
            self.request_host_stop("reset")
            return
        self.reset_host_task()

    def reset_host_task(self):
        if self.is_recording or self.is_counting_down or self.is_stopping:
            return
        self.cancel_prepared_host_stop()
        self._transition_token += 1
        self.is_in_cooldown = False
        self.abort_state = None
        self.hide_host_choices()
        self._sync_start_after = None
        self.set_task_controls_enabled(True)
        base_id = re.sub(r"-fix\d+$", "", self.original_task_before_abort or self.active_task_id)
        self.select_task_id(base_id)
        self.btn_host_start.config(state="normal" if self.odx_opened else "disabled")
        self.btn_host_emergency.config(state="disabled")
        self.lbl_host_status.config(text=f"SẴN SÀNG: [{base_id}] - BẤM F10 ĐỂ QUAY", fg=ModernTheme.ACCENT_GREEN)
        self.overlay.clear()
        self.log(f"Đã đặt lại task [{base_id}] sau khi xác nhận dừng.", "CANCEL")

    def host_choose_option(self, choice):
        if self.abort_state != "AWAITING_CHOICE" or self.operation_busy():
            return
        if choice not in ("FIX", "NEXT", "CANCEL"):
            return
        self._pending_sync_start_task = None
        self._sync_start_after = None
        if choice == "CANCEL":
            base_id = self.original_task_before_abort or re.sub(r"-fix\d+$", "", self.active_task_id)
            self.reset_host_task()
        else:
            task_id = self.pending_fix_task if choice == "FIX" else self.pending_next_task
            self.abort_state = None
            self.hide_host_choices()
            self.select_task_id(task_id)
        self.btn_host_start.config(state="normal" if self.odx_opened else "disabled")
        self.btn_host_emergency.config(state="disabled")
        text = f"ĐÃ CHỌN [{self.active_task_id}] - BẤM F10 ĐỂ QUAY"
        self.lbl_host_status.config(text=text, fg=ModernTheme.ACCENT_GREEN)
        self.overlay.show_roster_hud(text, fg_color="#A7F3D0")
        self.log(text, "TASK")

    def show_abort_choices(self):
        base_id = re.sub(r"-fix\d+$", "", self.active_task_id)
        self.original_task_before_abort = base_id
        try:
            self.pending_fix_task = be.get_next_available_fix_task_id(base_id)
        except (OSError, RuntimeError, ValueError) as exc:
            self.abort_state = None
            self.pending_fix_task = ""
            self.hide_host_choices()
            self.lbl_host_status.config(text=f"CHƯA KIỂM TRA ĐƯỢC TASK - CHƯA CHO PHÉP QUAY: {exc}", fg=ModernTheme.ACCENT_RED)
            self.log(f"Không xác định được mã -fix chưa sử dụng: {exc}", "ERROR")
            return
        match = re.search(r"(\d+)$", base_id)
        self.pending_next_task = (base_id[:match.start(1)] + str(int(match.group(1)) + 1).zfill(len(match.group(1)))
                                  if match else base_id + "-next")
        self.abort_state = "AWAITING_CHOICE"
        panel = getattr(self, "frame_host_choices", None)
        if panel is not None:
            panel.pack(fill="x", padx=12, pady=(0, 8))
        if self.tracker:
            self.tracker.pause_hud()
        text = f"F11: CHỌN -FIX [{self.pending_fix_task}] | F10: QUAY TASK TIẾP | F9: CHỌN TASK GỐC"
        self.lbl_host_status.config(text=text, fg=ModernTheme.ACCENT_AMBER)
        self.overlay.show_roster_hud(text, fg_color="#F59E0B")
        self.log(text, "TASK")

    def run_post_capture_validation(self, task_id, token=None):
        if token is None:
            token = self._transition_token
        is_pass, all_ok, error = False, False, None
        duration_seconds = None
        try:
            audit = be.validate_finalized_capture(
                task_id, self.follower,
                should_continue=lambda: self.is_running and token == self._transition_token,
                on_message=lambda line: self.log(line, "AUDIT"))
            if audit.cancelled or not self.is_running or token != self._transition_token:
                return
            is_pass, error = audit.passed, audit.error or None
            duration_seconds = getattr(audit, "duration_seconds", None)
            deadline = time.monotonic() + 15
            while self.is_running and token == self._transition_token and time.monotonic() < deadline:
                self.follower.check_new_lines()
                result = self.follower.last_round_result
                if self.follower.last_all_ok == task_id or (result and result[0] == task_id and "all ok" in str(result[1]).lower()):
                    all_ok = True
                    break
                if result and result[0] == task_id and any(word in str(result[1]).lower() for word in ("failed", "record again", "not done")):
                    error = str(result[1])
                    break
                time.sleep(0.2)
        except Exception as exc:
            error = str(exc)
        if duration_seconds is None:
            self.post_ui(self.finish_validation, token, task_id, is_pass, all_ok, error)
        else:
            self.post_ui(self.finish_validation, token, task_id, is_pass, all_ok, error, duration_seconds)

    def finish_validation(self, token, task_id, is_pass, all_ok, error, duration_seconds=None):
        if token != self._transition_token or not self.is_running:
            return
        if (token, task_id) in self._completed_validation_tokens:
            return
        if duration_seconds is not None:
            self.lbl_timer.config(text=format_recording_clock(duration_seconds, self.target_duration))
            self.log(f"Thời lượng video Odx đã lưu [{task_id}]: {duration_seconds:.3f}s.", "RECORD")
        self.is_validating = False
        self.set_task_controls_enabled(True)
        self.btn_host_start.config(state="normal")
        if is_pass and all_ok and not error:
            if getattr(self, "_round_test_mode", False):
                self._completed_validation_tokens.add((token, task_id))
                self.log(f"[TEST] Kiểm định cục bộ PASS và ALL OK từ {self.expected_players()} máy: [{task_id}]. Không ghi tiến độ sản xuất.", "SUCCESS")
                if self.var_autoincrement.get():
                    base = re.sub(r"-fix\d+$", "", task_id)
                    match = re.search(r"(\d+)$", base)
                    next_task = (base[:match.start(1)] + str(int(match.group(1)) + 1).zfill(len(match.group(1)))
                                 if match else base + "-next")
                    self.select_task_id(next_task)
                self.is_in_cooldown = True
                self.cooldown_tick(token, int(self.config.get("cooldown_between_rounds_seconds", 12)))
                return
            result, next_task = self.record_task_completion(task_id)
            if result == "failed":
                self.lbl_host_status.config(text="CHƯA LƯU ĐƯỢC TIẾN ĐỘ - GIỮ NGUYÊN TASK", fg=ModernTheme.ACCENT_RED)
                return
            self._completed_validation_tokens.add((token, task_id))
            if result == "duplicate":
                self.log(f"Task gốc [{task_id}] đã được tính vào tiến độ; không tăng lần hai.", "PROGRESS")
                return
            self.announced_all_ok.add(task_id)
            self.log(f"PASS cục bộ và ALL OK từ {self.expected_players()} máy: [{task_id}].", "SUCCESS")
            if next_task:
                self.select_task_id(next_task)
            if self.batch_complete():
                self.lbl_host_status.config(text=self.completion_text(), fg=ModernTheme.ACCENT_GREEN)
                self.log(self.completion_text(), "PROGRESS")
                return
            self.is_in_cooldown = True
            self.cooldown_tick(token, int(self.config.get("cooldown_between_rounds_seconds", 12)))
        else:
            detail = error or ("Chưa nhận ALL OK từ các máy trong phòng." if is_pass else "Dữ liệu cục bộ chưa đạt kiểm định.")
            self.lbl_host_status.config(text=f"CHƯA HOÀN TẤT [{task_id}]: {detail}", fg=ModernTheme.ACCENT_AMBER)
            self.log(f"Giữ nguyên task [{task_id}]: {detail}", "WARN")

    def cooldown_tick(self, token, seconds):
        if token != self._transition_token or not self.is_running or not self.is_in_cooldown:
            return
        if seconds <= 0:
            self.is_in_cooldown = False
            self.lbl_host_status.config(text=f"SẴN SÀNG: [{self.active_task_id}] - BẤM F10 ĐỂ QUAY", fg=ModernTheme.ACCENT_GREEN)
            return
        self.lbl_host_status.config(text=f"NGHỈ {seconds}s - TASK TIẾP: [{self.active_task_id}]", fg=ModernTheme.ACCENT_AMBER)
        self.root.after(1000, lambda: self.cooldown_tick(token, seconds - 1))

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
        if self.operation_busy():
            return
        pid = self.ent_p_id.get().strip() if hasattr(self, "ent_p_id") else ""
        pname = self.ent_p_name.get().strip() if hasattr(self, "ent_p_name") else ""
        self.config["player_id"] = pid
        self.config["player_name"] = pname
        self.save_config()
        if self.owned_odx_window():
            be.set_player_info(pid, pname)

    def player_apply_info_to_odx(self):
        if self.operation_busy():
            return
        pid = self.ent_p_id.get().strip() if hasattr(self, "ent_p_id") else ""
        pname = self.ent_p_name.get().strip() if hasattr(self, "ent_p_name") else ""
        self.config["player_id"] = pid
        self.config["player_name"] = pname
        self.save_config()
        if not self.owned_odx_window():
            self.log("Đã lưu thông tin cục bộ; hãy kết nối qua tool trước khi điền vào Odx.", "PLAYER")
            return
        be.set_player_info(pid, pname)
        self.log(f"Đã cập nhật Mã người chơi [{pid}] & Tên [{pname}] vào Odx Capture [OK]!", "PLAYER")
        messagebox.showinfo("Đã điền", f"Đã cập nhật vào Odx Capture:\n- Mã người chơi: {pid}\n- Tên người chơi: {pname}")

    def player_start_connection(self):
        failed = getattr(self, "_player_failed_audit", None)
        if (self.current_mode == "PLAYER" and self.player_connection_active
                and self._player_validation_failed and not self.operation_busy() and failed
                and failed[0] == self._player_connection_generation
                and failed[2] == self._player_round_token):
            self.begin_player_round_validation(*failed)
            return
        if self.player_connection_active and self._player_validation_failed and not self.operation_busy():
            self.cancel_player_ready(cancel_connection=True)
            self._player_connection_generation += 1
            if self.tracker:
                self.tracker.stop()
                self.tracker = None
            self.player_connection_active = False
            self._player_validation_failed = False
        if self.current_mode != "PLAYER" or self.operation_busy() or self.player_connection_active:
            self.log("Đang kết nối hoặc xử lý bản quay; không tạo kết nối trùng.", "WAIT")
            return
        rcode = self.ent_p_room.get().strip().upper()
        if len(rcode) != 6 or not rcode.isascii() or not rcode.isalnum():
            messagebox.showwarning("Mã phòng không hợp lệ", "Mã phòng phải gồm đúng 6 chữ/số ASCII.")
            return
        pid = self.ent_p_id.get().strip()
        pname = self.ent_p_name.get().strip()
        if not pid or not pname:
            messagebox.showwarning("Thiếu thông tin", "Hãy nhập Player ID và tên trước khi kết nối.")
            return
        self.config.update(player_id=pid, player_name=pname)
        if hasattr(self, "ent_sync_host"):
            self.config["sync_host_address"] = self.ent_sync_host.get().strip()
        if not self.save_config():
            return
        self.stop_settings_sync()
        self._player_connection_generation += 1
        generation = self._player_connection_generation
        self._connection_operation_token += 1
        operation_token = self._connection_operation_token
        self.cancel_player_ready(cancel_connection=True)
        cancel_event = threading.Event()
        self._connection_cancel_event = cancel_event
        self.is_connecting = True
        self._player_round_token = 0
        self._player_validation_token = None
        self._player_validation_failed = False
        self._player_failed_audit = None
        self._player_ready_after_round = None
        self.player_connection_active = True
        self.btn_p_connect.config(state="disabled", text="ĐANG KẾT NỐI...")
        self.lbl_p_status.config(text=f"ĐANG KIỂM TRA PHÒNG [{rcode}]...", fg=ModernTheme.ACCENT_AMBER)
        self.follower.reset_to_end()
        round_token = self._player_round_token
        host_address = self.config.get("sync_host_address", "")

        def can_ready():
            return (not cancel_event.is_set() and self.player_ready_request_allowed(
                generation, round_token, pid, operation_token, connecting=True))

        def _status(message, tag):
            if (self.is_running and generation == self._player_connection_generation
                    and not cancel_event.is_set()):
                self.log(message, tag)
                self.post_ui(self.player_connection_status, generation, rcode, message, tag)

        def _worker():
            try:
                data = be.fetch_room_status(rcode)
                if not isinstance(data, dict) or data.get("error"):
                    raise RuntimeError(str(data.get("message") or data.get("error")) if isinstance(data, dict) else "Không nhận được trạng thái phòng")
                if generation != self._player_connection_generation or not self.is_running:
                    return
                if str(data.get("host") or "").strip() == pid:
                    raise RuntimeError(f"Player ID [{pid}] trùng với Host. Hãy nhập Player ID khác cho máy ảo rồi kết nối lại; giữ nguyên Odx đang mở.")
                def current():
                    return (self.is_running and generation == self._player_connection_generation
                            and not cancel_event.is_set())
                test_mode = False
                if getattr(self, "_sync_required", False):
                    host = str(data.get("host") or "")
                    if not host:
                        raise RuntimeError("Phòng chưa xác nhận danh tính Host; hãy thử kết nối lại.")
                    client = PlayerSettingsClient(rcode, host, host_address=host_address or None, timeout=1)
                    settings = None
                    deadline = time.monotonic() + 10
                    while current() and time.monotonic() < deadline:
                        envelope = client.fetch()
                        if envelope is not None:
                            settings = envelope["settings"]
                            break
                        time.sleep(.2)
                    if settings is None:
                        raise RuntimeError("Chưa lấy được cấu hình Host trước khi vào room; kiểm tra ô IP Host và cổng 45455.")
                    test_mode = settings.get("test_mode", False) is True
                if not current():
                    return
                existing = be.find_odx_window()
                if be.find_unmanaged_odx_window() or (existing and not be.is_managed_odx_window(existing)):
                    raise RuntimeError("Odx đang mở bên ngoài tool. Hãy đóng cửa sổ đó rồi kết nối lại qua tool.")
                be.set_player_info(pid, pname)
                be.set_clipboard(rcode)
                if not existing:
                    exe = self.odx_gui_executable()
                    ok, error = be.prepare_capture_mode_launch(test_mode)
                    if not ok:
                        raise RuntimeError(error)
                    if not current():
                        return
                    self.player_proc = be.register_managed_process(subprocess.Popen([exe], cwd=be.ODX_ROOT))
                self.configure_capture_mode_for_room(test_mode, current)
                if not current():
                    return
                self._room_test_mode = test_mode
                self.post_ui(self.apply_player_capture_mode, generation, test_mode)
                self.post_ui(self.install_player_tracking, generation, rcode, pid)
                joining = be.player_auto_join_and_ready(
                    rcode, pid, pname, on_status=_status,
                    cancel_event=cancel_event, can_ready=can_ready)
                if joining is None:
                    raise RuntimeError("Không khởi tạo được luồng kết nối Odx.")
                joining.join()
                if not current():
                    return
                if not getattr(joining, "success", False) or not getattr(joining, "joined", False):
                    raise RuntimeError(getattr(joining, "error", "") or "Odx chưa xác nhận vào đúng phòng; giữ nguyên cửa sổ để thử lại.")
                self.post_ui(self.player_connection_confirmed, generation, rcode,
                             bool(getattr(joining, "ready", False)))
            except Exception as exc:
                self.post_ui(self.player_connection_failed, generation, str(exc))
            finally:
                self.post_ui(self.finish_player_connection_operation, operation_token)
        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            self.player_connection_failed(generation, str(exc))
            self.finish_player_connection_operation(operation_token)

    def apply_player_capture_mode(self, generation, enabled):
        if generation != self._player_connection_generation or self.current_mode != "PLAYER":
            return
        self.config["test_mode"] = enabled
        if hasattr(self, "var_test_mode"):
            self.var_test_mode.set(enabled)
        self.overlay.set_preferences(self.config)
        self.log("[TEST] Theo Host: quay màn hình chính, không cần game."
                 if enabled else "Theo Host: chế độ quay game thông thường.", "PLAYER")

    def player_connection_confirmed(self, generation, room_code, ready):
        if generation != self._player_connection_generation or not self.is_running:
            return
        self.player_connection_active = True
        self.btn_p_connect.config(text=f"ĐÃ VÀO PHÒNG [{room_code}]", state="disabled")
        self.lbl_p_status.config(text=f"ĐÃ XÁC NHẬN VÀO PHÒNG [{room_code}] - " +
                                 ("SẴN SÀNG" if ready else "CHỜ ĐỒNG BỘ CẤU HÌNH VÀ READY"),
                                 fg=ModernTheme.ACCENT_GREEN if ready else ModernTheme.ACCENT_AMBER)

    def finish_player_connection_operation(self, operation_token):
        # Room generation can change on an error before the automation exits.
        # A separate token keeps cleanup blocked until this exact worker ends.
        if operation_token == self._connection_operation_token:
            self.is_connecting = False
            self._connection_cancel_event = None

    def player_connection_status(self, generation, rcode, message, tag):
        if generation != self._player_connection_generation:
            return
        if self.is_player_recording or self.p_counting_down or self.is_validating:
            return
        self.lbl_p_status.config(text=message, fg=ModernTheme.ACCENT_GREEN if tag == "SUCCESS" else ModernTheme.ACCENT_AMBER)
        if tag == "SUCCESS":
            self.btn_p_connect.config(text=f"ĐÃ KẾT NỐI [{rcode}]", state="disabled")
        elif tag == "ERROR":
            self.player_connection_failed(generation, message)

    def player_connection_failed(self, generation, error):
        if generation != self._player_connection_generation:
            return
        self.cancel_player_ready(cancel_connection=True)
        self._player_connection_generation += 1
        self.player_connection_active = False
        self.p_counting_down = False
        self.is_player_recording = False
        self.is_validating = False
        self._player_validation_token = None
        self._player_validation_failed = False
        self._player_failed_audit = None
        self._player_ready_after_round = None
        if self.tracker:
            self.tracker.stop()
            self.tracker = None
        self.btn_p_connect.config(state="normal", text="KẾT NỐI VÀO PHÒNG")
        self.lbl_p_status.config(text=f"KẾT NỐI THẤT BẠI: {error}", fg=ModernTheme.ACCENT_RED)
        self.log(f"Kết nối thất bại: {error} Giữ nguyên cửa sổ Odx; có thể thử lại.", "ERROR")

    def install_player_tracking(self, generation, rcode, pid):
        if generation != self._player_connection_generation:
            return
        if self.tracker:
            self.tracker.stop()
        def _roster(room, host_id, members):
            self.post_ui(self.update_player_roster, generation, host_id, members)
        self.tracker = be.RoomTracker(rcode, overlay=self.overlay, on_change_callback=_roster,
                                      expected_players=self.expected_players())
        self.room_code = rcode
        self.tracker.start()
        threading.Thread(target=self.player_follow_worker, args=(generation, rcode, pid), daemon=True).start()

    def update_player_roster(self, generation, host_id, members):
        if generation != self._player_connection_generation:
            return
        for index, label in enumerate(self.p_slot_labels):
            if index >= len(members):
                label.config(text=f"Slot {index + 1}: Chờ...", fg=ModernTheme.TEXT_MUTED)
                continue
            member = members[index]
            host = str(member.get("player_id")) == str(host_id) or (index == 0 and not host_id)
            ready = not member.get("problem") and (host or member.get("ready"))
            name = member.get("name") or member.get("player_id")
            label.config(text=f"{name}: {'Sẵn sàng' if ready else 'Chờ'}", fg=ModernTheme.ACCENT_GREEN if ready else ModernTheme.ACCENT_AMBER)

    def player_follow_worker(self, generation, rcode, pid):
        recording, preparing, task, started = False, False, "", None
        round_token = 0
        ready_at, warned_timeout = 0, False
        target = 240
        handled_tasks = set()
        while self.is_running and generation == self._player_connection_generation:
            self.follower.check_new_lines()
            actual = self.follower.current_recording
            if actual and not recording and (not preparing or actual != task):
                self.cancel_player_ready(cancel_connection=True)
                preparing, task, started = True, actual, None
                round_token += 1
                warned_timeout = False
                self.post_ui(self.player_countdown_state, generation, task)
            elif self.follower.last_armed and not recording and not preparing:
                self.cancel_player_ready(cancel_connection=True)
                preparing, task, started = True, self.follower.last_armed, None
                round_token += 1
                self.post_ui(self.player_countdown_state, generation, task)
            if preparing:
                if started is None and actual == task:
                    started = be.get_recording_start_time(task)
                self.follower.check_new_lines()
                actual = self.follower.current_recording
                if started is not None and time.monotonic() >= started and actual == task:
                    recording, preparing = True, False
                    target = self.round_target_duration(task)
                    self.follower.last_armed = None
                    self.post_ui(self.player_recording_state, generation, task, True, round_token)
                elif actual == task:
                    remaining = max(0, started - time.monotonic()) if started is not None else None
                    self.overlay.show_pending_recording(task, remaining, is_host=False)
                    self.post_ui(self.player_clock_state, generation, task, started, False)
            stopped = self.follower.current_stopped
            result = self.follower.last_round_result
            ended = (recording or preparing) and (
                (stopped and stopped[0] == task) or (result and result[0] == task and not actual))
            if ended:
                handled_tasks.add(task)
                self.cancel_player_ready(cancel_connection=True)
                stop_at = getattr(self.follower, "current_stopped_at", None)
                elapsed = max(0, (stop_at if stop_at is not None else time.monotonic()) - started) if started is not None else 0
                if started is not None:
                    self.post_ui(self.player_clock_state, generation, task, started, True, elapsed)
                cancelled = elapsed < self.round_target_duration(task) or bool(
                    stopped and stopped[0] == task and any(word in str(stopped[1]).lower() for word in ("cancel", "abort")))
                recording, preparing = False, False
                if self.follower.last_armed == task:
                    self.follower.last_armed = None
                self.post_ui(self.begin_player_round_validation, generation, task, round_token, cancelled)
                if self.follower.current_stopped and self.follower.current_stopped[0] == task:
                    self.follower.current_stopped = None
            elif not recording and not preparing and not actual and not self.follower.last_armed:
                # Busy VMs can deliver arm/start/stop/final in one read. The
                # matching final result is still evidence the round ended;
                # without its start clock we never claim a full PASS.
                terminal_task = stopped[0] if stopped else (
                    result[0] if result and str(result[1]).strip().lower() != "ok" else "")
                shared = getattr(self, "_shared_settings", {})
                known_tasks = {shared.get("task_id"), shared.get("base_task_id"), task}
                if terminal_task and terminal_task in known_tasks and terminal_task not in handled_tasks:
                    task, started = terminal_task, None
                    handled_tasks.add(task)
                    round_token += 1
                    self.cancel_player_ready(cancel_connection=True)
                    self.post_ui(self.begin_player_round_validation, generation, task, round_token, True)
                    self.log(f"Đã nhận kết thúc [{task}] nhưng không có mốc bắt đầu; chờ Host, không tự gắn PASS.", "WAIT")
                    ended = True
                if stopped and self.follower.current_stopped == stopped:
                    self.follower.current_stopped = None
                    if not ended:
                        self.post_ui(self.player_countdown_cancelled, generation)
            if recording:
                elapsed = time.monotonic() - started
                self.overlay.update_recording(task, elapsed, target, blink=(int(elapsed) % 2 == 0), is_host=False)
                self.post_ui(self.player_clock_state, generation, task, started, True, elapsed)
                if elapsed > target + 10 and not warned_timeout:
                    warned_timeout = True
                    self.log("Chưa nhận xác nhận dừng từ Host; chưa tự đánh dấu hoàn tất hoặc bật Ready.", "WARN")
            elif not preparing and not ended and time.monotonic() - ready_at > 4 and not self.p_counting_down and not self.is_validating and not self._player_validation_failed:
                data = self.tracker.latest_data if self.tracker else None
                if data and not data.get("current_session"):
                    mine = next((m for m in data.get("members", []) if str(m.get("player_id")) == pid), None)
                    if mine and not mine.get("ready"):
                        ready_at = time.monotonic()
                        self.post_ui(self.auto_ready_player_next_round)
            time.sleep(0.2)

    def player_clock_state(self, generation, task, started_at, recording, elapsed_snapshot=None):
        if generation != self._player_connection_generation:
            return
        label = getattr(self, "lbl_p_timer", None)
        if label is None:
            return
        if not recording or started_at is None:
            label.config(text="CHỜ ODX GHI THẬT")
            return
        elapsed = max(0, time.monotonic() - started_at) if elapsed_snapshot is None else elapsed_snapshot
        target = self.round_target_duration(task)
        label.config(text=f"{task}: {format_recording_clock(elapsed, target)}")

    def player_countdown_state(self, generation, task):
        if generation == self._player_connection_generation and not self.is_player_recording:
            self.cancel_player_ready(cancel_connection=True)
            self.p_counting_down = True
            if self.tracker:
                self.tracker.pause_hud()
            self.lbl_p_status.config(text=f"ODX ĐANG CHUẨN BỊ [{task}]...", fg=ModernTheme.ACCENT_AMBER)

    def player_countdown_cancelled(self, generation):
        if generation == self._player_connection_generation:
            self.p_counting_down = False
            self.lbl_p_status.config(text="ĐÃ HỦY ĐẾM NGƯỢC - ĐANG CHỜ HOST", fg=ModernTheme.ACCENT_AMBER)

    def player_recording_state(self, generation, task, recording, round_token):
        if generation != self._player_connection_generation:
            return
        if round_token < self._player_round_token:
            return
        self.cancel_player_ready(cancel_connection=True)
        self._player_round_token = round_token
        self.p_counting_down = False
        self.is_player_recording = recording
        if recording:
            self._player_ready_after_round = None
            self._player_failed_audit = None
            self._round_test_mode = self.capture_test_mode()
        if self.tracker:
            self.tracker.pause_hud() if recording else self.tracker.resume_hud()
        self.lbl_p_status.config(text=f"{'ODX ĐÃ XÁC NHẬN QUAY' if recording else 'ĐÃ DỪNG - ĐANG KIỂM ĐỊNH'} [{task}]", fg=ModernTheme.ACCENT_GREEN)
        if not recording:
            self.overlay.clear()
            self.is_validating = True
            self._player_validation_token = (generation, task, round_token)
            self.lbl_p_status.config(text=f"ĐÃ DỪNG [{task}] - CHỜ ODX GHI XONG VÀ KIỂM ĐỊNH", fg=ModernTheme.ACCENT_AMBER)
            self.btn_p_connect.config(state="disabled", text="ĐANG KIỂM ĐỊNH...")

    def begin_player_round_validation(self, generation, task, round_token, cancelled=False):
        # Install the token on the Tk thread before starting its worker. A busy
        # VM may otherwise run the audit before queued UI state has caught up.
        if (not self.is_running or generation != self._player_connection_generation
                or round_token < self._player_round_token):
            return
        if self._player_validation_token == (generation, task, round_token):
            return
        self.player_recording_state(generation, task, False, round_token)
        threading.Thread(target=self.post_player_round,
                         args=(generation, task, round_token, cancelled), daemon=True).start()

    def post_player_round(self, generation, task, round_token, cancelled=False):
        if cancelled:
            self.log(f"Task [{task}] đã kết thúc sớm hoặc bị hủy; chờ lựa chọn từ Host, không tự gắn PASS.", "CANCEL")
            self.post_ui(self.finish_player_validation, generation, task, round_token, True, True)
            return
        def current():
            return (self.is_running and generation == self._player_connection_generation
                    and round_token == self._player_round_token
                    and self._player_validation_token == (generation, task, round_token))
        if not current():
            return
        ok, error = False, ""
        duration_seconds = None
        try:
            audit = be.validate_finalized_capture(task, self.follower, should_continue=current,
                                                on_message=lambda line: self.log(line, "AUDIT"))
            if audit.cancelled or not current():
                return
            ok, error = audit.passed, audit.error
            duration_seconds = getattr(audit, "duration_seconds", None)
            self.log(f"Kiểm định máy con [{'PASS' if ok else 'FAIL'}]: [{task}]", "SUCCESS" if ok else "WARN")
        except Exception as exc:
            error = str(exc)
            self.log(f"Kiểm định máy con lỗi: {exc}", "ERROR")
        if current():
            self.post_ui(self.finish_player_validation, generation, task, round_token, bool(ok), False, error, duration_seconds)

    def finish_player_validation(self, generation, task, round_token, ok, cancelled=False, error="", duration_seconds=None):
        if (generation != self._player_connection_generation or
                self._player_validation_token != (generation, task, round_token) or
                self._player_round_token != round_token):
            return
        self.is_validating = False
        self._player_validation_token = None
        self._player_validation_failed = not ok
        if duration_seconds is not None:
            self.lbl_p_timer.config(text=f"{task}: {format_recording_clock(duration_seconds, self.round_target_duration(task))}")
            self.log(f"Thời lượng video Odx đã lưu [{task}]: {duration_seconds:.3f}s.", "RECORD")
        if ok:
            self._player_failed_audit = None
            self._player_ready_after_round = (generation, round_token, task)
            prefix = "[TEST] " if getattr(self, "_round_test_mode", False) else ""
            self.lbl_p_status.config(text=f"{prefix}{'ĐÃ HỦY' if cancelled else 'PASS CỤC BỘ'} [{task}] - CHỜ HOST", fg=ModernTheme.ACCENT_GREEN)
            self.btn_p_connect.config(state="disabled", text=f"ĐÃ KẾT NỐI [{self.room_code}]")
            self.auto_ready_player_next_round()
        else:
            self._player_ready_after_round = None
            self._player_failed_audit = (generation, task, round_token)
            detail = error or "Dữ liệu chưa đạt; xem chi tiết AUDIT."
            self.lbl_p_status.config(text=f"KIỂM ĐỊNH CHƯA ĐẠT [{task}]: {detail}", fg=ModernTheme.ACCENT_RED)
            self.btn_p_connect.config(state="normal", text="KIỂM ĐỊNH LẠI")

    def auto_ready_player_next_round(self):
        if self.current_mode != "PLAYER" or self.operation_busy() or self._player_validation_failed or getattr(self, "_ready_inflight", False):
            return
        if not self.tracker or not self.tracker.latest_data or self.tracker.latest_data.get("current_session"):
            return
        if not self.owned_odx_window():
            return
        rcode = self.ent_p_room.get().strip().upper()
        player_id = self.ent_p_id.get().strip()
        generation = self._player_connection_generation
        round_token = self._player_round_token
        if not self.valid_room_code(rcode) or not player_id:
            return
        self._ready_inflight = True
        self._ready_operation_token += 1
        operation_token = self._ready_operation_token
        cancel_event = threading.Event()
        self._ready_cancel_event = cancel_event

        def can_ready():
            return (not cancel_event.is_set() and self.player_ready_request_allowed(
                generation, round_token, player_id, operation_token))

        def on_status(message, tag):
            if can_ready():
                self.log(message, tag)

        def _worker():
            result, error = None, ""
            try:
                if not can_ready():
                    return
                if not self.owned_odx_window():
                    raise RuntimeError("Cửa sổ Odx đã đóng hoặc không còn do tool quản lý.")
                result = be.player_ensure_ready_in_room(
                    rcode, player_id=player_id, cancel_event=cancel_event,
                    can_ready=can_ready, on_status=on_status)
                if result is None:
                    raise RuntimeError("Không khởi tạo được luồng xác nhận Ready.")
                result.join()
            except Exception as exc:
                error = str(exc)
                self.log(f"Không thể hoàn tất bật Ready: {exc}", "ERROR")
            finally:
                self.post_ui(self.finish_player_ready_operation, generation, operation_token,
                             round_token, result, error)
        try:
            threading.Thread(target=_worker, daemon=True).start()
        except Exception as exc:
            self.log(f"Không khởi tạo được luồng Ready: {exc}", "ERROR")
            self.finish_player_ready_operation(generation, operation_token)

    def finish_player_ready_operation(self, generation, operation_token,
                                      round_token=None, result=None, error=""):
        if operation_token != self._ready_operation_token:
            return
        self._ready_inflight = False
        self._ready_cancel_event = None
        if (generation != self._player_connection_generation
                or (round_token is not None and round_token != self._player_round_token)
                or not self.is_running or self.current_mode != "PLAYER"
                or self.is_player_recording or self.p_counting_down or self.is_validating):
            return
        status = str(getattr(result, "status", "")) if result is not None else ""
        if status in {"cancelled", "recording", "busy"}:
            return
        if result is not None and getattr(result, "success", False) is True:
            self.lbl_p_status.config(text="SẴN SÀNG ĐÃ ĐƯỢC MÁY CHỦ XÁC NHẬN - CHỜ HOST", fg=ModernTheme.ACCENT_GREEN)
        else:
            detail = error or str(getattr(result, "error", "") or "Chưa có xác nhận Ready từ máy chủ.")
            self.lbl_p_status.config(text=f"CHƯA XÁC NHẬN READY: {detail}", fg=ModernTheme.ACCENT_AMBER)

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
        if self.operation_busy():
            self.log("Hãy chờ thao tác hiện tại hoàn tất trước khi kiểm định.", "WAIT")
            return
        target = self.ent_audit_path.get().strip()
        if not target or not os.path.isdir(target):
            messagebox.showwarning("Thư mục không tồn tại", "Vui lòng chọn thư mục capture hợp lệ!")
            return
        self.is_validating = True
        self.log(f"Bắt đầu kiểm định: [{os.path.basename(target)}]", "AUDIT")
        def _worker():
            try:
                ok = be.validate_and_inspect_capture(target, is_room_mode=True, on_message=lambda line: self.log(line, "AUDIT"))
                self.post_ui(_finish, ok, "")
            except Exception as exc:
                self.post_ui(_finish, False, str(exc))
        def _finish(ok, error):
            self.is_validating = False
            self.log(f"Kiểm định [{'PASS' if ok else 'FAIL'}]: {target} {error}", "SUCCESS" if ok else "ERROR")
            messagebox.showinfo("Kết quả kiểm định", "PASS" if ok else f"FAIL. {error} Xem nhật ký kiểm định để biết chi tiết.")
        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------------
    # GLOBAL HOTKEYS & TIMER UPDATES
    # ------------------------------------------------------------------
    def ensure_host_hotkey_priority(self, force=False):
        if not getattr(self, "_hotkey_precedence_required", False):
            return True
        if self.current_mode != "HOST":
            return True
        hwnd = self.owned_odx_window()
        if not hwnd:
            return False
        pid, _ = be._window_process_path(hwnd)
        if not pid:
            return False
        target = (int(hwnd), int(pid))
        if not force and self._hotkey_owner_identity == target and self.hotkeys_ready:
            return True
        if not force and (self._hotkey_refresh_target == target or time.monotonic() < self._hotkey_refresh_retry_at):
            return False
        thread_id = self._hook_thread_id
        if not thread_id:
            return False
        self._hotkey_refresh_generation += 1
        generation = self._hotkey_refresh_generation
        self._hotkey_refresh_target = target
        self.hotkeys_ready = False
        api = ctypes.windll.user32
        api.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        api.PostThreadMessageW.restype = wintypes.BOOL
        if not api.PostThreadMessageW(thread_id, 0x8001, generation, 0):
            self.finish_hotkey_refresh(generation, *target, False, "Không gửi được yêu cầu giành lại F10.")
        return False

    def finish_hotkey_refresh(self, generation, hwnd, pid, success, error=""):
        if (not self.is_running or generation != self._hotkey_refresh_generation
                or self._hotkey_refresh_target != (hwnd, pid)):
            return
        self._hotkey_refresh_target = None
        self.hotkeys_ready = bool(success)
        self._hotkey_owner_identity = (hwnd, pid) if success else None
        self._hotkey_refresh_retry_at = 0 if success else time.monotonic() + 2
        self.log("Đã đặt bộ xử lý F10 của Auto-Pilot trước Odx." if success else f"Chưa bảo vệ được F10: {error}",
                 "HOTKEY" if success else "ERROR")

    def start_hotkey_listener(self):
        """Own physical HOST keys; injected commands continue to Odx unchanged."""
        def _keyboard_worker():
            api = ctypes.windll.user32
            kernel = ctypes.windll.kernel32
            hook = None
            LRESULT = ctypes.c_ssize_t
            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                            ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_size_t)]
            api.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            api.SetWindowsHookExW.restype = wintypes.HANDLE
            api.CallNextHookEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            api.CallNextHookEx.restype = LRESULT
            api.UnhookWindowsHookEx.argtypes = [wintypes.HANDLE]
            api.UnhookWindowsHookEx.restype = wintypes.BOOL
            api.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            api.GetMessageW.restype = wintypes.BOOL
            api.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            api.TranslateMessage.restype = wintypes.BOOL
            api.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            api.DispatchMessageW.restype = LRESULT
            api.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            api.PostThreadMessageW.restype = wintypes.BOOL
            kernel.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
            kernel.GetModuleHandleW.restype = wintypes.HMODULE
            kernel.GetCurrentThreadId.restype = wintypes.DWORD
            pressed = set()
            def _callback(code, message, pointer):
                try:
                    if code >= 0:
                        key = ctypes.cast(pointer, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                        if self.should_capture_hotkey(key.vkCode, bool(key.flags & 0x10)):
                            if message in (0x0100, 0x0104):
                                pressed.add(key.vkCode)
                                return 1
                            if message in (0x0101, 0x0105):
                                if key.vkCode in pressed:
                                    pressed.discard(key.vkCode)
                                    self.ui_queue.put((self.handle_host_hotkey, (key.vkCode,), {}))
                                return 1
                except Exception:
                    pass
                return api.CallNextHookEx(hook, code, message, pointer)
            try:
                self._hook_thread_id = kernel.GetCurrentThreadId()
                self._keyboard_hook_callback = HOOKPROC(_callback)
                hook = api.SetWindowsHookExW(13, self._keyboard_hook_callback, kernel.GetModuleHandleW(None), 0)
                if not hook:
                    raise ctypes.WinError()
                self._keyboard_hook = hook
                self.hotkeys_ready = True
                msg = wintypes.MSG()
                while self.is_running:
                    status = api.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if status <= 0:
                        break
                    if msg.message == 0x8001:
                        generation = int(msg.wParam)
                        target = self._hotkey_refresh_target
                        if generation != self._hotkey_refresh_generation or target is None:
                            continue
                        try:
                            replacement = replace_keyboard_hook(api, self._keyboard_hook_callback,
                                                                kernel.GetModuleHandleW(None), hook)
                        except OSError as exc:
                            extra = getattr(exc, "replacement_handle", None)
                            if extra:
                                api.UnhookWindowsHookEx(extra)
                            replacement = None
                        if replacement:
                            hook = replacement
                            self._keyboard_hook = hook
                        self.post_ui(self.finish_hotkey_refresh, generation, *target, bool(replacement),
                                     "Không cài lại được bộ xử lý phím.")
                        continue
                    api.TranslateMessage(ctypes.byref(msg))
                    api.DispatchMessageW(ctypes.byref(msg))
            except Exception as exc:
                self.post_ui(self.hotkey_install_failed, str(exc))
            finally:
                self.hotkeys_ready = False
                if hook:
                    api.UnhookWindowsHookEx(hook)
                self._keyboard_hook = None
                self._hook_thread_id = None
        threading.Thread(target=_keyboard_worker, daemon=True).start()

        def _mouse_worker():
            while self.is_running:
                if user32.GetAsyncKeyState(0x05) & 0x8000 or user32.GetAsyncKeyState(0x06) & 0x8000:
                    now = time.monotonic()
                    if now - getattr(self, "last_side_warn_time", 0) > 1.2:
                        self.last_side_warn_time = now
                        name = "XBUTTON 1" if user32.GetAsyncKeyState(0x05) & 0x8000 else "XBUTTON 2"
                        self.post_ui(self.on_side_mouse_button_pressed, name)
                time.sleep(0.03)
        threading.Thread(target=_mouse_worker, daemon=True).start()

    def should_capture_hotkey(self, key, injected=False):
        if not self.is_running or injected:
            return False
        if self.current_mode == "HOST":
            return ((self.odx_opened or self.is_host_opening)
                    and key in (0x79, 0x7A, 0x78, 0x77))
        return (self.current_mode == "PLAYER" and key == 0x7A
                and (self.player_connection_active or self.is_connecting
                     or self.is_player_recording or self.p_counting_down))

    def hotkey_install_failed(self, error):
        self.log(f"Không cài được bộ bảo vệ phím nóng: {error}", "ERROR")
        messagebox.showerror("Lỗi phím nóng", "Không thể chặn phím F10/F11 của Odx. Chế độ Host sẽ chưa được mở. Hãy khởi động lại tool cùng quyền với Odx.")

    def handle_host_hotkey(self, key):
        if self.current_mode != "HOST":
            return
        if self.is_host_opening:
            self.log("Đang mở Odx và tạo phòng; hãy chờ mã room được xác nhận trước khi dùng phím nóng.", "WAIT")
            return
        if not self.odx_opened:
            self.log("Hãy mở Odx Capture trước khi dùng phím nóng.", "WARN")
            return
        self.log(f"Auto-Pilot nhận {'F10' if key == 0x79 else 'F11' if key == 0x7A else 'phím chọn task'}; task [{self.active_task_id}].", "HOTKEY")
        if key == 0x79:
            if self._pending_sync_start_task:
                self.host_trigger_emergency_stop()
            elif self.is_stopping or self.is_validating:
                self.log("Đang kết thúc hoặc kiểm định dữ liệu; F10 không phát thêm lệnh. Hãy chờ hoàn tất.", "WAIT")
            elif self.capture_state_unknown or self._stop_confirmation_failed:
                self.log("Chưa xác nhận trạng thái Odx; không gửi lại F10. Hãy kiểm tra Odx.", "ERROR")
            elif self.abort_state == "AWAITING_CHOICE":
                self.host_choose_option("NEXT")
                self.host_trigger_start()
            elif self.is_recording or self.is_counting_down:
                self.host_trigger_emergency_stop()
            else:
                self.host_trigger_start()
        elif key == 0x7A:
            if self.abort_state == "AWAITING_CHOICE":
                self.host_choose_option("FIX")
            else:
                self.log("F11 chỉ chọn mã task -fix; bấm F10 sau khi chọn mới bắt đầu quay.", "WAIT")
        elif key in (0x78, 0x77):
            if self.abort_state == "AWAITING_CHOICE":
                self.host_choose_option("CANCEL")
            else:
                self.host_cancel_and_reset()

    def on_side_mouse_button_pressed(self, btn_name):
        """Cảnh báo bằng HUD để không đưa tiếng beep vào bản quay."""
        if self.is_recording or self.is_player_recording:
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
        if not self.is_running:
            return
        if self.current_mode == "HOST" and (self.odx_opened or self.is_host_opening):
            self.ensure_host_hotkey_priority()
        self.follower.check_new_lines()
        if self.current_mode == "HOST":
            actual = self.follower.current_recording
            if (actual and not self.is_recording and not self.is_counting_down
                    and not self.is_stopping and not self._pending_stop_action):
                self.log(f"Odx bắt đầu [{actual}] ngoài lệnh quay của Auto-Pilot; đang đồng bộ trạng thái.", "WARN")
                self.active_task_id = actual
                self.is_counting_down = True
                self._start_command_sent = True
                self._transition_token += 1
                self.set_task_controls_enabled(False)
                if self.tracker:
                    self.tracker.pause_hud()
                threading.Thread(target=self.watch_start, args=(self._transition_token, actual), daemon=True).start()
            stopped = self.follower.current_stopped
            task_id = self._pending_stop_task or self.active_task_id
            matching_stop = stopped and stopped[0] == task_id
            finished = self.has_round_finished(task_id)
            if (not self.is_stopping and (self.is_recording or self.is_counting_down or self._pending_stop_action)
                    and (matching_stop or finished)):
                cancelled = matching_stop and "cancel" in str(stopped[1]).lower()
                early = self.is_counting_down or self.rec_start_time <= 0 or self.capture_elapsed() < self.target_duration
                action = self._pending_stop_action or ("emergency" if cancelled or early else "validate")
                if (matching_stop and not finished and not self._pending_stop_action
                        and not self._stop_confirmation_failed):
                    # A local recorder can stop before the room/other Players.
                    # The scoped backend verifies whether EndRound is still
                    # required; it never toggles a room which already ended.
                    self.request_host_stop(action)
                    self.root.after(200, self.update_live_timer)
                    return
                if action in ("emergency", "reset", "close") and not finished:
                    self._pending_stop_action = action
                    self._pending_stop_task = task_id
                    self.is_counting_down = False
                    self.overlay.show_roster_hud(f"[{task_id}] DANG CHO ODX KET THUC...", fg_color="#F59E0B")
                    self.root.after(200, self.update_live_timer)
                    return
                self.is_stopping = True
                self._transition_token += 1
                self.finish_host_stop(self._transition_token, task_id, True, action)
            if self.is_counting_down and not matching_stop and not self.is_stopping:
                anchor = self._scheduled_start_at
                if anchor is not None and self._scheduled_start_task == actual and time.monotonic() >= anchor:
                    self.begin_confirmed_recording(actual, anchor)
                else:
                    self.show_host_pending_clock(self.active_task_id, anchor)
        if self.is_recording and self.rec_start_time > 0:
            elapsed = self.capture_elapsed()
            self.lbl_timer.config(text=format_recording_clock(elapsed, self.target_duration))
            active = self.follower.current_recording == self.active_task_id
            if not active:
                self.overlay.show_roster_hud(f"[{self.active_task_id}] DANG CHO ODX KET THUC...", fg_color="#F59E0B")
            elif time.time() < getattr(self, "mouse_warning_until", 0):
                self.overlay.show_roster_hud(self.mouse_warning_text, fg_color="#EF4444")
            else:
                self.overlay.update_recording(self.active_task_id, elapsed, self.target_duration, blink=(int(elapsed) % 2 == 0), is_host=True)
            if active:
                self.prepare_host_stop_if_needed(elapsed)
            if (elapsed >= self.target_duration and not self.is_stopping
                    and active and not self.capture_state_unknown and not self._stop_confirmation_failed):
                self.request_host_stop("validate")
        self.root.after(200, self.update_live_timer)

    # ------------------------------------------------------------------
    # UTILITY ACTIONS & SAFE WINDOW EXIT
    # ------------------------------------------------------------------
    def run_clean_all(self):
        """Compatibility callback: refresh connection without closing Odx."""
        if self.operation_busy():
            messagebox.showwarning("Đang xử lý dữ liệu", "Hãy chờ thao tác hiện tại hoàn tất trước khi làm mới kết nối. Odx được giữ nguyên.")
            return
        if self.follower.current_recording or self.follower.last_armed or (self.tracker and
                (self.tracker.latest_data or {}).get("current_session")):
            self.log("Odx đang quay hoặc xử lý round; giữ nguyên phiên kết nối.", "WAIT")
            return
        self.log("Làm mới kết nối trong cửa sổ Odx hiện tại; không đóng tiến trình.", "CONNECT")
        if self.current_mode == "HOST":
            self.host_open_odx()
        else:
            self.cancel_player_ready(cancel_connection=True)
            self._player_connection_generation += 1
            self.player_connection_active = False
            self.stop_settings_sync()
            if self.tracker:
                self.tracker.stop()
                self.tracker = None
            self.player_start_connection()

    def run_sync_ntp(self):
        if self.operation_busy():
            self.log("Hãy chờ quay/lưu dữ liệu hoàn tất trước khi đồng bộ đồng hồ.", "WAIT")
            return
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
            pid = self.ent_p_id.get().strip() if hasattr(self, 'ent_p_id') else ""
            pname = self.ent_p_name.get().strip() if hasattr(self, 'ent_p_name') else ""
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
        if self.operation_busy():
            if not silent_if_no_update:
                messagebox.showwarning("Đang bận", "Hãy hoàn tất quay/lưu dữ liệu trước khi cập nhật.")
            return
        def _on_result(has_update, remote_info, error):
            self.post_ui(_show_result, has_update, remote_info, error)
        def _show_result(has_update, remote_info, error):
            if self.operation_busy():
                return
            if error:
                if not silent_if_no_update:
                    messagebox.showwarning("Kiểm tra cập nhật", str(error))
                return
            current = get_current_version_info().get("version", "")
            if not has_update or not remote_info:
                if not silent_if_no_update:
                    messagebox.showinfo("Cập nhật", f"Đang sử dụng phiên bản mới nhất (v{current}).")
                return
            version = remote_info.get("version", "")
            if not messagebox.askyesno("Cập nhật", f"Cài đặt v{version}?\n\n{remote_info.get('changelog', '')}"):
                return
            self.is_updating = True
            self.set_task_controls_enabled(False)
            def _worker():
                try:
                    ok, error = download_and_apply_update(
                        remote_info.get("download_url", ""),
                        on_progress=lambda message, percent: self.log(message, "UPDATE"),
                        expected_sha256=remote_info.get("sha256") or remote_info.get("download_sha256"),
                        expected_version=version)
                    self.post_ui(_finish, ok, error)
                except Exception as exc:
                    self.post_ui(_finish, False, str(exc))
            threading.Thread(target=_worker, daemon=True).start()
        def _finish(ok, error):
            self.is_updating = False
            if ok:
                self.log("Đã chuẩn bị cập nhật; đang đóng tool an toàn.", "UPDATE")
                self.shutdown_now()
            else:
                self.set_task_controls_enabled(True)
                messagebox.showerror("Lỗi cập nhật", str(error))
        check_for_updates_async(_on_result)

    def show_update_status(self):
        path = os.path.join(be.TOOL_DIR, "update_status.json")
        if not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as stream:
                status = json.load(stream)
            state = str(status.get("state", ""))
            message = str(status.get("message", ""))
            self.log(f"Cập nhật [{state}]: {message}", "UPDATE")
            if "failed" in state:
                messagebox.showwarning("Trạng thái cập nhật", message)
        except (OSError, ValueError) as exc:
            self.log(f"Không đọc được trạng thái cập nhật: {exc}", "WARN")

    def on_close_window(self):
        if self._closing:
            return
        if self.is_host_opening:
            self.shutdown_now()
            return
        if (self.is_connecting or self._ready_inflight) and not (self.is_player_recording or self.p_counting_down):
            self.shutdown_now()
            return
        if self._stop_confirmation_failed and not self.is_stopping:
            if messagebox.askyesno("BUỘC ĐÓNG ODX DO TOOL QUẢN LÝ", "Chưa xác nhận kết thúc; có thể mất bản quay hiện tại.\n\nBuộc đóng tool và chỉ dừng các tiến trình Odx do tool quản lý?\n\nChọn Không để giữ nguyên và chờ Odx xác nhận."):
                self.shutdown_now()
            return
        if (self.is_stopping or self.is_validating or self.is_updating
                or self.is_connecting or self._ready_inflight):
            messagebox.showinfo("Đang lưu/xử lý", "Hãy chờ thao tác hoàn tất rồi đóng tool.")
            return
        if self.is_player_recording or self.p_counting_down:
            messagebox.showwarning("Máy con đang quay", "Hãy chờ Host dừng và lưu bản quay trước khi đóng tool.")
            return
        if self.is_recording or self.is_counting_down or getattr(self, "capture_state_unknown", False):
            if messagebox.askyesno("Dừng và đóng", "Đang ghi hình. Dừng, lưu dữ liệu rồi đóng tool?"):
                self._closing = True
                self.request_host_stop("close")
            return
        self.shutdown_now()

    def shutdown_now(self):
        if getattr(self, "_shutdown_complete", False):
            return
        self._shutdown_complete = True
        self._closing = True
        self.is_running = False
        self._pending_sync_start_task = None
        self._transition_token += 1
        self._player_connection_generation += 1
        preferences = getattr(self, "window_preferences", None)
        actions = [self.cancel_prepared_host_stop, self.stop_settings_sync, self.cancel_host_room_creation,
                   lambda: self.cancel_player_ready(cancel_connection=True)]
        if preferences is not None:
            actions.insert(0, preferences.close)
        if self.tracker:
            actions.append(self.tracker.stop)
        if self._hook_thread_id:
            actions.append(lambda: ctypes.windll.user32.PostThreadMessageW(self._hook_thread_id, 0x0012, 0, 0))
        if self.overlay:
            actions.append(self.overlay.close)
        for action in actions:
            try:
                action()
            except Exception:
                # One failed callback must not prevent cancelling the others
                # or final process cleanup when the user actually closes Tool.
                pass
        try:
            be.cleanup_all_odx(kill_gui=True, tool_closing=True)
        finally:
            self.root.destroy()

def main():
    from odx_instance import SingleInstance
    instance = SingleInstance()
    try:
        acquired = instance.acquire()
    except OSError as exc:
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showerror("Không thể khởi động", f"Không tạo được khóa phiên chạy: {exc}", parent=root)
        finally:
            root.destroy()
        return 1
    if not acquired:
        root = tk.Tk()
        root.withdraw()
        try:
            messagebox.showinfo("Tool đang chạy", "ODX Auto-Pilot đã được mở. Hãy chuyển sang cửa sổ hiện có.", parent=root)
        finally:
            root.destroy()
        return 1
    try:
        be.setup_process_protection()
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
        initial_mode = "host" if "--host" in sys.argv else ("player" if "--player" in sys.argv else None)
        root = tk.Tk()
        app = OdxAutoPilotGUI(root, initial_mode=initial_mode)
        root.mainloop()
        return 0
    finally:
        instance.release()

if __name__ == "__main__":
    raise SystemExit(main())

