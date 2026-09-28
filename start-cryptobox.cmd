@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-cryptobox.ps1" %*
set "CRYPTBOX_EXIT_CODE=%ERRORLEVEL%"

if not "%CRYPTBOX_EXIT_CODE%"=="0" (
    echo.
    echo Cryptobox 启动失败，退出代码：%CRYPTBOX_EXIT_CODE%
    pause
)

exit /b %CRYPTBOX_EXIT_CODE%
