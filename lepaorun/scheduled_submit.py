# -*- coding: utf-8 -*-
"""等到 07:10 执行正式提交；先验 token，死亡则不提交。"""
import datetime, subprocess, json, sys, time

target = datetime.datetime.combine(datetime.date.today(), datetime.time(7, 10, 0))
now = datetime.datetime.now()
wait = (target - now).total_seconds()
print(f"[schedule] now={now:%H:%M:%S} wait={wait:.0f}s -> 07:10:00", flush=True)
if wait > 0:
    time.sleep(wait)

sys.path.insert(0, "/tmp/lepaorun")
import os
os.chdir("/tmp/lepaorun")
import driver
from lepao.v3api import V3Error
try:
    c = driver.AuthShim().make_client()
    r = c.post("/v3/api.php/WpRun/getTermList", {})
    if isinstance(r, dict) and r.get("status") == 101:
        print("TOKEN_DEAD — 不提交，需要重新抓号", flush=True)
        sys.exit(2)
    print("token alive, submitting...", flush=True)
except Exception as e:
    print("PRECHECK FAIL:", e, flush=True)
    sys.exit(4)

out = subprocess.run(["/tmp/cuavenv/bin/python", "/tmp/lepaorun/driver.py", "--submit"],
                     capture_output=True, text=True, timeout=300)
print(out.stdout, flush=True)
print(out.stderr[-2000:], flush=True)
print("SUBMIT_DONE", flush=True)
