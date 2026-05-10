@echo off
chcp 65001 >nul
echo ========================================
echo  Lamix Windows 一键安装
echo ========================================
echo.

REM 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：未检测到 Python，请先安装 Python 3.11+
    echo 下载地址：https://www.python.org/downloads/
    echo 安装时务必勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)

REM 检查 Git
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：未检测到 Git，请先安装 Git
    echo 下载地址：https://git-scm.com/download/win
    pause
    exit /b 1
)

REM 安装
echo [1/3] 安装依赖...
pip install -e .
pip install pyinstaller

REM 构建 exe
echo.
echo [2/3] 构建 exe...
python scripts\build_exe.py

REM 完成
echo.
echo [3/3] 完成！
echo.
if exist dist\lamix.exe (
    echo 双击这些文件即可使用：
    echo   dist\lamix.exe
    echo   dist\lamix-uninstall.exe
    echo.
    echo 可以把它们复制到桌面。
) else (
    echo 构建失败，请检查上方错误信息。
)

echo.
pause
