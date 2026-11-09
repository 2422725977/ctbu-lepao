# -*- coding: utf-8 -*-
"""乐跑 Token 抓号工具（tkinter GUI）
用法：python capture_gui.py [--server http://localhost:8080]
一键启动 → 切代理 → 打开小程序 → 自动抓 token → 自动上传服务器
"""
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, threading, json, os, sys, time, shutil, re, tempfile, asyncio
import urllib.request
from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster
import token_addon as _addon_mod

# ---------- 安全校验 ----------
def _safe_sn(sn):
    """学号仅允许字母数字下划线（防路径穿越）"""
    sn = str(sn).strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', sn):
        raise ValueError(f"非法学号: {sn}")
    return sn

def _safe_server(url):
    """服务器 URL 仅允许回环或局域网私网地址（防 SSRF，放行跨机上传）"""
    import ipaddress
    url = str(url).strip().rstrip("/")
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        ok = ip.is_loopback or ip.is_private
    except ValueError:
        ok = host in ("localhost",)
    if parsed.scheme not in ("http", "https") or not ok:
        raise ValueError(f"服务器地址不在白名单(仅允许本机/局域网): {url}")
    return url, urllib.parse

SERVER_URL = "http://localhost:8080"
PROXY_PORT = 8888
TOKEN_FILE = os.path.join(tempfile.gettempdir(), "lepao_captured_token.json")
# 打包(PyInstaller)后资源与同级文件位于可执行文件目录；源码运行时取脚本目录
_BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
ADDON_FILE = os.path.join(_BASE, "token_addon.py")

for arg in sys.argv:
    if arg.startswith("--server="):
        SERVER_URL = arg.split("=", 1)[1]

MITMDUMP = None  # 已改为进程内 mitmproxy，不再依赖外部 mitmdump（单文件打包的关键）

# ---------- 进程内 mitmproxy（对照 RunForge main.pyc 的 DumpMaster 方案） ----------
_PROXY = {"master": None, "loop": None, "thread": None}


def proxy_start():
    """在当前进程内启动 mitmproxy DumpMaster（后台线程）"""
    if _PROXY["thread"] and _PROXY["thread"].is_alive():
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _amain():
            opts = options.Options(listen_host="127.0.0.1", listen_port=PROXY_PORT,
                                   ssl_insecure=True, confdir="~/.mitmproxy")
            master = DumpMaster(opts, with_termlog=False, with_dumper=False)
            master.addons.add(*_addon_mod.addons)
            _PROXY["master"] = master
            _PROXY["loop"] = loop
            await master.run()

        try:
            loop.run_until_complete(_amain())
        finally:
            loop.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    _PROXY["thread"] = t
    time.sleep(1.5)  # 等待监听就绪


def proxy_stop():
    m, loop = _PROXY["master"], _PROXY["loop"]
    if m and loop:
        try:
            loop.call_soon_threadsafe(m.shutdown)
        except Exception:
            pass
    _PROXY["master"] = None
    _PROXY["loop"] = None

# 服务器地址持久化（GUI 里填过一次即可）
try:
    with open("config.json", encoding="utf-8") as _f:
        SERVER_URL = json.load(_f).get("server") or SERVER_URL
except (OSError, ValueError):
    pass


# ---------- 系统代理自动化（RunForge 登录器同款体验；argv 全字面量） ----------
_PROXY_SERVICES = ("Wi-Fi", "Ethernet", "USB 10/100/1000 LAN", "Thunderbolt Ethernet")


def proxy_apply():
    """开启系统代理指向本机 8888"""
    try:
        if sys.platform == "win32":
            subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                            "/v", "ProxyServer", "/t", "REG_SZ", "/d", "127.0.0.1:8888", "/f"],
                           capture_output=True, timeout=10)
            subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                            "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "1", "/f"],
                           capture_output=True, timeout=10)
        elif sys.platform == "darwin":
            for svc in _PROXY_SERVICES:
                subprocess.run(["networksetup", "-setwebproxy", svc, "127.0.0.1", "8888"],
                               capture_output=True, timeout=10)
                subprocess.run(["networksetup", "-setsecurewebproxy", svc, "127.0.0.1", "8888"],
                               capture_output=True, timeout=10)
    except Exception:
        pass


def proxy_restore():
    """关闭系统代理（对齐 RunForge 退出行为；如原用其他代理请重新开启）"""
    try:
        if sys.platform == "win32":
            subprocess.run(["reg", "add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings",
                            "/v", "ProxyEnable", "/t", "REG_DWORD", "/d", "0", "/f"],
                           capture_output=True, timeout=10)
        elif sys.platform == "darwin":
            for svc in _PROXY_SERVICES:
                subprocess.run(["networksetup", "-setwebproxystate", svc, "off"],
                               capture_output=True, timeout=10)
                subprocess.run(["networksetup", "-setsecurewebproxystate", svc, "off"],
                               capture_output=True, timeout=10)
    except Exception:
        pass


class CaptureApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("乐跑 Token 抓号工具")
        self.root.geometry("520x480")
        self.root.resizable(False, False)
        self.root.configure(bg="#0f0f23")
        self.proxy_proc = None
        self.running = False
        self._build_ui()

    def _build_ui(self):
        bg, fg, accent = "#0f0f23", "#e0e0e0", "#7c4dff"
        self.root.configure(bg=bg)
        tk.Label(self.root, text="🏃 乐跑 Token 抓号工具",
                 font=("Arial", 16, "bold"), bg=bg, fg=accent).pack(pady=(20, 5))
        f1 = tk.Frame(self.root, bg=bg); f1.pack(fill="x", padx=20, pady=5)
        tk.Label(f1, text="服务器:", bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self.server_var = tk.StringVar(value=SERVER_URL)
        tk.Entry(f1, textvariable=self.server_var, width=40, bg="#1a1a3e", fg=fg, insertbackground=fg).pack(side="left", fill="x", expand=True)
        f2 = tk.Frame(self.root, bg=bg); f2.pack(fill="x", padx=20, pady=5)
        tk.Label(f2, text="学号:", bg=bg, fg=fg, width=10, anchor="w").pack(side="left")
        self.student_var = tk.StringVar(value="2025410030")
        tk.Entry(f2, textvariable=self.student_var, width=40, bg="#1a1a3e", fg=fg, insertbackground=fg).pack(side="left", fill="x", expand=True)
        f3 = tk.Frame(self.root, bg=bg); f3.pack(fill="x", padx=20, pady=15)
        self.start_btn = tk.Button(f3, text="🚀 开始抓取", command=self.start_capture, bg=accent, fg="white", font=("Arial", 12, "bold"), width=15, relief="flat", cursor="hand2")
        self.start_btn.pack(side="left", padx=5)
        self.stop_btn = tk.Button(f3, text="⏹ 停止", command=self.stop_capture, bg="#333", fg=fg, font=("Arial", 12), width=10, relief="flat", state="disabled", cursor="hand2")
        self.stop_btn.pack(side="left", padx=5)
        self.status_var = tk.StringVar(value="就绪 — 点击「开始抓取」后按提示操作")
        tk.Label(self.root, textvariable=self.status_var, bg=bg, fg="#4caf50", font=("Arial", 10), wraplength=480, justify="left").pack(fill="x", padx=20, pady=5)
        tk.Label(self.root, text="日志:", bg=bg, fg=fg, anchor="w").pack(fill="x", padx=20)
        self.log_text = tk.Text(self.root, height=12, bg="#1a1a3e", fg="#a5d6a7", font=("Courier", 9), state="disabled", relief="flat")
        self.log_text.pack(fill="both", expand=True, padx=20, pady=(5, 10))

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_status(self, msg):
        self.status_var.set(msg)
        self._log(msg)

    def start_capture(self):
        if self.running: return
        try:
            student = _safe_sn(self.student_var.get())
            server = _safe_server(self.server_var.get())
        except ValueError as e:
            self._log(f"❌ {e}"); return
        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log("=== 开始抓取流程 ===")
        student = self.student_var.get().strip()
        if not student:
            self._log("❌ 请填写学号"); self.running = False; return
        server = self.server_var.get().rstrip("/")
        try: os.remove(TOKEN_FILE)
        except FileNotFoundError: pass
        self._log(f"启动内置代理 (port {PROXY_PORT})…")
        try:
            proxy_start()
        except Exception as e:
            self._log(f"❌ 代理启动失败: {e}"); self.running = False
            self.start_btn.configure(state="normal"); return
        proxy_apply()
        self._log("系统代理已指向 127.0.0.1:8888（退出自动关闭）")
        self._set_status("⏳ 代理已启动 — 请在企业微信中打开智慧体育小程序")
        threading.Thread(target=self._wait_for_token, args=(server, student), daemon=True).start()

    def _wait_for_token(self, server, student):
        timeout = 180; start = time.time()
        while time.time() - start < timeout and self.running:
            try:
                if os.path.exists(TOKEN_FILE):
                    with open(TOKEN_FILE, encoding="utf-8") as f:
                        data = json.load(f)
                    token = data.get("token", "")
                    uid = data.get("uid", 0)
                    if token and len(token) >= 16:
                        self._log(f"✅ Token 捕获成功! ({token[:8]}…)")
                        self.root.after(0, lambda: self._set_status("✅ Token 已捕获，正在上传…"))
                        self._upload(server, student, token, uid)
                        return
            except (json.JSONDecodeError, KeyError): pass
            time.sleep(2)
        if self.running:
            self._log("⏱ 超时"); self.root.after(0, lambda: self._set_status("❌ 超时"))
        self.running = False
        self.root.after(0, lambda: (self.start_btn.configure(state="normal"), self.stop_btn.configure(state="disabled")))

    def _upload(self, server, student, token, uid):
        try:
            body = json.dumps({"student_num": student, "token": token, "uid": uid}).encode()
            req = urllib.request.Request(server + "/api/token", data=body, method="POST", headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=10)
            result = json.loads(resp.read().decode())
            if result.get("ok"):
                self._log("✅ Token 已上传!")
                self.root.after(0, lambda: self._set_status("✅ 完成! Token 已上传"))
                messagebox.showinfo("成功", f"Token 已上传!\n学号: {student}\nToken: {token[:12]}…")
            else:
                self._log(f"⚠ {result}")
        except Exception as e:
            self._log(f"❌ 上传失败: {e}")
        finally:
            self.stop_proxy()
            self.root.after(0, lambda: (self.start_btn.configure(state="normal"), self.stop_btn.configure(state="disabled")))

    def stop_capture(self):
        self.running = False; self.stop_proxy()
        self._log("已停止"); self._set_status("已停止")
        self.start_btn.configure(state="normal"); self.stop_btn.configure(state="disabled")

    def stop_proxy(self):
        proxy_stop()
        proxy_restore()
        self.proxy_proc = None

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False; self.stop_proxy(); self.root.destroy()


if __name__ == "__main__":
    CaptureApp().run()
