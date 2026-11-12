import os
import sys
import json

CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "watchlist": ["CEO", "DIG", "DBC", "HPG", "SSI", "VND"],
    "default_ticker": "CEO",
    "only_today": True,
    "case_sensitive": False,
    "output_dir": "reports",
    "export_format": "both", # "csv", "html", or "both"
    "custom_zalo_path": ""
}

def get_base_dir():
    """Lấy thư mục gốc chứa file thực thi hoặc script."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def get_config_path():
    return os.path.join(get_base_dir(), CONFIG_FILE)

def load_config():
    config_path = get_config_path()
    if not os.path.exists(config_path):
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Đảm bảo có đủ các key mặc định nếu file cũ thiếu
            updated = False
            for k, v in DEFAULT_CONFIG.items():
                if k not in data:
                    data[k] = v
                    updated = True
            if updated:
                save_config(data)
            return data
    except Exception as e:
        print(f"[!] Lỗi khi đọc config ({e}), khôi phục về mặc định.")
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(config):
    config_path = get_config_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"[!] Lỗi khi lưu config: {e}")
        return False
