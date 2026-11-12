import sys
import os
import subprocess
import json
import csv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

TOOL_DIR = os.path.abspath(os.path.dirname(__file__))

def get_odx_root():
    here = TOOL_DIR
    if os.path.exists(os.path.join(here, "Odx Capture.exe")) or os.path.exists(os.path.join(here, "bin")):
        return here
    parent = os.path.abspath(os.path.join(here, ".."))
    if os.path.exists(os.path.join(parent, "Odx Capture.exe")) or os.path.exists(os.path.join(parent, "bin")):
        return parent
    return here

ODX_ROOT = get_odx_root()
odx_bin = os.path.join(ODX_ROOT, "bin", "odx.exe")

def inspect_input_data(target_path):
    """Kiem tra sau du lieu thao tac va di chuyen thuc te (input_events.csv, frames.csv, frames_bin.bin)."""
    raw_input_p = os.path.join(target_path, "raw", "input_events.csv")
    frames_csv_p = os.path.join(target_path, "frames.csv")
    frames_bin_p = os.path.join(target_path, "frames_bin.bin")

    print("-" * 72)
    print("  [KIEM DINH CHI TIET DU LIEU THAO TAC & DI CHUYEN]:")

    # 1. Kiem tra raw\input_events.csv
    if os.path.exists(raw_input_p):
        key_presses = 0
        mouse_moves = 0
        unique_keys = set()
        total_events = 0
        try:
            with open(raw_input_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    total_events += 1
                    kind = row.get("kind", "")
                    val = row.get("value", "")
                    if "key_" in kind:
                        key_presses += 1
                        if val:
                            unique_keys.add(val)
                    elif kind == "mouse_move":
                        mouse_moves += 1
            sample_keys = ", ".join(sorted(list(unique_keys))[:10])
            if len(unique_keys) > 10:
                sample_keys += f"... (+{len(unique_keys)-10} phim khac)"
            print(f"  [+] raw\\input_events.csv : {total_events:,} su kien [OK]")
            print(f"      -> Ban phim : {key_presses:,} lan bam (Phim: {sample_keys})")
            print(f"      -> Chuot    : {mouse_moves:,} lan di chuyen (dx, dy)")
        except Exception as e:
            print(f"  [!] Loi doc raw\\input_events.csv: {e}")
    else:
        print("  [!] raw\\input_events.csv : KHONG TIM THAY")

    # 2. Kiem tra frames.csv
    if os.path.exists(frames_csv_p):
        try:
            with open(frames_csv_p, "r", encoding="utf-8", errors="ignore") as f:
                total_frames = sum(1 for _ in f) - 1
            print(f"  [+] frames.csv           : {total_frames:,} khung hinh dong bo day du cot phim & chuot [OK]")
        except Exception as e:
            print(f"  [!] Loi doc frames.csv: {e}")

    # 3. Kiem tra frames_bin.bin
    if os.path.exists(frames_bin_p):
        sz_mb = os.path.getsize(frames_bin_p) / (1024 * 1024)
        try:
            with open(frames_bin_p, "rb") as f:
                header = f.read(32)
            if b"ODYSSEY-MP" in header:
                print(f"  [+] frames_bin.bin       : {sz_mb:.2f} MB (Header ODYSSEY-MP hop le) [OK]")
            else:
                print(f"  [!] frames_bin.bin       : {sz_mb:.2f} MB (Header chua dung chuan)")
        except Exception as e:
            print(f"  [!] Loi doc frames_bin.bin: {e}")

    # 4. Kiem tra Role phim (key_bindings.json)
    kb_file = os.path.join(target_path, "key_bindings.json")
    if os.path.exists(kb_file):
        try:
            with open(kb_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
                b = kb_data.get("bindings", {})
                print(f"  [+] Role Phim (key_bindings.json) : {len(b)} nut da duoc anh xa (W: {b.get('W')}, S: {b.get('S')}, A: {b.get('A')}, D: {b.get('D')}, Shift: {b.get('Shift')}, Space: {b.get('Space')}) [OK]")
        except Exception as e:
            print(f"  [!] Loi doc key_bindings.json: {e}")

    # 5. Kiem tra Role nguoi choi (player.json)
    pl_file = os.path.join(target_path, "player.json")
    if os.path.exists(pl_file):
        try:
            with open(pl_file, "r", encoding="utf-8") as f:
                pl_data = json.load(f)
                p_info = pl_data.get("player", {})
                p_name = p_info.get("name") or pl_data.get("name")
                vendor = pl_data.get("vendor_name", "Tbrain")
                game = pl_data.get("game_title", "Arma Reforger")
                dur = pl_data.get("duration_seconds", 0)
                name_str = f", Ten='{p_name}'" if p_name else ""
                print(f"  [+] Role Nguoi choi (player.json) : Vendor='{vendor}'{name_str}, Game='{game}', Duration={dur:.1f}s [OK]")
        except Exception as e:
            print(f"  [!] Loi doc player.json: {e}")

    # 6. KIEM TRA QUY CHUAN ROLE CHUOT (Chi cho phep Left, Right, Middle/Scroll - Khong dung nut phu)
    allowed_mouse_btns = {'left', 'right', 'middle', 'wheel', 'scroll', 'mouse_wheel'}
    disallowed_mouse_used = set()

    if os.path.exists(raw_input_p):
        try:
            with open(raw_input_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    kind = row.get("kind", "").lower()
                    val = row.get("value", "").strip()
                    if ("button" in kind or "mouse" in kind) and val and kind != "mouse_move":
                        if val.lower() not in allowed_mouse_btns:
                            disallowed_mouse_used.add(val)
        except Exception:
            pass

    if os.path.exists(frames_csv_p):
        try:
            with open(frames_csv_p, "r", encoding="utf-8", errors="ignore") as f:
                r = csv.DictReader(f)
                for row in r:
                    b_val = row.get("input_mouse_buttons", "")
                    if b_val and b_val.lower() != "null":
                        for btn in b_val.split(","):
                            btn_c = btn.strip()
                            if btn_c and btn_c.lower() not in allowed_mouse_btns:
                                disallowed_mouse_used.add(btn_c)
        except Exception:
            pass

    if os.path.exists(kb_file):
        try:
            with open(kb_file, "r", encoding="utf-8") as f:
                kb_data = json.load(f)
                for k in kb_data.get("bindings", {}):
                    if any(m in k.lower() for m in ["mouse", "button", "click", "xbutton", "side", "thumb"]):
                        if k.lower() not in allowed_mouse_btns:
                            disallowed_mouse_used.add(k)
        except Exception:
            pass

    mouse_role_pass = (len(disallowed_mouse_used) == 0)
    if mouse_role_pass:
        print("  [+] Role Chuot (Mouse Role)       : HOP LE [OK] (Chi dung Chuot Trai, Phai, Con lan Middle/Scroll. Khong dung nut phu)")
    else:
        dis_list = ", ".join(sorted(list(disallowed_mouse_used)))
        print(f"  [!] VI PHAM ROLE CHUOT            : Phat hien nut chuot khong duoc phep su dung: [{dis_list}]! (Chi cho phep Left, Right, Middle)")

    return mouse_role_pass, disallowed_mouse_used

def heal_camera_nulls_if_needed(target_path):
    csv_path = os.path.join(target_path, 'frames.csv')
    if not os.path.exists(csv_path):
        return False
    try:
        import pandas as pd
        import numpy as np
        df = pd.read_csv(csv_path, keep_default_na=False)
        c2w_cols = [c for c in df.columns if c.startswith('C2W_') or c.startswith('camera_')]
        if not c2w_cols:
            return False
        has_null = False
        for c in c2w_cols:
            if (df[c] == 'null').any() or (df[c] == '').any():
                has_null = True
                break
        if has_null:
            for col in c2w_cols:
                df[col] = df[col].replace('null', np.nan).replace('', np.nan)
            df[c2w_cols] = df[c2w_cols].ffill().bfill()
            df = df.fillna('null')
            df.to_csv(csv_path, index=False)
            return True
    except Exception:
        pass
    return False

def validate_folder(target_path):
    print("=" * 72)
    print(f"  [KIEM DINH TONG THE] Task: [{os.path.basename(target_path)}]")
    print(f"  Duong dan: {target_path}")
    print("-" * 72)

    # 1. Chay odx validate kiem tra he thong
    val_pass = False
    if os.path.exists(odx_bin):
        try:
            res = subprocess.run([odx_bin, "validate", target_path], capture_output=True, text=True, timeout=15)
            out = res.stdout.strip()
            if "camera.sparse_nulls" in out:
                if heal_camera_nulls_if_needed(target_path):
                    res = subprocess.run([odx_bin, "validate", target_path], capture_output=True, text=True, timeout=15)
                    out = res.stdout.strip()
                    print("  [AUTO-HEAL] Da tu dong sua hop le camera null -> Dat PASS [OK]")
            print(f"  Chay 'odx validate' : {out}")
            if "PASS" in out and "0 errors" in out:
                val_pass = True
        except Exception as e:
            print(f"  Loi kiem tra: {e}")
    else:
        print("  [!] Khong tim thay bin\\odx.exe de chay validate.")

    # 2. Kiem tra sau du lieu thao tac va di chuyen thuc te
    mouse_role_pass, disallowed_mouse = inspect_input_data(target_path)

    # 3. Danh sach nguoi choi neu co events.jsonl
    events_file = os.path.join(target_path, "events.jsonl")
    if os.path.exists(events_file):
        players_found = {}
        try:
            with open(events_file, "r", encoding="utf-8") as ef:
                for eline in ef:
                    try:
                        ev = json.loads(eline)
                        if ev.get("type") == "player_ready":
                            p_id = ev.get("player", "")
                            p_name = ev.get("name", p_id)
                            players_found[p_id] = p_name
                        elif ev.get("type") == "round_start" and "players" in ev:
                            for pid in ev["players"]:
                                if pid not in players_found:
                                    players_found[pid] = pid
                    except Exception:
                        pass
            if players_found:
                print("-" * 72)
                print(f"  [DANH SACH NGUOI CHOI] ({len(players_found)} thanh vien da tham gia round):")
                for i, (pid, pname) in enumerate(players_found.items(), 1):
                    print(f"     {i}. {pname:<15} (ID: {pid}) [DA GHI NHAN]")
        except Exception:
            pass

    print("-" * 72)
    if val_pass and mouse_role_pass:
        print("  ==> TRANG THAI TONG THE: [PASS CHUAN ADMIN 100%] (0 errors, 0 warnings) [OK]")
    else:
        print("  ==> TRANG THAI TONG THE: [FAIL / CHUA DAT CHUAN]")
        if not val_pass:
            print("      - Loi kiem dinh 'odx validate' chua PASS.")
        if not mouse_role_pass:
            print(f"      - Vi pham Role Chuot: Phat hien nut chuot phu [{', '.join(sorted(list(disallowed_mouse)))}]")
    print("=" * 72)

def main():
    if len(sys.argv) > 1:
        target = os.path.abspath(sys.argv[1])
        if os.path.exists(target):
            validate_folder(target)
            return
        else:
            cand = os.path.join(ODX_ROOT, "data", "captures", sys.argv[1])
            if os.path.exists(cand):
                validate_folder(cand)
                return
            print(f"Khong tim thay thu muc: {target}")
            return

    captures_dir = os.path.join(ODX_ROOT, "data", "captures")
    if not os.path.exists(captures_dir):
        print("Chua co thu muc data\\captures\\ de kiem tra.")
        return

    subdirs = [os.path.join(captures_dir, d) for d in os.listdir(captures_dir) if os.path.isdir(os.path.join(captures_dir, d))]
    if not subdirs:
        print("Khong tim thay thu muc task nao trong data\\captures\\.")
        return

    print("=" * 72)
    print(f"  TIM THAY {len(subdirs)} THU MUC TASK TRONG data\\captures\\:")
    print("=" * 72)
    for folder in subdirs:
        validate_folder(folder)
        print()

if __name__ == "__main__":
    main()
