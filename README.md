# ⚡ Bộ Lọc Tin Nhắn Chứng Khoán Zalo PC (Tự Động & Độc Lập)

Ứng dụng lọc và trích xuất tin nhắn từ các nhóm chứng khoán trên **Zalo PC**, giúp nhà đầu tư nắm bắt ngay các khuyến nghị, thảo luận về các mã cổ phiếu quan tâm (như **CEO**, **DIG**, **HPG**,...) mà **không cần lướt hàng nghìn tin nhắn rác mỗi ngày** và **không cần mở Zalo**.

---

## 🌟 Tính Năng Nổi Bật

1. **Chạy ngay không cần cài Python:** Đã được đóng gói sẵn thành file `.exe` độc lập cho Windows.
2. **Không phụ thuộc cấu hình máy:** Phần mềm siêu nhẹ (~15MB), tiêu tốn cực ít RAM/CPU (< 30MB RAM), mở lên tức thì, mượt mà trên cả máy tính văn phòng cấu hình thấp.
3. **An toàn 100% cho tài khoản Zalo:** Đọc cơ sở dữ liệu SQLite cục bộ (offline) trên máy, hoàn toàn không can thiệp API mạng hay gửi tin nhắn, không lo bị khóa tài khoản.
4. **Không bị xung đột file lock:** Tự động tạo bản sao tạm thời để truy vấn nên bạn có thể quét dữ liệu thoải mái ngay cả khi Zalo PC đang mở.
5. **Giao diện trực quan (GUI):**
   - Quản lý danh sách mã theo dõi (Watchlist): Thêm, xóa mã, đổi mã mặc định chỉ bằng 1 cú nhấp chuột.
   - Xem kết quả tin nhắn trực tiếp ngay trên bảng của phần mềm.
   - Nhấp đúp vào tin nhắn để đọc nội dung đầy đủ.
6. **Báo cáo đa dạng & chuyên nghiệp:**
   - **File Excel/CSV (UTF-8 có BOM):** Mở trực tiếp bằng Microsoft Excel trên mọi máy tính Windows mà không bao giờ bị lỗi font tiếng Việt.
   - **Báo cáo HTML giao diện hiện đại:** Tự động mở xem trên trình duyệt web, có sẵn thanh tìm kiếm và đánh dấu (highlight) nổi bật mã cổ phiếu.
7. **Hỗ trợ hẹn giờ tự động:** Có sẵn lệnh CLI để tích hợp vào Windows Task Scheduler chạy tự động vào cuối phiên giao dịch (15h30 hàng ngày).

---

## 🚀 Cách Tải & Sử Dụng (Dành Cho Mọi Máy Tính)

### Bước 1: Tải phần mềm về máy
1. Truy cập vào mục **[Releases](https://github.com/hunghillboss-blip/filter_zalo_messaage/releases)** của kho lưu trữ này.
2. Tải về file **`ZaloStockFilter.exe`** (hoặc file nén `ZaloStockFilter-Windows.zip`).
3. Lưu vào một thư mục bất kỳ trên máy tính của bạn (ví dụ: `D:\ZaloStockFilter\`).

### Bước 2: Sử dụng phần mềm
- Nhấp đúp chuột vào file **`ZaloStockFilter.exe`**.
- Giao diện ứng dụng sẽ xuất hiện:
  - Chọn hoặc nhập mã cổ phiếu cần tìm (ví dụ: `CEO`).
  - Bấm **"🔍 Quét Mã Đang Chọn"** (hoặc **"🚀 Quét Toàn Bộ Danh Sách"**).
  - Kết quả tin nhắn sẽ hiển thị ngay trên bảng.
  - Bấm **"🌐 Xem Báo Cáo HTML"** hoặc **"📁 Mở Thư Mục Báo Cáo"** để xem và mở file Excel kết quả.

---

## ⏰ Hẹn Giờ Tự Động Hàng Ngày (Windows Task Scheduler)

Nếu bạn muốn chương trình tự động chạy ngầm vào 15:30 mỗi ngày (sau giờ đóng cửa sàn chứng khoán) và tự xuất file báo cáo:

1. Bấm phím **Windows**, gõ tìm kiếm **Task Scheduler** và mở lên.
2. Ở cột bên phải, chọn **Create Basic Task...**
3. Đặt tên tác vụ: `Auto_Filter_Zalo_Stocks` -> Bấm **Next**.
4. Chọn **Daily** (Hàng ngày) -> Đặt giờ: **15:30:00** -> Bấm **Next**.
5. Chọn **Start a program** -> Bấm **Next**.
6. Tại ô **Program/script**: Bấm *Browse* chọn đường dẫn đến file `ZaloStockFilter.exe`.
7. Tại ô **Add arguments (optional)**: Nhập `--auto` (hoặc `--auto --all` nếu muốn quét hết các mã trong danh sách).
8. Bấm **Next** -> Bấm **Finish**.

Từ nay, cứ 15h30 mỗi ngày, chương trình sẽ tự động quét ngầm và xuất file Excel/HTML vào thư mục `reports/` cho bạn.

---

## ⚙️ Cấu Trúc Tập Tin Cấu Hình (`config.json`)

Chương trình sẽ tự động tạo file `config.json` nằm cùng thư mục với file `.exe`:

```json
{
    "watchlist": [
        "CEO",
        "DIG",
        "DBC",
        "HPG",
        "SSI",
        "VND"
    ],
    "default_ticker": "CEO",
    "case_sensitive": false,
    "output_dir": "reports",
    "export_format": "both",
    "custom_zalo_path": ""
}
```

- `watchlist`: Danh sách mã cổ phiếu đang theo dõi.
- `default_ticker`: Mã ưu tiên mặc định.
- `case_sensitive`: Phân biệt chữ hoa / chữ thường (`false` là bắt cả `ceo` lẫn `CEO`).
- `output_dir`: Tên thư mục chứa báo cáo xuất ra.
- `export_format`: Định dạng xuất (`both`: cả CSV và HTML; hoặc `csv`, `html`).
- `custom_zalo_path`: Để trống để phần mềm tự dò tìm ZaloPC trên máy; chỉ điền nếu bạn cài Zalo ở ổ đĩa tùy biến.

---

## 🛠️ Dành Cho Lập Trình Viên (Chạy Từ Mã Nguồn)

Nếu bạn có sẵn môi trường Python và muốn phát triển thêm:

```bash
# 1. Clone repository
git clone https://github.com/hunghillboss-blip/filter_zalo_messaage.git
cd filter_zalo_messaage

# 2. Khởi chạy giao diện đồ họa
python main.py

# Hoặc khởi chạy giao diện dòng lệnh:
python cli.py

# 3. Tự đóng gói thành file .exe:
pip install pyinstaller
build_exe.bat
```
