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

# Biến lưu trữ nhật ký chẩn đoán quét lần gần nhất
_last_scan_diagnostics = {
    "checked_dirs": [],
    "sqlite_dbs": [],
    "leveldb_files": [],
    "total_sources": 0,
    "last_ticker": "",
    "results_count": 0,
    "errors": [],
    "summary_text": ""
}

def get_last_scan_diagnostics():
    """Trả về bản sao thông tin chẩn đoán quét gần nhất để hiển thị trên giao diện hoặc xuất log."""
    return _last_scan_diagnostics.copy()

def decompress_snappy(data):
    """
    Giải nén dữ liệu Snappy thuần Python (không phụ thuộc vào thư viện bên ngoài hay C++).
    Đảm bảo tương thích 100% khi build PyInstaller portable.
    """
    if not data:
        return b""
    pos = 0
    length = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        length |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7

    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        elem_type = tag & 0x03
        if elem_type == 0:  # Literal
            lit_len = tag >> 2
            if lit_len < 60:
                lit_len += 1
            elif lit_len == 60:
                if pos >= len(data):
                    break
                lit_len = data[pos] + 1
                pos += 1
            elif lit_len == 61:
                if pos + 2 > len(data):
                    break
                lit_len = int.from_bytes(data[pos:pos+2], "little") + 1
                pos += 2
            elif lit_len == 62:
                if pos + 3 > len(data):
                    break
                lit_len = int.from_bytes(data[pos:pos+3], "little") + 1
                pos += 3
            elif lit_len == 63:
                if pos + 4 > len(data):
                    break
                lit_len = int.from_bytes(data[pos:pos+4], "little") + 1
                pos += 4
            if pos + lit_len > len(data):
                out.extend(data[pos:])
                break
            out.extend(data[pos:pos+lit_len])
            pos += lit_len
        elif elem_type == 1:  # Copy 1-byte offset
            copy_len = ((tag >> 2) & 7) + 4
            if pos >= len(data):
                break
            offset = ((tag & 0xe0) << 3) | data[pos]
            pos += 1
            if offset <= 0:
                break
            for _ in range(copy_len):
                if len(out) >= offset:
                    out.append(out[-offset])
        elif elem_type == 2:  # Copy 2-byte offset
            copy_len = (tag >> 2) + 1
            if pos + 2 > len(data):
                break
            offset = int.from_bytes(data[pos:pos+2], "little")
            pos += 2
            if offset <= 0:
                break
            for _ in range(copy_len):
                if len(out) >= offset:
                    out.append(out[-offset])
        elif elem_type == 3:  # Copy 4-byte offset
            copy_len = (tag >> 2) + 1
            if pos + 4 > len(data):
                break
            offset = int.from_bytes(data[pos:pos+4], "little")
            pos += 4
            if offset <= 0:
                break
            for _ in range(copy_len):
                if len(out) >= offset:
                    out.append(out[-offset])
    return bytes(out)

def read_varint(data, pos):
    """Đọc số nguyên varint từ buffer data tại vị trí pos."""
    val = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        val |= (b & 0x7f) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, pos

LEVELDB_MAGIC = b"\x57\xfb\x80\x8b\x24\x75\x47\xdb"

def is_leveldb_sstable(file_path):
    """Kiểm tra xem file có phải là SSTable (.ldb) của LevelDB hay không qua 8-byte magic footer."""
    try:
        if not os.path.isfile(file_path) or os.path.getsize(file_path) < 48:
            return False
        with open(file_path, "rb") as f:
            f.seek(-8, os.SEEK_END)
            return f.read(8) == LEVELDB_MAGIC
    except Exception:
        return False

def extract_sstable_blocks(data):
    """Bóc tách và giải nén các Data Blocks từ LevelDB SSTable (.ldb)."""
    blocks = []
    if len(data) < 48:
        return blocks
    magic = data[-8:]
    if magic != LEVELDB_MAGIC:
        return blocks

    pos = len(data) - 48
    try:
        meta_off, pos = read_varint(data, pos)
        meta_sz, pos = read_varint(data, pos)
        idx_off, pos = read_varint(data, pos)
        idx_sz, pos = read_varint(data, pos)

        if idx_off + idx_sz > len(data):
            return blocks

        idx_block_data = data[idx_off : idx_off + idx_sz]
        comp_type = data[idx_off + idx_sz] if idx_off + idx_sz < len(data) else 0
        if comp_type == 1:
            try:
                idx_block_data = decompress_snappy(idx_block_data)
            except Exception:
                pass

        if len(idx_block_data) < 4:
            return blocks

        num_restarts = int.from_bytes(idx_block_data[-4:], "little")
        entries_end = len(idx_block_data) - 4 - (4 * num_restarts)
        if entries_end <= 0:
            entries_end = len(idx_block_data) - 4

        p = 0
        while p < entries_end:
            shared_len, p = read_varint(idx_block_data, p)
            unshared_len, p = read_varint(idx_block_data, p)
            val_len, p = read_varint(idx_block_data, p)
            p += unshared_len
            val = idx_block_data[p : p + val_len]
            p += val_len

            if val:
                b_off, vp = read_varint(val, 0)
                b_sz, vp = read_varint(val, vp)
                if 0 <= b_off and b_off + b_sz <= len(data):
                    b_data = data[b_off : b_off + b_sz]
                    b_comp = data[b_off + b_sz] if b_off + b_sz < len(data) else 0
                    if b_comp == 1:
                        try:
                            b_data = decompress_snappy(b_data)
                        except Exception:
                            pass
                    blocks.append(b_data)
    except Exception:
        pass
    return blocks

def extract_log_records(data):
    """Bóc tách các bản ghi từ LevelDB Write-Ahead Log (.log) với block 32KB."""
    records = []
    current_record = bytearray()
    pos = 0
    BLOCK_SIZE = 32768

    while pos + 7 <= len(data):
        block_offset = pos % BLOCK_SIZE
        if BLOCK_SIZE - block_offset < 7:
            pos += (BLOCK_SIZE - block_offset)
            continue

        crc = int.from_bytes(data[pos:pos+4], "little")
        length = int.from_bytes(data[pos+4:pos+6], "little")
        rec_type = data[pos+6]
        pos += 7

        if rec_type == 0:  # Zero type / padding
            if length == 0:
                pos += (BLOCK_SIZE - (pos % BLOCK_SIZE)) % BLOCK_SIZE
                continue

        if pos + length > len(data):
            break

        chunk = data[pos:pos+length]
        pos += length

        if rec_type == 1:  # Full
            records.append(bytes(chunk))
            current_record = bytearray()
        elif rec_type == 2:  # First
            current_record = bytearray(chunk)
        elif rec_type == 3:  # Middle
            current_record.extend(chunk)
        elif rec_type == 4:  # Last
            current_record.extend(chunk)
            records.append(bytes(current_record))
            current_record = bytearray()

    return records

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

def extract_text_representations(byte_data):
    """
    Chuyển đổi một khối byte thành các chuỗi văn bản đại diện:
    - UTF-8
    - UTF-16 LE (offset chẵn)
    - UTF-16 LE (offset lẻ)
    Hỗ trợ 100% các tin nhắn tiếng Việt có dấu được V8/Chromium/Electron lưu trữ dưới dạng 2-byte UTF-16LE.
    """
    texts = []
    if not byte_data or len(byte_data) == 0:
        return texts

    # 1. UTF-8
    try:
        texts.append(byte_data.decode("utf-8", errors="ignore"))
    except Exception:
        pass

    # 2. UTF-16 LE chẵn (offset 0)
    try:
        texts.append(byte_data.decode("utf-16le", errors="ignore"))
    except Exception:
        pass

    # 3. UTF-16 LE lẻ (offset 1)
    if len(byte_data) > 1:
        try:
            texts.append(byte_data[1:].decode("utf-16le", errors="ignore"))
        except Exception:
            pass

    return texts

def search_binary_ticker(data, ticker, case_sensitive=False):
    """
    Tìm kiếm trực tiếp mã cổ phiếu dưới dạng byte UTF-8 và UTF-16LE trong buffer nhị phân,
    cắt ra các cửa sổ văn bản giải mã đúng alignment để không bao giờ bỏ sót tin nhắn.
    """
    extracted_texts = []
    if not data or not ticker:
        return extracted_texts

    flags = 0 if case_sensitive else re.IGNORECASE

    # 1. Byte pattern UTF-8
    try:
        p_u8 = re.compile(rf"(?:^|[^\w\x00])({re.escape(ticker)})(?=[^\w\x00]|$)" .encode("utf-8"), flags)
        for m in p_u8.finditer(data):
            pos = m.start(1)
            start = max(0, pos - 1500)
            end = min(len(data), pos + 1500)
            try:
                extracted_texts.append(data[start:end].decode("utf-8", errors="ignore"))
            except Exception:
                pass
    except Exception:
        pass

    # 2. Byte pattern UTF-16 LE
    try:
        parts = []
        for c in ticker:
            if case_sensitive:
                parts.append(re.escape(c.encode("utf-16le")))
            else:
                parts.append(rb"[" + bytes([ord(c.lower()), ord(c.upper())]) + rb"]\x00")
        p_u16 = re.compile(rb"(?:^|[^\w\x00]|\x00)(" + rb"".join(parts) + rb")(?=[^\w\x00]|\x00|$)", re.IGNORECASE)
        for m in p_u16.finditer(data):
            match_pos = m.start(1)
            start = max(0, match_pos - 2000)
            if (match_pos - start) % 2 != 0:
                start += 1
            end = min(len(data), match_pos + 2000)
            try:
                extracted_texts.append(data[start:end].decode("utf-16le", errors="ignore"))
            except Exception:
                pass
    except Exception:
        pass

    return extracted_texts

def clean_surrounding_garbage(text):
    """Xóa các ký tự CJK, symbol lạ, control characters do lệch byte UTF-16LE ở đầu và cuối chuỗi."""
    if not text:
        return ""
    # Giữ nguyên tiếng Việt và ASCII, xóa các ký tự ngoài dải chữ viết thông thường
    text = re.sub(r"^[\u2200-\ufffd\x00-\x1f]+", "", text)
    text = re.sub(r"[\u2200-\ufffd\x00-\x1f]+$", "", text)
    return text.strip()

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
        add_dir(os.path.join(localappdata, "ZaloApp"))
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
            os.path.join("AppData", "Local", "ZaloApp"),
            os.path.join("AppData", "Local", "Programs", "Zalo"),
            os.path.join("AppData", "Roaming", "ZaloPC"),
            os.path.join("AppData", "Roaming", "ZaloApp"),
            os.path.join("AppData", "Roaming", "ZaloData"),
            os.path.join("AppData", "Roaming", "Zalo"),
            os.path.join("Documents", "Zalo Received Files"),
        ]:
            add_dir(os.path.join(user_home, sub))
    except Exception:
        pass

    # 4. Quét qua các profile người dùng trong C:\Users nếu chạy với quyền Admin
    try:
        system_drive = os.environ.get("SystemDrive", "C:")
        users_dir = os.path.join(system_drive, os.sep, "Users")
        if os.path.isdir(users_dir):
            for user_entry in os.listdir(users_dir):
                user_path = os.path.join(users_dir, user_entry)
                if os.path.isdir(user_path) and user_entry.lower() not in ["public", "all users", "default", "default user"]:
                    for sub in [
                        os.path.join("AppData", "Local", "ZaloPC"),
                        os.path.join("AppData", "Local", "Zalo"),
                        os.path.join("AppData", "Local", "ZaloData"),
                        os.path.join("AppData", "Roaming", "ZaloPC"),
                        os.path.join("AppData", "Roaming", "ZaloApp"),
                        os.path.join("AppData", "Roaming", "ZaloData"),
                    ]:
                        add_dir(os.path.join(user_path, sub))
    except Exception:
        pass

    # 5. Các ổ đĩa phổ biến nếu người dùng di chuyển Zalo sang ổ khác (D:\ZaloPC, E:\ZaloPC...)
    for drive in ["D:\\", "E:\\", "F:\\", "C:\\", "D:/", "E:/", "F:/", "C:/"]:
        for folder in ["ZaloPC", "Zalo", "ZaloData", "ZaloApp"]:
            add_dir(os.path.join(drive, folder))

    # 6. Linux / macOS (nếu chạy Wine hoặc client Zalo desktop tương ứng)
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

def find_all_zalo_storage_sources(search_dirs=None):
    """
    Tìm tất cả các file lưu trữ tin nhắn Zalo PC gồm cả SQLite (.db) và IndexedDB / LevelDB / Data files (.ldb, .log, .dat, .bin).
    Duyệt đệ quy và hỗ trợ cả symbolic links/junctions.
    Trả về tuple: (sqlite_paths, leveldb_paths)
    """
    if search_dirs is None:
        search_dirs = get_possible_zalo_dirs()

    found_sqlite = []
    found_leveldb = []
    seen = set()

    for s_dir in search_dirs:
        if not os.path.exists(s_dir):
            continue

        # Nếu đường dẫn trỏ trực tiếp đến 1 file
        if os.path.isfile(s_dir):
            abs_f = os.path.abspath(s_dir)
            if abs_f in seen:
                continue
            ext = os.path.splitext(abs_f)[1].lower()
            if (ext in [".db", ".sqlite", ".sqlite3"] or is_sqlite_file(abs_f)) and not is_leveldb_sstable(abs_f):
                seen.add(abs_f)
                found_sqlite.append(abs_f)
            else:
                seen.add(abs_f)
                found_leveldb.append(abs_f)
            continue

        for root, dirs, files in os.walk(s_dir, followlinks=True):
            dirs[:] = [d for d in dirs if d.lower() not in [
                "crashpad", "gpucache", "temp", "node_modules", ".git", "asset", "blob_storage", "code cache"
            ]]
            root_lower = root.lower()
            is_in_data_dir = any(k in root_lower for k in ["leveldb", "indexeddb", "storage", "sqlite", "zalopc", "zaloapp", "zalodata"])

            for file in files:
                lower_file = file.lower()
                ext = os.path.splitext(lower_file)[1]

                # Bỏ qua các file media, thực thi, ảnh, audio, web resources
                if ext in [
                    ".exe", ".dll", ".png", ".jpg", ".jpeg", ".gif", ".mp4", ".mp3", ".wav", ".ico",
                    ".webp", ".asar", ".zip", ".7z", ".rar", ".tmp", ".pak", ".node", ".json", ".css",
                    ".html", ".js", ".map", ".svg", ".ttf", ".woff", ".woff2"
                ]:
                    continue
                if lower_file.endswith("-wal") or lower_file.endswith("-shm") or lower_file.endswith("-journal") or lower_file in ["lock", "log.old"]:
                    continue

                full_path = os.path.abspath(os.path.join(root, file))
                if full_path in seen:
                    continue

                try:
                    sz = os.path.getsize(full_path)
                    if sz == 0 or sz > 600 * 1024 * 1024:
                        continue
                except Exception:
                    continue

                # 1. Kiểm tra SQLite
                if ext in [".db", ".sqlite", ".sqlite3"] or is_sqlite_file(full_path):
                    seen.add(full_path)
                    found_sqlite.append(full_path)
                    continue

                # 2. Kiểm tra LevelDB / IndexedDB / Data files
                if ext in [".ldb", ".sst"]:
                    seen.add(full_path)
                    found_leveldb.append(full_path)
                    continue

                if ext == ".log":
                    stem = os.path.splitext(lower_file)[0]
                    if stem.isdigit() or is_in_data_dir:
                        seen.add(full_path)
                        found_leveldb.append(full_path)
                        continue

                if is_in_data_dir or ext in [".dat", ".bin", ".data"] or ext == "" or is_leveldb_sstable(full_path):
                    seen.add(full_path)
                    found_leveldb.append(full_path)

    # Sắp xếp ưu tiên
    def score_sqlite(path):
        lower = os.path.basename(path).lower()
        score = 0
        if "message" in lower: score += 12
        if "chat" in lower: score += 10
        if "zalo" in lower: score += 6
        if "history" in lower: score += 6
        if "production" in lower: score += 5
        if "sqlite" in lower: score += 4
        return score

    def score_leveldb(path):
        lower = path.lower()
        score = 0
        if "indexeddb" in lower: score += 12
        if "file__0" in lower: score += 10
        if "chat.zalo.me" in lower: score += 8
        if lower.endswith(".ldb"): score += 5
        if lower.endswith(".log"): score += 4
        if lower.endswith(".dat"): score += 2
        return score

    found_sqlite.sort(key=score_sqlite, reverse=True)
    found_leveldb.sort(key=score_leveldb, reverse=True)

    return found_sqlite, found_leveldb

def find_all_sqlite_dbs(search_dirs=None):
    """Giữ lại hàm tìm SQLite để tương thích ngược với các module khác."""
    sql_dbs, _ = find_all_zalo_storage_sources(search_dirs)
    return sql_dbs

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
                for key in ["content", "message", "msg", "text", "body", "title", "description", "data", "caption", "subTitle", "desc"]:
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
    Hỗ trợ cả UTF-8 và UTF-16LE cho các cột dạng BLOB.
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
                cursor.execute(f"PRAGMA table_info([{table}]);")
                columns_info = cursor.fetchall()
                col_names = [col[1] for col in columns_info]

                if not col_names:
                    continue

                time_col, sender_col, group_col, content_candidates = find_best_columns(col_names)
                if not content_candidates:
                    continue

                selected_cols = [f"[{col}]" for col in col_names]
                query = f"SELECT {', '.join(selected_cols)} FROM [{table}]"
                cursor.execute(query)

                rows = cursor.fetchall()
                for row in rows:
                    row_dict = dict(zip(col_names, row))

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
                            candidates_t = extract_text_representations(raw_c)
                            for ct in candidates_t:
                                if pattern.search(ct):
                                    text_val = ct
                                    break
                        if text_val and pattern.search(text_val):
                            matched_content = extract_clean_text(text_val)
                            break

                    # 2. Nếu chưa thấy, thử các cột khác trong dòng
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
                                candidates_t = extract_text_representations(raw_c)
                                for ct in candidates_t:
                                    if pattern.search(ct):
                                        text_val = ct
                                        break
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
                            "content": clean_surrounding_garbage(matched_content),
                            "source_table": table,
                            "source_db": os.path.basename(db_path)
                        })
            except Exception:
                continue

    except Exception as e:
        # Nếu SQLite query lỗi (ví dụ file bị mã hóa hoặc non-standard), fallback quét nhị phân
        lvl_res, _ = inspect_and_filter_leveldb(temp_file, ticker, case_sensitive)
        if lvl_res:
            for r in lvl_res:
                r["source_db"] = os.path.basename(db_path)
            return lvl_res, None
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

def extract_balanced_json(text, pos, max_lookback=4000, max_lookforward=4000):
    """
    Trích xuất khối JSON bao quanh vị trí pos một cách cân bằng và chính xác.
    Hỗ trợ chuỗi có escape quote, nested objects.
    """
    start_search = max(0, pos - max_lookback)
    end_search = min(len(text), pos + max_lookforward)

    open_indices = [i for i in range(pos - 1, start_search - 1, -1) if text[i] == "{"]

    for start_idx in open_indices:
        depth = 0
        in_string = False
        escape = False
        for i in range(start_idx, end_search):
            c = text[i]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        if i >= pos:
                            chunk = text[start_idx:i+1]
                            try:
                                return json.loads(chunk)
                            except Exception:
                                pass
                        break
    return None

def extract_message_from_data(data, ticker_pattern):
    """
    Trích xuất các trường content, sender, group, time từ dữ liệu dict của Zalo.
    """
    if not isinstance(data, dict):
        return None

    content = None
    for k in ["content", "message", "msg", "text", "body", "dMsg", "description", "title", "caption", "subTitle", "desc", "raw_text"]:
        v = data.get(k)
        if isinstance(v, str) and ticker_pattern.search(v):
            content = extract_clean_text(v)
            break
        elif isinstance(v, dict):
            for sub_k in ["content", "message", "msg", "text", "title"]:
                sub_v = v.get(sub_k)
                if isinstance(sub_v, str) and ticker_pattern.search(sub_v):
                    content = extract_clean_text(sub_v)
                    break
        if content:
            break

    if not content:
        for k, v in data.items():
            if isinstance(v, str) and ticker_pattern.search(v) and 2 <= len(v) <= 3000:
                content = extract_clean_text(v)
                break

    if not content:
        return None

    sender = "Ẩn danh"
    for k in ["dName", "fromDName", "sender_name", "senderName", "displayName", "display_name", "from_name", "nickname", "sender", "author", "user_name", "username"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip() and v != content:
            sender = v.strip()
            break

    group = "Cá nhân/Nhóm"
    for k in ["room", "room_name", "groupName", "group_name", "conv_name", "thread_name", "chat_name", "title", "convName", "threadName"]:
        v = data.get(k)
        if isinstance(v, str) and v.strip() and v != content and v != sender and len(v) < 100:
            group = v.strip()
            break

    ts = None
    for k in ["ts", "time", "date", "created_time", "create_time", "sendTime", "send_time", "timestamp", "datetime"]:
        v = data.get(k)
        if v is not None:
            ts = v
            break

    return {
        "content": clean_surrounding_garbage(content),
        "sender": sender,
        "group": group,
        "time": format_timestamp(ts)
    }

def extract_from_raw_window(window, ticker_pattern):
    """
    Bóc tách thông tin tin nhắn từ cửa sổ văn bản khi không parse được JSON hoàn chỉnh.
    Hỗ trợ xử lý sạch sẽ các ký tự thừa do chuyển đổi UTF-16LE.
    """
    content = None
    c_m = re.search(r'"(?:content|message|msg|text|body|dMsg|description|title)"\s*:\s*"((?:\\.|[^"\\])+)"', window)
    if c_m:
        raw_c = c_m.group(1)
        try:
            raw_c = json.loads(f'"{raw_c}"')
        except Exception:
            pass
        if ticker_pattern.search(raw_c):
            content = extract_clean_text(raw_c)

    if not content:
        m = ticker_pattern.search(window)
        if not m:
            return None
        start = m.start()
        s = start
        while s > 0 and ord(window[s-1]) >= 32 and window[s-1] not in "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0e\x0f":
            s -= 1
        e = m.end()
        while e < len(window) and ord(window[e]) >= 32 and window[e] not in "\x00\x01\x02\x03\x04\x05\x06\x07\x08\x0e\x0f":
            e += 1
        cand = window[s:e].strip()
        if len(cand) >= 3 and ticker_pattern.search(cand):
            content = cand

    if not content:
        return None

    content = clean_surrounding_garbage(content)
    if not content or len(content) > 3000:
        return None

    sender = "Ẩn danh"
    s_m = re.search(r'"(?:dName|fromDName|sender_name|senderName|displayName|display_name|from_name|author|nickname|sender)"\s*:\s*"((?:\\.|[^"\\])+)"', window)
    if s_m:
        raw_s = s_m.group(1)
        try:
            raw_s = json.loads(f'"{raw_s}"')
        except Exception:
            pass
        if raw_s and raw_s != content:
            sender = raw_s.strip()

    group = "Cá nhân/Nhóm"
    g_m = re.search(r'"(?:room|room_name|groupName|group_name|conv_name|thread_name|chat_name|title)"\s*:\s*"((?:\\.|[^"\\])+)"', window)
    if g_m:
        raw_g = g_m.group(1)
        try:
            raw_g = json.loads(f'"{raw_g}"')
        except Exception:
            pass
        if raw_g and raw_g != content and raw_g != sender and len(raw_g) < 100:
            group = raw_g.strip()

    time_val = ""
    t_m = re.search(r'"(?:ts|created_time|create_time|sendTime|send_time|timestamp|time)"\s*:\s*(\d{10,16})', window)
    if t_m:
        time_val = format_timestamp(t_m.group(1))

    return {
        "content": content,
        "sender": sender,
        "group": group,
        "time": time_val
    }

def inspect_and_filter_leveldb(file_path, ticker, case_sensitive=False):
    """
    Quét một file LevelDB / IndexedDB / Data (.ldb, .sst, .log, .dat, .bin) để tìm các tin nhắn chứa mã cổ phiếu.
    Hỗ trợ giải nén Snappy SSTable blocks, WAL 32KB log records, và phân tích đa bảng mã UTF-8 & UTF-16LE.
    """
    results = []
    try:
        if not os.path.isfile(file_path) or os.path.getsize(file_path) == 0:
            return results, None
    except Exception:
        return results, None

    # Đọc nội dung file an toàn (nếu bị lock thì thử copy sang file tạm)
    file_bytes = None
    try:
        with open(file_path, "rb") as f:
            file_bytes = f.read()
    except PermissionError:
        try:
            temp_dir = tempfile.gettempdir()
            temp_copy = os.path.join(temp_dir, f"lvl_scan_{uuid.uuid4().hex[:8]}_{os.path.basename(file_path)}")
            shutil.copyfile(file_path, temp_copy)
            with open(temp_copy, "rb") as f:
                file_bytes = f.read()
            try:
                os.remove(temp_copy)
            except Exception:
                pass
        except Exception as e:
            return results, f"Không thể truy cập file {os.path.basename(file_path)}: {e}"
    except Exception as e:
        return results, f"Lỗi đọc file {os.path.basename(file_path)}: {e}"

    if not file_bytes:
        return results, None

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(rf"\b{re.escape(ticker)}\b", flags)

    # Thu thập các đoạn văn bản (text blocks) từ file với cả UTF-8 và UTF-16LE
    text_chunks = []

    # 1. Nếu là SSTable (.ldb / .sst), giải mã các Data Blocks
    ext = os.path.splitext(file_path)[1].lower()
    is_sstable = is_leveldb_sstable(file_path) or ext in [".ldb", ".sst"]

    if is_sstable:
        sstable_blocks = extract_sstable_blocks(file_bytes)
        for block in sstable_blocks:
            text_chunks.extend(extract_text_representations(block))

    # 2. Nếu là file log (.log), trích xuất các bản ghi 32KB
    if ext == ".log" or "log" in ext:
        log_recs = extract_log_records(file_bytes)
        for rec in log_recs:
            text_chunks.extend(extract_text_representations(rec))

    # 3. Luôn decode toàn bộ file bytes thô (cả UTF-8 và UTF-16LE chẵn/lẻ)
    text_chunks.extend(extract_text_representations(file_bytes))

    # 4. Trích xuất cửa sổ nhị phân trực tiếp (Binary pattern search) để đảm bảo không bỏ sót bất kỳ byte nào
    bin_windows = search_binary_ticker(file_bytes, ticker, case_sensitive)
    text_chunks.extend(bin_windows)

    file_name = os.path.basename(file_path)

    # Quét và bóc tách tin nhắn
    for text in text_chunks:
        if not text:
            continue
        # Tìm nhanh xem có chứa ticker không trước khi chạy finditer
        if not pattern.search(text):
            continue

        for m in pattern.finditer(text):
            pos = m.start()
            msg_info = None

            # Thử bóc tách JSON cân bằng
            json_data = extract_balanced_json(text, pos)
            if json_data:
                msg_info = extract_message_from_data(json_data, pattern)

            # Nếu chưa được, thử fallback theo cửa sổ văn bản
            if not msg_info or not msg_info.get("content"):
                window = text[max(0, pos - 1500) : min(len(text), pos + 1500)]
                msg_info = extract_from_raw_window(window, pattern)

            if msg_info and msg_info.get("content") and pattern.search(msg_info["content"]):
                c = msg_info["content"].strip()
                # Loại trừ trường hợp content chỉ là URL hoặc path
                if c.startswith(("http://", "https://")) and " " not in c:
                    continue

                results.append({
                    "ticker": ticker.upper(),
                    "time": msg_info.get("time", ""),
                    "sender": msg_info.get("sender", "Ẩn danh"),
                    "group": msg_info.get("group", "Cá nhân/Nhóm"),
                    "content": c,
                    "source_table": "IndexedDB / LevelDB",
                    "source_db": file_name
                })

    # Khử trùng lặp nội bộ trong cùng 1 file (giữa data block, raw bytes và binary search)
    unique_file_results = []
    seen = set()
    for r in results:
        key = (r.get("time", ""), r.get("sender", ""), r.get("content", "").strip())
        if key not in seen:
            seen.add(key)
            unique_file_results.append(r)

    return unique_file_results, None

def scan_messages_for_ticker(ticker, progress_callback=None):
    """
    Quét toàn bộ các cơ sở dữ liệu Zalo (cả SQLite và IndexedDB/LevelDB) tìm thấy để lọc tin nhắn chứa ticker.
    Hỗ trợ progress_callback(msg, percent) để cập nhật giao diện.
    Ghi nhận đầy đủ thông tin chẩn đoán (diagnostics) để người dùng có thể kiểm tra.
    """
    global _last_scan_diagnostics
    ticker = ticker.strip().upper()
    if not ticker:
        return [], "Mã cổ phiếu không hợp lệ."

    config = load_config()
    case_sensitive = config.get("case_sensitive", False)

    if progress_callback:
        progress_callback("Đang tìm kiếm thư mục và cơ sở dữ liệu (SQLite & LevelDB)...", 5)

    zalo_dirs = get_possible_zalo_dirs()
    sqlite_dbs, leveldb_files = find_all_zalo_storage_sources(zalo_dirs)
    total_files = len(sqlite_dbs) + len(leveldb_files)

    _last_scan_diagnostics = {
        "checked_dirs": zalo_dirs,
        "sqlite_dbs": [{"path": p, "size": os.path.getsize(p) if os.path.exists(p) else 0} for p in sqlite_dbs],
        "leveldb_files": [{"path": p, "size": os.path.getsize(p) if os.path.exists(p) else 0} for p in leveldb_files],
        "total_sources": total_files,
        "last_ticker": ticker,
        "results_count": 0,
        "errors": [],
        "summary_text": ""
    }

    if total_files == 0:
        checked_dirs_str = "\n".join(f" - {d}" for d in zalo_dirs) if zalo_dirs else " (Không tìm thấy thư mục nào)"
        err_msg = (
            f"Không tìm thấy file cơ sở dữ liệu Zalo PC nào (SQLite hoặc LevelDB) trên máy tính này.\n\n"
            f"Đã tự động kiểm tra các thư mục:\n{checked_dirs_str}\n\n"
            f"Hướng dẫn khắc phục:\n"
            f"1. Hãy đảm bảo Zalo PC đã được cài đặt và bạn đã đăng nhập tài khoản.\n"
            f"2. Nếu bạn cài hoặc chuyển Zalo sang ổ khác (như D:\\ZaloPC), hãy nhấn nút '⚙️ Chọn Thư Mục Zalo' để trỏ đến thư mục đó."
        )
        _last_scan_diagnostics["errors"].append(err_msg)
        return [], err_msg

    all_results = []
    errors = []
    processed = 0

    # 1. Quét các file SQLite
    for db_path in sqlite_dbs:
        processed += 1
        db_name = os.path.basename(db_path)
        percent = int(5 + (processed / total_files) * 85)
        if progress_callback:
            progress_callback(f"Đang quét SQLite ({processed}/{total_files}): {db_name}...", percent)

        res, err = inspect_and_filter_db(db_path, ticker, case_sensitive)
        if res:
            all_results.extend(res)
        if err:
            errors.append(err)

    # 2. Quét các file LevelDB / IndexedDB / Data files (.ldb, .log, .dat...)
    for lvl_path in leveldb_files:
        processed += 1
        lvl_name = os.path.basename(lvl_path)
        percent = int(5 + (processed / total_files) * 85)
        if progress_callback:
            progress_callback(f"Đang quét LevelDB ({processed}/{total_files}): {lvl_name}...", percent)

        res, err = inspect_and_filter_leveldb(lvl_path, ticker, case_sensitive)
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
        content_clean = item["content"].strip()
        key = (item["time"], item["sender"], content_clean)
        if key not in seen_keys:
            seen_keys.add(key)
            unique_results.append(item)

    # Sắp xếp theo thời gian mới nhất lên đầu
    unique_results.sort(key=lambda x: str(x["time"]), reverse=True)

    # Cập nhật thông tin chẩn đoán
    _last_scan_diagnostics["results_count"] = len(unique_results)
    _last_scan_diagnostics["errors"] = errors
    summary_lines = [
        f"=== BÁO CÁO CHI TIẾT QUÉT DỮ LIỆU ZALO PC ===",
        f"Mã tìm kiếm: {ticker}",
        f"Thời điểm quét: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Tổng số nguồn dữ liệu tìm thấy: {total_files} ({len(sqlite_dbs)} SQLite, {len(leveldb_files)} LevelDB/IndexedDB)",
        f"Tổng số tin nhắn tìm thấy: {len(unique_results)} tin nhắn",
        "",
        "--- CÁC THƯ MỤC ĐÃ ĐƯỢC KIỂM TRA ---"
    ]
    for d in zalo_dirs:
        summary_lines.append(f"  * {d}")
    summary_lines.append("")
    summary_lines.append("--- CÁC FILE CƠ SỞ DỮ LIỆU ĐÃ QUÉT ---")
    for item in _last_scan_diagnostics["sqlite_dbs"]:
        size_kb = item["size"] // 1024
        summary_lines.append(f"  [SQLite] {item['path']} ({size_kb} KB)")
    for item in _last_scan_diagnostics["leveldb_files"]:
        size_kb = item["size"] // 1024
        summary_lines.append(f"  [LevelDB] {item['path']} ({size_kb} KB)")
    
    _last_scan_diagnostics["summary_text"] = "\n".join(summary_lines)

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
