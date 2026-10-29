# CTBU 校园乐跑协议自动化（个人自用版）

> **出处署名（LRL-1.0 要求，不可删除）**
> 本项目是 **dan-cun / Lepao Research（乐跑协议研究）** 的**爆改衍生版**：
> 原作者 **dan-cun**，原始仓库 <https://github.com/dan-cun/lepao3>，原许可 **乐跑研究协议 1.0（LRL-1.0）**。
> 本仓库**继续以 LRL-1.0 授权**，不提供任何商业用途授权。
>
> **本仓库改了什么**（相对上游）：
> 1. 把学校参数具体化为**重庆工商大学（CTBU，自部署 `lepao.ctbu.edu.cn`，school_id=201）**，新增本校跑区轨迹生成器 `lepao/ctbu_track.py`；
> 2. 新增 `driver.py`：单文件协议重放引擎（真门校验 → 跑区/打卡点 → 当日额度 → 轨迹生成 → OSS 直传 → stopRun → 陀螺仪补传 → 复核），零依赖 Flask/CLI 框架；
> 3. 新增 `automation/`：随机窗口定时任务（`tick.py`）、抓号工具（`grab_token.py`）、统计（`stats.py`）；
> 4. 新增 macOS 部署适配：路径不再硬编码 `/tmp`，改为随文件位置定位；稳定 venv 位于部署目录；
> 5. 补齐协议实证文档 `技术文档.md`、风控跑本 `防封跑本.md`、自动化运维手册 `docs/自动化运维.md`。
>
> **风险自负**：按「现状（AS IS）」提供，无任何担保。账号限制、成绩作废、数据丢失等后果由使用者自行承担。
> 本项目与**乐跑平台、任何学校、腾讯公司无关联、无授权、无背书**。
> 完整条款见 [`lepaorun/LICENSE`](lepaorun/LICENSE)，简明版 [`lepaorun/DISCLAIMER.md`](lepaorun/DISCLAIMER.md)，第三方组件 [`lepaorun/NOTICE`](lepaorun/NOTICE)。
> **删除或隐藏上述声明 = LRL-1.0 第三条授权自动终止。**

---

## 这是什么

步道乐跑（lptiyu）系高校自部署平台的小程序协议层研究 + 可复现的提交链路。
纯 Python 实现：AES-128-CBC 请求加密 / 响应解密、SignMD5 签名、真机指纹头组、OSS STS 直传轨迹、
`stopRun` 提交、陀螺仪补传、`recordDetail` 复核。

**只用于本人账号**（或已明确同意的成员）。仓库内**不含任何个人身份信息**：
无学号、无 uid、无 token、无服务端记录号、无抓包流量。`config.json` 被 `.gitignore` 排除。

## 目录结构

```
ctbu-lepao/
├── lepaorun/                   # 协议库（上游主体，LRL-1.0）
│   ├── lepao/                  # crypto/transport/v3api/accounts/auth/capture/track/
│   │                           #   record/pipeline/state/wechat_ctl/license_guard + ctbu_track
│   ├── driver.py               # ★ 本仓库主驱动：干跑 / --submit / --canary
│   ├── cli.py                  # 上游命令行入口（成员管理/token 状态/cert 体检）
│   ├── config.sample.json      # 配置骨架（成员表为空）
│   ├── data/真实轨迹_035明文.json # 轨迹模板（仅跑道坐标，无身份字段）
│   ├── LICENSE / NOTICE / DISCLAIMER.md / SECURITY.md / CONTRIBUTING.md
│   └── 部署与使用说明.md
├── automation/
│   ├── tick.py                 # 每 5 分钟被 cron 叫一次：随机时刻判定 → 提交 → 汇报
│   ├── stats.py                # 当次/当日/学期统计（只读）
│   ├── grab_token.py           # 从 mitm flow 文件解密取 token（请求体优先）
│   └── lepao_tick.sh           # cron 入口（用绝对路径调部署目录的 venv）
├── docs/自动化运维.md           # 部署、抓号、定时、坑清单
├── 技术文档.md                  # 协议实证全集（端点/密文/签名/白名单字段）
├── 防封跑本.md                  # 风控口径（试跑阶段参数、频率、复查节奏）
├── stopRun_packet.json         # 一次真实成功提交的完整报文样本
├── wire_log_sample.jsonl       # 脱敏流量样本
└── dump_detail.py              # 抓包落盘后的逐条详情打印
```

## 环境

- macOS / Windows / Linux 均可跑协议层；本仓库的部署脚本按 **macOS** 写（抓号用 mitmproxy + 企微桌面版）。
- Python 3.10+
- 依赖：`pycryptodome`（必装）、`mitmproxy`（仅抓号）、`brotli`（可选，响应 br 解压）、`ddddocr`（仅 H5 验证码登录链）
- 抓号另需：企业微信桌面版（小程序「智慧体育」）+ 已信任的 mitmproxy 根证书（`~/.mitmproxy`）

```bash
python3 -m venv venv && ./venv/bin/pip install pycryptodome mitmproxy brotli
```

## 快速开始

### 1. 配置

```bash
cp lepaorun/config.sample.json lepaorun/config.json
```

首跑前需在 `config.json` 里填本人 `student_num / uid / school_id`（uid 首次抓号后回填）与 `auth.token`，
并把 `environment.api_host` 指到本校平台域名（CTBU 为 `lepao.ctbu.edu.cn`）。

### 2. 抓 token

token 是**单活动会话**：任何地方重新登录都会顶掉旧的。抓法见 [`docs/自动化运维.md`](docs/自动化运维.md)，
要点：

```bash
# ① mitm 只拦本校域名（注意 allow-hosts 匹配的是 host:port）
./venv/bin/mitmdump -p 8888 --mode upstream:http://127.0.0.1:7897 \
  --set confdir=$HOME/.mitmproxy --ssl-insecure \
  --allow-hosts '^(.*\.)?ctbu\.edu\.cn(:\d+)?$' -w flows/cap.mitm

# ② 系统代理指到 127.0.0.1:8888，在企微里打开「智慧体育」小程序
# ③ 解密取 token（请求体里就带着，不必抓登录）
./venv/bin/python automation/grab_token.py flows/cap.mitm
# ④ 写回 config.json，然后干跑验证
./venv/bin/python lepaorun/driver.py
```

### 3. 干跑 / 提交

```bash
./venv/bin/python lepaorun/driver.py            # 零写入：验 token、跑区、额度，并生成轨迹自检
./venv/bin/python lepaorun/driver.py --submit    # 正式提交
./venv/bin/python lepaorun/driver.py --canary --submit   # 金丝雀：2.6–2.75km 最保守参数
```

`driver` 退出码：`0` 成功/干跑、`2` token 失效、`3` 当日额度已满、`4` 接口异常。

### 4. 自动定时（可选）

`automation/tick.py` 负责「当天在窗口内随机一个时刻提交一次」：

- 窗口：**12:00–14:00**、**18:00–22:30**（可改 `tick.py` 顶部 `WINDOWS`）
- 随机时刻用 `sha256(日期|窗口)` 做种子，**全天恒定**，不会每 5 分钟变一次
- 成功一次后写 `state/auto_state.json` 置 `done`，当天静默；token 死只提醒一次
- cron 只需每 5 分钟触发一次：`*/5 12,13,18-22 * * *` → `bash automation/lepao_tick.sh`
- 输出为空 = 不推送；成功才推完整数据（距离/用时/配速/时间窗/记录号/审查状态/学期累计）

## 本平台实证参数（CTBU，2026-10）

| 项 | 值 |
|---|---|
| API 域名 | `lepao.ctbu.edu.cn`（`/v3/api.php/{Module}/{method}`） |
| 鉴权真门 | `WpRun/getTermList`（返回 `status=101` 即 token 失效） |
| 加密 | AES-128-CBC，固定 KEY/IV；`sign = md5(升序拼接 + SIGN_KEY)` |
| 跑步时段 | `run_start_time 18000` – `run_end_time 81000`（05:00–22:30） |
| 距离 | 2.50–10.00 km |
| 配速 | 180–540 s/km（3'0"–9'0"） |
| 每日上限 | **1 次计入成绩**；同日第二次提交返回 `record_status 0` + `当天关联成绩次数已达到上限` |
| 学期 | `term_id 4773`，`term_num 40` |
| 审查结果 | `record_status 1` = 自动确认有效 |

## 隐私与边界

- 仓库内无任何个人数据；运行后产生的 `config.json`（成员、token）、`logs/`、`flows/`、`state/` 只属于本机使用者，**勿外传、勿提交**。
- 只打卡**本人或已明确同意者**；他人凭证、位置、轨迹数据一律不碰。
- `data/真实轨迹_035明文.json` 仅含跑道坐标序列（无身份字段）；**不要把 `data/` 换成含真实个人信息的数据后再分发**。
- 凭证建议短期保留、用后即清；抓包文件含完整业务报文，别上传网盘或公开仓库。

## 许可

**乐跑研究协议 1.0（LRL-1.0）** —— 非商业授权：仅供学习研究、不可牟利、再发布须署名引用原始仓库并保留全部声明与源文件版权头，衍生版必须继续以 LRL-1.0 授权。
删改声明即依第三条**自动失去使用与分发授权**。完整条款见 [`lepaorun/LICENSE`](lepaorun/LICENSE)。
