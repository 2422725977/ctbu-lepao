# -*- coding: utf-8 -*-
"""ctbu_track —— CTBU 南区定制轨迹生成器 v3

设计依据（2026-10-28 研究综合）:
  1. 真人黄金记录画像 (lepao3 data/真实轨迹_035明文.json, 服务器判有效):
     - s 字段 mean 3.99 / stdev 2.33（走跑结合, 非匀速）
     - b 字段 55% 点位存在, 取值 {1:67%, 2:33%}
     - c 字段 mean 0.067 / stdev 0.027 / 范围 [0, 0.10]
     - 位移 lag-1 自相关 0.803（强相关 GPS 漂移）
     - 点间隔 DT≈2.83s
  2. 防御侧检测规则 (vibe-GIF/budaolepao 红队方案):
     - step_distance: GPS里程/(步数×0.7m) ∈ [0.4, 2.5] → 步数按步幅 1.1~1.4m 生成
     - speed_physiological: max_speed<12, max_acc<10, 速度方差不可过低
     - noise_spectrum: 位移自相关 lag-1 ≥ 0.3
     - trajectory_similarity: Jaccard(100m 网格) < 0.9 → 每次路线形状必须不同
     - zone_enforcement: 出界点比例 < 10%
  3. 真实 App 源码 (Suzuran28 对 app-service.js 逆向):
     - 跑步时每 60ms 采 {t,ax,ay,az,gx,gy,gz}, stopRun 后补传 Run2/gyroscope
     - step_info 每 60s 一桶
"""
import json
import math
import random

DT = 2.833          # 采样步长(s), 与真人记录一致
CHECKIN_RADIUS_M = 40.0

# 学校官方下发的 主校区南跑区 边界多边形 (beforeRunV260.run_zone_latlng, [lng, lat])
ZONE_POLYGON = [
    (106.577693, 29.507149), (106.580866, 29.507050), (106.580650, 29.503342),
    (106.579180, 29.498396), (106.577559, 29.498575),
]
ZONE_MARGIN_M = 20.0  # 边界安全边距(向内), 防止 GPS 抖动压线


def point_in_zone(lat, lng, margin_m=ZONE_MARGIN_M):
    """点是否在跑区多边形内(留 margin)。ray casting + 距边距离。"""
    poly = ZONE_POLYGON
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > lat) != (yj > lat)) and \
           (lng < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    if not inside:
        return False
    # 边距检查: 到任一边的距离 >= margin
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # 点到线段距离(米, 等距投影)
        dx = (x2 - x1) * 111320 * math.cos(math.radians(lat))
        dy = (y2 - y1) * 111320
        px = (lng - x1) * 111320 * math.cos(math.radians(lat))
        py = (lat - y1) * 111320
        L2 = dx * dx + dy * dy
        tt = max(0.0, min(1.0, (px * dx + py * dy) / (L2 + 1e-9)))
        ex, ey = px - tt * dx, py - tt * dy
        if math.hypot(ex, ey) < margin_m:
            return False
    return True


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def quantize_geo(v):
    return int(round(v * 3600000.0)) / 3600000.0


# ---------------------------------------------------------------- 路线规划
def plan_route(cps, rnd):
    """打卡点散布全校, index 顺序即学校规划环线。随机: 方向/起点/中途折返。"""
    order = list(cps)
    if rnd.random() < 0.5:
        order = order[::-1]
    k = rnd.randrange(len(order))
    return order[k:] + order[:k]


def plan_route_vena(cps, rnd, n=4):
    """模拟真实分配机制: 从全部打卡点中均匀随机抽 n 个（C(12,4)=495 组合全覆盖,
    允许相邻点——真实分配就是任意 4 个）。按环线顺序连弦（不自交）。
    相邻弦可能顺路经过其他打卡点 → 那些也自然打卡（真人也会顺路经过，log_data ≥n）。"""
    order = list(cps)
    chosen = rnd.sample(order, min(n, len(order)))
    # 按环线位置排序, 保证连弦不自交
    idx = {c[2]: i for i, c in enumerate(order)}
    chosen.sort(key=lambda c: idx[c[2]])
    return chosen


def build_path(order, rnd, laps, perim):
    """把打卡点环线铺成 laps 圈的密集路径(每圈独立抖动, 防止轨迹重叠)。
    圈数须满足 laps*perim > 目标距离, 否则 route_at 会钳制导致原地踏步。"""
    path = []
    wob = rnd.uniform(0.10, 0.22)
    n = len(order)
    for lap in range(laps):
        ox = rnd.uniform(-7e-5, 7e-5)
        oy = rnd.uniform(-7e-5, 7e-5)
        seq = [(p[0] + ox + rnd.uniform(-3e-5, 3e-5),
                p[1] + oy + rnd.uniform(-3e-5, 3e-5)) for p in order]
        for i in range(n):
            a = seq[i]
            if lap == 0 or i > 0:      # 圈与圈衔接处不重复加点
                path.append(a)
            b = seq[(i + 1) % n]
            m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
            d = haversine_m(a[0], a[1], b[0], b[1])
            # 中间点摆动: 逐级收缩直到落在跑区多边形内(西区打卡点离边界仅 ~21m)
            mid = None
            for shrink in (1.0, 0.6, 0.3, 0.0):
                lat_o = math.copysign(wob * shrink * d / 111000.0 * rnd.uniform(0.4, 1.0), rnd.random() - 0.5)
                lng_o = math.copysign(wob * shrink * d / (111000.0 * math.cos(math.radians(m[0])))
                                      * rnd.uniform(0.4, 1.0), rnd.random() - 0.5)
                cand = (m[0] + lat_o, m[1] + lng_o)
                if point_in_zone(cand[0], cand[1]):
                    mid = cand
                    break
            path.append(mid or m)
    # 偶发大绕行凸起(真实跑者绕开楼栋/人群): 1-2 个段的中间点用大摆动, 不折返
    for _ in range(rnd.randrange(0, 3)):
        i = rnd.randrange(len(path) - 1)
        a, b = path[i], path[i + 1]
        m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        d = haversine_m(a[0], a[1], b[0], b[1])
        amp = rnd.uniform(0.45, 0.75)
        lat_o = math.copysign(amp * d / 111000.0, rnd.random() - 0.5)
        lng_o = math.copysign(amp * d / (111000.0 * math.cos(math.radians(m[0]))),
                              rnd.random() - 0.5)
        cand = (m[0] + lat_o, m[1] + lng_o)
        if point_in_zone(cand[0], cand[1]):
            path[i + 1] = cand
    return path


def route_at(path, cum, s):
    """弧长定位。"""
    s = min(max(s, 0.0), cum[-1] - 0.01)
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        m = (lo + hi) // 2
        if cum[m] < s:
            lo = m + 1
        else:
            hi = m
    i = max(lo - 1, 0)
    seg = cum[i + 1] - cum[i] or 1.0
    t = (s - cum[i]) / seg
    return (path[i][0] + (path[i + 1][0] - path[i][0]) * t,
            path[i][1] + (path[i + 1][1] - path[i][1]) * t)


# ---------------------------------------------------------------- 速度模型
def speed_profile(target_m, rnd):
    """走跑结合速度曲线的生成参数。返回 (base_v, walk_breaks, period, phase)"""
    base_v = rnd.uniform(3.2, 4.1)                       # 3'40"~5'50"/km 基础
    period = rnd.uniform(140, 260)                        # 体能波动周期
    phase = rnd.uniform(0, 6.28)
    # 走跑结合: 每次持续 15~45s, 速度 1.2~1.8 m/s, 间隔 2.5~5.5 min
    walks = []
    t = rnd.uniform(120, 330)
    while t < target_m / max(2.5, base_v * 0.9):
        walks.append((t, rnd.uniform(15, 45)))
        t += rnd.uniform(150, 330)
    return base_v, walks, period, phase


def walk_speed_at(t, walks):
    for w0, wlen in walks:
        if w0 <= t <= w0 + wlen:
            # 进入/离开渐变
            edge = min(t - w0, w0 + wlen - t)
            f = min(1.0, edge / 6.0)
            return 1.35 + 0.35 * f
    return None


# ---------------------------------------------------------------- 主生成器
def generate_run(cps, target_m, rnd=None, vena4=False):
    """生成一次完整跑步的全部数据。
    cps: [(lat, lng, point_id)]  vena4=True 时只访问随机抽取的 4 个打卡点。
    返回 dict(track, log_data, step_info, step_num, used_time, distance_km, gyr, crossings)"""
    rnd = rnd or random.Random()
    if vena4:
        order = plan_route_vena(cps, rnd, 4)
    else:
        order = plan_route(cps, rnd)
    # 先估单圈周长, 按目标距离铺足够的圈数(路径必须长于目标, 否则钳制原地踏步)
    perim = sum(haversine_m(order[i][0], order[i][1],
                            order[(i + 1) % len(order)][0], order[(i + 1) % len(order)][1])
                for i in range(len(order)))
    laps = max(1, math.ceil(target_m / max(perim, 1.0) * 1.15))
    path = build_path(order, rnd, laps, perim)
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(cum[-1] + haversine_m(path[i - 1][0], path[i - 1][1],
                                         path[i][0], path[i][1]))

    base_v, walks, period, phase = speed_profile(target_m, rnd)

    track = []
    steps_per_point = []
    s = t = 0.0
    i = 0
    jl = jn = 0.0
    while True:
        v = base_v * (1 + 0.10 * math.sin(2 * math.pi * t / period + phase))
        v += rnd.gauss(0, 0.20)
        if t < 30:                                   # 起步渐加速
            v *= 0.55 + 0.45 * (t / 30)
        w = walk_speed_at(t, walks)
        if w is not None:                            # 走跑结合(渐变, 加速度受控)
            v = v * 0.25 + w * 0.75
        # 覆盖检查提前: 未全覆盖时不做终点减速(跑者不知道目标, 继续正常跑)
        covered_now = all(any(haversine_m(q["a"], q["o"], c[0], c[1]) <= CHECKIN_RADIUS_M
                              for q in track) for c in order) if track else False
        remaining = target_m - s
        if remaining < 120 and covered_now:          # 终点线性减速(时间上自然)
            v *= max(0.30, 0.30 + 0.70 * remaining / 120.0)
        # 打卡点附近轻减速(非复利, 只刹高速, 避免起步段越走越慢)
        la, lo_ = route_at(path, cum, s)
        dmin = min(haversine_m(la, lo_, c[0], c[1]) for c in order)
        if dmin < 15 and v > 2.2:
            v *= rnd.uniform(0.70, 0.90)
        v = max(0.02, min(v, 5.6))

        # GPS 漂移: 指数平滑强相关(对齐真人 lag-1≈0.8) + 罕见跳变
        jl = jl * 0.86 + rnd.gauss(0, 1.6) / 111000.0
        jn = jn * 0.86 + rnd.gauss(0, 1.9) / 111000.0 / math.cos(math.radians(la))
        if rnd.random() < 0.008:
            jl += rnd.uniform(-0.00007, 0.00007)
            jn += rnd.uniform(-0.00007, 0.00007)
        lat, lng = la + jl, lo_ + jn
        # 校区硬约束: 点必须在南跑区多边形内(留边距), 否则回退到路径基准点
        if not point_in_zone(lat, lng):
            for _ in range(4):
                jl *= 0.4
                jn *= 0.4
                lat, lng = la + jl, lo_ + jn
                if point_in_zone(lat, lng):
                    break
            else:
                lat, lng = la, lo_

        pt = {"a": quantize_geo(lat), "o": quantize_geo(lng)}
        # s 字段: 真人记录首点无 s
        if i > 0 and rnd.random() > 0.004:
            pt["s"] = round(max(0.02, v + rnd.gauss(0, 0.15)), 2)
        # b 字段: 真人 55% 点位有, 近打卡点偏向 2
        if dmin < 20:
            if rnd.random() < 0.80:
                pt["b"] = 2 if rnd.random() < 0.72 else 1
        elif rnd.random() < 0.50:
            pt["b"] = 1 if rnd.random() < 0.67 else 2
        # c 字段: 真人 |N(0.067,0.027)| ∈ [0,0.10], 开头偏低
        if i < 8:
            c = rnd.uniform(0.0, 0.04)
        else:
            c = abs(rnd.gauss(0.067, 0.027))
            c = min(c, 0.10)
        pt["c"] = f"{c:.2f}"
        track.append(pt)

        # 步数: 步幅随速度(走小跑大), 与真人步幅 1.1~1.4m 一致
        stride = min(1.45, max(0.65, 0.5 + 0.22 * v))
        sps = v / stride if stride > 0 else 0        # steps/s
        steps_per_point.append(sps * (DT + rnd.uniform(-0.02, 0.02)) *
                               (0.9 + 0.2 * rnd.random()))

        for cp in order:
            if haversine_m(pt["a"], pt["o"], cp[0], cp[1]) < 14 and rnd.random() < 0.35:
                v *= rnd.uniform(0.75, 0.9)
                break
        s += v * (DT + rnd.uniform(-0.02, 0.02))
        t += DT
        i += 1
        covered = all(any(haversine_m(q["a"], q["o"], c[0], c[1]) <= CHECKIN_RADIUS_M
                          for q in track) for c in order)
        if s >= target_m and covered:
            break
        if i > 1100:
            break

    # 收尾两步(真人停止形态)
    # 限峰值段速: 真人记录 s 最大 7.25 m/s, 超过的把后点拉回
    for k in range(1, len(track)):
        a, b_ = track[k - 1], track[k]
        sp = haversine_m(a["a"], a["o"], b_["a"], b_["o"]) / DT
        if sp > 7.3:
            f = (7.3 * DT) / haversine_m(a["a"], a["o"], b_["a"], b_["o"])
            track[k]["a"] = a["a"] + (b_["a"] - a["a"]) * f
            track[k]["o"] = a["o"] + (b_["o"] - a["o"]) * f
    for _ in range(2):
        pt = dict(track[-1])
        pt["a"] = quantize_geo(track[-1]["a"] + rnd.gauss(0, 1.0) / 111000.0)
        pt["o"] = quantize_geo(track[-1]["o"] + rnd.gauss(0, 1.1) / 111000.0)
        pt["s"] = round(rnd.uniform(0.01, 0.06), 2)
        pt["b"] = 2
        pt["c"] = f"{rnd.uniform(0.05, 0.09):.2f}"
        track.append(pt)
        steps_per_point.append(rnd.uniform(0, 0.5))
        t += DT

    dist_m = 0.0
    for a, b_ in zip(track, track[1:]):
        dist_m += haversine_m(a["a"], a["o"], b_["a"], b_["o"])
    used = int(round(t))
    dist_km = round(dist_m / 1000.0, 2)

    # ---- log_data: 首过打卡点记录(真人形态: distance 字符串 quirk + 双拼写) ----
    cum_d = [0.0]
    for a, b_ in zip(track, track[1:]):
        cum_d.append(cum_d[-1] + haversine_m(a["a"], a["o"], b_["a"], b_["o"]))
    scale = used / max(1e-6, len(track) * DT)
    entries = []
    for cp in order:
        la, lo, pid = cp
        idx = None
        for k in range(len(track)):
            if haversine_m(track[k]["a"], track[k]["o"], la, lo) <= CHECKIN_RADIUS_M:
                idx = k
                break
        if idx is None:                  # 未经过的点不打卡（真实语义，vena4 关键）
            continue
        t_off = max(0.0, min(float(used), idx * DT * scale + rnd.uniform(-3.0, 3.0)))
        d_km = cum_d[idx] / 1000.0
        entries.append({
            "latitude": track[idx]["a"], "longitude": track[idx]["o"],
            "longtitude": track[idx]["o"], "point_id": str(pid),
            "time": int(t_off),  # 相对偏移, pipeline 再加 start_time
            "distance": "0.0" if d_km < 0.005 else round(d_km, 2),
        })

    # ---- step_info: 60s 一桶, 由逐点步数聚合 ----
    n_bucket = max(2, used // 60 + 1)
    buckets = [0] * n_bucket
    for k, sp in enumerate(steps_per_point):
        b_i = min(n_bucket - 1, int(k * DT / 60))
        buckets[b_i] += sp
    buckets = [max(0, int(round(b))) for b in buckets]
    step_num = int(sum(buckets))

    # ---- gyr: 60ms 采样, 步频与轨迹一致(冲击节拍随速度变化) ----
    gyr = _gen_gyro(steps_per_point, rnd)

    return {
        "track": track, "log_data": entries, "step_info": {"interval": 60, "list": buckets},
        "step_num": step_num, "used_time": used, "distance_km": dist_km,
        "gyr": gyr, "crossings": sorted(e["point_id"] for e in entries),
        "path_pts": len(track),
    }


def _gen_gyro(steps_per_point, rnd):
    """按轨迹步频生成陀螺仪样本(明文 JSON, 60ms 步进, 真实步态模型)。"""
    interval_ms = 60
    out = []
    t_ms = 0
    phase = 0.0
    for sp in steps_per_point:
        cadence = sp / DT                              # steps/s
        step_period = max(3.0, 1.0 / max(cadence, 0.05) / (interval_ms / 1000.0))
        n = int(DT * 1000 / interval_ms)
        for _ in range(n):
            ph = (phase % step_period) / step_period
            heel = math.exp(-((ph - 0.05) * 8) ** 2)
            toe = math.exp(-((ph - 0.55) * 10) ** 2) * 0.5
            az = 9.8 + heel * 3.5 + toe * 1.5 + rnd.uniform(-0.15, 0.15)
            sway = math.sin(phase / step_period * 2 * math.pi) * 0.6
            ax = sway + rnd.uniform(-0.2, 0.2)
            ay = math.cos(phase / step_period * 2 * math.pi) * 0.3 + rnd.uniform(-0.15, 0.15)
            gx = math.sin(phase / step_period * 2 * math.pi) * 0.25 + rnd.uniform(-0.05, 0.05)
            gy = math.cos(phase / step_period * 2 * math.pi) * 0.2 + rnd.uniform(-0.04, 0.04)
            gz = math.sin(phase / (step_period * 2) * 2 * math.pi) * 0.1 + rnd.uniform(-0.03, 0.03)
            out.append({"t": t_ms, "ax": round(ax, 3), "ay": round(ay, 3),
                        "az": round(az, 3), "gx": round(gx, 3), "gy": round(gy, 3),
                        "gz": round(gz, 3)})
            t_ms += interval_ms
            phase += 1
    return out


# ---------------------------------------------------------------- 自检(红队规则)
def self_check(res):
    """按公开防御规则自检, 返回 {rule: (passed, detail)}"""
    import statistics
    track = res["track"]
    tr = [p for p in track]
    # speed/acc
    speeds = []
    for a, b_ in zip(tr, tr[1:]):
        d = haversine_m(a["a"], a["o"], b_["a"], b_["o"])
        speeds.append(d / DT)
    accs = [abs(speeds[i] - speeds[i - 1]) / DT for i in range(1, len(speeds))]
    # noise spectrum
    m = statistics.mean(speeds)
    arr = [x - m for x in speeds]
    den = sum(a * a for a in arr) or 1e-9
    def ac(lag):
        num = sum(arr[i] * arr[i + lag] for i in range(len(arr) - lag))
        return num / den
    # step ratio
    dist_km = res["distance_km"]
    ratio = (dist_km * 1000) / (res["step_num"] * 0.7) if res["step_num"] else 9e9
    return {
        "max_speed(<=12)": (max(speeds) <= 12.0, f"{max(speeds):.2f} m/s"),
        "max_acc(<=10)": (max(accs) <= 10.0, f"{max(accs):.2f} m/s2"),
        "speed_var(>=0.1)": (statistics.variance(speeds) >= 0.1,
                             f"{statistics.variance(speeds):.2f}"),
        "autocorr1(>=0.3)": (abs(ac(1)) >= 0.3, f"{ac(1):.3f}"),
        "step_ratio(0.4~2.5)": (0.4 <= ratio <= 2.5,
                                f"{ratio:.2f} (steps={res['step_num']})"),
        "checkpoints(>=4)": (len(res["crossings"]) >= 4,
                             f"{len(res['crossings'])} 个打卡点"),
        "zone(100% in)": (all(point_in_zone(p["a"], p["o"], 0) for p in track),
                          "all points inside school zone polygon"),
        "points": (True, f"{len(track)} pts, {dist_km}km, {res['used_time']}s, "
                         f"pace={int(res['used_time']/dist_km)}s/km"),
    }
