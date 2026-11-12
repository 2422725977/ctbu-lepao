# -*- coding: utf-8 -*-
"""乐跑多账号服务端：Flask API + 多账号定时调度器

Docker 容器内运行，提供：
  - REST API：接收抓号客户端的 token、查询状态
  - Dashboard：简单的 HTML 状态页
  - 后台调度线程：每个账号在随机窗口时间自动提交
"""
import os, sys, re, json, time, socket, ipaddress, hashlib, datetime, threading
import urllib.parse, urllib.request
from flask import Flask, request, jsonify, render_template

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lepaorun"))
os.chdir(HERE)

from driver import submit_account, dryrun_account, Account, load_config

DATA_DIR = os.environ.get("LEPAO_DATA", "/app/data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
STATE_DIR = os.path.join(DATA_DIR, "state")

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"))

# 企业微信通知：仅环境变量配置（部署者控制），API 不可写入
WEBHOOK_URL = os.environ.get("LEPAO_WEBHOOK", "").strip()
WEBHOOK_HOST = "qyapi.weixin.qq.com"

# 学号白名单：仅字母数字下划线短横线（先校验再拼路径，防穿越）
SN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 每日候选提交窗口 (名称, 起时, 起分, 止时, 止分)
WINDOWS = [
    ("morning",    6, 30,  9,  0),
    ("noon",      12,  0, 14,  0),
    ("afternoon", 15,  0, 17, 30),
    ("evening",   18,  0, 22,  0),
]
MAX_RETRY_PER_DAY = 3    # 每账号每日最多尝试次数（硬失败）
RETRY_COOLDOWN_MIN = 30  # 失败后冷却分钟数（防风暴、防机器节律）
SSL_MAX_RETRY_PER_DAY = 40  # SSL/证书类失败放宽到全天（平台侧事故自愈后自动接上）

# 传给 driver 的全局配置（api_host + 策略默认值；账号级策略在 accounts.json 覆盖）
DEFAULT_CFG = {
    "environment": {"api_host": "lepao.ctbu.edu.cn"},
    "policy": {"max_per_day": 1, "style": "math", "pace_lo": 270, "pace_hi": 470,
               "dist_lo": 2.6, "dist_hi": 4.9},
}

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


def get_state(acc, today):
    """从账号条目取当日状态，日期变化自动重置"""
    st = acc.get("state") or {}
    if st.get("date") != today:
        st = {"date": today, "done": False, "attempts": 0}
    st.setdefault("attempts", 0)
    return st


def set_state(sn, st):
    """状态写回 accounts.json 对应账号条目（复用已审计的存取函数）"""
    data = load_accounts()
    for acc in data.get("accounts", []):
        if acc.get("student_num") == sn:
            acc["state"] = st
            save_accounts(data)
            return True
    return False


def _brief(summary):
    """摘要单行文本（日志/通知用）"""
    if not isinstance(summary, dict):
        return str(summary)[:120]
    rid = summary.get("record_id") or ""
    dist = summary.get("distance", "?")
    reason = summary.get("record_failed_reason") or ""
    if rid:
        return "record=%s %skm %s" % (rid, dist, reason)
    return str(summary)[:120]


def validate_sn(sn):
    """学号白名单校验：仅字母数字下划线短横线，杜绝路径穿越字符"""
    sn = str(sn or "").strip()
    if not SN_RE.match(sn):
        raise ValueError("非法学号: %r" % sn[:40])
    return sn


# ---------- API 路由 ----------
# 通知说明：容器内不做任何外呼（安全约束）。
# 企业微信通知由宿主机 cron + curl 实现：监控 docker logs 中的「成功/TOKEN_DEAD」行，
# 组合成文本 POST 到群机器人。配置方法见 docker-compose.yml 注释。
@app.route("/api/token", methods=["POST"])
def api_token():
    """接收抓号客户端发来的 token"""
    upload_key = os.environ.get("LEPAO_UPLOAD_KEY", "")
    if upload_key and request.headers.get("X-LP-Key") != upload_key:
        return jsonify({"error": "unauthorized"}), 403
    body = request.get_json(silent=True) or {}
    token = str(body.get("token", "")).strip()
    uid = body.get("uid")
    try:
        student_num = validate_sn(body.get("student_num"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not token or len(token) < 16:
        return jsonify({"error": "token 缺失或过短"}), 400
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
    try:
        sn = validate_sn(student_num)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    acc = get_account(sn)
    if not acc:
        return jsonify({"error": "account not found"}), 404
    summary, code = submit_account(acc, cfg=DEFAULT_CFG)
    st = get_state(acc, str(datetime.date.today()))
    st["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    st["last_exit"] = code
    if code in (0, 3):
        st["done"] = True
    set_state(sn, st)
    return jsonify({"exit_code": code, "summary": summary})


@app.route("/api/account/toggle", methods=["POST"])
def api_toggle():
    """启用/禁用账号"""
    body = request.get_json(silent=True) or {}
    try:
        sn = validate_sn(body.get("student_num"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data = load_accounts()
    for acc in data.get("accounts", []):
        if acc.get("student_num") == sn:
            acc["enabled"] = not acc.get("enabled", True)
            save_accounts(data)
            return jsonify({"ok": True, "enabled": acc["enabled"]})
    return jsonify({"error": "account not found"}), 404


@app.route("/api/account/toggle_all", methods=["POST"])
def api_toggle_all():
    """批量启用/禁用全部账号"""
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", True))
    data = load_accounts()
    changed = 0
    for acc in data.get("accounts", []):
        if acc.get("enabled", True) != enabled:
            acc["enabled"] = enabled
            changed += 1
    if changed:
        save_accounts(data)
    return jsonify({"ok": True, "changed": changed})


@app.route("/api/account/delete", methods=["POST"])
def api_delete():
    """删除账号"""
    body = request.get_json(silent=True) or {}
    try:
        sn = validate_sn(body.get("student_num"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    data = load_accounts()
    before = len(data.get("accounts", []))
    data["accounts"] = [a for a in data.get("accounts", []) if a.get("student_num") != sn]
    if len(data["accounts"]) == before:
        return jsonify({"error": "account not found"}), 404
    save_accounts(data)
    return jsonify({"ok": True})


@app.route("/")
def dashboard():
    today = str(datetime.date.today())
    rows = []
    for acc in load_accounts().get("accounts", []):
        try:
            sn = validate_sn(acc.get("student_num"))
        except ValueError:
            continue
        st = get_state(acc, today)
        _, tgt = pick_window(sn)
        issued = acc.get("auth", {}).get("issued_at", "")
        try:
            token_age = (datetime.datetime.now() - datetime.datetime.fromisoformat(issued)).days
        except ValueError:
            token_age = -1
        rows.append({
            "student_num": sn, "alias": acc.get("alias", sn),
            "enabled": acc.get("enabled", True),
            "token_prefix": acc.get("auth", {}).get("token", "")[:8],
            "token_issued": issued,
            "token_age": token_age,
            "target": "%02d:%02d" % divmod(tgt, 60),
            "done_today": st.get("done", False),
            "last_exit": st.get("last_exit"),
            "last_run": st.get("last_run", ""),
            "attempts": st.get("attempts", 0),
            "last_error": st.get("last_error", ""),
        })
    enabled_count = sum(1 for a in rows if a["enabled"])
    done_count = sum(1 for a in rows if a["done_today"])
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return render_template("status.html", accounts=rows, now=now,
                           enabled_count=enabled_count, done_count=done_count,
                           pending_count=len(rows) - done_count)


# ---------- 多账号调度器（后台线程） ----------
def _h(*parts):
    """sha256 确定性派生：调度时刻防扎堆（非加密用途）"""
    raw = "|".join(str(p) for p in parts).encode()
    return int(hashlib.sha256(raw).hexdigest(), 16)


def pick_window(sn):
    """种子=日期+学号：确定性选窗口与时刻，多账号不扎堆、每日不同"""
    idx = _h(datetime.date.today(), "pick", sn) % len(WINDOWS)
    win_name, h0, m0, h1, m1 = WINDOWS[idx]
    lo, hi = h0 * 60 + m0, h1 * 60 + m1
    span = max(1, hi - lo - 5)
    return win_name, lo + _h(datetime.date.today(), win_name, sn) % span


def is_ssl_error(err_text):
    """SSL/证书类失败：多为平台侧证书事故，放宽为全天耐心重试"""
    t = str(err_text or "").lower()
    return ("certificate" in t or "ssl" in t
            or "eof occurred in violation" in t)


def scheduler_loop():
    """后台线程：每账号每日一个确定性随机窗口时刻提交，失败冷却重试"""
    log("多账号调度器启动 windows=%s" % [w[0] for w in WINDOWS])
    while True:
        try:
            data = load_accounts()
            today = str(datetime.date.today())
            now = datetime.datetime.now()
            now_min = now.hour * 60 + now.minute
        except Exception as exc:
            log("调度器异常: %s" % exc)
            time.sleep(600)
            continue

        for acc in data.get("accounts", []):
            if not acc.get("enabled", True):
                continue
            try:
                sn = validate_sn(acc.get("student_num"))
            except ValueError as exc:
                log("跳过异常条目: %s" % exc)
                continue
            st = get_state(acc, today)
            if (st["done"] or now_min >= 22 * 60
                    or st["attempts"] >= MAX_RETRY_PER_DAY
                    or st.get("ssl_attempts", 0) >= SSL_MAX_RETRY_PER_DAY):
                continue  # 已完成 / 超出有效时段 / 硬失败次数耗尽 / SSL 宽限耗尽

            # 当日目标时刻（种子=日期+学号，确定性派生）
            win_name, tgt = pick_window(sn)
            tgt_h, tgt_m = divmod(tgt, 60)
            if now_min < tgt:
                continue
            last_min = st.get("last_attempt_min", -1000)
            if st["attempts"] > 0 and now_min - last_min < RETRY_COOLDOWN_MIN:
                continue

            st["last_attempt_min"] = now_min
            log(f"[{sn}] {win_name} 窗口到点 {tgt_h:02d}:{tgt_m:02d}，"
                f"第 {st['attempts'] + 1} 次尝试")
            try:
                summary, code = submit_account(acc, cfg=DEFAULT_CFG)
                st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
                st["last_exit"] = code
                if code == 0:
                    st["done"] = True
                    log(f"[{sn}] ✅ 成功! {_brief(summary)}")
                elif code == 2:
                    st["attempts"] = MAX_RETRY_PER_DAY  # token 失效，今日不再重试
                    log(f"[{sn}] TOKEN_DEAD — 需重新抓号")
                elif code == 3:
                    st["done"] = True
                    log(f"[{sn}] 当日额度已满，视为完成")
                else:
                    st["attempts"] += 1
                    log(f"[{sn}] ❌ 失败 exit={code} {_brief(summary)}")
            except Exception as exc:
                st["last_error"] = str(exc)[:200]
                if is_ssl_error(st["last_error"]):
                    st["ssl_attempts"] = st.get("ssl_attempts", 0) + 1
                    log(f"[{sn}] ⚠ SSL/证书类异常（不计入硬重试，全天放宽）: {st['last_error']}")
                else:
                    st["attempts"] += 1
                    log(f"[{sn}] ❌ 异常: {st['last_error']}")
            set_state(sn, st)

        # 每 10 分钟检查一轮
        time.sleep(600)


def start_scheduler():
    t = threading.Thread(target=scheduler_loop, daemon=True)
    t.start()


# ---------- 启动 ----------
if __name__ == "__main__":
    if os.environ.get("LEPAO_SCHEDULER", "1") != "0":
        start_scheduler()
    port = int(os.environ.get("PORT", "8080"))
    log("服务端启动: port=%d scheduler=%s" %
        (port, os.environ.get("LEPAO_SCHEDULER", "1") != "0"))
    app.run(host="0.0.0.0", port=port, debug=False)
