# -*- coding: utf-8 -*-
"""乐跑自动打卡 tick：被 cron 每 5 分钟叫一次。

规则：
  · 中午窗口 12:00–14:00、晚间窗口 18:00–22:30；
  · 每个窗口当天随机定一个分钟点（由日期+窗口名做种子，当天恒定）；
  · 到点且今天还没成功提交过 → 跑 driver.py --submit；
  · 已成功 / 还没到点 → 什么也不输出（no_agent cron 空输出=不推送）；
  · token 失效 → 当天只提醒一次。
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import time
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))      # ~/Documents/校园乐跑
HOME = HERE
PY = os.path.join(HOME, "venv", "bin", "python")
DRIVER = os.path.join(HOME, "lepaorun", "driver.py")
STATE = os.path.join(HOME, "state", "auto_state.json")
LOGDIR = os.path.join(HOME, "logs")

WINDOWS = {  # name: (start_h, start_m, end_h, end_m)
    "noon": (12, 0, 14, 0),
    "evening": (18, 0, 22, 30),
}


def log(msg):
    os.makedirs(LOGDIR, exist_ok=True)
    with open(os.path.join(LOGDIR, f"tick_{datetime.date.today():%Y%m%d}.log"), "a",
              encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now():%H:%M:%S} {msg}\n")


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if st.get("date") != str(datetime.date.today()):
        st = {"date": str(datetime.date.today()), "done": False, "dead_notified": False}
    return st


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def target_minutes(win, date_str):
    """当天该窗口的随机目标时刻（分钟数，自 00:00 起），种子固定 → 全天不变。"""
    h0, m0, h1, m1 = WINDOWS[win]
    lo, hi = h0 * 60 + m0, h1 * 60 + m1
    seed = int(hashlib.sha256(f"{date_str}|{win}|lepao".encode()).hexdigest()[:12], 16)
    # 最后一次 tick 在窗口结束前 5 分钟，随机点留出余量，避免落到无 tick 的分钟
    return random.Random(seed).randint(lo, max(lo, hi - 5))


def current_window(now_min):
    for name, (h0, m0, h1, m1) in WINDOWS.items():
        if h0 * 60 + m0 <= now_min <= h1 * 60 + m1:
            return name
    return None


def main():
    now = datetime.datetime.now()
    now_min = now.hour * 60 + now.minute
    win = current_window(now_min)
    if win is None:
        return
    st = load_state()
    if st.get("done"):
        log(f"{win} tick: 今天已成功，跳过")
        return
    if st.get("dead_notified"):
        log(f"{win} tick: 今天已报过 token 失效，跳过")
        return

    tgt = target_minutes(win, st["date"])
    st.setdefault("targets", {})[win] = f"{tgt // 60:02d}:{tgt % 60:02d}"
    save_state(st)
    force = os.environ.get("LEPAO_FORCE") == "1"
    if now_min < tgt and not force:
        log(f"{win} tick: 未到随机时刻 {st['targets'][win]}，等待")
        return

    log(f"{win} tick: 到点 {st['targets'][win]}，开始提交")
    try:
        p = subprocess.run([PY, DRIVER, "--submit"], cwd=os.path.dirname(DRIVER),
                           capture_output=True, text=True, timeout=420)
        out = p.stdout
        code = p.returncode
        for line in out.splitlines():
            if line.startswith("EXIT "):
                try:
                    code = int(line.split()[1])
                except Exception:
                    pass
    except subprocess.TimeoutExpired:
        log(f"{win}: driver 超时")
        print(f"🏃 乐跑自动打卡：driver 执行超过 7 分钟被中断，今天可能没打上，稍后我会再试。")
        return
    log(f"{win}: exit={code} out_tail={out[-300:]}")

    st["last_run"] = now.strftime("%Y-%m-%d %H:%M")
    st["last_exit"] = code

    # token 失效（driver 里的真门返回 101 → EXIT 2）
    if code == 2 or "TOKEN_DEAD" in out:
        st["dead_notified"] = True
        save_state(st)
        print("⚠️ 乐跑自动打卡：token 已失效，今天没有提交。需要在小程序里重新登录一次（我这边的抓号命令已备好）。")
        return

    # 当日额度已满（EXIT 3）
    if code == 3:
        st["done"] = True
        save_state(st)
        log(f"{win}: 当日额度已满，视为已完成")
        return

    detail = {}
    try:
        js = out[out.index("{\n"):]
        detail = json.loads(js[:js.rindex("}") + 1])
    except Exception:
        detail = {}

    if code == 0 and (detail.get("record_id") or detail.get("already")):
        st["done"] = True
        save_state(st)
        dist = float(detail.get("distance") or 0)
        used = detail.get("used_time")
        rid = detail.get("record_id")
        status = detail.get("record_status")
        reason = detail.get("record_failed_reason")
        stt, ent = detail.get("start_time"), detail.get("end_time")
        lines = [f"🏃 乐跑自动打卡成功  {now:%m-%d %H:%M}"]
        lines.append(f"距离 {dist:.2f} km")
        if used and dist:
            pace = used / dist
            lines.append(f"用时 {used // 60}分{used % 60}秒 · 配速 {int(pace) // 60}'{int(pace) % 60:02d}\"/km")
        if stt and ent:
            lines.append("时间窗 " + datetime.datetime.fromtimestamp(int(stt)).strftime("%H:%M")
                         + "–" + datetime.datetime.fromtimestamp(int(ent)).strftime("%H:%M"))
        if rid:
            lines.append(f"记录号 {rid}")
        if status is not None:
            status_txt = {1: "1 自动确认有效", 0: "0 待审", 2: "2 无效"}.get(status, str(status))
            lines.append(f"审查 {status_txt}" + (f"（{reason}）" if reason else ""))
        if detail.get("already"):
            lines.append("服务器显示今日已有记录")
        try:
            import stats
            s2 = stats.term_stats()
            if s2.get("term_count") is not None:
                lines.append(f"本学期 {s2['term_count']} 次 / {s2['term_distance']} km"
                             f"（达标 {s2['term_ok_count']} 次 / {s2['term_ok_distance']} km）")
        except Exception:
            pass
        print("\n".join(lines))
    else:
        st["failed_notified"] = st.get("failed_notified", 0) + 1
        save_state(st)
        if st["failed_notified"] <= 2:
            tail = "; ".join(l for l in out.splitlines()[-4:] if l.strip())[:300]
            print(f"❌ 乐跑自动打卡失败（{now:%H:%M}）：{tail or '无输出'}")


if __name__ == "__main__":
    main()
