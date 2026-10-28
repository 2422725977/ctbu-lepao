import base64, json
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from mitmproxy.io import FlowReader

KEY, IV = b"Wet2C8d34f62ndi3", b"K6iv85jBD8jgf32D"
def dec(b64):
    return unpad(AES.new(KEY, AES.MODE_CBC, IV).decrypt(base64.b64decode(b64)), 16).decode()

best = None
with open("/tmp/lepaoflows.mitm","rb") as f:
    for flow in FlowReader(f).stream():
        if not hasattr(flow,"request") or "lepao" not in flow.request.host: continue
        url = flow.request.pretty_url.split("api.php/")[1]
        try:
            j = json.loads(flow.response.get_text(strict=False))
            plain = dec(j["data"]) if isinstance(j.get("data"), str) else None
        except Exception:
            continue
        if url == "Run2/beforeRunV260" and plain:
            d = json.loads(plain)
            if d.get("run_line_info"):
                best = plain
if best:
    d = json.loads(best)
    with open("/tmp/ctbu_beforeRun.json","w") as f2:
        json.dump(d, f2, ensure_ascii=False, indent=1)
    rli = d.get("run_line_info", {})
    print("run_zone_id:", d.get("run_zone_id"), d.get("run_zone_name"))
    print("term_id:", d.get("term_id"))
    print("keys:", sorted(d.keys()))
    print("run_line_info keys:", sorted(rli.keys()) if isinstance(rli, dict) else type(rli))
    pts = rli.get("point_list") if isinstance(rli, dict) else None
    print("checkpoint count:", len(pts) if pts else 0)
    if pts:
        for p in pts[:20]:
            print("  point:", p.get("id"), p.get("jingwei"), p.get("name", ""))
    trs = d.get("time_rule_arr")
    print("time_rule_arr:", json.dumps(trs, ensure_ascii=False))
    # 其它距离/配速相关字段
    for k in d:
        if any(s in k.lower() for s in ("dist","pace","speed","num","time")):
            print(k, "=", json.dumps(d[k], ensure_ascii=False)[:200])
