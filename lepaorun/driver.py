# -*- coding: utf-8 -*-
# 乐跑驱动引擎（多账号版）：基于 lepao3 (LRL-1.0) + CTBU v3 轨迹生成器。
# 用法:
#   CLI:  python driver.py --submit [--canary] [--vena4]
#   API:  from driver import submit_account; submit_account(account_dict)
import sys, os, json, time, random, datetime
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from lepao.v3api import V3Client, V3Error
from lepao import record as REC
from lepao import ctbu_track as CT
from lepao.crypto import encrypt_data

# ---------- 可调参数（默认值，可被账号级覆盖） ----------
DEFAULTS = {
    "dist_lo": 2.60, "dist_hi": 4.90,
    "pace_lo": 200, "pace_hi": 500,
    "mobile_model": "iPhone14,3",
    "vena4": False,
}

CFG_PATH = os.path.join(_HERE, "config.json")


def load_config():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def log(msg):
    print(msg, flush=True)


# ---------- 账号上下文 ----------
class Account:
    """封装单个账号的全部信息，供提交流程使用"""
    def __init__(self, acc_dict: dict, cfg: dict):
        self.raw = acc_dict
        self.alias = acc_dict.get("alias", acc_dict.get("name", acc_dict["student_num"]))
        self.uid = acc_dict["uid"]
        self.school_id = acc_dict["school_id"]
        self.student_num = acc_dict["student_num"]
        self.card_id = acc_dict.get("card_id", acc_dict["student_num"])
        self.token = acc_dict.get("auth", {}).get("token", "")
        self.api_host = cfg.get("environment", {}).get("api_host", "lepao.ctbu.edu.cn")
        # 策略参数（账号级覆盖 > 全局默认）
        pol = {**DEFAULTS}
        pol.update(cfg.get("policy", {}))
        pol.update(acc_dict.get("policy", {}))
        self.dist_lo = pol["dist_lo"]
        self.dist_hi = pol["dist_hi"]
        self.pace_lo = pol["pace_lo"]
        self.pace_hi = pol["pace_hi"]
        self.vena4 = pol.get("vena4", False)
        self.mobile_model = pol.get("mobile_model", "iPhone14,3")

    def make_client(self, pace=0.0):
        return V3Client(uid=self.uid, token=self.token, school_id=self.school_id,
                        student_num=self.student_num, card_id=self.card_id,
                        host=self.api_host, pace=pace)

    def __repr__(self):
        return f"Account({self.alias}#{self.uid})"


# ---------- 轨迹生成 ----------
def gen_with_retry(cps, rnd, acc: Account, attempts=8, canary=False):
    d_lo = 2.60 if canary else acc.dist_lo
    d_hi = 2.85 if canary else acc.dist_hi
    p_lo = 300 if canary else acc.pace_lo
    p_hi = 460 if canary else acc.pace_hi
    for k in range(attempts):
        target = rnd.uniform(d_lo + 0.15, d_hi - 0.20) * 1000
        res = CT.generate_run(cps, target, rnd, vena4=acc.vena4)
        res["_target"] = target
        dist = res["distance_km"]
        used = res["used_time"]
        pace = used / dist if dist else 9e9
        ok_dist = d_lo <= dist <= d_hi
        ok_pace = p_lo <= pace <= p_hi
        ok_time = used <= 3300 if dist <= 3.0 else used <= 6900
        checks = CT.self_check(res)
        if acc.vena4:
            checks.pop("checkpoints(>=4)", None)
        failed = {k2: v for k2, v in checks.items() if not v[0]}
        want_cp = 4 if acc.vena4 else 12
        cp_n = len(res["crossings"])
        if acc.vena4 and cp_n < want_cp:
            failed["checkpoints"] = (f"{cp_n}<{want_cp}",)
        elif not acc.vena4 and cp_n != want_cp:
            failed["checkpoints"] = (f"{cp_n}/{want_cp}",)
        log(f"  [{acc.alias} gen.{k}] target={target:.0f}m {dist}km {used}s pace={int(pace)}s/km "
            f"pts={res['path_pts']} steps={res['step_num']} | "
            f"{'PASS' if not failed else 'FAIL ' + json.dumps({k2: v[1] for k2, v in failed.items()}, ensure_ascii=False)}")
        if ok_dist and ok_pace and ok_time and not failed:
            return res
    raise RuntimeError(f"[{acc.alias}] 轨迹生成重试耗尽")


# ---------- 提交全流程 ----------
def run_for_account(acc: Account, submit=False, canary=False):
    """对单个账号执行完整的乐跑提交流程"""
    rnd = random.Random()
    client = acc.make_client(pace=0.8)

    # [0] 真门
    r = client.post("/v3/api.php/WpRun/getTermList", {})
    if isinstance(r, dict) and r.get("status") == 101:
        log(f"[{acc.alias}] TOKEN_DEAD")
        return None, 2
    s = client.get_school_info()
    log(f"[{acc.alias} 0] token 有效 | {s.get('school_name')}")

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
    log(f"[{acc.alias} 1] 跑区={zone}({cfg.get('run_zone_name')}) 打卡点x{len(cps)} 学期={term_id}")
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
    log(f"[{acc.alias} 2] 今日已完成 {done} 次 (上限 1)")
    if done >= 1 and submit:
        log(f"[{acc.alias}] 当日额度已满, 不提交")
        return None, 3

    # [3] 服务器时间
    srv_now = client.get_timestamp()
    log(f"[{acc.alias} 3] 服务器时间={srv_now}")

    # [4] 生成
    log(f"[{acc.alias} 4] 生成轨迹:")
    res = gen_with_retry(cps, rnd, acc, canary=canary)
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
        "mobileModel": acc.mobile_model,
        "mobileOsVersion": 1,
        "step_info": step_info,
        "step_num": int(res["step_num"]),
        "used_time": int(res["used_time"]),
        "record_file": record_file,
    }
    win = datetime.datetime.fromtimestamp(start_time).strftime("%H:%M") + \
        "~" + datetime.datetime.fromtimestamp(end_time).strftime("%H:%M")
    log(f"[{acc.alias} 5] record_file={record_file} 时间窗={win} 距离={g['distance']}km")

    if not submit:
        out = {"stopRun_biz": g, "crossings": res["crossings"],
               "track_pts": res["path_pts"], "gyr_samples": len(res["gyr"]),
               "self_check": CT.self_check(res)}
        return out, 0

    # [6] OSS 轨迹上传
    time.sleep(rnd.uniform(0.5, 1.3))
    sts = client.get_oss_sts()
    if not isinstance(sts, dict) or "AccessKeySecret" not in sts:
        log(f"[{acc.alias}] getOssSts 异常: {str(sts)[:200]}")
        return None, 4
    status, rbody = REC.upload_to_oss(encrypt_data(tr_json), sts, now_ms, record_file, rnd)
    log(f"[{acc.alias} 6] OSS 轨迹上传 status={status}")
    if status not in (200, 203, 204):
        log(f"[{acc.alias}] OSS 上传失败: {rbody[:400]}")
        return None, 4

    # [7] stopRun
    time.sleep(rnd.uniform(0.8, 2.0))
    log(f"[{acc.alias} 7] stopRun: {g['distance']}km {g['used_time']}s steps={g['step_num']}")
    resp = client.stop_run(g)
    log(f"[{acc.alias} 7] 响应: " + json.dumps(resp, ensure_ascii=False)[:300])
    if isinstance(resp, str) and "has" in resp.lower():
        log(f"[{acc.alias} 7] 服务器返回已存在(has)——当日已有记录")
        return {"record_id": None, "distance": g["distance"], "already": True}, 0
    if not isinstance(resp, dict) or not resp.get("record_id"):
        return resp, 4
    record_id = resp["record_id"]

    # [8] 陀螺仪补传
    try:
        time.sleep(rnd.uniform(1.0, 2.5))
        sts2 = client.get_oss_sts()
        gkey = REC.gyroscope_key(rnd)
        st2, rb2 = REC.upload_oss_content(sts2, gkey,
                                          json.dumps(res["gyr"], separators=(",", ":")), rnd)
        log(f"[{acc.alias} 8] OSS 陀螺仪上传 status={st2} samples={len(res['gyr'])}")
        if st2 in (200, 203, 204):
            gy = client.post("/v3/api.php/Run2/gyroscope",
                             {"record_id": record_id, "gyroscope_file": gkey})
            log(f"[{acc.alias} 8] Run2/gyroscope → {json.dumps(gy, ensure_ascii=False)[:200]}")
        else:
            log(f"[{acc.alias} 8] 陀螺仪上传失败(不影响记录): {rb2[:200]}")
    except Exception as e:
        log(f"[{acc.alias} 8] 陀螺仪补传异常(不影响记录): {e}")

    # [9] 复核
    time.sleep(rnd.uniform(2.5, 5.0))
    detail = client.post("/v3/api.php/Run/recordDetailV270",
                         {"record_id": str(record_id), "year_num": str(datetime.date.today().year)})
    log(f"[{acc.alias} 9] recordDetail: " + json.dumps(detail, ensure_ascii=False)[:500])
    summary = {
        "record_id": record_id,
        "distance": g["distance"],
        "used_time": g["used_time"],
        "record_status": detail.get("record_status") if isinstance(detail, dict) else None,
        "record_failed_reason": detail.get("record_failed_reason") if isinstance(detail, dict) else None,
        "start_time": g["start_time"], "end_time": g["end_time"],
    }
    return summary, 0


# ---------- 公共 API ----------
def submit_account(account_dict: dict, cfg: dict = None, canary=False):
    """对外接口：提交单个账号的乐跑
    
    account_dict: 完整账号配置（含 auth.token, uid, school_id 等）
    cfg: 全局配置（不传则从 config.json 加载）
    返回: (summary_dict|None, exit_code)
    """
    if cfg is None:
        cfg = load_config()
    acc = Account(account_dict, cfg)
    return run_for_account(acc, submit=True, canary=canary)


def dryrun_account(account_dict: dict, cfg: dict = None):
    """对外接口：dry-run 单个账号（零写入）"""
    if cfg is None:
        cfg = load_config()
    acc = Account(account_dict, cfg)
    return run_for_account(acc, submit=False)


# ---------- CLI 兼容入口 ----------
if __name__ == "__main__":
    cfg = load_config()
    AK = list(cfg["accounts"].keys())[0]  # 默认第一个账号
    acc_dict = cfg["accounts"][AK]
    acc = Account(acc_dict, cfg)
    submit = "--submit" in sys.argv
    canary = "--canary" in sys.argv
    if "--vena4" in sys.argv:
        acc.vena4 = True
    summary, code = run_for_account(acc, submit=submit, canary=canary)
    print("EXIT", code, flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=1) if summary else "no summary")
