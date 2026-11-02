@echo off
cd /d "%~dp0"
echo === 乐跑 Token 抓号工具 ===
if not exist "..\venv_capture" (
  echo 创建 Python 环境...
  python -m venv ..\venv_capture
  ..\venv_capture\Scripts\pip install mitmproxy pycryptodome requests
)
..\venv_capture\Scripts\python capture_gui.py
pause
