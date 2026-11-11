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
    format_timestamp,
    extract_clean_text,
    find_best_columns,
    inspect_and_filter_db,
    scan_messages_for_ticker,
    export_results,
    generate_html_report,
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

if __name__ == "__main__":
    unittest.main()
