import sys
import argparse
from config_manager import load_config, save_config
from scanner import (
    scan_messages_for_ticker,
    export_results,
    find_all_sqlite_dbs,
    find_all_zalo_storage_sources,
    get_possible_zalo_dirs,
    get_last_scan_diagnostics
)

def menu_zalo_dir(config):
    while True:
        custom_path = config.get("custom_zalo_path", "").strip()
        print("\n" + "="*45)
        print("    CẤU HÌNH THƯ MỤC DỮ LIỆU ZALO PC")
        print("="*45)
        if custom_path:
            print(f"Thư mục hiện tại (Tùy chỉnh): {custom_path}")
        else:
            print("Thư mục hiện tại: Tự động phát hiện (%LOCALAPPDATA%\\ZaloPC & %APPDATA%\\ZaloApp)")

        # Thử tìm kiếm database với cấu hình hiện tại
        sql_dbs, lvl_files = find_all_zalo_storage_sources()
        total_sources = len(sql_dbs) + len(lvl_files)
        print(f"Trạng thái: Đã phát hiện {total_sources} nguồn dữ liệu Zalo ({len(sql_dbs)} SQLite, {len(lvl_files)} LevelDB/IndexedDB).")
        if sql_dbs or lvl_files:
            all_preview = sql_dbs + lvl_files
            for idx, db in enumerate(all_preview[:5], start=1):
                print(f"  {idx}. {db}")
            if total_sources > 5:
                print(f"  ... và {total_sources - 5} file khác.")

        print("\nCác tùy chọn:")
        print("  1. Thiết lập đường dẫn thư mục Zalo mới")
        print("  2. Đặt lại về tự động phát hiện mặc định")
        print("  0. Quay lại menu chính")

        opt = input("Nhập lựa chọn: ").strip()
        if opt == "1":
            new_path = input("Nhập đường dẫn đầy đủ đến thư mục Zalo PC: ").strip()
            if new_path:
                config["custom_zalo_path"] = new_path
                save_config(config)
                print(f"[+] Đã lưu đường dẫn: {new_path}")
        elif opt == "2":
            config["custom_zalo_path"] = ""
            save_config(config)
            print("[+] Đã đặt lại về chế độ tự động phát hiện.")
        elif opt == "0":
            break

def menu_watchlist(config):
    while True:
        print("\n" + "="*35)
        print("     QUẢN LÝ DANH SÁCH MÃ")
        print("="*35)
        print(f"Mã mặc định hiện tại: [{config.get('default_ticker', 'Chưa đặt')}]")
        print("Danh sách theo dõi:")
        for idx, t in enumerate(config.get("watchlist", []), start=1):
            tag = " (Mặc định)" if t == config.get("default_ticker") else ""
            print(f"  {idx}. {t}{tag}")
        
        print("\nCác tùy chọn:")
        print("  1. Thêm mã mới")
        print("  2. Xóa mã")
        print("  3. Đặt mã làm mặc định")
        print("  0. Quay lại menu chính")
        
        opt = input("Nhập lựa chọn: ").strip()
        
        if opt == "1":
            new_ticker = input("Nhập mã cổ phiếu muốn thêm: ").strip().upper()
            if not new_ticker:
                continue
            if new_ticker in config["watchlist"]:
                print(f"[!] Mã {new_ticker} đã tồn tại trong danh sách.")
            else:
                config["watchlist"].append(new_ticker)
                if not config.get("default_ticker"):
                    config["default_ticker"] = new_ticker
                save_config(config)
                print(f"[+] Đã thêm mã {new_ticker}.")
                
        elif opt == "2":
            del_ticker = input("Nhập mã hoặc số thứ tự cần xóa: ").strip().upper()
            target = None
            if del_ticker.isdigit():
                idx = int(del_ticker) - 1
                if 0 <= idx < len(config["watchlist"]):
                    target = config["watchlist"][idx]
            elif del_ticker in config["watchlist"]:
                target = del_ticker
                
            if target:
                config["watchlist"].remove(target)
                if config.get("default_ticker") == target:
                    config["default_ticker"] = config["watchlist"][0] if config["watchlist"] else ""
                save_config(config)
                print(f"[+] Đã xóa mã {target}.")
            else:
                print("[!] Không tìm thấy mã cần xóa.")

        elif opt == "3":
            def_ticker = input("Nhập mã hoặc số thứ tự muốn đặt làm mặc định: ").strip().upper()
            target = None
            if def_ticker.isdigit():
                idx = int(def_ticker) - 1
                if 0 <= idx < len(config["watchlist"]):
                    target = config["watchlist"][idx]
            elif def_ticker in config["watchlist"]:
                target = def_ticker
                
            if target:
                config["default_ticker"] = target
                save_config(config)
                print(f"[+] Đã đặt [{target}] làm mã mặc định.")
            else:
                print("[!] Không tìm thấy mã hợp lệ.")

        elif opt == "0":
            break

def print_diagnostics():
    diag = get_last_scan_diagnostics()
    summary = diag.get("summary_text", "")
    if summary:
        print("\n" + summary)
    else:
        print("\n[i] Chưa có dữ liệu quét gần nhất. Đang thực hiện kiểm tra cấu hình...")
        zdirs = get_possible_zalo_dirs()
        sql, lvl = find_all_zalo_storage_sources(zdirs)
        print(f"Tổng số thư mục Zalo tìm thấy: {len(zdirs)}")
        for d in zdirs:
            print(f"  * {d}")
        print(f"Tổng số nguồn dữ liệu: {len(sql) + len(lvl)} ({len(sql)} SQLite, {len(lvl)} LevelDB/IndexedDB)")

def run_single_filter(ticker):
    print(f"\n[*] Đang quét tin nhắn cho mã [{ticker}]...")
    def print_progress(msg, pct):
        print(f"    [{pct}%] {msg}")

    results, err = scan_messages_for_ticker(ticker, progress_callback=print_progress)
    if err:
        print(f"\n[!] THÔNG BÁO:\n{err}")
        return

    if not results:
        diag = get_last_scan_diagnostics()
        print(f"[-] Không có tin nhắn nào nhắc tới mã [{ticker}]. (Đã quét {diag.get('total_sources', 0)} file dữ liệu)")
    else:
        files = export_results(results, ticker)
        print(f"[+] Tìm thấy {len(results)} tin nhắn liên quan đến [{ticker}].")
        for f in files:
            print(f"    -> Đã xuất: {f}")

def run_cli_interactive():
    config = load_config()
    while True:
        default_ticker = config.get("default_ticker", "CEO")
        print("\n" + "="*45)
        print("    CÔNG CỤ LỌC TIN NHẮN CHỨNG KHOÁN ZALO")
        print("="*45)
        print(f"1. Quét nhanh theo mã mặc định [{default_ticker}]")
        print("2. Quét theo mã nhập tay (tùy chọn lưu lại)")
        print("3. Quét toàn bộ các mã trong danh sách")
        print("4. Quản lý danh sách mã & đổi mã mặc định")
        print("5. Cấu hình & Kiểm tra thư mục dữ liệu Zalo")
        print("6. Xem báo cáo chi tiết các file đã quét (Diagnostics)")
        print("0. Thoát")
        
        choice = input("Nhập thao tác: ").strip()
        
        if choice == "1":
            if default_ticker:
                run_single_filter(default_ticker)
            else:
                print("[!] Chưa thiết lập mã mặc định. Hãy chọn mục 4 để thiết lập.")
                
        elif choice == "2":
            ticker = input("Nhập mã cổ phiếu cần tìm: ").strip().upper()
            if ticker:
                if ticker not in config.get("watchlist", []):
                    ask_save = input(f"Mã [{ticker}] chưa có trong danh sách. Bạn có muốn lưu lại không? (y/n): ").strip().lower()
                    if ask_save == 'y':
                        config["watchlist"].append(ticker)
                        save_config(config)
                        print(f"[+] Đã lưu [{ticker}] vào danh sách.")
                run_single_filter(ticker)
                
        elif choice == "3":
            watchlist = config.get("watchlist", [])
            if not watchlist:
                print("[!] Danh sách rỗng. Hãy thêm mã vào danh sách trước.")
            else:
                for t in watchlist:
                    run_single_filter(t)

        elif choice == "4":
            menu_watchlist(config)

        elif choice == "5":
            menu_zalo_dir(config)

        elif choice == "6":
            print_diagnostics()

        elif choice == "0":
            print("Đã thoát chương trình.")
            break

def main():
    parser = argparse.ArgumentParser(description="Bộ lọc tin nhắn chứng khoán Zalo PC.")
    parser.add_argument("--auto", action="store_true", help="Chạy chế độ tự động cho Task Scheduler (quét mã mặc định hoặc toàn bộ)")
    parser.add_argument("--all", action="store_true", help="Quét toàn bộ danh sách mã theo dõi trong chế độ tự động")
    parser.add_argument("--ticker", type=str, help="Quét trực tiếp một mã cổ phiếu cụ thể")
    parser.add_argument("--zalo-dir", type=str, help="Chỉ định thư mục dữ liệu Zalo PC")
    parser.add_argument("--diag", action="store_true", help="Hiển thị thông tin chẩn đoán các file dữ liệu Zalo tìm thấy")

    args = parser.parse_args()

    if args.zalo_dir:
        cfg = load_config()
        cfg["custom_zalo_path"] = args.zalo_dir
        save_config(cfg)
        print(f"[+] Đã cấu hình thư mục Zalo: {args.zalo_dir}")

    if args.diag:
        print_diagnostics()
        return

    if args.ticker:
        run_single_filter(args.ticker)
    elif args.auto:
        config = load_config()
        if args.all:
            for t in config.get("watchlist", []):
                run_single_filter(t)
        else:
            default_t = config.get("default_ticker", "CEO")
            run_single_filter(default_t)
    else:
        run_cli_interactive()

if __name__ == "__main__":
    main()
