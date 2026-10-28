import sys, json
from mitmproxy.io import FlowReader

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/lepaoflows.mitm"
seen = set()
with open(path, "rb") as f:
    for flow in FlowReader(f).stream():
        if not hasattr(flow, "request"):
            continue
        req = flow.request
        if "lepao.ctbu.edu.cn" not in req.host:
            continue
        key = req.method + req.pretty_url
        # 同一 URL 只详细打印一次
        dup = key in seen
        seen.add(key)
        print("="*80)
        print(f"{req.method} {req.pretty_url}")
        if not dup:
            print("-- HEADERS --")
            for k, v in req.headers.items():
                print(f"  {k}: {v[:200]}")
        body = req.get_text(strict=False) if req.content else ""
        if body:
            print("-- REQ BODY --")
            print(" ", body[:1200])
        if hasattr(flow, "response") and flow.response:
            print(f"-- RESP {flow.response.status_code} --")
            try:
                print(" ", flow.response.get_text(strict=False)[:1500])
            except Exception as e:
                print("  <binary>", len(flow.response.raw_content or b""))
