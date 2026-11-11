import os
import sys
import subprocess
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config_manager import load_config, save_config, get_base_dir
from scanner import scan_messages_for_ticker, export_results, get_last_scan_diagnostics

class ZaloStockFilterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Bộ Lọc Tin Nhắn Chứng Khoán Zalo PC")
        self.root.geometry("880x620")
        self.root.minsize(750, 500)

        self.config = load_config()
        self.current_results = []
        self.last_exported_files = []
        self.is_scanning = False

        self._apply_style()
        self._build_ui()
        self._refresh_watchlist_ui()
        self._refresh_zalo_path_ui()

    def _apply_style(self):
        style = ttk.Style()
        style.theme_use("clam")

        # Màu sắc chủ đạo
        self.PRIMARY_COLOR = "#0068FF"
        self.BG_COLOR = "#F4F6F9"
        self.root.configure(bg=self.BG_COLOR)

        style.configure("TFrame", background=self.BG_COLOR)
        style.configure("TLabel", background=self.BG_COLOR, font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground=self.PRIMARY_COLOR)
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#666666")
        
        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), foreground="#FFFFFF", background=self.PRIMARY_COLOR, padding=6)
        
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", font=("Segoe UI", 9), rowheight=24)

    def _build_ui(self):
        # 1. Header
        header_frame = ttk.Frame(self.root, padding="15 10 15 5")
        header_frame.pack(fill="x")

        lbl_title = ttk.Label(header_frame, text="⚡ Bộ Lọc Tin Nhắn Chứng Khoán Zalo PC", style="Header.TLabel")
        lbl_title.pack(anchor="w")

        lbl_sub = ttk.Label(header_frame, text="Tự động quét database Zalo PC offline - Lọc mã cổ phiếu nhanh & không cần mở Zalo", style="SubHeader.TLabel")
        lbl_sub.pack(anchor="w")

        # 2. Watchlist & Controls Frame
        control_frame = ttk.LabelFrame(self.root, text=" Quản lý & Cấu Hình Quét ", padding="10")
        control_frame.pack(fill="x", padx=15, pady=5)

        # Hàng 1: Watchlist & Chọn mã
        r1_frame = ttk.Frame(control_frame)
        r1_frame.pack(fill="x", pady=2)

        ttk.Label(r1_frame, text="Mã cổ phiếu:").pack(side="left", padx=(0, 5))
        self.combo_ticker = ttk.Combobox(r1_frame, width=12, font=("Segoe UI", 10, "bold"))
        self.combo_ticker.pack(side="left", padx=5)

        btn_add = ttk.Button(r1_frame, text="➕ Thêm mã", command=self._on_add_ticker)
        btn_add.pack(side="left", padx=3)

        btn_del = ttk.Button(r1_frame, text="❌ Xóa mã", command=self._on_remove_ticker)
        btn_del.pack(side="left", padx=3)

        btn_set_def = ttk.Button(r1_frame, text="⭐ Đặt làm mặc định", command=self._on_set_default)
        btn_set_def.pack(side="left", padx=3)

        self.lbl_default_info = ttk.Label(r1_frame, text="", font=("Segoe UI", 9, "italic"), foreground="#008800")
        self.lbl_default_info.pack(side="left", padx=10)

        # Hàng 2: Nút hành động quét
        r2_frame = ttk.Frame(control_frame)
        r2_frame.pack(fill="x", pady=(8, 2))

        self.btn_scan_one = ttk.Button(r2_frame, text="🔍 Quét Mã Đang Chọn", style="Primary.TButton", command=self._on_scan_selected)
        self.btn_scan_one.pack(side="left", padx=(0, 5))

        self.btn_scan_all = ttk.Button(r2_frame, text="🚀 Quét Toàn Bộ Danh Sách", command=self._on_scan_all)
        self.btn_scan_all.pack(side="left", padx=5)

        self.btn_open_folder = ttk.Button(r2_frame, text="📁 Mở Thư Mục Báo Cáo", command=self._on_open_report_folder)
        self.btn_open_folder.pack(side="right", padx=5)

        self.btn_open_last = ttk.Button(r2_frame, text="🌐 Xem Báo Cáo HTML", command=self._on_open_last_html, state="disabled")
        self.btn_open_last.pack(side="right", padx=5)

        self.btn_diag = ttk.Button(r2_frame, text="📋 Chi Tiết Quét", command=self._on_show_diagnostics)
        self.btn_diag.pack(side="right", padx=5)

        # Hàng 3: Cấu hình thư mục Zalo PC
        r3_frame = ttk.Frame(control_frame)
        r3_frame.pack(fill="x", pady=(8, 2))

        ttk.Label(r3_frame, text="Thư mục Zalo:").pack(side="left", padx=(0, 5))

        btn_reset_dir = ttk.Button(r3_frame, text="🔄 Tự Động", command=self._on_reset_zalo_dir)
        btn_reset_dir.pack(side="right", padx=3)

        btn_pick_dir = ttk.Button(r3_frame, text="⚙️ Chọn Thư Mục...", command=self._on_choose_zalo_dir)
        btn_pick_dir.pack(side="right", padx=5)

        self.lbl_zalo_path = ttk.Label(r3_frame, text="", font=("Segoe UI", 9), foreground="#333333")
        self.lbl_zalo_path.pack(side="left", fill="x", expand=True, padx=5)

        # 3. Status Bar & Progress
        status_frame = ttk.Frame(self.root, padding="15 5 15 5")
        status_frame.pack(fill="x")

        self.progress_bar = ttk.Progressbar(status_frame, mode="determinate")
        self.progress_bar.pack(fill="x", pady=2)

        self.lbl_status = ttk.Label(status_frame, text="Sẵn sàng quét.", font=("Segoe UI", 9))
        self.lbl_status.pack(anchor="w")

        # 4. Results Table
        table_frame = ttk.LabelFrame(self.root, text=" Kết Quả Tin Nhắn Tìm Thấy ", padding="5")
        table_frame.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        cols = ("idx", "time", "ticker", "sender", "group", "content", "source_db")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="browse")

        self.tree.heading("idx", text="#")
        self.tree.heading("time", text="Thời Gian")
        self.tree.heading("ticker", text="Mã")
        self.tree.heading("sender", text="Người Gửi")
        self.tree.heading("group", text="Nhóm/Cuộc Trò Chuyện")
        self.tree.heading("content", text="Nội Dung Tin Nhắn")
        self.tree.heading("source_db", text="File Nguồn")

        self.tree.column("idx", width=35, anchor="center")
        self.tree.column("time", width=125, anchor="center")
        self.tree.column("ticker", width=55, anchor="center")
        self.tree.column("sender", width=110)
        self.tree.column("group", width=130)
        self.tree.column("content", width=320)
        self.tree.column("source_db", width=110)

        # Scrollbars
        scroll_y = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll_x = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side="right", fill="y")
        scroll_x.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)

        # Bind đúp chuột để xem chi tiết tin nhắn
        self.tree.bind("<Double-1>", self._on_tree_double_click)

    def _refresh_watchlist_ui(self):
        watchlist = self.config.get("watchlist", [])
        default_ticker = self.config.get("default_ticker", "")
        self.combo_ticker["values"] = watchlist
        if default_ticker and default_ticker in watchlist:
            self.combo_ticker.set(default_ticker)
            self.lbl_default_info.config(text=f"(Mặc định: {default_ticker})")
        elif watchlist:
            self.combo_ticker.set(watchlist[0])
            self.lbl_default_info.config(text=f"(Mặc định: {watchlist[0]})")
        else:
            self.combo_ticker.set("")
            self.lbl_default_info.config(text="(Chưa có mã nào)")

    def _on_add_ticker(self):
        ticker = self.combo_ticker.get().strip().upper()
        if not ticker:
            messagebox.showwarning("Nhắc nhở", "Vui lòng nhập mã cổ phiếu cần thêm.")
            return

        watchlist = self.config.get("watchlist", [])
        if ticker in watchlist:
            messagebox.showinfo("Thông báo", f"Mã [{ticker}] đã có sẵn trong danh sách.")
            return

        watchlist.append(ticker)
        self.config["watchlist"] = watchlist
        if not self.config.get("default_ticker"):
            self.config["default_ticker"] = ticker
        save_config(self.config)
        self._refresh_watchlist_ui()
        self.combo_ticker.set(ticker)
        messagebox.showinfo("Thành công", f"Đã thêm mã [{ticker}] vào danh sách.")

    def _on_remove_ticker(self):
        ticker = self.combo_ticker.get().strip().upper()
        watchlist = self.config.get("watchlist", [])
        if ticker not in watchlist:
            messagebox.showwarning("Nhắc nhở", f"Mã [{ticker}] không có trong danh sách.")
            return

        watchlist.remove(ticker)
        self.config["watchlist"] = watchlist
        if self.config.get("default_ticker") == ticker:
            self.config["default_ticker"] = watchlist[0] if watchlist else ""
        save_config(self.config)
        self._refresh_watchlist_ui()
        messagebox.showinfo("Thành công", f"Đã xóa mã [{ticker}] khỏi danh sách.")

    def _refresh_zalo_path_ui(self):
        custom = self.config.get("custom_zalo_path", "").strip()
        if custom:
            disp = custom if len(custom) <= 45 else custom[:20] + "..." + custom[-22:]
            self.lbl_zalo_path.config(text=f"📁 {disp}", foreground="#006600")
        else:
            self.lbl_zalo_path.config(text="🔍 Tự động phát hiện (AppData / ZaloPC / ZaloApp)", foreground="#555555")

    def _on_choose_zalo_dir(self):
        dir_selected = filedialog.askdirectory(title="Chọn thư mục chứa dữ liệu Zalo PC")
        if dir_selected:
            self.config["custom_zalo_path"] = dir_selected
            save_config(self.config)
            self._refresh_zalo_path_ui()
            messagebox.showinfo("Thành công", f"Đã lưu đường dẫn Zalo PC:\n{dir_selected}")

    def _on_reset_zalo_dir(self):
        self.config["custom_zalo_path"] = ""
        save_config(self.config)
        self._refresh_zalo_path_ui()
        messagebox.showinfo("Thông báo", "Đã đặt lại chế độ tự động phát hiện thư mục Zalo PC.")

    def _on_set_default(self):
        ticker = self.combo_ticker.get().strip().upper()
        if not ticker:
            return
        watchlist = self.config.get("watchlist", [])
        if ticker not in watchlist:
            watchlist.append(ticker)
            self.config["watchlist"] = watchlist
        self.config["default_ticker"] = ticker
        save_config(self.config)
        self._refresh_watchlist_ui()
        messagebox.showinfo("Thành công", f"Đã đặt [{ticker}] làm mã mặc định.")

    def _set_ui_state(self, is_running):
        self.is_scanning = is_running
        state = "disabled" if is_running else "normal"
        self.btn_scan_one.config(state=state)
        self.btn_scan_all.config(state=state)

    def _update_progress(self, text, percent):
        self.root.after(0, lambda: self._do_update_progress(text, percent))

    def _do_update_progress(self, text, percent):
        self.lbl_status.config(text=text)
        self.progress_bar["value"] = percent

    def _on_scan_selected(self):
        ticker = self.combo_ticker.get().strip().upper()
        if not ticker:
            messagebox.showwarning("Nhắc nhở", "Vui lòng chọn hoặc nhập mã cổ phiếu cần quét.")
            return

        threading.Thread(target=self._scan_thread_worker, args=([ticker],), daemon=True).start()

    def _on_scan_all(self):
        watchlist = self.config.get("watchlist", [])
        if not watchlist:
            messagebox.showwarning("Nhắc nhở", "Danh sách rỗng! Hãy thêm mã cổ phiếu trước.")
            return

        threading.Thread(target=self._scan_thread_worker, args=(watchlist,), daemon=True).start()

    def _scan_thread_worker(self, tickers):
        self.root.after(0, lambda: self._set_ui_state(True))
        all_results = []
        exported_all = []
        errors = []

        for idx, ticker in enumerate(tickers, start=1):
            def prog(msg, pct):
                # Tính phần trăm trên tổng số ticker
                scaled = int(((idx - 1) + (pct / 100)) / len(tickers) * 100)
                self._update_progress(f"[{ticker}] {msg}", scaled)

            res, err = scan_messages_for_ticker(ticker, progress_callback=prog)
            if res:
                all_results.extend(res)
                files = export_results(res, ticker)
                exported_all.extend(files)
            if err:
                errors.append(err)

        self.root.after(0, lambda: self._scan_finished(all_results, exported_all, tickers, errors))

    def _scan_finished(self, results, exported_files, tickers, errors=None):
        self._set_ui_state(False)
        self.current_results = results
        self.last_exported_files = exported_files

        # Xóa dữ liệu cũ trên bảng
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Nạp dữ liệu mới
        for idx, r in enumerate(results, start=1):
            self.tree.insert("", "end", values=(
                idx,
                r.get("time", ""),
                r.get("ticker", ""),
                r.get("sender", ""),
                r.get("group", ""),
                r.get("content", ""),
                r.get("source_db", "")
            ))

        if exported_files:
            self.btn_open_last.config(state="normal")

        msg = f"Quét hoàn tất: Tìm thấy {len(results)} tin nhắn cho {len(tickers)} mã."
        self._do_update_progress(msg, 100)

        if results:
            messagebox.showinfo("Hoàn Tất", f"Đã tìm thấy {len(results)} tin nhắn!\nĐã xuất kết quả ra thư mục 'reports'.")
        elif errors:
            messagebox.showerror("Thông Báo Quét Dữ Liệu Zalo", errors[0])
        else:
            ticker_list_str = ", ".join(tickers)
            diag = get_last_scan_diagnostics()
            total_src = diag.get("total_sources", 0)
            messagebox.showinfo(
                "Kết quả Quét", 
                f"Đã quét qua {total_src} file cơ sở dữ liệu Zalo PC (SQLite & LevelDB/IndexedDB) nhưng không có tin nhắn nào chứa mã [{ticker_list_str}].\n\n"
                f"Gợi ý kiểm tra:\n"
                f"1. Nhấn nút '📋 Chi Tiết Quét' trên giao diện để xem danh sách file và thư mục đã quét.\n"
                f"2. Hãy mở Zalo PC và bấm 'Đồng bộ tin nhắn từ điện thoại' để Zalo tải tin nhắn các nhóm về máy tính.\n"
                f"3. Bạn có thể thử tìm một từ chắc chắn đang có trong tin nhắn gần đây (ví dụ: 'chào', 'alo' hoặc mã cổ phiếu khác đang chat).\n"
                f"4. Nếu bạn lưu dữ liệu Zalo ở ổ đĩa khác (như D:\\ZaloPC), hãy nhấn nút '⚙️ Chọn Thư Mục...'."
            )

    def _on_show_diagnostics(self):
        diag = get_last_scan_diagnostics()
        diag_win = tk.Toplevel(self.root)
        diag_win.title("Nhật Ký & Chi Tiết Quét Cơ Sở Dữ Liệu Zalo PC")
        diag_win.geometry("720x520")
        diag_win.minsize(550, 400)

        frame = ttk.Frame(diag_win, padding="15")
        frame.pack(fill="both", expand=True)

        lbl_title = ttk.Label(frame, text="📋 Chi Tiết Phát Hiện & Quét Cơ Sở Dữ Liệu", font=("Segoe UI", 12, "bold"), foreground=self.PRIMARY_COLOR)
        lbl_title.pack(anchor="w", pady=(0, 8))

        summary = diag.get("summary_text", "")
        if not summary:
            summary = "Chưa thực hiện lần quét nào hoặc chưa tìm thấy dữ liệu.\n\nHãy nhấn nút '🔍 Quét Mã Đang Chọn' để bắt đầu quét."

        text_area = tk.Text(frame, wrap="none", font=("Consolas", 9), padx=10, pady=10)
        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=text_area.yview)
        scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=text_area.xview)
        text_area.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)

        scroll_y.pack(side="right", fill="y")
        scroll_x.pack(side="bottom", fill="x")
        text_area.pack(fill="both", expand=True)

        text_area.insert("1.0", summary)
        text_area.config(state="disabled")

        btn_frame = ttk.Frame(frame, padding="0 10 0 0")
        btn_frame.pack(fill="x")

        def copy_to_clipboard():
            self.root.clipboard_clear()
            self.root.clipboard_append(summary)
            messagebox.showinfo("Thành Công", "Đã sao chép toàn bộ nhật ký quét vào Clipboard!")

        btn_copy = ttk.Button(btn_frame, text="📄 Sao Chép Nhật Ký", command=copy_to_clipboard)
        btn_copy.pack(side="left")

        btn_close = ttk.Button(btn_frame, text="Đóng", command=diag_win.destroy)
        btn_close.pack(side="right")

    def _on_tree_double_click(self, event):
        selected_item = self.tree.focus()
        if not selected_item:
            return
        item_vals = self.tree.item(selected_item)["values"]
        if item_vals and len(item_vals) >= 6:
            detail_win = tk.Toplevel(self.root)
            detail_win.title(f"Chi Tiết Tin Nhắn - [{item_vals[2]}]")
            detail_win.geometry("560x380")
            detail_win.minsize(420, 260)

            pad = 12
            frame = ttk.Frame(detail_win, padding=pad)
            frame.pack(fill="both", expand=True)

            ttk.Label(frame, text=f"Thời gian: {item_vals[1]}", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=2)
            ttk.Label(frame, text=f"Mã CP: {item_vals[2]}", font=("Segoe UI", 9, "bold"), foreground=self.PRIMARY_COLOR).pack(anchor="w", pady=2)
            ttk.Label(frame, text=f"Người gửi: {item_vals[3]}").pack(anchor="w", pady=2)
            ttk.Label(frame, text=f"Nhóm: {item_vals[4]}").pack(anchor="w", pady=2)
            if len(item_vals) >= 7 and item_vals[6]:
                ttk.Label(frame, text=f"File database: {item_vals[6]}", font=("Segoe UI", 8), foreground="#666666").pack(anchor="w", pady=1)
            ttk.Label(frame, text="Nội dung:", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 2))

            text_box = tk.Text(frame, wrap="word", font=("Segoe UI", 10), height=8)
            text_box.insert("1.0", str(item_vals[5]))
            text_box.config(state="disabled")
            text_box.pack(fill="both", expand=True, pady=4)

            ttk.Button(frame, text="Đóng", command=detail_win.destroy).pack(anchor="e", pady=(5, 0))

    def _on_open_report_folder(self):
        base_dir = get_base_dir()
        out_dir = os.path.join(base_dir, self.config.get("output_dir", "reports"))
        os.makedirs(out_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(out_dir)
        else:
            webbrowser.open(f"file://{out_dir}")

    def _on_open_last_html(self):
        html_files = [f for f in self.last_exported_files if f.endswith(".html")]
        if html_files:
            webbrowser.open(f"file://{os.path.abspath(html_files[0])}")
        else:
            self._on_open_report_folder()

def launch_gui():
    root = tk.Tk()
    app = ZaloStockFilterGUI(root)
    root.mainloop()

if __name__ == "__main__":
    launch_gui()
