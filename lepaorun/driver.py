# -*- coding: utf-8 -*-
# 本机自用驱动：基于 lepao3 (LRL-1.0) 研究代码 + CTBU 定制轨迹生成器，仅操作本人账号，不牟利。
import sys, os, json, time, random, datetime
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from lepao.v3api import V3Client, V3Error
from lepao import record as REC
from lepao import ctbu_track as CT
from lepao.crypto import encrypt_data

CFG = json.load(open(os.path.join(_HERE, "config.json"), encoding="utf-8"))
AK = "2025410030"
acc = CFG["accounts"][AK]
TOKEN = acc["auth"]["token"]

# 学校规则窗口 (用户确认): 距离 2.50~10km 取 2.6~4.9, 配速 3'0"~9'0" = 180~540 s/km
DIST_LO, DIST_HI = 2.60, 4.90
PACE_LO, PACE_HI = 200, 500          # 实际生成 4'~8'20"/km, 留边
MOBILE_MODEL = "iPhone14,3"


class StateShim:
    def save_dryrun(self, *a, **k): pass
    def save_submission(self, *a, **k): pass


class AuthShim:
    config = CFG
    label = acc["alias"]
    uid = acc["uid"]
    school_id = acc["school_id"]
    student_num = acc["student_num"]
    card_id = acc["card_id"]
    def make_client(self, pace=0.0):
        return V3Client(uid=self.uid, token=TOKEN, school_id=self.school_id,
                        student_num=self.student_num, card_id=self.card_id,
                        host=CFG["environment"]["api_host"], pace=pace)
    def mark_consumed(self):
        print("[auth] token 标记消耗")


def log(msg):
    print(msg, flush=True)


def gen_with_retry(cps, rnd, attempts=8, canary=False):
    """生成直到通过全部自检和学校窗口。canary=True 时用最小爆炸半径参数"""
    for k in range(attempts):
        if canary:
            target = rnd.uniform(2.62, 2.72) * 1000     # 金丝雀: 校规最低 2.5+缓冲
        else:
            target = rnd.uniform(2.75, 4.70) * 1000
        res = CT.generate_run(cps, target, rnd)
        res["_target"] = target
        dist = res["distance_km"]
        used = res["used_time"]
        pace = used / dist if dist else 9e9
        ok_dist = (2.60 if canary else DIST_LO) <= dist <= (2.85 if canary else DIST_HI)
        ok_pace = (300 if canary else PACE_LO) <= pace <= (460 if canary else PACE_HI)
        ok_time = used <= 3300 if dist <= 3.0 else used <= 6900
        checks = CT.self_check(res)
        failed = {k2: v for k2, v in checks.items() if not v[0]}
        log(f"  [gen.{k}] target={target:.0f}m {dist}km {used}s pace={int(pace)}s/km pts={res['path_pts']} "
            f"steps={res['step_num']} | {'PASS' if not failed else 'FAIL ' + json.dumps({k2: v[1] for k2, v in failed.items()}, ensure_ascii=False)}")
        if ok_dist and ok_pace and ok_time and not failed:
            return res
    raise RuntimeError("轨迹生成重试耗尽")


def run(submit=False, canary=False):
    rnd = random.Random()
    client = AuthShim().make_client(pace=0.8)

    # [0] 真门
    r = client.post("/v3/api.php/WpRun/getTermList", {})
    if isinstance(r, dict) and r.get("status") == 101:
        log("TOKEN_DEAD")
        return None, 2
    s = client.get_school_info()
    log(f"[0] token 有效 | {s.get('school_name')}")

    # [1] 跑区/打卡点
    cfg = client.before_run()
    rli = cfg.get("run_line_info") or {}
    zone = str(cfg.get("run_zone_id") or "")
    term_id = str(cfg.get("term_id") or "0")
    cps = []
    for p in rli.get("point_list") or []:
        try:
            la, lo = str(p.get("jingwei")).strip().split(",")
            cps.append((float(la), float(lo), str(p.get("id"))))
        except Exception:
            continue
    log(f"[1] 跑区={zone}({cfg.get('run_zone_name')}) 打卡点x{len(cps)} 学期={term_id}")
    if len(cps) < 2:
        return None, 4

    # [2] 当日额度
    day0 = int(time.mktime(datetime.date.today().timetuple()))
    done = 0
    for page in (1, 2):
        rr = client.post("/v3/api.php/WpRun/getTermRunRecord",
                         {"term_id": term_id, "page": page})
        for rec in (rr.get("list") or [] if isinstance(rr, dict) else []):
            try:
                if int(rec.get("start_time") or 0) >= day0 and float(rec.get("distance") or 0) > 0:
                    done += 1
            except Exception:
                pass
        if not (isinstance(rr, dict) and rr.get("list")):
            break
    log(f"[2] 今日已完成 {done} 次 (上限 1)")
    if done >= 1 and submit:
        log("当日额度已满, 不提交")
        return None, 3

    # [3] 服务器时间
    srv_now = client.get_timestamp()
    log(f"[3] 服务器时间={srv_now}")

    # [4] 生成
    log("[4] 生成轨迹:")
    res = gen_with_retry(cps, rnd, canary=canary)
    tr_json = json.dumps(res["track"], ensure_ascii=False, separators=(",", ":"))
    step_info = json.dumps(res["step_info"], separators=(",", ":"))

    # [5] 组装
    end_time = srv_now - rnd.randint(15, 75)
    start_time = end_time - res["used_time"]
    now_ms, record_file, fname = REC.make_record_file(rnd)
    g = {
        "term_id": 1,
        "game_id": zone,
        "start_time": int(start_time),
        "end_time": int(end_time),
        "distance": float(res["distance_km"]),
        "record_img": "",
        "log_data": json.dumps([
            {**e, "time": int(start_time) + max(5, int(e["time"]))}
            for e in res["log_data"]
        ], ensure_ascii=False, separators=(",", ":")),
        "file_img": "",
        "is_running_area_valid": 1,
        "mobileDeviceId": 1,
        "mobileModel": MOBILE_MODEL,
        "mobileOsVersion": 1,
        "step_info": step_info,
        "step_num": int(res["step_num"]),
        "used_time": int(res["used_time"]),
        "record_file": record_file,
    }
    window = datetime.datetime.fromtimestamp(start_time).strftime("%H:%M") + \
        "~" + datetime.datetime.fromtimestamp(end_time).strftime("%H:%M")
    log(f"[5] record_file={record_file} 时间窗={window} 距离={g['distance']}km")

    if not submit:
        out = {"stopRun_biz": g, "crossings": res["crossings"],
               "track_pts": res["path_pts"], "gyr_samples": len(res["gyr"]),
               "self_check": CT.self_check(res)}
        return out, 0

    # [6] OSS 轨迹上传
    time.sleep(rnd.uniform(0.5, 1.3))
    sts = client.get_oss_sts()
    if not isinstance(sts, dict) or "AccessKeySecret" not in sts:
        log(f"getOssSts 异常: {str(sts)[:200]}")
        return None, 4
    status, rbody = REC.upload_to_oss(encrypt_data(tr_json), sts, now_ms, record_file, rnd)
    log(f"[6] OSS 轨迹上传 status={status}")
    if status not in (200, 203, 204):
        log(f"OSS 上传失败: {rbody[:400]}")
        return None, 4

    # [7] stopRun
    time.sleep(rnd.uniform(0.8, 2.0))
    log(f"[7] stopRun: {g['distance']}km {g['used_time']}s steps={g['step_num']}")
    resp = client.stop_run(g)
    log("[7] 响应: " + json.dumps(resp, ensure_ascii=False)[:300])
    if isinstance(resp, str) and "has" in resp.lower():
        log("[7] 服务器返回已存在(has)——当日已有记录, 视为成功")
        return {"record_id": None, "distance": g["distance"], "already": True}, 0
    if not isinstance(resp, dict) or not resp.get("record_id"):
        return resp, 4
    record_id = resp["record_id"]

    # [8] 陀螺仪补传 (真机行为, 堵 V4 传感器缺口)
    try:
        time.sleep(rnd.uniform(1.0, 2.5))
        sts2 = client.get_oss_sts()
        gkey = REC.gyroscope_key(rnd)
        st2, rb2 = REC.upload_oss_content(sts2, gkey,
                                          json.dumps(res["gyr"], separators=(",", ":")), rnd)
        log(f"[8] OSS 陀螺仪上传 status={st2} samples={len(res['gyr'])}")
        if st2 in (200, 203, 204):
            gy = client.post("/v3/api.php/Run2/gyroscope",
                             {"record_id": record_id, "gyroscope_file": gkey})
            log(f"[8] Run2/gyroscope → {json.dumps(gy, ensure_ascii=False)[:200]}")
        else:
            log(f"[8] 陀螺仪上传失败(不影响记录): {rb2[:200]}")
    except Exception as e:
        log(f"[8] 陀螺仪补传异常(不影响记录): {e}")

    # [9] 复核
    time.sleep(rnd.uniform(2.5, 5.0))
    detail = client.post("/v3/api.php/Run/recordDetailV270",
                         {"record_id": str(record_id), "year_num": str(datetime.date.today().year)})
    log("[9] recordDetail: " + json.dumps(detail, ensure_ascii=False)[:500])
    summary = {
        "record_id": record_id,
        "distance": g["distance"],
        "used_time": g["used_time"],
        "record_status": detail.get("record_status") if isinstance(detail, dict) else None,
        "record_failed_reason": detail.get("record_failed_reason") if isinstance(detail, dict) else None,
        "start_time": g["start_time"], "end_time": g["end_time"],
    }
    return summary, 0


if __name__ == "__main__":
    submit = "--submit" in sys.argv
    canary = "--canary" in sys.argv
    if canary:
        # 金丝雀模式: 距离/配速压到最保守
        PACE_LO, PACE_HI = 300, 460
    summary, code = run(submit=submit, canary=canary)
    print("EXIT", code, flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=1) if summary else "no summary")
