# -*- coding: utf-8 -*-
"""乐跑 Token 抓号工具（tkinter GUI）
用法：python capture_gui.py [--server http://localhost:8080]
一键启动 → 切代理 → 打开小程序 → 自动抓 token → 自动上传服务器
"""
import tkinter as tk
from tkinter import ttk, messagebox
import subprocess, threading, json, os, sys, time, shutil
import urllib.request, urllib.parse

SERVER_URL = "http://localhost:8080"
PROXY_PORT = 8888
TOKEN_FILE = "/tmp/lepao_captured_token.json"
ADDON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token_addon.py")

for arg in sys.argv:
    if arg.startswith("--server="):
        SERVER_URL = arg.split("=", 1)[1]

MITMDUMP = None
for p in ["/tmp/cuavenv/bin/mitmdump",
          os.path.join(sys.prefix, "bin", "mitmdump"),
          os.path.join(sys.prefix, "Scripts", "mitmdump.exe"),
          "mitmdump"]:
    if shutil.which(p):
        MITMDUMP = p
        break


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
        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._log("=== 开始抓取流程 ===")
        if not MITMDUMP:
            self._log("❌ 未找到 mitmdump"); self.running = False
            self.start_btn.configure(state="normal"); return
        student = self.student_var.get().strip()
        if not student:
            self._log("❌ 请填写学号"); self.running = False; return
        server = self.server_var.get().rstrip("/")
        try: os.remove(TOKEN_FILE)
        except FileNotFoundError: pass
        self._log(f"启动代理 (port {PROXY_PORT})…")
        self.proxy_proc = subprocess.Popen(
            [MITMDUMP, "-p", str(PROXY_PORT), "-s", ADDON_FILE,
             "--set", "confdir=~/.mitmproxy", "--ssl-insecure", "-q"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        if self.proxy_proc:
            try: self.proxy_proc.terminate(); self.proxy_proc.wait(timeout=5)
            except Exception: pass
            self.proxy_proc = None

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self.running = False; self.stop_proxy(); self.root.destroy()


if __name__ == "__main__":
    CaptureApp().run()
