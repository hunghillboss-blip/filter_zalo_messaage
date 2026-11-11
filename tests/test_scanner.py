import os
import sys
import unittest
import tempfile
import sqlite3
import json
import shutil
import time

# Thêm thư mục gốc vào path để import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from scanner import (
    is_sqlite_file,
    get_possible_zalo_dirs,
    find_all_sqlite_dbs,
    find_all_zalo_storage_sources,
    format_timestamp,
    extract_clean_text,
    find_best_columns,
    inspect_and_filter_db,
    inspect_and_filter_leveldb,
    decompress_snappy,
    scan_messages_for_ticker,
    export_results,
    generate_html_report,
    get_last_scan_diagnostics,
    clean_surrounding_garbage,
    search_binary_ticker,
    extract_text_representations,
)
from config_manager import load_config, save_config

class TestScanner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_is_sqlite_file(self):
        # File không tồn tại
        self.assertFalse(is_sqlite_file(os.path.join(self.test_dir, "nonexistent.db")))

        # File rỗng
        empty_file = os.path.join(self.test_dir, "empty.db")
        with open(empty_file, "wb") as f:
            f.write(b"")
        self.assertFalse(is_sqlite_file(empty_file))

        # File văn bản thường
        text_file = os.path.join(self.test_dir, "plain.txt")
        with open(text_file, "w") as f:
            f.write("Hello world " * 20)
        self.assertFalse(is_sqlite_file(text_file))

        # File SQLite hợp lệ
        real_db = os.path.join(self.test_dir, "real.db")
        conn = sqlite3.connect(real_db)
        conn.execute("CREATE TABLE t (id INT, name TEXT);")
        conn.commit()
        conn.close()
        self.assertTrue(is_sqlite_file(real_db))

    def test_format_timestamp(self):
        # Empty
        self.assertEqual(format_timestamp(None), "")
        self.assertEqual(format_timestamp(""), "")

        # Seconds (1700000000 = 2023-11-14 ...)
        ts_sec = 1700000000
        formatted_sec = format_timestamp(ts_sec)
        self.assertTrue(formatted_sec.startswith("2023-11"))

        # Milliseconds (1700000000000)
        ts_msec = 1700000000000
        formatted_msec = format_timestamp(ts_msec)
        self.assertTrue(formatted_msec.startswith("2023-11"))

        # Microseconds (> 1e14: 1700000000000000)
        ts_usec = 1700000000000000
        formatted_usec = format_timestamp(ts_usec)
        self.assertTrue(formatted_usec.startswith("2023-11"))

        # String datetime giữ nguyên
        self.assertEqual(format_timestamp("2024-05-01 10:00:00"), "2024-05-01 10:00:00")

    def test_extract_clean_text(self):
        # Plain text
        self.assertEqual(extract_clean_text("Khuyến nghị mua HPG giá 28"), "Khuyến nghị mua HPG giá 28")

        # JSON payload
        payload = json.dumps({"content": "Khuyến nghị bán SSI", "msgType": 1})
        self.assertEqual(extract_clean_text(payload), "Khuyến nghị bán SSI")

        payload_msg = json.dumps({"message": "CEO target 25", "id": 12345})
        self.assertEqual(extract_clean_text(payload_msg), "CEO target 25")

        # None or number
        self.assertEqual(extract_clean_text(None), "")
        self.assertEqual(extract_clean_text(123), "123")

    def test_find_best_columns(self):
        # Đảm bảo msgId không bị chọn làm content_col
        cols = ["msgId", "cliMsgId", "timestamp", "sender_name", "group_name", "content"]
        time_col, sender_col, group_col, candidates = find_best_columns(cols)
        self.assertEqual(time_col, "timestamp")
        self.assertEqual(sender_col, "sender_name")
        self.assertEqual(group_col, "group_name")
        self.assertIn("content", candidates)
        self.assertNotIn("msgId", candidates)
        self.assertNotIn("cliMsgId", candidates)

    def test_inspect_and_filter_db_with_wal(self):
        db_path = os.path.join(self.test_dir, "messages.db")
        conn = sqlite3.connect(db_path)
        # Bật WAL mode
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("""
            CREATE TABLE chat_messages (
                msgId INTEGER PRIMARY KEY,
                created_time BIGINT,
                sender_name TEXT,
                room_name TEXT,
                message TEXT
            );
        """)

        # Insert 3 messages: 1 matching CEO, 1 matching CEOL (không được match vì boundary), 1 unrelated
        now_ms = int(time.time() * 1000)
        conn.execute("""
            INSERT INTO chat_messages (msgId, created_time, sender_name, room_name, message)
            VALUES (?, ?, ?, ?, ?)
        """, (1, now_ms, "Admin VIP", "Room Chứng Khoán", "Mọi người gom CEO quanh giá 18 nhé"))

        conn.execute("""
            INSERT INTO chat_messages (msgId, created_time, sender_name, room_name, message)
            VALUES (?, ?, ?, ?, ?)
        """, (2, now_ms, "Member", "Room Chứng Khoán", "Cổ phiếu CEOL hôm nay tăng trần"))

        conn.execute("""
            INSERT INTO chat_messages (msgId, created_time, sender_name, room_name, message)
            VALUES (?, ?, ?, ?, ?)
        """, (3, now_ms, "Member", "Room Chứng Khoán", "HPG đang tích lũy tốt"))

        conn.commit()
        # Không đóng hoặc checkpoint để dữ liệu nằm trong WAL
        
        # Test lọc mã CEO
        results, err = inspect_and_filter_db(db_path, "CEO")
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "CEO")
        self.assertEqual(results[0]["sender"], "Admin VIP")
        self.assertEqual(results[0]["group"], "Room Chứng Khoán")
        self.assertIn("gom CEO quanh giá 18", results[0]["content"])

        # Test lọc mã HPG
        res_hpg, _ = inspect_and_filter_db(db_path, "HPG")
        self.assertEqual(len(res_hpg), 1)

        # Test lọc mã SSI (không có)
        res_ssi, _ = inspect_and_filter_db(db_path, "SSI")
        self.assertEqual(len(res_ssi), 0)

        conn.close()

    def test_scan_and_export(self):
        # Tạo cấu trúc thư mục giả lập Zalo
        zalo_dir = os.path.join(self.test_dir, "ZaloPC", "12345678")
        os.makedirs(zalo_dir, exist_ok=True)
        db_path = os.path.join(zalo_dir, "message.db")

        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                time BIGINT,
                sender TEXT,
                group_name TEXT,
                content TEXT
            );
        """)
        conn.execute("""
            INSERT INTO messages VALUES (1, 1700000000000, 'Broker', 'VIP Room', 'KBC và VCG điểm mua đẹp');
        """)
        conn.commit()
        conn.close()

        # Quét với đường dẫn chỉ định
        dbs = find_all_sqlite_dbs([self.test_dir])
        self.assertTrue(len(dbs) >= 1)

        # Cấu hình custom_zalo_path trỏ tới test_dir
        cfg = load_config()
        orig_custom = cfg.get("custom_zalo_path", "")
        try:
            cfg["custom_zalo_path"] = self.test_dir
            save_config(cfg)

            results, err = scan_messages_for_ticker("KBC")
            self.assertIsNone(err)
            self.assertEqual(len(results), 1)
            self.assertIn("KBC", results[0]["content"])

            # Xuất báo cáo CSV & HTML
            exported_files = export_results(results, "KBC")
            self.assertTrue(len(exported_files) >= 1)
            for f in exported_files:
                self.assertTrue(os.path.exists(f))
                self.assertGreater(os.path.getsize(f), 0)
        finally:
            cfg["custom_zalo_path"] = orig_custom
            save_config(cfg)

    def test_decompress_snappy(self):
        # 1. Literal đơn giản
        raw_msg = b"Hello Zalo LevelDB"
        snappy_lit = bytes([len(raw_msg), (len(raw_msg) - 1) << 2]) + raw_msg
        self.assertEqual(decompress_snappy(snappy_lit), raw_msg)

        # 2. Chuỗi lặp lại kiểm tra copy offset
        # Chuỗi: "ABCDEABCDE" (10 bytes) -> 5 bytes literal "ABCDE", copy 5 bytes với offset 5
        snappy_copy = bytes([10, (4 << 2) | 0]) + b"ABCDE" + bytes([((1 << 2) | 1), 5])
        self.assertEqual(decompress_snappy(snappy_copy), b"ABCDEABCDE")

        # 3. Dữ liệu rỗng
        self.assertEqual(decompress_snappy(b""), b"")

    def test_inspect_and_filter_leveldb_log(self):
        # Giả lập file 000003.log của LevelDB chứa tin nhắn JSON Zalo PC giống hệt như ảnh của người dùng
        log_file = os.path.join(self.test_dir, "000003.log")
        user_msg = {
            "msgId": "8234729384",
            "cliMsgId": "1715000000000",
            "dName": "Quốc Cường Ichimoku",
            "content": "vùng cản ngắn hạn của CEO vẫn là 13-13.5 a nhé",
            "ts": 1715000000000,
            "room": "Kỹ Thuật Nhóm Bất Động Sản"
        }
        json_bytes = json.dumps(user_msg, ensure_ascii=False).encode("utf-8")
        
        # Ghi log LevelDB dạng 32KB block
        import struct
        record_header = struct.pack("<IHb", 0x12345678, len(json_bytes), 1)
        with open(log_file, "wb") as f:
            f.write(record_header + json_bytes)

        # Quét mã CEO
        results, err = inspect_and_filter_leveldb(log_file, "CEO")
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "CEO")
        self.assertEqual(results[0]["sender"], "Quốc Cường Ichimoku")
        self.assertEqual(results[0]["group"], "Kỹ Thuật Nhóm Bất Động Sản")
        self.assertIn("vùng cản ngắn hạn của CEO", results[0]["content"])
        self.assertEqual(results[0]["source_db"], "000003.log")
        self.assertEqual(results[0]["source_table"], "IndexedDB / LevelDB")

    def test_inspect_and_filter_leveldb_sstable(self):
        # Tạo file 000005.ldb hợp lệ có footer, index block và data block
        import struct

        def make_varint(val):
            buf = bytearray()
            while True:
                b = val & 0x7f
                val >>= 7
                if val:
                    buf.append(b | 0x80)
                else:
                    buf.append(b)
                    break
            return bytes(buf)

        msg_payload = json.dumps({
            "dName": "Broker Pro",
            "content": "Khuyến nghị chốt lời CEO giá 14",
            "ts": 1715000000000,
            "room": "VIP Đầu Tư"
        }, ensure_ascii=False).encode("utf-8")

        data_block = msg_payload + struct.pack("<II", 0, 1)
        data_block_bytes = data_block + b"\x00" + b"\x00\x00\x00\x00"

        val = make_varint(0) + make_varint(len(data_block))
        index_entry = make_varint(0) + make_varint(3) + make_varint(len(val)) + b"key" + val
        index_block = index_entry + struct.pack("<II", 0, 1)
        index_block_bytes = index_block + b"\x00" + b"\x00\x00\x00\x00"

        file_content = data_block_bytes + index_block_bytes
        idx_off = len(data_block_bytes)
        idx_sz = len(index_block)

        meta_h = make_varint(0) + make_varint(0)
        idx_h = make_varint(idx_off) + make_varint(idx_sz)
        footer_body = meta_h + idx_h
        padding = b"\x00" * (48 - len(footer_body) - 8)
        footer = footer_body + padding + b"\x57\xfb\x80\x8b\x24\x75\x47\xdb"
        file_content += footer

        sstable_file = os.path.join(self.test_dir, "000005.ldb")
        with open(sstable_file, "wb") as f:
            f.write(file_content)

        results, err = inspect_and_filter_leveldb(sstable_file, "CEO")
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "CEO")
        self.assertEqual(results[0]["sender"], "Broker Pro")
        self.assertEqual(results[0]["group"], "VIP Đầu Tư")
        self.assertIn("chốt lời CEO", results[0]["content"])

    def test_find_all_zalo_storage_sources(self):
        # Tạo cấu trúc thư mục gồm cả SQLite và IndexedDB
        zalo_root = os.path.join(self.test_dir, "ZaloPC_Test")
        sqlite_dir = os.path.join(zalo_root, "sqlite", "_production")
        idb_dir = os.path.join(zalo_root, "123456", "IndexedDB", "file__0.indexeddb.leveldb")
        os.makedirs(sqlite_dir, exist_ok=True)
        os.makedirs(idb_dir, exist_ok=True)

        # 1. Tạo SQLite file
        conn = sqlite3.connect(os.path.join(sqlite_dir, "app.db"))
        conn.execute("CREATE TABLE t (id INT);")
        conn.close()

        # 2. Tạo LevelDB files
        with open(os.path.join(idb_dir, "000001.log"), "wb") as f:
            f.write(b"LevelDB log test")
        with open(os.path.join(idb_dir, "000002.ldb"), "wb") as f:
            f.write(b"\x00" * 40 + b"\x57\xfb\x80\x8b\x24\x75\x47\xdb")

        sql_found, lvl_found = find_all_zalo_storage_sources([zalo_root])
        self.assertEqual(len(sql_found), 1)
        self.assertTrue(sql_found[0].endswith("app.db"))
        self.assertEqual(len(lvl_found), 2)
        found_basenames = [os.path.basename(f) for f in lvl_found]
        self.assertIn("000001.log", found_basenames)
        self.assertIn("000002.ldb", found_basenames)

    def test_inspect_and_filter_leveldb_utf16le(self):
        # Kiểm tra phát hiện tin nhắn tiếng Việt được mã hóa UTF-16LE trong LevelDB/IndexedDB
        utf16_file = os.path.join(self.test_dir, "000088.log")
        user_msg = {
            "dName": "Quốc Cường Ichimoku",
            "content": "vùng cản ngắn hạn của CEO vẫn là 13-13.5 a nhé",
            "room": "Kỹ Thuật Nhóm Bất Động Sản",
            "ts": 1715000000000
        }
        json_bytes = json.dumps(user_msg, ensure_ascii=False).encode("utf-16le")
        with open(utf16_file, "wb") as f:
            f.write(b"\x00\x01\x02\x03" + json_bytes + b"\x00\x00")

        results, err = inspect_and_filter_leveldb(utf16_file, "CEO")
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "CEO")
        self.assertEqual(results[0]["sender"], "Quốc Cường Ichimoku")
        self.assertEqual(results[0]["group"], "Kỹ Thuật Nhóm Bất Động Sản")
        self.assertIn("vùng cản ngắn hạn của CEO", results[0]["content"])

    def test_search_binary_ticker_utf16le_odd(self):
        # Kiểm tra chuỗi UTF-16LE bắt đầu tại byte offset lẻ (odd byte offset)
        msg = "khuyến nghị mua CEO giá 12.5"
        raw = b"\xaa\xbb\xcc" + msg.encode("utf-16le") + b"\xdd"
        extracted = search_binary_ticker(raw, "CEO")
        self.assertTrue(len(extracted) >= 1)
        self.assertIn("khuyến nghị mua CEO", extracted[0])

    def test_clean_surrounding_garbage(self):
        # Kiểm tra loại bỏ ký tự CJK do lệch byte UTF-16LE
        raw_text = "瞈vùng cản ngắn hạn của CEO vẫn là 13-13.5 a nhé∑"
        cleaned = clean_surrounding_garbage(raw_text)
        self.assertEqual(cleaned, "vùng cản ngắn hạn của CEO vẫn là 13-13.5 a nhé")

    def test_inspect_and_filter_db_utf16le_blob(self):
        # Kiểm tra SQLite lưu tin nhắn dưới dạng BLOB UTF-16LE
        db_path = os.path.join(self.test_dir, "blob_test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE t (id INT, sender TEXT, payload BLOB, send_time BIGINT);")
        viet_msg = "Chốt lời CEO quanh giá 14".encode("utf-16le")
        conn.execute("INSERT INTO t VALUES (1, 'Admin', ?, 1700000000000);", (sqlite3.Binary(viet_msg),))
        conn.commit()
        conn.close()

        results, err = inspect_and_filter_db(db_path, "CEO")
        self.assertIsNone(err)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["sender"], "Admin")
        self.assertIn("Chốt lời CEO quanh giá 14", results[0]["content"])

    def test_diagnostics(self):
        # Kiểm tra cấu trúc nhật ký chẩn đoán quét
        scan_messages_for_ticker("CEO")
        diag = get_last_scan_diagnostics()
        self.assertIn("checked_dirs", diag)
        self.assertIn("total_sources", diag)
        self.assertIn("summary_text", diag)
        self.assertEqual(diag["last_ticker"], "CEO")

if __name__ == "__main__":
    unittest.main()

