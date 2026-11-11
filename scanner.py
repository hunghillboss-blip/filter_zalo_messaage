import os
import sys
import re
import csv
import glob
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime
from config_manager import get_base_dir, load_config

def get_possible_zalo_dirs():
    """Tìm các thư mục lưu dữ liệu Zalo PC trên Windows."""
    dirs = []
    
    # 1. Kiểm tra cấu hình tùy chỉnh
    config = load_config()
    custom_path = config.get("custom_zalo_path", "").strip()
    if custom_path and os.path.exists(custom_path):
        dirs.append(custom_path)

    # 2. %APPDATA%\ZaloPC
    appdata = os.environ.get("APPDATA")
    if appdata:
        zalo_pc = os.path.join(appdata, "ZaloPC")
        if os.path.exists(zalo_pc):
            dirs.append(zalo_pc)

    # 3. %LOCALAPPDATA%\Programs\Zalo
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        local_zalo = os.path.join(localappdata, "Programs", "Zalo")
        if os.path.exists(local_zalo):
            dirs.append(local_zalo)

    # 4. C:\Users\<Username>\AppData\Roaming\ZaloPC (dự phòng)
    try:
        user_home = os.path.expanduser("~")
        fallback_roaming = os.path.join(user_home, "AppData", "Roaming", "ZaloPC")
        if os.path.exists(fallback_roaming) and fallback_roaming not in dirs:
            dirs.append(fallback_roaming)
    except Exception:
        pass

    return dirs

def find_all_sqlite_dbs(search_dirs=None):
    """Tìm tất cả các file SQLite có khả năng chứa dữ liệu tin nhắn."""
    if search_dirs is None:
        search_dirs = get_possible_zalo_dirs()

    found_dbs = []
    seen = set()

    for s_dir in search_dirs:
        if not os.path.exists(s_dir):
            continue
        for root, _, files in os.walk(s_dir):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in [".db", ".sqlite", ".sqlite3"]:
                    full_path = os.path.abspath(os.path.join(root, file))
                    if full_path not in seen:
                        seen.add(full_path)
                        found_dbs.append(full_path)

    # Ưu tiên các file có tên chứa từ khóa gợi ý
    def score_db(path):
        lower = os.path.basename(path).lower()
        score = 0
        if "message" in lower: score += 10
        if "chat" in lower: score += 8
        if "zalo" in lower: score += 5
        if "main" in lower: score += 3
        return score

    found_dbs.sort(key=score_db, reverse=True)
    return found_dbs

def format_timestamp(ts):
    """Chuyển đổi timestamp (giây hoặc mili-giây) sang dạng datetime dễ đọc."""
    if not ts:
        return ""
    try:
        if isinstance(ts, (int, float)):
            if ts > 10000000000: # Mili-giây
                ts = ts / 1000.0
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        ts_str = str(ts).strip()
        if ts_str.isdigit():
            val = float(ts_str)
            if val > 10000000000:
                val = val / 1000.0
            return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M:%S")
        return ts_str
    except Exception:
        return str(ts)

def inspect_and_filter_db(db_path, ticker, case_sensitive=False):
    """
    Quét một file SQLite để tìm các tin nhắn chứa mã cổ phiếu.
    Sử dụng bản sao tạm thời để không bị khóa file nếu Zalo đang mở.
    """
    results = []
    temp_dir = tempfile.gettempdir()
    temp_file = os.path.join(temp_dir, f"zalo_scan_{uuid.uuid4().hex[:8]}.db")

    try:
        shutil.copyfile(db_path, temp_file)
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

        for table in tables:
            try:
                # Lấy thông tin cột
                cursor.execute(f"PRAGMA table_info([{table}]);")
                columns_info = cursor.fetchall()
                col_names = [col[1] for col in columns_info]

                # Tìm cột chứa nội dung tin nhắn
                content_col = None
                time_col = None
                sender_col = None
                group_col = None

                for col in col_names:
                    clow = col.lower()
                    if not content_col and any(k in clow for k in ["content", "message", "msg", "text", "body", "dmsg", "payload"]):
                        content_col = col
                    if not time_col and any(k in clow for k in ["time", "timestamp", "date", "created"]):
                        time_col = col
                    if not sender_col and any(k in clow for k in ["sender", "sender_name", "user_name", "author", "from_name"]):
                        sender_col = col
                    if not group_col and any(k in clow for k in ["group", "conversation", "thread", "room", "title"]):
                        group_col = col

                # Nếu không tìm thấy cột có tên gợi ý, thử kiểm tra nếu bảng có từ 2 cột trở lên
                if not content_col:
                    continue

                # Truy vấn dữ liệu bảng
                selected_cols = [f"[{col}]" for col in col_names]
                query = f"SELECT {', '.join(selected_cols)} FROM [{table}]"
                cursor.execute(query)

                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(zip(col_names, row))
                    content_val = row_dict.get(content_col)
                    if content_val and isinstance(content_val, str) and pattern.search(content_val):
                        ts_val = row_dict.get(time_col) if time_col else ""
                        sender_val = row_dict.get(sender_col) if sender_col else "Ẩn danh"
                        group_val = row_dict.get(group_col) if group_col else "Cá nhân/Nhóm"

                        results.append({
                            "ticker": ticker.upper(),
                            "time": format_timestamp(ts_val),
                            "sender": str(sender_val) if sender_val is not None else "",
                            "group": str(group_val) if group_val is not None else "",
                            "content": content_val.strip(),
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
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
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
        return [], "Không tìm thấy file cơ sở dữ liệu Zalo PC nào trên máy tính này. Vui lòng kiểm tra Zalo PC đã được cài đặt và đăng nhập chưa."

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
