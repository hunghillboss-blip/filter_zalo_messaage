import os
import sys

# Đảm bảo an toàn khi chạy chế độ windowed không có console trên Windows
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

def main():
    # Nếu có tham số dòng lệnh thì chạy CLI
    if len(sys.argv) > 1:
        if "--gui" in sys.argv:
            from gui import launch_gui
            launch_gui()
        else:
            from cli import main as cli_main
            cli_main()
    else:
        # Mặc định khi người dùng click đúp file .exe thì mở giao diện đồ họa
        try:
            from gui import launch_gui
            launch_gui()
        except Exception as e:
            # Nếu chạy trên môi trường không có GUI (server/headless), tự động chuyển sang CLI
            try:
                print(f"[!] Không thể khởi động giao diện đồ họa ({e}). Đang chuyển sang dòng lệnh...")
            except Exception:
                pass
            from cli import main as cli_main
            cli_main()

if __name__ == "__main__":
    main()
