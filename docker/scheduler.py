# -*- coding: utf-8 -*-
"""乐跑 Docker 容器调度器：每天在随机窗口时刻自动提交一次。

运行模式：长期驻留进程（Docker CMD），无需宿主机 cron。
流程：
  1. 计算今天的目标时刻（日期种子随机，窗口内）
  2. sleep 到目标时刻
  3. 执行 driver.py --submit（含 token 预检 / 额度检查 / OSS / stopRun / 陀螺仪）
  4. 写状态文件 + 日志
  5. 休眠到明天重复

环境变量（均可选）：
  LEPAO_WINDOWS   覆盖默认窗口，格式 "HH:MM-HH:MM,HH:MM-HH:MM"（默认中午+晚间）
  LEPAO_MAX_PER_DAY  每日最大提交次数（默认 1）
"""
import datetime
import hashlib
import json
import os
import random
import subprocess
import sys
import time

# ---------- 路径 ----------
APP_DIR = "/app"
DATA_DIR = os.path.join(APP_DIR, "data")
DRIVER = os.path.join(APP_DIR, "lepaorun", "driver.py")
PYTHON = sys.executable
STATE_FILE = os.path.join(DATA_DIR, "state", "auto_state.json")
LOG_DIR = os.path.join(DATA_DIR, "logs")

# ---------- 窗口配置 ----------
DEFAULT_WINDOWS = [
    ("noon",    12, 0, 14, 0),     # 中午 12:00-14:00
    ("evening", 18, 0, 22, 30),    # 晚间 18:00-22:30
]

# 可通过环境变量覆盖窗口
_env_win = os.environ.get("LEPAO_WINDOWS", "")
if _env_win:
    try:
        parts = []
        for seg in _env_win.split(","):
            h0, m0, h1, m1 = seg.strip().split("-")
            parts.append((h0, m0, h1, m1))
        DEFAULT_WINDOWS = parts
    except Exception:
        pass

MAX_PER_DAY = int(os.environ.get("LEPAO_MAX_PER_DAY", "1"))


def log(msg):
    """写日志到文件 + stdout（Docker logs 可见）"""
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"scheduler_{datetime.date.today():%Y%m%d}.log"),
              "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_state():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    today = str(datetime.date.today())
    if st.get("date") != today:
        st = {"date": today, "done": False, "dead_notified": False,
              "failed_count": 0}
    return st


def save_state(st):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def target_time_today(win_name, h0, m0, h1, m1):
    """日期种子 → 当天该窗口的随机目标分钟数（稳定全天不变）"""
    seed_str = f"{datetime.date.today()}{win_name}lepao"
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:12], 16)
    lo = h0 * 60 + m0
    hi = h1 * 60 + m1
    # 留 5 分钟余量（最后一次检查在窗口结束前）
    return random.Random(seed).randint(lo, max(lo, hi - 5))


def windows_today():
    """今天所有窗口的 (name, target_minutes, h0, m0, h1, m1) 列表"""
    today = str(datetime.date.today())
    result = []
    for name, h0, m0, h1, m1 in DEFAULT_WINDOWS:
        tgt = target_time_today(name, h0, m0, h1, m1)
        result.append((name, tgt, h0, m0, h1, m1))
    return result


def run_submit():
    """执行 driver.py --submit，返回 (exit_code, stdout)"""
    try:
        p = subprocess.run(
            [PYTHON, DRIVER, "--submit"],
            capture_output=True, text=True, timeout=300,
            cwd=os.path.dirname(DRIVER),
        )
        return p.returncode, p.stdout + p.stderr[-500:]
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT (300s)"
    except Exception as e:
        return -1, str(e)


def main():
    log(f"=== 乐跑 Docker 调度器启动 ===")
    log(f"窗口配置: {[(n, f'{h0:02d}:{m0:02d}-{h1:02d}:{m1:02d}') for n, h0, m0, h1, m1 in DEFAULT_WINDOWS]}")
    log(f"每日上限: {MAX_PER_DAY} 次")

    while True:
        st = load_state()

        if st.get("done"):
            # 今天已完成 → 睡到明天 00:05
            log("今天已完成，休眠至明天")
            tomorrow = datetime.datetime.combine(
                datetime.date.today() + datetime.timedelta(days=1),
                datetime.time(0, 5))
            wait = max(60, (tomorrow - datetime.datetime.now()).total_seconds())
            time.sleep(wait)
            continue

        # 计算下一个待触发的窗口
        now = datetime.datetime.now()
        now_min = now.hour * 60 + now.minute
        wins = windows_today()

        # 找下一个还没过的窗口（目标时刻 > 当前，或已过但今天还没成功）
        next_win = None
        for name, tgt, h0, m0, h1, m1 in wins:
            if now_min < tgt:
                next_win = (name, tgt)
                break
        if next_win is None:
            # 所有窗口都过了 → 睡到明天
            log("今天窗口全部已过，休眠至明天")
            tomorrow = datetime.datetime.combine(
                datetime.date.today() + datetime.timedelta(days=1),
                datetime.time(0, 5))
            wait = max(60, (tomorrow - datetime.datetime.now()).total_seconds())
            time.sleep(wait)
            continue

        name, tgt = next_win
        tgt_h, tgt_m = divmod(tgt, 60)
        target_dt = datetime.datetime.combine(
            datetime.date.today(), datetime.time(tgt_h, tgt_m))
        wait = max(5, (target_dt - datetime.datetime.now()).total_seconds())

        log(f"等待 {name} 窗口随机时刻 {tgt_h:02d}:{tgt_m:02d}（{wait:.0f}s 后）")
        time.sleep(wait)

        # 到点 → 检查状态后提交
        st = load_state()
        if st.get("done"):
            log("到点后发现已完成（其他方式），跳过")
            continue

        log("开始提交...")
        code, output = run_submit()
        st = load_state()
        st["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        st["last_exit"] = code

        # 解析结果
        summary = {}
        try:
            js_start = output.index("{\n")
            js_end = output.rindex("}") + 1
            summary = json.loads(output[js_start:js_end])
        except (ValueError, json.JSONDecodeError):
            pass

        if code == 0 and summary.get("record_id"):
            st["done"] = True
            save_state(st)
            log(f"✅ 提交成功! record_id={summary.get('record_id')} "
                f"distance={summary.get('distance')}km "
                f"status={summary.get('record_status')} "
                f"reason={summary.get('record_failed_reason', '')}")
        elif code == 2 or "TOKEN_DEAD" in output:
            st["dead_notified"] = True
            save_state(st)
            log("❌ TOKEN_DEAD — 需要更新 config.json 中的 token 后重启容器")
            log("   提示: docker restart ctbu-lepao")
            # token 死了不重试，等 config 更新
            time.sleep(3600)  # 1 小时后再检查（可能用户已更新）
        elif code == 3:
            st["done"] = True
            save_state(st)
            log("今日额度已满，标记完成")
        else:
            st["failed_count"] = st.get("failed_count", 0) + 1
            save_state(st)
            log(f"❌ 提交失败 (exit={code}), 失败次数 {st['failed_count']}")
            log(f"   输出: {output[-300:]}")
            # 失败后等 30 分钟再重试下一个窗口
            time.sleep(1800)


if __name__ == "__main__":
    main()
