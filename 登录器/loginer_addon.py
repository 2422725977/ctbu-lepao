# -*- coding: utf-8 -*-
"""乐跑登录器 - mitmproxy 插件：拦截智慧体育登录响应，本地解密提取 token。

设计对照 RunForge 登录器的 proxy.py，但有两点不同：
  1. 凭据在本地解密，只上传 token，不上传加密原文（RunForge 把密文原文传它的服务器）
  2. 抓到后通过 stdout 交给主程序，上传到【用户自己配置的】服务器，不外传第三方
"""
import base64
import json

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from mitmproxy import http, ctx

KEY = b"Wet2C8d34f62ndi3"
IV = b"K6iv85jBD8jgf32D"

# 拦截目标：登录签发 token 的接口 + 返回身份信息的接口
LOGIN_URL = "api.php/WpLogin/loginByQyCodeCommon"
USER_URL = "api.php/WpLogin/UserInfo"


def _decrypt(b64_text):
    raw = base64.b64decode(b64_text)
    return json.loads(unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(raw), 16).decode("utf-8"))


class LoginCapture:
    def response(self, flow: http.HTTPFlow):
        url = flow.request.pretty_url
        info = {}
        try:
            body = json.loads(flow.response.get_text(strict=False))
            data = body.get("data")
            if isinstance(data, str) and len(data) > 32:
                info = _decrypt(data)
            elif isinstance(data, dict):
                info = data
        except Exception:
            info = {}

        token = str(info.get("token") or "")
        uid = info.get("uid") or 0
        student_num = str(info.get("account") or info.get("student_num") or "")
        name = str(info.get("name") or "")

        if USER_URL in url and student_num:
            print(">>> 👤 身份确认：%s（%s）" % (name or "未知", student_num), flush=True)

        if not token:
            return  # UserInfo 无 token 时等待真正的登录响应

        print(">>> ✅ 已捕获登录 token（%s…）" % token[:8], flush=True)
        marker = base64.b64encode(json.dumps({
            "token": token, "uid": uid,
            "student_num": student_num, "alias": name,
        }, ensure_ascii=False).encode("utf-8")).decode("ascii")
        print("[TOKEN_RESULT]" + marker, flush=True)
        ctx.master.shutdown()


addons = [LoginCapture()]
