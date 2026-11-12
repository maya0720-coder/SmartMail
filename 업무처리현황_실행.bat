@echo off
rem SmartMail launcher (double-click). Starts the local server and opens the browser.
rem Stop: press Ctrl+C in this window or close it. Sessions and temp browser data are removed on exit.
rem Keep this file ASCII-only with CRLF line endings (cmd.exe reads batch files in the OEM code page).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run.ps1" -Open
echo.
echo Server stopped. Press any key to close this window.
pause >nul
