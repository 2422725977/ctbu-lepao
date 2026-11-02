#!/bin/bash
cd "$(dirname "$0")"
echo "=== 乐跑 Token 抓号工具 ==="
if [ ! -d "../venv_capture" ]; then
  echo "创建 Python 环境..."
  python3 -m venv ../venv_capture
  ../venv_capture/bin/pip install --quiet mitmproxy pycryptodome requests
fi
exec ../venv_capture/bin/python capture_gui.py
