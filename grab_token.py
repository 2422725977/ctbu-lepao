# -*- coding: utf-8 -*-
"""从 mitm 抓包文件里提取乐跑 token（loginByCode / loginByQyCodeCommon 响应）。

用法: ./venv/bin/python grab_token.py [flows.mitm ...]
输出: 每行 JSON {ts, token, uid, student_num, endpoint}
"""
import sys, os, json, glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lepaorun"))

from mitmproxy.io import FlowReader
from lepao.crypto import decrypt_data
from urllib.parse import parse_qs, unquote

TARGET = "lepao.ctbu.edu.cn"


def try_decrypt(text):
    """响应体 → 业务 JSON（明文/密文都试）。"""
    try:
        obj = json.loads(text)
    except Exception:
        return None
    if isinstance(obj, dict) and obj.get("is_encrypt") and obj.get("data"):
        try:
            return json.loads(decrypt_data(obj["data"]))
        except Exception:
            return None
    return obj


def scan(path):
    """优先从请求体解密拿 token（小程序会话里每条请求都带），再退回登录响应。"""
    out = []
    with open(path, "rb") as f:
        for flow in FlowReader(f).stream():
            req = getattr(flow, "request", None)
            if not req or TARGET not in req.host:
                continue
            # 1) 请求体：ostype=5&data=<AES(dict 含 token)>
            raw = req.get_text(strict=False) or ""
            if "data=" in raw:
                try:
                    q = parse_qs(raw)
                    plain = decrypt_data(unquote(q["data"][0]))
                    body = json.loads(plain)
                    if body.get("token"):
                        out.append({
                            "ts": req.timestamp_start,
                            "endpoint": req.path.split("v3/api.php/")[-1],
                            "token": body["token"],
                            "uid": body.get("uid"),
                            "student_num": body.get("student_num"),
                            "src": "request",
                        })
                        continue
                except Exception:
                    pass
            # 2) 响应体：登录接口返回 token
            resp = getattr(flow, "response", None)
            if not resp:
                continue
            body = try_decrypt(resp.get_text(strict=False) or "")
            if not isinstance(body, dict):
                continue
            tok = body.get("token")
            if not tok:
                continue
            out.append({
                "ts": req.timestamp_start,
                "endpoint": req.path.split("v3/api.php/")[-1],
                "token": tok,
                "uid": body.get("uid") or (body.get("user_info") or {}).get("uid"),
                "student_num": body.get("student_num") or body.get("card_id"),
                "src": "response",
            })
    return out


if __name__ == "__main__":
    paths = sys.argv[1:] or sorted(glob.glob(os.path.join(HERE, "flows", "*.mitm")))
    found = []
    for p in paths:
        try:
            found += scan(p)
        except Exception as e:
            print(f"[warn] {p}: {e}", file=sys.stderr)
    found.sort(key=lambda r: r["ts"] or 0)
    for r in found:
        print(json.dumps(r, ensure_ascii=False))
    if not found:
        print("NO_TOKEN", file=sys.stderr)
        sys.exit(2)
