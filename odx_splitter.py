"""
ODX CAPTURE SPLITTER: CẮT 1 PHIÊN QUAY DÀI THÀNH 15 FOLDER RIÊNG BIỆT (MỖI FOLDER 4 PHÚT)
Tự động cắt Video, Audio, Input Events, Video Frames, Camera Pose và Build ra frames_bin.bin + session.json chuẩn
"""

import sys
import os
import time
import subprocess
import json
import shutil
import csv

def log(msg, tag="SPLIT"):
    print(f"[{tag}] {msg}", flush=True)

def get_ffmpeg_path():
    p = os.path.join(os.path.dirname(__file__), "bin", "ffmpeg", "ffmpeg.exe")
    if os.path.exists(p):
        return p
    return "ffmpeg"

def get_odx_path():
    p = os.path.join(os.path.dirname(__file__), "bin", "odx.exe")
    return p

def split_session(src_dir, output_root=None, prefix="asm-s221-", start_idx=1, total_slices=15, duration_s=240):
    if not output_root:
        output_root = os.path.join(os.path.dirname(__file__), "data", "captures")
    
    ffmpeg = get_ffmpeg_path()
    odx_exe = get_odx_path()
    profile_path = os.path.join(os.path.dirname(__file__), "bin", "games", "arma-reforger.json")

    print("=" * 70)
    print(f"          BẮT ĐẦU CẮT PHIÊN QUAY THÀNH {total_slices} FOLDER")
    print(f"  Nguồn: {src_dir}")
    print(f"  Thời lượng mỗi phần: {duration_s} giây ({duration_s/60:.1f} phút)")
    print(f"  Tiền tố: {prefix}{str(start_idx).zfill(2)} -> {prefix}{str(start_idx+total_slices-1).zfill(2)}")
    print("=" * 70)

    # Detect raw folder location
    raw_src_dir = os.path.join(src_dir, "raw") if os.path.exists(os.path.join(src_dir, "raw")) else src_dir
    src_video = os.path.join(src_dir, "video.mp4")
    src_audio = os.path.join(raw_src_dir, "audio.wav")
    src_input = os.path.join(raw_src_dir, "input_events.csv")
    src_vframes = os.path.join(raw_src_dir, "video_frames.csv")
    src_rec_json = os.path.join(raw_src_dir, "recording.json")
    src_camera = os.path.join(raw_src_dir, "reforger_camera.csv")
    src_clock = os.path.join(raw_src_dir, "clock_samples.csv")

    if not os.path.exists(src_video):
        log(f"Lỗi: Không tìm thấy video.mp4 trong {src_dir}", "ERROR")
        return False

    # Read video_frames.csv
    vframe_rows = []
    fps = 30.0
    if os.path.exists(src_vframes):
        with open(src_vframes, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    try:
                        vframe_rows.append((int(row[0]), float(row[1])))
                    except ValueError:
                        pass
        if len(vframe_rows) > 1:
            total_duration_ms = vframe_rows[-1][1] - vframe_rows[0][1]
            if total_duration_ms > 0:
                fps = round(len(vframe_rows) / (total_duration_ms / 1000.0))

    # Read recording.json
    rec_data = {}
    if os.path.exists(src_rec_json):
        try:
            with open(src_rec_json, "r", encoding="utf-8") as f:
                rec_data = json.load(f)
                fps = rec_data.get("fps", fps)
        except Exception:
            pass

    frames_per_slice = int(fps * duration_s)
    log(f"FPS: {fps}, Số khung hình mỗi folder: {frames_per_slice} frames", "INFO")

    # Read input events
    input_rows = []
    if os.path.exists(src_input):
        with open(src_input, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            in_header = next(reader, None)
            for row in reader:
                if len(row) >= 2:
                    try:
                        input_rows.append((float(row[0]), float(row[1]), row))
                    except ValueError:
                        pass

    # Read reforger_camera.csv
    camera_rows = []
    cam_header = None
    if os.path.exists(src_camera):
        with open(src_camera, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            cam_header = next(reader, None)
            for row in reader:
                if len(row) >= 1:
                    try:
                        camera_rows.append((float(row[0]), row))
                    except ValueError:
                        pass

    # Slicing loop
    success_count = 0
    for i in range(total_slices):
        slice_num = start_idx + i
        slice_id = f"{prefix}{str(slice_num).zfill(2)}"
        out_dir = os.path.join(output_root, slice_id)
        out_raw_dir = os.path.join(out_dir, "raw")
        os.makedirs(out_raw_dir, exist_ok=True)

        t_start = i * duration_s
        t_end = (i + 1) * duration_s
        print("\n" + "-" * 70)
        log(f"Đang xử lý phần {i+1}/{total_slices}: [{slice_id}] (từ {t_start//60:02d}:{t_start%60:02d} đến {t_end//60:02d}:{t_end%60:02d})...", "SLICE")

        # 1. Slice video.mp4
        out_video = os.path.join(out_dir, "video.mp4")
        cmd_vid = [ffmpeg, "-y", "-ss", str(t_start), "-t", str(duration_s), "-i", src_video, "-c:v", "copy", "-avoid_negative_ts", "make_zero", out_video]
        subprocess.run(cmd_vid, capture_output=True)

        # 2. Slice audio.wav
        if os.path.exists(src_audio):
            out_audio = os.path.join(out_raw_dir, "audio.wav")
            cmd_aud = [ffmpeg, "-y", "-ss", str(t_start), "-t", str(duration_s), "-i", src_audio, out_audio]
            subprocess.run(cmd_aud, capture_output=True)

        # 3. Slice video_frames.csv
        start_f_idx = i * frames_per_slice
        end_f_idx = (i + 1) * frames_per_slice
        slice_vframes = vframe_rows[start_f_idx:end_f_idx]
        
        out_vframes = os.path.join(out_raw_dir, "video_frames.csv")
        t0_grid = 0.0
        with open(out_vframes, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["frame_index", "t_grid_ms"])
            if slice_vframes:
                t0_grid = slice_vframes[0][1]
                for idx, (_, t_grid) in enumerate(slice_vframes):
                    writer.writerow([idx, round(t_grid - t0_grid, 3)])
            else:
                dt = 1000.0 / fps
                for idx in range(frames_per_slice):
                    writer.writerow([idx, round(idx * dt, 3)])

        # 4. Slice input_events.csv
        out_input = os.path.join(out_raw_dir, "input_events.csv")
        with open(out_input, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["t_qpc_ms", "t_utc_ms", "device", "type", "detail", "dx", "dy"])
            if input_rows and vframe_rows and start_f_idx < len(vframe_rows):
                base_qpc = vframe_rows[start_f_idx][1]
                end_qpc = vframe_rows[min(end_f_idx-1, len(vframe_rows)-1)][1] if end_f_idx <= len(vframe_rows) else base_qpc + duration_s * 1000
                for qpc, utc, raw in input_rows:
                    if base_qpc <= qpc < end_qpc:
                        adj_raw = list(raw)
                        adj_raw[0] = round(qpc - base_qpc, 3)
                        writer.writerow(adj_raw)

        # 5. Slice reforger_camera.csv
        if camera_rows and cam_header:
            out_camera = os.path.join(out_raw_dir, "reforger_camera.csv")
            with open(out_camera, "w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(cam_header)
                t_cam_start = t_start * 1000.0
                t_cam_end = t_end * 1000.0
                for t_ms, raw in camera_rows:
                    if t_cam_start <= t_ms < t_cam_end:
                        adj_raw = list(raw)
                        adj_raw[0] = round(t_ms - t_cam_start, 3)
                        writer.writerow(adj_raw)

        # 6. Copy clock_samples.csv
        if os.path.exists(src_clock):
            shutil.copy2(src_clock, os.path.join(out_raw_dir, "clock_samples.csv"))

        # 7. Write recording.json
        out_rec_json = os.path.join(out_raw_dir, "recording.json")
        slice_rec = dict(rec_data)
        slice_rec["capture_id"] = slice_id
        slice_rec["fps"] = fps
        slice_rec["frames"] = len(slice_vframes) if slice_vframes else frames_per_slice
        slice_rec["duration_seconds"] = duration_s
        with open(out_rec_json, "w", encoding="utf-8") as f:
            json.dump(slice_rec, f, indent=2)

        # 8. Build dataset files (frames_bin.bin, frames.csv, session.json)
        if os.path.exists(odx_exe) and os.path.exists(profile_path):
            log(f"Chạy odx build cho [{slice_id}]...", "BUILD")
            b_cmd = [odx_exe, "build", out_dir, "--profile", profile_path]
            b_res = subprocess.run(b_cmd, capture_output=True, text=True)
            
            # Validate
            v_cmd = [odx_exe, "validate", out_dir]
            v_res = subprocess.run(v_cmd, capture_output=True, text=True)
            v_msg = v_res.stdout.strip() or v_res.stderr.strip()
            log(f"Kết quả Validate [{slice_id}]: {v_msg}", "VALIDATE")
            if "PASS" in v_msg:
                success_count += 1
        else:
            success_count += 1

    print("\n" + "=" * 70)
    log(f"HOÀN TẤT! Đã cắt và tạo thành công {success_count}/{total_slices} folder đạt chuẩn trong data\\captures\\", "SUCCESS")
    print("=" * 70)
    return True

def main():
    print("=" * 70)
    print("       CÔNG CỤ CẮT 1 PHIÊN QUAY DÀI THÀNH 15 FOLDER (4 PHÚT/FOLDER)")
    print("=" * 70)
    
    captures_dir = os.path.join(os.path.dirname(__file__), "data", "captures")
    sessions = []
    if os.path.exists(captures_dir):
        sessions = [os.path.join(captures_dir, d) for d in os.listdir(captures_dir) if os.path.isdir(os.path.join(captures_dir, d))]
        sessions.sort(key=os.path.getmtime, reverse=True)

    if not sessions:
        print(f">> Chưa có thư mục phiên quay nào trong: {captures_dir}")
        src_path = input(">> Hãy nhập đường dẫn thư mục phiên quay cần cắt: ").strip()
    else:
        print("Danh sách các phiên quay gần nhất:")
        for idx, s in enumerate(sessions[:5], 1):
            sz = 0
            vid = os.path.join(s, "video.mp4")
            if os.path.exists(vid):
                sz = os.path.getsize(vid) / (1024 * 1024)
            print(f"  {idx}. {os.path.basename(s):<25} ({sz:.1f} MB)")
        
        choice = input(f">> Chọn phiên quay cần cắt (1-{min(5, len(sessions))}) [Mặc định 1]: ").strip()
        c_idx = int(choice) - 1 if choice.isdigit() and 1 <= int(choice) <= len(sessions) else 0
        src_path = sessions[c_idx]

    prefix = input(">> Nhập tiền tố tên task [Mặc định: 'asm-s221-']: ").strip() or "asm-s221-"
    count = input(">> Số lượng folder cần cắt [Mặc định: 15]: ").strip()
    count_int = int(count) if count.isdigit() and int(count) > 0 else 15
    
    dur = input(">> Thời lượng mỗi folder (giây) [Mặc định: 240 = 4 phút]: ").strip()
    dur_int = int(dur) if dur.isdigit() and int(dur) > 0 else 240

    split_session(src_path, prefix=prefix, total_slices=count_int, duration_s=dur_int)

if __name__ == "__main__":
    main()
