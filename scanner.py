import os
import sys
import re
import csv
import json
import glob
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from config_manager import get_base_dir, load_config

# Đảm bảo stdout/stderr an toàn khi chạy chế độ không có console (windowed)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

def is_sqlite_file(file_path):
    """Kiểm tra xem file có phải là file SQLite hợp lệ hay không (dựa trên 16 bytes magic header)."""
    try:
        if not os.path.isfile(file_path) or os.path.getsize(file_path) < 100:
            return False
        with open(file_path, "rb") as f:
            header = f.read(16)
            return header == b"SQLite format 3\x00"
    except Exception:
        return False

def get_possible_zalo_dirs():
    """
    Tìm tất cả các thư mục có khả năng lưu trữ dữ liệu Zalo PC trên máy tính.
    Hỗ trợ đầy đủ đường dẫn mặc định trên Windows, đường dẫn symlink và các ổ đĩa tùy biến.
    """
    dirs = []
    seen = set()

    def add_dir(d):
        if not d:
            return
        d = os.path.abspath(d)
        if d not in seen and os.path.exists(d):
            seen.add(d)
            dirs.append(d)

    # 1. Kiểm tra cấu hình tùy chỉnh
    config = load_config()
    custom_path = config.get("custom_zalo_path", "").strip()
    if custom_path:
        add_dir(custom_path)

    # 2. Windows Environment Variables
    # %LOCALAPPDATA% (C:\Users\<User>\AppData\Local) - ĐÂY LÀ ĐƯỜNG DẪN LƯU DỮ LIỆU CHÍNH CỦA ZALO PC
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        add_dir(os.path.join(localappdata, "ZaloPC"))
        add_dir(os.path.join(localappdata, "Zalo"))
        add_dir(os.path.join(localappdata, "ZaloData"))
        add_dir(os.path.join(localappdata, "Programs", "Zalo"))

    # %APPDATA% (C:\Users\<User>\AppData\Roaming)
    appdata = os.environ.get("APPDATA")
    if appdata:
        add_dir(os.path.join(appdata, "ZaloPC"))
        add_dir(os.path.join(appdata, "ZaloApp"))
        add_dir(os.path.join(appdata, "ZaloData"))
        add_dir(os.path.join(appdata, "Zalo"))

    # 3. Quét dự phòng theo User Home directory
    try:
        user_home = os.path.expanduser("~")
        for sub in [
            os.path.join("AppData", "Local", "ZaloPC"),
            os.path.join("AppData", "Local", "Zalo"),
            os.path.join("AppData", "Local", "ZaloData"),
            os.path.join("AppData", "Local", "Programs", "Zalo"),
            os.path.join("AppData", "Roaming", "ZaloPC"),
            os.path.join("AppData", "Roaming", "ZaloApp"),
            os.path.join("AppData", "Roaming", "ZaloData"),
            os.path.join("Documents", "Zalo Received Files"),
        ]:
            add_dir(os.path.join(user_home, sub))
    except Exception:
        pass

    # 4. Các ổ đĩa phổ biến nếu người dùng di chuyển Zalo sang ổ khác (D:\ZaloPC, E:\ZaloPC...)
    for drive in ["D:\\", "E:\\", "F:\\", "C:\\"]:
        for folder in ["ZaloPC", "Zalo", "ZaloData", "ZaloApp"]:
            add_dir(os.path.join(drive, folder))

    # 5. Linux / macOS (nếu chạy Wine hoặc client Zalo desktop tương ứng)
    try:
        user_home = os.path.expanduser("~")
        add_dir(os.path.join(user_home, ".config", "ZaloPC"))
        add_dir(os.path.join(user_home, ".config", "Zalo"))
        add_dir(os.path.join(user_home, ".local", "share", "ZaloPC"))
        add_dir(os.path.join(user_home, "Library", "Application Support", "ZaloPC"))
        add_dir(os.path.join(user_home, "Library", "Application Support", "Zalo"))
    except Exception:
        pass

    return dirs

def find_all_sqlite_dbs(search_dirs=None):
    """
    Tìm tất cả các file SQLite có khả năng chứa dữ liệu tin nhắn.
    Duyệt đệ quy và hỗ trợ cả symbolic links/junctions.
    """
    if search_dirs is None:
        search_dirs = get_possible_zalo_dirs()

    found_dbs = []
    seen = set()

    for s_dir in search_dirs:
        if not os.path.exists(s_dir):
            continue

        # Nếu đường dẫn trỏ trực tiếp đến 1 file database
        if os.path.isfile(s_dir):
            ext = os.path.splitext(s_dir)[1].lower()
            if ext in [".db", ".sqlite", ".sqlite3"] or is_sqlite_file(s_dir):
                abs_f = os.path.abspath(s_dir)
                if abs_f not in seen:
                    seen.add(abs_f)
                    found_dbs.append(abs_f)
            continue

        # Duyệt thư mục đệ quy (followlinks=True để quét qua junction sang ổ D/E)
        for root, dirs, files in os.walk(s_dir, followlinks=True):
            # Bỏ qua các thư mục không liên quan để tối ưu tốc độ quét
            dirs[:] = [d for d in dirs if d.lower() not in [
                "cache", "crashpad", "gpucache", "logs", "temp", "node_modules", ".git", "asset", "blob_storage"
            ]]
            for file in files:
                lower_file = file.lower()
                ext = os.path.splitext(lower_file)[1]

                # Bỏ qua các file media, thực thi, log
                if ext in [".exe", ".dll", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".webp", ".log", ".txt", ".json", ".asar", ".tmp", ".zip"]:
                    continue
                # Bỏ qua file phụ trợ WAL/SHM trong danh sách file chính
                if lower_file.endswith("-wal") or lower_file.endswith("-shm") or lower_file.endswith("-journal"):
                    continue

                full_path = os.path.abspath(os.path.join(root, file))
                if full_path in seen:
                    continue

                # Nhận diện nếu có đuôi db/sqlite hoặc có magic header SQLite 3
                if ext in [".db", ".sqlite", ".sqlite3"] or is_sqlite_file(full_path):
                    seen.add(full_path)
                    found_dbs.append(full_path)

    # Ưu tiên các file có tên chứa từ khóa gợi ý
    def score_db(path):
        lower = os.path.basename(path).lower()
        score = 0
        if "message" in lower: score += 12
        if "chat" in lower: score += 10
        if "zalo" in lower: score += 6
        if "history" in lower: score += 6
        if "production" in lower: score += 5
        if "sqlite" in lower: score += 4
        if "main" in lower: score += 3
        return score

    found_dbs.sort(key=score_db, reverse=True)
    return found_dbs

def format_timestamp(ts):
    """Chuyển đổi timestamp (giây, mili-giây, hoặc micro-giây) sang dạng datetime dễ đọc."""
    if not ts:
        return ""
    try:
        if isinstance(ts, (int, float)):
            val = float(ts)
        else:
            ts_str = str(ts).strip()
            if not ts_str.isdigit():
                return ts_str
            val = float(ts_str)

        # Microseconds (> 1e14)
        if val > 1e14:
            val = val / 1e6
        # Milliseconds (> 1e11)
        elif val > 1e11:
            val = val / 1e3

        return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)

def extract_clean_text(raw_val):
    """
    Trích xuất chuỗi văn bản sạch từ nội dung thô (hỗ trợ bóc tách JSON nếu Zalo lưu message dạng JSON).
    """
    if not isinstance(raw_val, str):
        return str(raw_val) if raw_val is not None else ""
    val = raw_val.strip()
    if (val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]")):
        try:
            data = json.loads(val)
            if isinstance(data, dict):
                # Thử các trường phổ biến chứa nội dung tin nhắn
                for key in ["content", "message", "msg", "text", "body", "title", "description", "data", "caption"]:
                    v = data.get(key)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        except Exception:
            pass
    return val

def find_best_columns(col_names):
    """
    Phân tích tên các cột để xác định cột thời gian, người gửi, nhóm chat và các cột nội dung.
    Tránh nhầm lẫn các cột ID như msgId, sender_id thành cột nội dung.
    """
    time_col = None
    sender_col = None
    group_col = None
    content_candidates = []

    # 1. Tìm cột thời gian
    for col in col_names:
        cl = col.lower()
        if any(k == cl for k in ["timestamp", "createtime", "create_time", "created_time", "sendtime", "send_time", "datetime", "date_time"]):
            time_col = col
            break
    if not time_col:
        for col in col_names:
            cl = col.lower()
            if ("time" in cl or "date" in cl) and not any(bad in cl for bad in ["timeout", "timer", "expire", "update", "limit", "diff"]):
                time_col = col
                break

    # 2. Tìm cột người gửi
    for col in col_names:
        cl = col.lower()
        if any(k == cl for k in ["sender_name", "from_name", "sendername", "author_name", "display_name", "nickname", "user_name", "username"]):
            sender_col = col
            break
    if not sender_col:
        for col in col_names:
            cl = col.lower()
            if any(k in cl for k in ["sender", "from", "author"]) and not any(bad in cl for bad in ["id", "uid", "type", "key"]):
                sender_col = col
                break
    if not sender_col:
        for col in col_names:
            cl = col.lower()
            if any(k in cl for k in ["sender", "from_uid", "fromuid", "author", "src_id", "srcid"]):
                sender_col = col
                break

    # 3. Tìm cột nhóm / cuộc hội thoại
    for col in col_names:
        cl = col.lower()
        if any(k == cl for k in ["group_name", "room_name", "conv_name", "thread_name", "chat_name", "title"]):
            group_col = col
            break
    if not group_col:
        for col in col_names:
            cl = col.lower()
            if any(k in cl for k in ["group", "room", "conversation", "thread", "channel"]) and not any(bad in cl for bad in ["id", "type"]):
                group_col = col
                break
    if not group_col:
        for col in col_names:
            cl = col.lower()
            if any(k in cl for k in ["group", "room", "conversation", "thread", "conv_id", "grid"]):
                group_col = col
                break

    # 4. Tìm các cột có khả năng chứa nội dung tin nhắn (ưu tiên cột có tên rõ ràng trước)
    # Tuyệt đối không chọn cột ID thuần túy (msgid, cliMsgId, type, status...)
    for col in col_names:
        cl = col.lower()
        if any(bad in cl for bad in ["id", "type", "status", "seq", "flag", "size", "width", "height", "version", "crc"]):
            continue
        if any(k in cl for k in ["content", "message", "msg", "text", "body", "dmsg", "payload", "raw_text", "description", "data", "caption"]):
            content_candidates.append(col)

    # Nếu không tìm thấy cột có tên gợi ý, thử tất cả các cột không phải ID
    if not content_candidates:
        for col in col_names:
            cl = col.lower()
            if not any(bad in cl for bad in ["id", "type", "status", "seq", "flag", "size", "version"]):
                content_candidates.append(col)

    return time_col, sender_col, group_col, content_candidates

def inspect_and_filter_db(db_path, ticker, case_sensitive=False):
    """
    Quét một file SQLite để tìm các tin nhắn chứa mã cổ phiếu.
    Sử dụng bản sao tạm thời gồm cả file .db, -wal, -shm để không bị khóa file và đọc được dữ liệu mới nhất.
    """
    results = []
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex[:8]
    temp_file = os.path.join(temp_dir, f"zalo_scan_{unique_id}.db")
    temp_wal = temp_file + "-wal"
    temp_shm = temp_file + "-shm"

    wal_source = db_path + "-wal"
    shm_source = db_path + "-shm"

    try:
        shutil.copyfile(db_path, temp_file)
        if os.path.exists(wal_source):
            try:
                shutil.copyfile(wal_source, temp_wal)
            except Exception:
                pass
        if os.path.exists(shm_source):
            try:
                shutil.copyfile(shm_source, temp_shm)
            except Exception:
                pass
    except Exception as e:
        return results, f"Không thể copy file {os.path.basename(db_path)}: {e}"

    conn = None
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(rf"\b{re.escape(ticker)}\b", flags)

    try:
        conn = sqlite3.connect(temp_file)
        cursor = conn.cursor()

        # Lấy danh sách các bảng
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [row[0] for row in cursor.fetchall() if not row[0].startswith("sqlite_")]

        # Bỏ qua các bảng hệ thống/cấu hình không chứa tin nhắn
        skip_tables = ["settings", "options", "preferences", "schema_migrations", "sqlite_stat", "metadata"]

        for table in tables:
            if any(s in table.lower() for s in skip_tables):
                continue
            try:
                # Lấy thông tin cột
                cursor.execute(f"PRAGMA table_info([{table}]);")
                columns_info = cursor.fetchall()
                col_names = [col[1] for col in columns_info]

                if not col_names:
                    continue

                time_col, sender_col, group_col, content_candidates = find_best_columns(col_names)
                if not content_candidates:
                    continue

                # Truy vấn dữ liệu bảng
                selected_cols = [f"[{col}]" for col in col_names]
                query = f"SELECT {', '.join(selected_cols)} FROM [{table}]"
                cursor.execute(query)

                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(zip(col_names, row))

                    # Kiểm tra các cột nội dung ứng viên
                    matched_content = None
                    # 1. Thử các cột ứng viên trước
                    for c_col in content_candidates:
                        raw_c = row_dict.get(c_col)
                        if raw_c is None:
                            continue
                        text_val = None
                        if isinstance(raw_c, str):
                            text_val = raw_c
                        elif isinstance(raw_c, (bytes, bytearray)):
                            try:
                                text_val = raw_c.decode("utf-8", errors="ignore")
                            except Exception:
                                pass
                        if text_val and pattern.search(text_val):
                            matched_content = extract_clean_text(text_val)
                            break

                    # 2. Nếu chưa thấy, thử quét qua tất cả các cột văn bản khác trong dòng (trừ cột thời gian/người gửi)
                    if not matched_content:
                        for c_col, raw_c in row_dict.items():
                            if c_col in [time_col, sender_col, group_col] or c_col in content_candidates:
                                continue
                            if raw_c is None or isinstance(raw_c, (int, float, bool)):
                                continue
                            text_val = None
                            if isinstance(raw_c, str):
                                text_val = raw_c
                            elif isinstance(raw_c, (bytes, bytearray)):
                                try:
                                    text_val = raw_c.decode("utf-8", errors="ignore")
                                except Exception:
                                    pass
                            if text_val and pattern.search(text_val):
                                matched_content = extract_clean_text(text_val)
                                break

                    if matched_content:
                        ts_val = row_dict.get(time_col) if time_col else ""
                        sender_val = row_dict.get(sender_col) if sender_col else "Ẩn danh"
                        group_val = row_dict.get(group_col) if group_col else "Cá nhân/Nhóm"

                        results.append({
                            "ticker": ticker.upper(),
                            "time": format_timestamp(ts_val),
                            "sender": str(sender_val) if sender_val is not None else "",
                            "group": str(group_val) if group_val is not None else "",
                            "content": matched_content.strip(),
                            "source_table": table,
                            "source_db": os.path.basename(db_path)
                        })
            except Exception:
                continue

    except Exception as e:
        return results, f"Lỗi truy vấn {os.path.basename(db_path)}: {e}"
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        for f in [temp_file, temp_wal, temp_shm]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    return results, None

def scan_messages_for_ticker(ticker, progress_callback=None):
    """
    Quét toàn bộ các cơ sở dữ liệu Zalo tìm thấy để lọc tin nhắn chứa ticker.
    Hỗ trợ progress_callback(msg, percent) để cập nhật giao diện.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        return [], "Mã cổ phiếu không hợp lệ."

    config = load_config()
    case_sensitive = config.get("case_sensitive", False)

    if progress_callback:
        progress_callback("Đang tìm kiếm thư mục và cơ sở dữ liệu Zalo PC...", 10)

    db_paths = find_all_sqlite_dbs()
    if not db_paths:
        zalo_dirs = get_possible_zalo_dirs()
        checked_dirs_str = "\n".join(f" - {d}" for d in zalo_dirs) if zalo_dirs else " (Không tìm thấy thư mục nào)"
        return [], (
            f"Không tìm thấy file cơ sở dữ liệu Zalo PC nào trên máy tính này.\n\n"
            f"Đã tự động kiểm tra các thư mục:\n{checked_dirs_str}\n\n"
            f"Hướng dẫn khắc phục:\n"
            f"1. Hãy đảm bảo Zalo PC đã được cài đặt và bạn đã đăng nhập tài khoản.\n"
            f"2. Nếu bạn chuyển Zalo sang ổ khác (như D:\\ZaloPC), hãy nhấn nút '⚙️ Chọn Thư Mục Zalo' để trỏ đến thư mục đó."
        )

    total_dbs = len(db_paths)
    all_results = []
    errors = []

    for idx, db_path in enumerate(db_paths, start=1):
        db_name = os.path.basename(db_path)
        percent = int(10 + (idx / total_dbs) * 80)
        if progress_callback:
            progress_callback(f"Đang quét file ({idx}/{total_dbs}): {db_name}...", percent)

        res, err = inspect_and_filter_db(db_path, ticker, case_sensitive)
        if res:
            all_results.extend(res)
        if err:
            errors.append(err)

    if progress_callback:
        progress_callback("Hoàn tất quét, đang tổng hợp dữ liệu...", 95)

    # Khử trùng lặp tin nhắn nếu cùng thời gian, người gửi và nội dung
    unique_results = []
    seen_keys = set()
    for item in all_results:
        key = (item["time"], item["sender"], item["content"])
        if key not in seen_keys:
            seen_keys.add(key)
            unique_results.append(item)

    # Sắp xếp theo thời gian mới nhất lên đầu
    unique_results.sort(key=lambda x: str(x["time"]), reverse=True)

    if progress_callback:
        progress_callback(f"Tìm thấy {len(unique_results)} tin nhắn liên quan đến [{ticker}].", 100)

    return unique_results, None

def export_results(results, ticker):
    """
    Xuất kết quả ra file CSV chuẩn UTF-8-BOM và file HTML báo cáo đẹp mắt.
    Không phụ thuộc vào thư viện bên ngoài.
    """
    if not results:
        return []

    config = load_config()
    base_dir = get_base_dir()
    out_dir_name = config.get("output_dir", "reports")
    out_dir = os.path.join(base_dir, out_dir_name)
    os.makedirs(out_dir, exist_ok=True)

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_format = config.get("export_format", "both")
    generated_files = []

    # 1. Xuất file CSV (UTF-8-SIG với BOM để mở trực tiếp trong Excel không bị lỗi font)
    if export_format in ["csv", "both"]:
        csv_file = os.path.join(out_dir, f"Zalo_{ticker}_{now_str}.csv")
        try:
            with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Mã CK", "Thời Gian", "Người Gửi", "Hội Nhóm / Cuộc Trò Chuyện", "Nội Dung Tin Nhắn", "Bảng Dữ Liệu", "File Nguồn"])
                for r in results:
                    writer.writerow([
                        r.get("ticker", ""),
                        r.get("time", ""),
                        r.get("sender", ""),
                        r.get("group", ""),
                        r.get("content", ""),
                        r.get("source_table", ""),
                        r.get("source_db", "")
                    ])
            generated_files.append(csv_file)
        except Exception as e:
            print(f"[!] Lỗi khi xuất file CSV: {e}")

    # 2. Xuất file HTML báo cáo trực quan
    if export_format in ["html", "both"]:
        html_file = os.path.join(out_dir, f"Zalo_{ticker}_{now_str}.html")
        try:
            html_content = generate_html_report(results, ticker, now_str)
            with open(html_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            generated_files.append(html_file)
        except Exception as e:
            print(f"[!] Lỗi khi xuất file HTML: {e}")

    return generated_files

def generate_html_report(results, ticker, now_str):
    """Tạo báo cáo giao diện HTML hiện đại, có ô tìm kiếm và giao diện dễ nhìn."""
    rows_html = []
    pattern = re.compile(rf"(\b{re.escape(ticker)}\b)", re.IGNORECASE)

    for idx, r in enumerate(results, start=1):
        content = r.get("content", "").replace("<", "&lt;").replace(">", "&gt;")
        highlighted = pattern.sub(r'<span class="badge-ticker">\1</span>', content)
        time_str = r.get("time", "---")
        sender = r.get("sender", "Ẩn danh")
        group = r.get("group", "Nhóm chat")

        rows_html.append(f"""
        <tr>
            <td class="text-center">{idx}</td>
            <td class="nowrap text-secondary">{time_str}</td>
            <td><strong>{sender}</strong></td>
            <td><span class="group-tag">{group}</span></td>
            <td class="msg-content">{highlighted}</td>
        </tr>
        """)

    table_body = "\n".join(rows_html)

    html = f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Báo Cáo Tin Nhắn Zalo - Mã {ticker}</title>
    <style>
        :root {{
            --primary: #0068FF;
            --primary-light: #EBF3FF;
            --success: #10B981;
            --bg: #F8FAFC;
            --text: #1E293B;
            --border: #E2E8F0;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 24px; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: #fff; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); padding: 24px; }}
        header {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
        h1 {{ font-size: 24px; color: var(--primary); display: flex; align-items: center; gap: 10px; }}
        .stats {{ display: flex; gap: 16px; font-size: 14px; color: #64748B; }}
        .badge-count {{ background: var(--primary-light); color: var(--primary); font-weight: bold; padding: 4px 12px; border-radius: 20px; }}
        .badge-ticker {{ background: #FEF08A; color: #854D0E; font-weight: bold; padding: 2px 6px; border-radius: 4px; }}
        .group-tag {{ background: #F1F5F9; color: #475569; padding: 2px 8px; border-radius: 6px; font-size: 12px; }}
        .controls {{ display: flex; gap: 12px; margin-bottom: 16px; }}
        .search-input {{ flex: 1; padding: 10px 16px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; outline: none; }}
        .search-input:focus {{ border-color: var(--primary); }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 14px; }}
        th {{ background: #F8FAFC; padding: 12px; border-bottom: 2px solid var(--border); font-weight: 600; color: #475569; }}
        td {{ padding: 12px; border-bottom: 1px solid var(--border); vertical-align: top; }}
        tr:hover td {{ background: #F8FAFC; }}
        .text-center {{ text-align: center; }}
        .nowrap {{ white-space: nowrap; }}
        .text-secondary {{ color: #64748B; font-size: 13px; }}
        .msg-content {{ line-height: 1.5; word-break: break-word; }}
        footer {{ text-align: center; margin-top: 24px; color: #94A3B8; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>📊 Tổng Hợp Tin Nhắn Zalo: Mã <span class="badge-ticker">{ticker}</span></h1>
                <div class="stats" style="margin-top: 6px;">
                    <span>Xuất lúc: {datetime.now().strftime('%H:%M:%S - %d/%m/%Y')}</span>
                    <span>Tổng số tin: <strong class="badge-count">{len(results)} tin nhắn</strong></span>
                </div>
            </div>
        </header>

        <div class="controls">
            <input type="text" id="filterInput" class="search-input" placeholder="🔍 Tìm kiếm nhanh trong danh sách kết quả..." onkeyup="filterTable()">
        </div>

        <div style="overflow-x: auto;">
            <table id="resultsTable">
                <thead>
                    <tr>
                        <th style="width: 50px;" class="text-center">#</th>
                        <th style="width: 150px;">Thời Gian</th>
                        <th style="width: 150px;">Người Gửi</th>
                        <th style="width: 180px;">Nhóm Chat</th>
                        <th>Nội Dung Tin Nhắn</th>
                    </tr>
                </thead>
                <tbody>
                    {table_body}
                </tbody>
            </table>
        </div>

        <footer>
            Công cụ lọc tin nhắn Zalo PC tự động - Dành riêng cho Nhà đầu tư Chứng khoán
        </footer>
    </div>

    <script>
        function filterTable() {{
            const input = document.getElementById('filterInput').value.toLowerCase();
            const rows = document.querySelectorAll('#resultsTable tbody tr');
            rows.forEach(row => {{
                const text = row.innerText.toLowerCase();
                row.style.display = text.includes(input) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""
    return html
