# Docker 部署指南

## 前提
- 任意有 Docker 的机器（VPS / NAS / 树莓派 / 群晖），需能访问 lepao.ctbu.edu.cn（已验证外网可达）
- config.json（含有效 token）

## 快速部署

```bash
# 1. 克隆代码
git clone https://github.com/2422725977/ctbu-lepao.git
cd ctbu-lepao/docker

# 2. 放配置（从 Mac 拷贝过来，或手动创建）
mkdir -p data/state data/logs
# 把 config.json 放到 data/config.json

# 3. 构建并启动
docker compose up -d

# 4. 查看日志
docker logs -f ctbu-lepao
```

## token 更新

token 过期后（tick 日志出现 TOKEN_DEAD），在 Mac 上重新抓号，然后：
```bash
# 方法 1：直接编辑宿主机上的 data/config.json
vim data/config.json   # 改 token 字段
docker restart ctbu-lepao

# 方法 2：从 Mac scp 过去
scp ~/Documents/校园乐跑/lepaorun/config.json server:~/ctbu-lepao/docker/data/
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| TZ | Asia/Shanghai | 时区（时间窗口依赖此设置） |
| LEPAO_WINDOWS | 12:00-14:00,18:00-22:30 | 提交窗口（逗号分隔多个） |
| LEPAO_MAX_PER_DAY | 1 | 每日最大提交次数 |

## 文件结构

```
docker/
  Dockerfile           # python:3.12-slim + pycryptodome
  docker-compose.yml   # 编排（volume 挂载 config/state/logs）
  scheduler.py         # 容器内调度器（随机窗口 + 状态机 + 自动重试）
  data/
    config.json        # 挂载到 /app/lepaorun/config.json
    state/             # auto_state.json（当日完成状态）
    logs/              # scheduler 日志
```

## 工作原理

容器内调度器作为长期驻留进程运行（PID 1）：
1. 每天根据日期种子在窗口内随机一个目标分钟（当天恒定）
2. sleep 到目标时刻
3. 检查当日额度 → 执行 driver.py --submit（含 OSS 上传/陀螺仪/复核）
4. 成功 → 标记 done，休眠至明天
5. TOKEN_DEAD → 日志告警，等 config 更新
6. 失败 → 等 30 分钟重试

## 注意事项
- 服务器需能访问 lepao.ctbu.edu.cn（外网可达，已验证）
- 时区必须是 Asia/Shanghai（时间窗按北京时间）
- config.json 含 token，勿公开
