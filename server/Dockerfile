FROM python:3.12-slim

ENV TZ=Asia/Shanghai
# 系统 tzdata：python:3.12-slim 默认不含 /usr/share/zoneinfo。
# scheduler_loop / daily_target / 日志文件名都依赖进程本地时区，缺 tzdata 会退化为 UTC。
# DEBIAN_FRONTEND 内联，避免 tzdata 的 debconf 交互卡住构建。
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY server/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 乐跑核心模块：driver.py 落在 /app，lepao 包落在 /app/lepao，
# 与 server.py 顶部的 sys.path.insert / os.chdir 布局保持一致，可直接 import。
COPY lepaorun/lepao/ /app/lepao/
COPY lepaorun/driver.py /app/driver.py

# 服务端：模板与静态资源由前端交付，构建镜像前 server/templates/dashboard.html
# 与 server/static/ 必须已存在，否则 docker build 会因源路径缺失失败。
COPY server/server.py /app/server.py
COPY server/templates/ /app/templates/
COPY server/static/ /app/static/

RUN mkdir -p /app/data/state /app/data/logs

VOLUME ["/app/data"]

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/status', timeout=4)"

CMD ["python", "-u", "/app/server.py"]
