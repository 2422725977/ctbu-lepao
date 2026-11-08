#!/usr/bin/env bash
# =====================================================================
# CTBU 乐跑主页管理系统 · codexi.cn 幂等上线脚本
#
# 目标机：腾讯云 Lighthouse 成都 / OpenCloudOS 9.6 / 宝塔 Nginx / Docker + Compose
# 用法  ：sudo bash /opt/ctbu-lepao/deploy/deploy.sh     （必须 root）
# 首次引导：脚本本身来自仓库，需先手动 clone 一次（见部署配置.md §5）：
#   git clone -b deploy/codexi --depth 1 https://github.com/2422725977/ctbu-lepao.git /opt/ctbu-lepao
#   bash /opt/ctbu-lepao/deploy/deploy.sh
# 之后重复执行即走分支 fetch + reset 的幂等更新路径。
#
# 影响面：docker compose up -d --build 会重建容器，有秒级服务中断；首次构建
#   （pip install）期间经 Nginx 访问可能短暂 502，备案前无公网流量，影响可控。
# 数据安全：不打印、不改动 server/data/accounts.json 内容；不执行 down -v、
#   git clean、rm -rf data；state/ 与 logs/ 为 bind mount，重建容器不丢数据。
# =====================================================================
set -euo pipefail

# ------------------------------ 常量 ------------------------------
REPO_DIR="/opt/ctbu-lepao"
REPO_URL="https://github.com/2422725977/ctbu-lepao.git"
BRANCH="deploy/codexi"
VHOST_DIR="/www/server/panel/vhost/nginx"
VHOST_DST="${VHOST_DIR}/codexi.cn.conf"
VHOST_SRC="${REPO_DIR}/deploy/nginx-codexi.cn.conf"
BACKUP_DIR="/opt/ctbu-lepao-backups"
COMPOSE_FILE="${REPO_DIR}/server/docker-compose.yml"
SERVICE="lepao-server"
ACCOUNTS_REL="server/data/accounts.json"
HOST_HEADER="codexi.cn"
BACKEND="http://127.0.0.1:8080"
WAIT_ROUNDS=60          # 健康检查最多 60 轮 × 2s = 120s
WAIT_INTERVAL=2
STAMP="$(date +%Y%m%d-%H%M%S)-$$"

# ------------------------------ 自我保护 ------------------------------
# 本脚本存放在仓库内，第 1 步的 git reset --hard 可能覆写它；bash 按块读取脚本文件，
# 覆写会导致后续指令错乱。因此先把自身复制到 /tmp 再 exec 副本（副本不在仓库内）。
SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || printf '%s' "${BASH_SOURCE[0]}")"
if [[ "$SELF" == "$REPO_DIR"/* ]]; then
    RUNNER="/tmp/ctbu-lepao-deploy.$$.sh"
    cp -p "$SELF" "$RUNNER"
    chmod 0700 "$RUNNER"
    echo ">>> 已生成 /tmp 运行副本（避免 git reset --hard 覆写正在执行的脚本）"
    exec bash "$RUNNER" "$@"
fi
if [[ "$SELF" == /tmp/ctbu-lepao-deploy.*.sh ]]; then
    trap 'rm -f "$SELF"' EXIT
fi

say()  { echo; echo ">>> $*"; }
fail() { echo; echo "[失败] $*" >&2; exit 1; }
trap 'echo "[中断] 有命令返回非零，部署已停止；若 vhost 已变更，请从 '"${BACKUP_DIR}"' 恢复后执行 nginx -t && nginx -s reload" >&2' ERR

# ------------------------------ 前置检查 ------------------------------
say "[0/7] 前置检查（root / git / curl / docker compose / nginx / 宝塔目录）"
[ "$(id -u)" -eq 0 ] || fail "请用 root 执行：需要写 ${VHOST_DIR} 并操作 docker"
for BIN in git curl docker; do
    command -v "$BIN" >/dev/null 2>&1 || fail "缺少命令：${BIN}"
done
docker compose version >/dev/null 2>&1 || fail "'docker compose version' 不可用，请确认 Compose 插件已安装"
[ -d "$VHOST_DIR" ] || fail "宝塔 vhost 目录不存在：${VHOST_DIR}"
[ -f /www/server/nginx/conf/nginx.conf ] || echo "提示：未找到 /www/server/nginx/conf/nginx.conf，请人工确认宝塔路径"
if ! grep -qF 'include /www/server/panel/vhost/nginx/*.conf;' /www/server/nginx/conf/nginx.conf 2>/dev/null; then
    echo "警告：主配置中未检测到 vhost 通配 include，本站点可能不会生效，请人工确认"
fi

NGINX_BIN="$(command -v nginx || true)"
[ -n "$NGINX_BIN" ] || NGINX_BIN="/www/server/nginx/sbin/nginx"
[ -x "$NGINX_BIN" ] || fail "找不到可执行的 nginx（已试 PATH 与 /www/server/nginx/sbin/nginx）：${NGINX_BIN}"

mkdir -p "$BACKUP_DIR"
chmod 0700 "$BACKUP_DIR"       # 备份内含 accounts.json，收紧权限

# ------------------------------ 1. 同步代码 ------------------------------
say "[1/7] 同步代码（分支 ${BRANCH}）"
if [ -d "${REPO_DIR}/.git" ]; then
    git -C "$REPO_DIR" fetch origin "$BRANCH"

    # accounts.json 含真实 token 且被仓库公开跟踪：reset --hard 会把它打回分支版本，
    # 覆盖 /api/token 在运行期写入的凭证。检测到本地改动时先备份、reset 后原样还原
    # （全程不打印文件内容，最终内容与执行前一致）。未改动则让分支版本正常生效。
    PROTECT_ACCOUNTS=0
    if [ -f "${REPO_DIR}/${ACCOUNTS_REL}" ] && \
       [ -n "$(git -C "$REPO_DIR" status --porcelain -- "$ACCOUNTS_REL")" ]; then
        cp -p "${REPO_DIR}/${ACCOUNTS_REL}" "${BACKUP_DIR}/accounts.json.${STAMP}"
        chmod 0600 "${BACKUP_DIR}/accounts.json.${STAMP}"
        PROTECT_ACCOUNTS=1
        echo "检测到 ${ACCOUNTS_REL} 含运行期数据，已备份（内容不打印、不改动）"
    fi

    if git -C "$REPO_DIR" rev-parse --verify --quiet "refs/remotes/origin/${BRANCH}" >/dev/null; then
        TARGET_REF="origin/${BRANCH}"
    else
        TARGET_REF="FETCH_HEAD"       # 单分支浅 clone 的兜底
    fi
    git -C "$REPO_DIR" reset --hard "$TARGET_REF"

    if [ "$PROTECT_ACCOUNTS" -eq 1 ]; then
        cp -p "${BACKUP_DIR}/accounts.json.${STAMP}" "${REPO_DIR}/${ACCOUNTS_REL}"
        echo "已把 ${ACCOUNTS_REL} 还原为执行前的运行数据（避免覆盖抓号写入的 token）"
    fi
else
    if [ -e "$REPO_DIR" ]; then
        fail "${REPO_DIR} 已存在但不是 git 仓库，请人工确认后处理，脚本不会删除它"
    fi
    git clone -b "$BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"
fi
[ -f "$VHOST_SRC" ]    || fail "缺少 ${VHOST_SRC}：分支是否包含 deploy/ 目录？"
[ -f "$COMPOSE_FILE" ] || fail "缺少 ${COMPOSE_FILE}"
[ -f "${REPO_DIR}/${ACCOUNTS_REL}" ] || fail "缺少 ${ACCOUNTS_REL}：compose 会把它当目录挂载，容器内看板将读不到账号"

# ------------------------------ 2. 持久化目录 ------------------------------
# .gitignore 已忽略任意目录下的 state/ 与 logs/，reset --hard 不会清空这两个目录。
say "[2/7] 准备宿主机持久化目录并授权（不删除任何已有数据）"
mkdir -p "${REPO_DIR}/server/data/state" "${REPO_DIR}/server/data/logs" /www/wwwlogs
chmod 0775 "${REPO_DIR}/server/data/state" "${REPO_DIR}/server/data/logs"
# 容器默认以 root 运行（python:3.12-slim 未设 USER），宿主机目录属主 root 即可写；
# 第 6 步用真实写探针复核，若环境启用了 userns-remap 会在这里明确报错。

# ------------------------------ 3. 安装 vhost ------------------------------
say "[3/7] 安装 Nginx vhost 到 ${VHOST_DST}"
VHOST_PREV=""
if [ -f "$VHOST_DST" ]; then
    VHOST_PREV="${BACKUP_DIR}/codexi.cn.conf.${STAMP}.bak"
    cp -p "$VHOST_DST" "$VHOST_PREV"
    echo "已存在同名 vhost，已备份到 ${VHOST_PREV}，本次覆盖"
fi
CONFLICTS="$(grep -RlF --include='*.conf' 'server_name codexi.cn' "$VHOST_DIR" 2>/dev/null \
             | grep -vF "$VHOST_DST" || true)"
if [ -n "$CONFLICTS" ]; then
    echo "检测到其他也声明了 codexi.cn 的 vhost（宝塔面板生成的同名站点）：" >&2
    echo "$CONFLICTS" >&2
    fail "同名站点会造成 Nginx 'conflicting server name' 且由先加载者命中，页面可能打不开。请先在宝塔面板停用/删除同名站点（或人工确认后移除对应 .conf），再重跑本脚本；本次未安装 vhost、未 reload"
fi
cp -f "$VHOST_SRC" "$VHOST_DST"
chmod 0644 "$VHOST_DST"

# ------------------------------ 4. 校验并重载 Nginx ------------------------------
say "[4/7] nginx -t 校验后 reload（校验失败自动回滚本次 vhost 变更）"
if ! "$NGINX_BIN" -t 2>&1; then
    echo "[失败] nginx -t 未通过，正在恢复 vhost 原状" >&2
    if [ -n "$VHOST_PREV" ]; then
        cp -pf "$VHOST_PREV" "$VHOST_DST"
    else
        rm -f "$VHOST_DST"            # 首次安装失败：移除坏配置，绝不留隐患
    fi
    "$NGINX_BIN" -t 2>&1 || echo "回滚后 nginx -t 仍未通过，请人工检查主配置" >&2
    fail "新 vhost 语法校验失败，已恢复原状，未执行 reload"
fi
"$NGINX_BIN" -s reload || fail "nginx reload 失败（Nginx 是否在运行？可用 ${NGINX_BIN} -t 与 ps aux | grep nginx 排查）"
echo "nginx -t 通过，已 reload"

# ------------------------------ 5. 构建并启动容器 ------------------------------
say "[5/7] docker compose up -d --build（重建有秒级中断；bind mount 数据不删）"
if ! docker compose -f "$COMPOSE_FILE" up -d --build; then
    docker compose -f "$COMPOSE_FILE" logs --tail=80 "$SERVICE" || true
    fail "容器构建/启动失败：vhost 已 reload 但上游未就绪，请修复后重跑本脚本，或按部署配置.md §3.1 先停用站点"
fi

# ------------------------------ 6. 健康检查 ------------------------------
say "[6/7] 健康检查：容器内 / 宿主机 127.0.0.1:8080 / 持久化目录写探针"
wait_status() {
    local label="$1"; shift
    local i
    for (( i=1; i<=WAIT_ROUNDS; i++ )); do
        if "$@" >/dev/null 2>&1; then
            echo "  OK: ${label}（第 ${i} 次探测）"
            return 0
        fi
        sleep "$WAIT_INTERVAL"
    done
    return 1
}

if ! wait_status "容器内 GET /api/status = 200" \
     docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" python -c \
       "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4); sys.exit(0 if r.status==200 else 1)"; then
    echo "---- 容器日志（末尾 80 行）----"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 "$SERVICE" || true
    fail "容器内 /api/status 未在 $((WAIT_ROUNDS*WAIT_INTERVAL))s 内返回 200"
fi

if ! wait_status "宿主机 GET 127.0.0.1:8080/api/status = 200" \
     curl -fsS -o /dev/null --max-time 5 "${BACKEND}/api/status"; then
    fail "宿主机 8080 未就绪：检查端口占用与 compose 的 127.0.0.1:8080:8080 绑定"
fi

if ! docker compose -f "$COMPOSE_FILE" exec -T "$SERVICE" sh -c \
     'touch /app/data/state/.deploy_write_probe /app/data/logs/.deploy_write_probe && rm -f /app/data/state/.deploy_write_probe /app/data/logs/.deploy_write_probe'; then
    fail "容器对 /app/data/state 或 /app/data/logs 不可写：检查宿主机目录属主 / SELinux / ACL，勿删已有数据"
fi
echo "  OK: state/ 与 logs/ 容器可写探针通过"

# ------------------------------ 7. 经 Nginx 反代验证 ------------------------------
# 未备案：大陆节点按域名访问 80 会被拦，用 Host 头让本机 Nginx 命中本 vhost。
say "[7/7] 经 Nginx 反代验证（未备案：curl -H 'Host: ${HOST_HEADER}' http://127.0.0.1/）"
check_url() {
    local label="$1" path="$2" host="$3" code
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: ${host}" "http://127.0.0.1${path}" || true)"
    [ "$code" = "200" ] || fail "${label} 期望 200，实际 ${code:-无响应}（path=${path}, Host=${host}）"
    echo "  OK: ${label} → 200"
}
check_url "GET / (codexi.cn)"           "/"                       "codexi.cn"
check_url "GET / (www.codexi.cn)"       "/"                       "www.codexi.cn"
check_url "GET /static/dashboard.css"   "/static/dashboard.css"  "codexi.cn"
check_url "GET /static/dashboard.js"    "/static/dashboard.js"   "codexi.cn"
check_url "GET /api/status"             "/api/status"            "codexi.cn"
check_url "GET /api/dashboard"          "/api/dashboard"         "codexi.cn"

STATIC_HEADERS="$(curl -sSI --max-time 10 -H "Host: ${HOST_HEADER}" http://127.0.0.1/static/dashboard.css)" \
    || fail "静态资源 HEAD 请求失败（Nginx 反代或 Flask 静态路由异常）"
echo "$STATIC_HEADERS" | grep -qiE 'Cache-Control:.*max-age=300' \
    || fail "静态资源未命中 5 分钟短缓存头（期望 public, max-age=300, must-revalidate）"
echo "$STATIC_HEADERS" | grep -qi 'must-revalidate' \
    || fail "静态资源缺少 must-revalidate（改版后需重新校验，否则可能一直用旧 CSS/JS）"
echo "$STATIC_HEADERS" | grep -qi 'X-Content-Type-Options: nosniff' || fail "静态资源缺少 nosniff 安全头"
if echo "$STATIC_HEADERS" | grep -qiE 'max-age=604800|immutable'; then
    fail "静态资源仍带 7d / immutable 长缓存头，与 5 分钟短缓存仲裁冲突"
fi
echo "  OK: /static/ 5 分钟短缓存（public, max-age=300, must-revalidate）与安全头"

# HTML 与 /api/* 一律 no-store：server 级 proxy_hide_header 隐藏上游 Cache-Control，
# 由 Nginx 在 location / 单一来源下发（/api/* 也落在 location /）。
for P in / /api/status /api/dashboard; do
    H="$(curl -sSI --max-time 10 -H "Host: ${HOST_HEADER}" "http://127.0.0.1${P}")" \
        || fail "${P} 的 HEAD 请求失败"
    echo "$H" | grep -qi 'Cache-Control: no-store' \
        || fail "${P} 未返回 Cache-Control: no-store（改版后会拿到旧 HTML 壳子 / 旧数据）"
    echo "  OK: ${P} → Cache-Control: no-store"
done

DASH_JSON="$(curl -fsS --max-time 10 -H "Host: ${HOST_HEADER}" http://127.0.0.1/api/dashboard)" \
    || fail "GET /api/dashboard 失败"
for KEY in '"server_time"' '"data_time"' '"health"' '"summary"' '"windows"' \
           '"accounts"' '"alerts"' '"recent_events"' '"logs_tail"' '"public_base_url"'; do
    printf '%s' "$DASH_JSON" | grep -qF "$KEY" || fail "/api/dashboard 缺少字段 ${KEY}"
done
echo "  OK: /api/dashboard 固定键齐全"

PAGE="$(curl -fsS --max-time 10 -H "Host: ${HOST_HEADER}" http://127.0.0.1/)" || fail "GET / 失败"
printf '%s' "$PAGE" | grep -qF 'LRL-1.0' || fail "首页缺少 LRL-1.0 合规页脚"
if printf '%s' "$PAGE" | grep -qiE "http-equiv=['\"]?refresh"; then
    fail "首页仍存在整页 meta refresh（应为 /api/dashboard 局部刷新）"
fi
if printf '%s' "$PAGE" | grep -qE 'https?://|140\.143\.87\.163|codexi\.cn'; then
    echo "警告：首页出现外链 / 绝对 IP / 域名字符串，请按部署配置.md 的显示效果清单人工复核（不阻断上线）"
fi
echo "  OK: LRL-1.0 页脚、无整页 meta refresh"

# ------------------------------ 完成 ------------------------------
say "部署完成（未备案状态：仅 80 + Host 头验证，443 未启用）"
echo "容器      : docker compose -f ${COMPOSE_FILE} ps"
echo "vhost     : ${VHOST_DST}（备份目录 ${BACKUP_DIR}）"
echo "Nginx 日志: /www/wwwlogs/codexi.cn.log、/www/wwwlogs/codexi.cn.error.log"
echo "应用日志  : ${REPO_DIR}/server/data/logs/server_YYYYMMDD.log"
echo "域名验证  : curl -H 'Host: codexi.cn' http://127.0.0.1/"
echo "回滚说明  : ${REPO_DIR}/deploy/部署配置.md §3"
echo "提醒      : 本脚本未做开机自启演练；容器 restart: unless-stopped，Nginx 由宝塔托管，重启后请复核 docker compose ps 与 nginx -t"
exit 0
