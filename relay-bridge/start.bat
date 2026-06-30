@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo   relay-bridge 启动�?..
echo ========================================
echo.

where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 Node.js，请先安�?Node.js 18+
  echo   下载地址: https://nodejs.org/
  pause
  exit /b 1
)

if not exist config.json (
  echo [错误] 未找�?config.json，请先复�?config.example.json 并填�?relayWebhookUrl
  pause
  exit /b 1
)

node server.js
pause
