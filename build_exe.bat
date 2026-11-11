@echo off
chcp 65001 >nul
echo ========================================================
echo   TỰ ĐỘNG ĐÓNG GÓI PHẦN MỀM THÀNH FILE CHẠY (.EXE)
echo ========================================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Máy tính chưa cài đặt Python. Vui lòng tải bản .exe đã được GitHub đóng gói sẵn trong mục Releases!
    pause
    exit /b
)

echo [*] Đang cài đặt công cụ đóng gói PyInstaller...
pip install -q pyinstaller

echo [*] Đang tiến hành đóng gói ZaloStockFilter.exe...
pyinstaller --noconfirm --clean --onefile --windowed --name "ZaloStockFilter" main.py

if exist "dist\ZaloStockFilter.exe" (
    echo.
    echo ========================================================
    echo   ĐÓNG GÓI THÀNH CÔNG!
    echo   File thực thi nằm tại: dist\ZaloStockFilter.exe
    echo   Bạn có thể copy file này sang bất kỳ máy tính nào để dùng.
    echo ========================================================
) else (
    echo [!] Có lỗi xảy ra trong quá trình đóng gói.
)

pause
