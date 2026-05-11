@echo off
chcp 65001 >nul
echo ========================================
echo  Lamix Windows 一键安装
echo ========================================
echo.

REM 切换到项目根目录（install.bat 在 scripts/ 下）
cd /d "%~dp0.."

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
    echo   dist\lamix.exe
    if exist dist\lamix-uninstall.exe (
        echo   dist\lamix-uninstall.exe
        echo.
        echo 可以把它们复制到桌面。
    ) else (
        echo.
        echo 注意：lamix-uninstall.exe 未生成，但不影响使用。
        echo 如需卸载，请手动删除 ~/.lamix 目录和任务计划。
    )
) else (
    echo 构建失败，请检查上方错误信息。
)

echo.
pause
