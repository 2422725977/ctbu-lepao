# -*- coding: utf-8 -*-
"""乐跑多账号服务端：Flask API + 多账号定时调度器

Docker 容器内运行，提供：
  - REST API：接收抓号客户端的 token、查询状态
  - Dashboard：简单的 HTML 状态页
  - 后台调度线程：每个账号在随机窗口时间自动提交
"""
import os, sys, json, time, random, datetime, hashlib, threading
from flask import Flask, request, jsonify, render_template_string, render_template

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lepaorun"))
os.chdir(HERE)

from driver import submit_account, dryrun_account, Account, load_config

DATA_DIR = os.environ.get("LEPAO_DATA", "/app/data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
STATE_DIR = os.path.join(DATA_DIR, "state")

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"))

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<title>乐跑多账号管理</title>
<style>
body { font-family: system-ui, sans-serif; background: #0f0f23; color: #eee; padding: 2em; }
h1 { color: #7c4dff; } table { border-collapse: collapse; width: 100%; margin: 1em 0; }
td, th { border: 1px solid #333; padding: 10px 14px; text-align: left; }
th { background: #16213e; color: #7c4dff; } tr:hover { background: #1a1a3e; }
.ok { color: #4caf50; } .warn { color: #ff9800; } .err { color: #f44336; }
.badge-ok { background: #1b5e20; color: #a5d6a7; padding: 2px 8px; border-radius: 4px; }
.stat-cards { display: flex; gap: 1em; margin: 1em 0; flex-wrap: wrap; }
.stat-card { background: #16213e; padding: 1em 1.5em; border-radius: 8px; min-width: 120px; }
.stat-card .num { font-size: 2em; font-weight: bold; color: #7c4dff; }
.stat-card .label { color: #888; font-size: 0.85em; }
</style></head><body>
<h1>🏃 乐跑多账号管理</h1>
<div class="stat-cards">
  <div class="stat-card"><div class="num">{{ accounts|length }}</div><div class="label">账号数</div></div>
  <div class="stat-card"><div class="num">{{ enabled_count }}</div><div class="label">已启用</div></div>
</div>
<table>
<tr><th>学号</th><th>姓名</th><th>Token</th><th>签发时间</th><th>状态</th></tr>
{% for acc in accounts %}
<tr>
  <td>{{ acc.student_num }}</td>
  <td>{{ acc.alias }}</td>
  <td>{{ acc.auth.token[:8] }}…</td>
  <td>{{ acc.auth.issued_at }}</td>
  <td><span class="badge-ok">✓ 有效</span></td>
</tr>
{% endfor %}
</table>
<p class="refresh-info">自动刷新: 60s</p>
</body></html>'''

# ---------- 账号管理 ----------
def load_accounts():
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"accounts": []}


def save_accounts(data):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def get_account(student_num):
    data = load_accounts()
    for acc in data.get("accounts", []):
        if acc.get("student_num") == student_num:
            return acc
    return None


def update_token(student_num, token, uid=None):
    data = load_accounts()
    for acc in data.get("accounts", []):
        if acc.get("student_num") == student_num:
            acc.setdefault("auth", {})["token"] = token
            acc["auth"]["issued_at"] = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            if uid:
                acc["uid"] = uid
            save_accounts(data)
            return True
    return False


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(os.path.join(LOG_DIR, f"server_{datetime.date.today():%Y%m%d}.log"),
              "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- API 路由 ----------
@app.route("/api/token", methods=["POST"])
def api_token():
    """接收抓号客户端发来的 token"""
    body = request.get_json(silent=True) or {}
    student_num = body.get("student_num", "")
    token = body.get("token", "")
    uid = body.get("uid")
    if not student_num or not token:
        return jsonify({"error": "student_num and token required"}), 400
    ok = update_token(student_num, token, uid)
    if ok:
        log(f"token updated: {student_num} → {token[:8]}…")
        return jsonify({"ok": True, "msg": "token updated"})
    # 新账号 → 自动添加
    data = load_accounts()
    data.setdefault("accounts", []).append({
        "student_num": student_num, "uid": uid or 0,
        "school_id": 201, "alias": student_num, "enabled": True,
        "auth": {"token": token, "issued_at": datetime.datetime.now().isoformat()}
    })
    save_accounts(data)
    log(f"new account added: {student_num} → {token[:8]}…")
    return jsonify({"ok": True, "msg": "new account created"})


@app.route("/api/status", methods=["GET"])
def api_status():
    data = load_accounts()
    accounts = []
    for acc in data.get("accounts", []):
        accounts.append({
            "student_num": acc.get("student_num"),
            "name": acc.get("alias", acc.get("name", "")),
            "enabled": acc.get("enabled", True),
            "token_prefix": acc.get("auth", {}).get("token", "")[:8],
            "token_issued": acc.get("auth", {}).get("issued_at", ""),
        })
    return jsonify({"accounts": accounts, "count": len(accounts)})


@app.route("/api/submit/<student_num>", methods=["POST"])
def api_submit(student_num):
    """手动触发单个账号提交"""
    acc = get_account(student_num)
    if not acc:
        return jsonify({"error": "account not found"}), 404
    summary, code = submit_account(acc)
    return jsonify({"exit_code": code, "summary": summary})


@app.route("/")
def dashboard():
    data = load_accounts()
    accounts = data.get("accounts", [])
    enabled_count = sum(1 for a in accounts if a.get("enabled", True))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template_string(DASHBOARD_HTML, accounts=accounts,
                                  enabled_count=enabled_count, now=now)


# ---------- 多账号调度器（后台线程） ----------
DEFAULT_WINDOWS = [
    ("noon",    12, 0, 14, 0),
    ("evening", 18, 0, 22, 30),
]


def daily_target(win_name, h0, m0, h1, m1, student_num):
    seed_str = f"{datetime.date.today()}{win_name}{student_num}"
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:12], 16)
    lo, hi = h0 * 60 + m0, h1 * 60 + m1
    return random.Random(seed).randint(lo, max(lo, hi - 5))


def scheduler_loop():
    """后台线程：每个账号在独立随机窗口时刻自动提交"""
    log("多账号调度器启动")
    while True:
        data = load_accounts()
        today = str(datetime.date.today())
        now = datetime.datetime.now()
        now_min = now.hour * 60 + now.minute

        for acc in data.get("accounts", []):
            if not acc.get("enabled", True):
                continue
            sn = acc["student_num"]
            state_file = os.path.join(STATE_DIR, f"{sn}_state.json")
            try:
                with open(state_file, encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                st = {}
            if st.get("date") != today:
                st = {"date": today, "done": False, "failed_count": 0}
            if st.get("done"):
                continue

            # 计算当日目标时刻（每账号不同：种子=日期+学号）
            tgt = daily_target("noon", 12, 0, 14, 0, sn)
            tgt_h, tgt_m = divmod(tgt, 60)
            if now_min >= tgt:
                log(f"[{sn}] 到点 {tgt_h:02d}:{tgt_m:02d}，开始提交")
                acc_dict = {**acc, "auth": {"token": acc.get("auth", {}).get("token", "")}}
                try:
                    summary, code = submit_account(acc_dict)
                    st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
                    st["last_exit"] = code
                    if code == 0:
                        st["done"] = True
                        log(f"[{sn}] ✅ 成功! {summary}")
                    elif code == 2:
                        log(f"[{sn}] TOKEN_DEAD — 需要重新抓号")
                    elif code == 3:
                        st["done"] = True
                        log(f"[{sn}] 当日额度已满")
                    else:
                        st["failed_count"] = st.get("failed_count", 0) + 1
                        log(f"[{sn}] ❌ 失败 exit={code}")
                except Exception as e:
                    log(f"[{sn}] ❌ 异常: {e}")
                # 保存状态
                os.makedirs(STATE_DIR, exist_ok=True)
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False, indent=1)

        # 每 60 秒检查一轮
        time.sleep(60)


def start_scheduler():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


# ---------- 启动 ----------
if __name__ == "__main__":
    # 启动调度器后台线程
    sched_thread = threading.Thread(target=scheduler_loop, daemon=True)
    sched_thread.start()
    # 启动 Flask
    port = int(os.environ.get("PORT", "8080"))
    log(f"服务端启动: port={port}")
    app.run(host="0.0.0.0", port=port, debug=False)
