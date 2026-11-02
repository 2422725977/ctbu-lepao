# -*- coding: utf-8 -*-
"""mitmproxy addon: 拦截 lepao.ctbu.edu.cn 流量，解密提取 token。

由 capture_gui.py 以 mitmdump -s token_addon.py 方式启动。
捕获到 token 后写入 /tmp/lepao_captured_token.json 供主程序读取。
"""
import json
import base64
import urllib.parse as up
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

KEY = b"Wet2C8d34f62ndi3"
IV = b"K6iv85jBD8jgf32D"
TOKEN_FILE = "/tmp/lepao_captured_token.json"


def decrypt_data(b64_text):
    raw = base64.b64decode(b64_text)
    plain = unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(raw), 16)
    return json.loads(plain.decode("utf-8"))


class TokenCapture:
    def __init__(self):
        self.captured = False

    def response(self, flow):
        if self.captured:
            return
        url = flow.request.pretty_url
        if "lepao.ctbu.edu.cn" not in url:
            return
        try:
            # 从响应解密提取 token
            body = flow.response.get_text(strict=False)
            j = json.loads(body)
            if isinstance(j.get("data"), str) and len(j["data"]) > 32:
                plain = dec(j["data"])
                token = plain.get("token", "")
                uid = plain.get("uid", 0)
                if token and len(token) >= 16:
                    self._emit(token, uid, url)
                    return
        except Exception:
            pass
        # 也从请求体提取（某些接口请求带 token）
        try:
            body = flow.request.get_text(strict=False) or ""
            if "data=" not in body:
                return
            encrypted = up.unquote(body.split("data=", 1)[1])
            plain = dec(encrypted)
            token = plain.get("token", "")
            uid = plain.get("uid", 0)
            if token and len(token) >= 16:
                self._emit(token, uid, url)
        except Exception:
            pass

    def _emit(self, token, uid, url):
        self.captured = True
        data = {"token": token, "uid": uid, "url": url,
                "captured_at": str(__import__("datetime").datetime.now())}
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"[TOKEN_CAPTURED] {token[:8]}… uid={uid} → {TOKEN_FILE}")


def dec(b64_text):
    raw = base64.b64decode(b64_text)
    return json.loads(unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(raw), 16).decode())


addons = [TokenCapture()]
