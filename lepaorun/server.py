# -*- coding: utf-8 -*-
"""乐跑多账号服务端（零文件 I/O 版——配置通过 import 加载，状态存内存）"""
import os, sys, json, time, datetime, hashlib, threading
from flask import Flask, request, jsonify

WINDOWS = [("noon", 12, 0, 14, 0), ("evening", 18, 0, 22, 30)]
_states = {}
_lock = threading.Lock()


def _validate_sn(sn):
    sn = str(sn).strip()
    if not re.match(r'^[a-zA-Z0-9_-]+$', sn):
        raise ValueError(f"bad sn: {sn}")
    return sn


def _sn_key(sn):
    return hashlib.sha256(sn.encode()).hexdigest()[:16]


def _get_accounts():
    """从 accounts 模块读取（由环境变量 LEPAO_ACCOUNTS_IMPORT 指定）"""
    import accounts  # accounts.py 定义 ACCOUNTS = [...]
    return accounts.ACCOUNTS


def _save_token(student_num, token):
    """token 热更新：写内存 + 标记持久化文件"""
    # 内存更新
    for acc in _get_accounts():
        if acc["student_num"] == student_num:
            acc.setdefault("auth", {})["token"] = token
            break
    # 同时通知 accounts.py 持久化
    try:
        import accounts as acc_mod
        if hasattr(acc_mod, "update_token"):
            acc_mod.update_token(student_num, token)
    except Exception:
        pass


def get_account(sn):
    sn = _validate_sn(sn)
    for acc in _get_accounts():
        if acc.get("student_num") == sn:
            return acc
    return None


def daily_target(win_name, h0, m0, h1, m1, sn):
    seed_str = str(datetime.date.today()) + win_name + sn
    h = int(hashlib.sha256(seed_str.encode()).hexdigest()[:12], 16)
    lo, hi = h0 * 60 + m0, h1 * 60 + m1
    return lo + h % (hi - lo)


# ---------- 内存状态 ----------
class _MemState:
    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, sn):
        key = hashlib.sha256(sn.encode()).hexdigest()[:16]
        with self._lock:
            st = self._data.get(key, {})
            if st.get("date") != str(datetime.date.today()):
                st = {"date": str(datetime.date.today()), "done": False, "failed_count": 0}
            self._data[key] = st
            return st

    def set(self, sn, st):
        key = hashlib.sha256(sn.encode()).hexdigest()[:16]
        with self._lock:
            st["date"] = str(datetime.date.today())
            self._data[key] = st


_mem = _MemState()


def log(msg):
    print(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + msg, flush=True)


# ---------- 提交逻辑 ----------
def _do_submit(sn):
    from lepao.v3api import V3Client
    from lepao import record as REC
    from lepao import ctbu_track as CT
    from lepao.crypto import encrypt_data
    import random as _rnd
    import secrets

    acc = get_account(sn)
    if not acc: return {"error": "not found"}, 404, None
    if not acc.get("enabled", True): return {"error": "disabled"}, 400, None

    uid = acc["uid"]
    token = acc.get("auth", {}).get("token", "")
    school_id = acc.get("school_id", 201)
    sn_num = acc["student_num"]
    alias = acc.get("alias", sn)

    client = V3Client(uid=uid, token=token, school_id=school_id,
                      student_num=sn_num, card_id=acc.get("card_id", sn_num),
                      host="lepao.ctbu.edu.cn", pace=0.8)

    r = client.post("/v3/api.php/WpRun/getTermList", {})
    if isinstance(r, dict) and r.get("status") == 101:
        log(f"[{alias}] TOKEN_DEAD"); return None, 2

    cfg = client.before_run()
    rli = cfg.get("run_line_info") or {}
    zone = str(cfg.get("run_zone_id") or "")
    cps = []
    for p in rli.get("point_list") or []:
        try:
            la, lo_ = str(p.get("jingwei")).strip().split(",")
            cps.append((float(la), float(lo_), str(p.get("id"))))
        except Exception: continue
    if len(cps) < 2: return None, 4

    day0 = int(time.mktime(datetime.date.today().timetuple()))
    done = 0
    for page in (1, 2):
        rr = client.post("/v3/api.php/WpRun/getTermRunRecord", {"term_id": str(cfg.get("term_id","0")), "page": page})
        for rec in (rr.get("list") or [] if isinstance(rr, dict) else []):
            try:
                if int(rec.get("start_time") or 0) >= day0 and float(rec.get("distance") or 0) > 0: done += 1
            except Exception: pass
        if not (isinstance(rr, dict) and rr.get("list")): break
    if done >= 1: return None, 3

    srv_now = client.get_timestamp()
    res = CT.generate_run(cps, _rnd.uniform(2750, 4700), _rnd)
    tr_json = json.dumps(res["track"], ensure_ascii=False, separators=(",", ":"))

    end_time = srv_now - _rnd.randint(15, 75)
    start_time = end_time - res["used_time"]
    ts = int(time.time() * 1000)
    record_file = f"run_record/{datetime.date.today().isoformat()}/{ts % 1000:03d}/{ts}-{secrets.randbelow(90)+10}.txt"
    g = {
        "term_id": 1, "game_id": zone,
        "start_time": int(start_time), "end_time": int(end_time),
        "distance": float(res["distance_km"]), "record_img": "",
        "log_data": json.dumps([{**e, "time": int(start_time) + max(5, int(e["time"]))} for e in res["log_data"]], ensure_ascii=False, separators=(",", ":")),
        "file_img": "", "is_running_area_valid": 1,
        "mobileDeviceId": 1, "mobileModel": "iPhone14,3", "mobileOsVersion": 1,
        "step_info": json.dumps(res["step_info"], separators=(",", ":")),
        "step_num": int(res["step_num"]), "used_time": int(res["used_time"]),
        "record_file": record_file,
    }

    sts = client.get_oss_sts()
    if not isinstance(sts, dict) or "AccessKeySecret" not in sts: return None, 4
    status, _ = REC.upload_to_oss(encrypt_data(tr_json), sts, ts, record_file, _rnd)
    if status not in (200, 203, 204): return None, 4

    resp = client.stop_run(g)
    if not isinstance(resp, dict) or not resp.get("record_id"): return resp, 4
    record_id = resp["record_id"]

    try:
        sts2 = client.get_oss_sts()
        gkey = "run_gyroscope/" + str(ts % 1000).zfill(3)
        st2, _ = REC.upload_oss_content(sts2, gkey, json.dumps(res["gyr"], separators=(",", ":")), _rnd)
        if st2 in (200, 203, 204):
            client.post("/v3/api.php/Run2/gyroscope", {"record_id": record_id, "gyroscope_file": gkey})
    except Exception: pass

    time.sleep(2)
    detail = client.post("/v3/api.php/Run/recordDetailV270", {"record_id": str(record_id), "year_num": str(datetime.date.today().year)})
    summary = {
        "record_id": record_id, "distance": g["distance"], "used_time": g["used_time"],
        "record_status": detail.get("record_status") if isinstance(detail, dict) else None,
        "record_failed_reason": detail.get("record_failed_reason") if isinstance(detail, dict) else None,
    }
    log(f"[{alias}] 成功: {record_id} {g['distance']}km status={summary['record_status']}")
    return summary, 0


def submit_for(sn):
    acc = get_account(sn)
    if not acc: return {"error": "not found"}, 404, None
    if not acc.get("enabled", True): return {"error": "disabled"}, 400, None
    return _do_submit(sn)


# ============================ Flask ============================
app = Flask(__name__)


@app.route("/api/status")
def api_status():
    data = _get_accounts()
    result = []
    for acc in data:
        sn = acc["student_num"]
        st = _mem.get(sn)
        result.append({
            "student_num": sn, "alias": acc.get("alias", sn),
            "enabled": acc.get("enabled", True),
            "token": acc.get("auth", {}).get("token", "")[:8] + "…",
            "issued": acc.get("auth", {}).get("issued_at", ""),
            "done_today": st.get("done", False),
            "failed": st.get("failed_count", 0),
            "last_run": st.get("last_run", ""),
        })
    return jsonify({"accounts": result, "count": len(result)})


@app.route("/api/account/add", methods=["POST"])
def api_add():
    body = request.get_json(silent=True) or {}
    try:
        sn = _validate_sn(body.get("student_num", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not sn: return jsonify({"error": "学号必填"}), 400
    data = _get_accounts()
    for acc in data:
        if acc["student_num"] == sn: return jsonify({"error": "已存在"}), 409
    data.append({
        "student_num": sn, "uid": body.get("uid", 0), "school_id": 201,
        "alias": body.get("alias", sn), "card_id": sn, "enabled": True,
        "auth": {"token": body.get("token", ""), "issued_at": datetime.datetime.now().isoformat()},
    })
    return jsonify({"ok": True, "msg": f"已添加 {sn}"})


@app.route("/api/account/delete", methods=["POST"])
def api_delete():
    body = request.get_json(silent=True) or {}
    sn = str(body.get("student_num", ""))
    return jsonify({"ok": True})


@app.route("/api/account/toggle", methods=["POST"])
def api_toggle():
    body = request.get_json(silent=True) or {}
    sn = str(body.get("student_num", ""))
    return jsonify({"ok": True})


@app.route("/api/token", methods=["POST"])
def api_token():
    body = request.get_json(silent=True) or {}
    sn = _validate_sn(body.get("student_num", ""))
    token = str(body.get("token", ""))
    if not sn or not token: return jsonify({"error": "missing"}), 400
    return jsonify({"ok": True})


@app.route("/api/submit/<sn>", methods=["POST"])
def api_submit(sn):
    sn = _validate_sn(sn)
    summary, code, _ = submit_for(sn)
    return jsonify({"exit_code": code, "summary": summary})


@app.route("/")
def dashboard():
    data = _get_accounts()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    enabled = 0
    done_today = 0
    rows = ""
    for acc in data:
        sn = _validate_sn(acc["student_num"])
        st = _mem.get(sn)
        tok = acc.get("auth", {}).get("token", "")
        is_en = acc.get("enabled", True)
        if is_en: enabled += 1
        if st.get("done"): done_today += 1
        tok_cls = "token-ok" if tok else "token-dead"
        done_cls = "badge-ok" if st.get("done") else ("badge-dead" if st.get("failed_count") else "badge-off")
        done_txt = "✓ 已完成" if st.get("done") else ("✗ 失败" if st.get("failed_count") else "待提交")
        last = st.get("last_run", "—")
        submit_btn = f'<button class="btn btn-green" onclick="submitOne(\'{sn}\')">▶</button>' if not st.get("done") else ""
        rows += f"<tr><td>{sn}</td><td>{acc.get('alias', sn)}</td><td><span class='{tok_cls}'>{tok[:8]}…</span></td><td>{acc.get('auth',{}).get('issued_at','—')[:16]}</td><td><span class='{done_cls}'>{done_txt}</span></td><td>{last}</td><td>{submit_btn}<button class='btn' onclick=\"toggleAccount('{sn}')\">{'禁用' if is_en else '启用'}</button><button class='btn btn-red' onclick=\"deleteAccount('{sn}')\">🗑</button></td></tr>"

    return f"""<!DOCTYPE html><html lang="zh-CN"><head>
<meta charset="utf-8"><meta http-equiv="refresh" content="60">
<title>乐跑多账号管理</title>
<style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ font-family: system-ui, sans-serif; background: #0d1117; color: #c9d1d9; padding: 2em; max-width: 1100px; margin: 0 auto; }}
h1 {{ color: #58a6ff; font-size: 1.5em; }}
.cards {{ display: flex; gap: 1em; flex-wrap: wrap; margin: 1em 0; }}
.card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1em 1.5em; min-width: 140px; }}
.card .num {{ font-size: 2.2em; font-weight: 700; color: #58a6ff; }}
.card .num.green {{ color: #3fb950; }} .card .num.orange {{ color: #d29922; }}
.card .label {{ color: #8b949e; font-size: 0.85em; }}
.section {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1.5em; margin: 1.5em 0; }}
.section h2 {{ color: #58a6ff; font-size: 1.1em; margin-bottom: 1em; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ border: 1px solid #30363d; padding: 8px 12px; text-align: left; }}
th {{ background: #21262d; color: #8b949e; }} tr:hover td {{ background: #1c2128; }}
.btn {{ padding: 5px 14px; border-radius: 6px; border: 1px solid #30363d; background: #21262d; color: #c9d1d9; cursor: pointer; font-size: 0.85em; }}
.btn-green {{ background: #238636; color: white; }} .btn-green:hover {{ background: #2ea043; }}
.btn-red {{ background: #da3633; color: white; }} .btn:hover {{ background: #30363d; }}
.token-ok {{ color: #3fb950; }} .token-dead {{ color: #f85149; }}
.badge-ok {{ background: #0d4429; color: #3fb950; padding: 3px 10px; border-radius: 12px; font-size: 0.82em; }}
.badge-off {{ background: #30363d; color: #8b949e; padding: 3px 10px; border-radius: 12px; font-size: 0.82em; }}
.badge-dead {{ background: #490202; color: #f85149; padding: 3px 10px; border-radius: 12px; font-size: 0.82em; }}
</style></head><body>
<h1>🏃 乐跑多账号管理</h1>
<p style="color:#8b949e">{now} · 自动刷新 60s</p>
<div class="cards">
<div class="card"><div class="num">{len(data)}</div><div class="label">总账号</div></div>
<div class="card"><div class="num green">{enabled}</div><div class="label">已启用</div></div>
<div class="card"><div class="num green">{done_today}</div><div class="label">今日已完成</div></div>
<div class="card"><div class="num orange">{len(data) - done_today}</div><div class="label">待提交</div></div>
</div>
<div class="section"><h2>📋 账号列表</h2>
<table><tr><th>学号</th><th>姓名</th><th>Token</th><th>签发</th><th>今日</th><th>上次</th><th>操作</th></tr>
{rows}
</table></div>
</body></html>"""


# ============================ 调度器 ============================
def scheduler_loop():
    log("调度器启动")
    while True:
        try:
            data = _get_accounts()
            now = datetime.datetime.now()
            now_min = now.hour * 60 + now.minute
            for acc_raw in data:
                sn = _validate_sn(acc_raw["student_num"])
                if not acc_raw.get("enabled", True): continue
                st = _mem.get(sn)
                if st.get("done"): continue
                for win_name, h0, m0, h1, m1 in WINDOWS:
                    tgt = daily_target(win_name, h0, m0, h1, m1, sn)
                    if now_min >= tgt and not st.get(f"fired_{win_name}"):
                        st[f"fired_{win_name}"] = True
                        _mem.set(sn, st)
                        log(f"[{sn}] 到点，提交")
                        summary, code, _ = submit_for(sn)
                        if code == 0:
                            st = _mem.get(sn)
                            st["done"] = True
                            _mem.set(sn, st)
                            log(f"[{sn}] ✅ 成功")
                        elif code == 2: log(f"[{sn}] TOKEN_DEAD")
                        elif code == 3:
                            st = _mem.get(sn); st["done"] = True; _mem.set(sn, st)
                            log(f"[{sn}] 额度满")
                        else:
                            st = _mem.get(sn); st["failed_count"] = st.get("failed_count", 0) + 1
                            _mem.set(sn, st)
                            log(f"[{sn}] fail exit={code}")
                        break
            time.sleep(30)
        except Exception as e:
            log(f"sched err: {e}")
            time.sleep(60)


def start_scheduler():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


if __name__ == "__main__":
    start_scheduler()
    port = int(os.environ.get("PORT", "8080"))
    log(f"服务端启动: port={port}")
    app.run(host="127.0.0.1", port=port, debug=False)
