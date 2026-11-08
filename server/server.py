# -*- coding: utf-8 -*-
"""乐跑多账号服务端：Flask API + 静态管理台 + 多账号定时调度器

Docker 容器内单进程运行，提供：
  - REST API：接收抓号客户端的 token、查询状态、手动提交、看板聚合
  - Dashboard：render_template("dashboard.html")，数据由前端 JS 调 /api/dashboard 局部刷新
  - 后台调度线程：每个账号在随机窗口时间自动提交（语义保持不变）

部署约定：公网由 Nginx 反代到 127.0.0.1:8080；域名 / IP 不写进业务代码。
"""
import os, sys, json, time, random, datetime, hashlib, threading, re
from flask import Flask, request, jsonify, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lepaorun"))
os.chdir(HERE)

from driver import submit_account, dryrun_account, Account, load_config

# ---------- 数据路径 / 运行期配置 ----------
DATA_DIR = os.environ.get("LEPAO_DATA", "/app/data")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")
STATE_DIR = os.path.join(DATA_DIR, "state")

# 仅用于生成展示用链接；空字符串 = 同源相对链接。域名只在 Nginx / 部署层配置。
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
ENV = os.environ.get("LEPAO_ENV", "production")
# 可选「只读展示」开关（默认关闭）：仅影响 /api/dashboard 下发的展示态字段，
# 不拦截任何写接口、不改变既有 API 契约。真正的公网写入拦截由 Nginx 层负责
# （注释示例见 server/接口契约.md §8.1）。
LEPAO_READ_ONLY = os.environ.get("LEPAO_READ_ONLY", "false").strip().lower() in (
    "1", "true", "yes", "on")
TERM_TARGET = 40        # 学期达标目标（设计规格 §7）
TOKEN_STALE_DAYS = 7    # 凭证签发超过该天数且无有效证据 → unknown（需重新抓号校验）

# Asia/Shanghai：容器由 TZ + 系统 tzdata 保证；此处显式用于展示时间与时刻比较。
# 无 tzdata 时退回固定 +08:00（Asia/Shanghai 自 1991 年起无夏令时，等价）。
try:
    from zoneinfo import ZoneInfo
    SHANGHAI = ZoneInfo("Asia/Shanghai")
except Exception:
    SHANGHAI = datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai")

app = Flask(
    __name__,
    template_folder=os.path.join(HERE, "templates"),
    static_folder=os.path.join(HERE, "static"),   # 默认 server/static/ → 容器内 /app/static/
)
app.json.ensure_ascii = False   # Flask 3 的 app.json.provider 配置项（非 JSONEncoder）
# 反代还原真实 scheme / host / client ip：X-Forwarded-* 各信任 1 层
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# 页面模板由前端交付：templates/dashboard.html + static/dashboard.css + static/dashboard.js
# 后端不再内联 HTML、不再有整页 meta refresh；数据全部走 /api/dashboard。

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
    # 模板内不做数据注入：全部状态由前端 JS 调 /api/dashboard 获取（数据区 30s 局部刷新）。
    # 仅传无害的启动元数据；静态资源由 url_for('static', ...) 生成同源相对路径。
    return render_template("dashboard.html", env=ENV, tz="Asia/Shanghai",
                           public_base_url=PUBLIC_BASE_URL)


# ---------- 看板聚合（GET /api/dashboard） ----------
# 只读、零网络调用。数据来源：accounts.json + data/state/<学号>_state.json
# + data/logs/server_YYYYMMDD.log + 既有 daily_target() / DEFAULT_WINDOWS。
# 所有文件读取均容错：文件不存在 / 损坏一律返回空数组或 0，不编造数值。
EXIT_MESSAGES = {
    0: "提交成功，等待审查；仅 record_status=1 计入成绩与达标",
    2: "凭证失效：重新抓号后可恢复，请更新凭证",
    3: "当日额度已满：无需重试，次日自动恢复",
    4: "接口异常：已生成告警，请查看日志或重试",
}

# 设计规格 §5 六类状态徽章：API 传稳定的机器键，中文标签由前端统一渲染。
STATUS_LABELS = {
    "ok": "正常", "pending": "待执行", "running": "运行中",
    "attention": "需关注", "error": "失败", "disabled": "已停用",
}


def _readonly_fields():
    """可选只读展示字段：LEPAO_READ_ONLY=true 时才下发；默认不下发，保持默认响应结构不变。"""
    return {"read_only": True, "permissions": {"write": False}} if LEPAO_READ_ONLY else {}


def _now_sh():
    return datetime.datetime.now(SHANGHAI)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


def _parse_time(value):
    """日志时间 / issued_at / last_run → 带 Asia/Shanghai 时区的 datetime；失败返回 None。"""
    if not value:
        return None
    s = str(value).strip().replace("T", " ")
    dt = None
    try:
        dt = datetime.datetime.fromisoformat(s)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.datetime.strptime(s, fmt)
                break
            except Exception:
                continue
    if dt is None:
        return None
    return dt.replace(tzinfo=SHANGHAI) if dt.tzinfo is None else dt.astimezone(SHANGHAI)


def _as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _as_exit(value):
    """退出码 → int；缺失 / 非法 / 布尔一律 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _redact(text, secrets):
    """日志兜底脱敏：任何完整 token 一律只留前 8 位，绝不进入响应。"""
    for tok in secrets:
        if tok and len(tok) >= 8:
            text = text.replace(tok, tok[:8] + "…")
    return re.sub(r"\b[0-9a-fA-F]{32}\b", lambda m: m.group(0)[:8] + "…", text)


def _log_level(msg):
    if "TOKEN_DEAD" in msg or "❌" in msg or "失败" in msg or "异常" in msg:
        return "error"
    if "额度已满" in msg:
        return "warn"
    if "✅" in msg or "成功" in msg:
        return "success"
    return "info"


def _read_log_lines():
    """读当日 server_YYYYMMDD.log；文件不存在 / 不可读返回 []（设计规格 §7 容错要求）。"""
    candidates = []
    for d in (datetime.date.today(), _now_sh().date()):
        p = os.path.join(LOG_DIR, f"server_{d:%Y%m%d}.log")
        if p not in candidates:
            candidates.append(p)
    for p in candidates:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = [ln.rstrip("\r\n") for ln in f if ln.strip()]
            if lines:
                return lines
        except Exception:
            continue
    return []


def _parse_events(lines, secrets):
    events = []
    for raw in lines:
        raw = _redact(raw, secrets)
        m = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(.*)$", raw)
        if not m:
            continue
        ts, msg = m.group(1), m.group(2)
        sm = re.match(r"^\[([^\]]+)\]\s*(.*)$", msg)
        sn = sm.group(1) if sm else ""
        if sm:
            msg = sm.group(2)
        events.append({"time": ts, "level": _log_level(msg),
                       "student_num": sn, "message": msg})
    return events


def _read_state(student_num):
    p = os.path.join(STATE_DIR, f"{student_num}_state.json")
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        return (st if isinstance(st, dict) else {}), p
    except Exception:
        return {}, p


def _term_stats(st):
    """仅 record_status == 1 计入达标；无明细 / 无汇总字段 → (0, 0)，不编造。"""
    recs = st.get("records")
    if isinstance(recs, list):
        valid = sum(1 for r in recs
                    if isinstance(r, dict) and str(r.get("record_status")) == "1")
        return len(recs), valid
    if isinstance(st.get("term_total"), (int, float)):
        return _as_int(st.get("term_total")), _as_int(st.get("term_valid"))
    return 0, 0


def _credential(auth, st, last_exit, dead_from_logs):
    """ok | dead | unknown：由 token 存在与否 + 签发时间 + state/日志的 TOKEN_DEAD 线索推断。"""
    if not str(auth.get("token") or ""):
        return "unknown"
    issued = _parse_time(auth.get("issued_at"))
    dead = bool(st.get("dead_notified") or st.get("token_dead") or dead_from_logs)
    if last_exit == 2:
        # 若凭证在 TOKEN_DEAD 之后被重新签发，该线索失效
        lr = _parse_time(st.get("last_run"))
        if issued is None or lr is None or issued <= lr:
            dead = True
    if dead:
        return "dead"
    if issued is None:
        return "unknown"
    if (_now_sh() - issued).days > TOKEN_STALE_DAYS:
        return "unknown"          # 签发过久且无有效证据，不宣称「有效」
    return "ok"


def _today_status(enabled, done, failed_count, last_exit, credential, target_min, now_min):
    """设计规格 §5 六类状态，返回机器键（与前端 STATUS 映射一致）。"""
    if not enabled:
        return "disabled"
    if last_exit == 2 or credential == "dead":
        return "attention"
    if last_exit == 4:
        return "error"
    if done:
        return "ok"               # 含 exit 3 额度已满：当日已闭环
    if failed_count > 0:
        return "attention"
    if now_min >= target_min:
        return "running"          # 已到目标时刻、调度中，尚无成功结果
    return "pending"


def _windows_payload():
    return [{"name": n, "start": f"{h0:02d}:{m0:02d}", "end": f"{h1:02d}:{m1:02d}"}
            for (n, h0, m0, h1, m1) in DEFAULT_WINDOWS]


def _dashboard_error_payload(exc):
    """聚合异常也返回固定键结构，保证前端错误态不崩、不静默失败。"""
    now = _now_sh()
    return {
        "server_time": _fmt(now), "data_time": "", "env": ENV, "health": "error",
        "summary": {"accounts_total": 0, "enabled": 0, "today_done": 0,
                    "today_total": 0, "alerts_count": 1, "term_total": 0,
                    "term_valid": 0, "term_target": TERM_TARGET,
                    "stat_caliber": "仅 record_status=1 计入达标；数据源异常，term_total/term_valid 为 0"},
        "windows": _windows_payload(), "accounts": [],
        "alerts": [{"level": "error", "message": f"看板数据聚合失败：{type(exc).__name__}",
                    "student_num": "", "time": _fmt(now)}],
        "recent_events": [], "logs_tail": [], "public_base_url": PUBLIC_BASE_URL,
        **_readonly_fields(),
    }


def build_dashboard():
    now = _now_sh()
    now_min = now.hour * 60 + now.minute
    today = str(datetime.date.today())   # 与 scheduler_loop / daily_target 同一日期口径

    accounts_raw = []
    accounts_ok = True
    try:
        with open(ACCOUNTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        accounts_raw = data.get("accounts", []) if isinstance(data, dict) else []
        if not isinstance(accounts_raw, list):
            accounts_raw = []
    except Exception:
        accounts_ok = False

    secrets = [str((a.get("auth") or {}).get("token") or "")
               for a in accounts_raw if isinstance(a, dict)]
    lines = _read_log_lines()
    events = _parse_events(lines, secrets)

    mtimes = []
    try:
        mtimes.append(os.path.getmtime(ACCOUNTS_FILE))
    except Exception:
        pass
    for d in (datetime.date.today(), now.date()):
        try:
            mtimes.append(os.path.getmtime(os.path.join(LOG_DIR, f"server_{d:%Y%m%d}.log")))
        except Exception:
            pass

    account_views, alerts, seen_alerts = [], [], set()

    def add_alert(level, message, sn, when):
        key = (sn, message)
        if key in seen_alerts:      # M05 告警去重
            return
        seen_alerts.add(key)
        alerts.append({"level": level, "message": message,
                       "student_num": sn, "time": when or ""})

    enabled_count = today_done = term_total = term_valid = 0
    any_dead = any_missing_token = False

    for acc in accounts_raw:
        if not isinstance(acc, dict):
            continue
        sn = str(acc.get("student_num") or "")
        if not sn:
            continue
        enabled = bool(acc.get("enabled", True))
        enabled_count += 1 if enabled else 0
        auth = acc.get("auth") if isinstance(acc.get("auth"), dict) else {}
        token = str(auth.get("token") or "")
        if not token:
            any_missing_token = True
        issued_raw = str(auth.get("issued_at") or "")
        issued_dt = _parse_time(issued_raw)

        st_full, state_path = _read_state(sn)
        try:
            mtimes.append(os.path.getmtime(state_path))
        except Exception:
            pass
        # 非当日 state 不视作今日状态（与 scheduler_loop 的 date 重置语义一致）；
        # st_full 仍用于「上次结果」与凭证线索。
        st = st_full if str(st_full.get("date") or "") == today else {}
        done = bool(st.get("done"))
        today_done += 1 if done else 0
        failed_count = _as_int(st.get("failed_count"))
        last_exit_today = _as_exit(st.get("last_exit"))
        last_exit_last = _as_exit(st_full.get("last_exit"))
        last_run = str(st_full.get("last_run") or "")
        dead_notified = bool(st_full.get("dead_notified"))

        tt, tv = _term_stats(st_full)
        term_total, term_valid = term_total + tt, term_valid + tv

        # 目标时刻沿用调度器真实算法（noon 窗口，日期种子 + 学号，全天恒定）
        tgt = daily_target("noon", 12, 0, 14, 0, sn)
        target_time = f"{tgt // 60:02d}:{tgt % 60:02d}"

        dead_from_logs = any(
            e["student_num"] == sn and "TOKEN_DEAD" in e["message"]
            and (issued_dt is None or (_parse_time(e["time"]) or issued_dt) >= issued_dt)
            for e in events
        )
        credential = _credential(auth, st_full, last_exit_last, dead_from_logs)
        any_dead = any_dead or credential == "dead"
        status = _today_status(enabled, done, failed_count, last_exit_today,
                               credential, tgt, now_min)
        summary_text = (EXIT_MESSAGES.get(last_exit_last,
                        f"执行失败（exit={last_exit_last}）：请查看日志或重试")
                        if last_exit_last is not None else "")

        account_views.append({
            "student_num": sn,
            "name": str(acc.get("name") or acc.get("alias") or ""),
            "enabled": enabled,
            "token_prefix": token[:8],
            "token_issued": issued_raw,
            "credential": credential,
            "today": {"done": done, "target_time": target_time,
                      "status": status, "status_label": STATUS_LABELS.get(status, ""),
                      "last_exit": last_exit_today, "failed_count": failed_count,
                      "dead_notified": dead_notified},
            "last_result": {"exit_code": last_exit_last, "summary": summary_text},
        })

        ev_time = last_run or _fmt(now)
        if not token:
            add_alert("warn", "未配置凭证，账号无法参与调度", sn, ev_time)
        elif credential == "unknown":
            add_alert("warn", "凭证状态未知（缺失签发时间或签发过久），建议抓号校验", sn, ev_time)
        elif credential == "dead":
            add_alert("error", EXIT_MESSAGES[2], sn, ev_time)
        if last_exit_today == 3:
            add_alert("warn", EXIT_MESSAGES[3], sn, ev_time)
        if last_exit_today == 4:
            add_alert("error", EXIT_MESSAGES[4], sn, ev_time)
        if failed_count > 0 and last_exit_today not in (2, 4):
            add_alert("warn", f"今日累计失败 {failed_count} 次，请查看运行日志", sn, ev_time)

    data_time = (_fmt(datetime.datetime.fromtimestamp(max(mtimes), SHANGHAI))
                 if mtimes else _fmt(now))

    if not accounts_ok:
        health = "error"
        add_alert("error", "accounts.json 读取失败，看板数据不可用", "", _fmt(now))
    elif any_dead or any_missing_token or any(a["level"] == "error" for a in alerts):
        health = "degraded"
    else:
        health = "ok"

    return {
        "server_time": _fmt(now),
        "data_time": data_time,
        "env": ENV,
        "health": health,
        "summary": {
            "accounts_total": len(account_views),
            "enabled": enabled_count,
            "today_done": today_done,
            "today_total": enabled_count,
            "alerts_count": len(alerts),
            "term_total": term_total,
            "term_valid": term_valid,
            "term_target": TERM_TARGET,
            "stat_caliber": ("达标口径：仅 record_status=1 计入 term_valid；"
                             "当前 state/ 无学期记录明细（getTermRunRecord 为网络接口，看板不调用），"
                             "故 term_total/term_valid 回退为 0，不编造数值"),
        },
        "windows": _windows_payload(),
        "accounts": account_views,
        "alerts": alerts,
        "recent_events": list(reversed(events))[:30],
        "logs_tail": [_redact(ln, secrets) for ln in lines[-50:]],
        "public_base_url": PUBLIC_BASE_URL,
        **_readonly_fields(),
    }


@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """只读看板聚合：前端 JS 每 30s 局部刷新调用，不整页刷新。"""
    try:
        payload = build_dashboard()
    except Exception as e:
        try:
            log(f"dashboard aggregation error: {e}")
        except Exception:
            pass
        payload = _dashboard_error_payload(e)
    return jsonify(payload)


@app.after_request
def _security_headers(resp):
    if "X-Content-Type-Options" not in resp.headers:
        resp.headers["X-Content-Type-Options"] = "nosniff"
    if "Referrer-Policy" not in resp.headers:
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if request.path.startswith("/static/"):
        # Flask 默认给 send_static_file 追加 Cache-Control: no-cache，会干扰
        # Nginx 对静态资源的长期缓存；此处移除，缓存策略完全交给 Nginx。
        if "Cache-Control" in resp.headers:
            del resp.headers["Cache-Control"]
    elif request.path.startswith("/api/"):
        # 仅 API 禁缓存，保证 30s 局部刷新读到实时状态；静态资源不受影响。
        resp.headers["Cache-Control"] = "no-store"
    return resp


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
