FROM python:3.12-slim

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

RUN pip install --no-cache-dir pycryptodome

# 复制代码（build context = 仓库根目录）
COPY lepaorun/ /app/lepaorun/
COPY docker/scheduler.py /app/scheduler.py

RUN mkdir -p /app/data/state /app/data/logs

VOLUME ["/app/data"]

HEALTHCHECK --interval=300s --timeout=5s \
  CMD python -c "import sys; sys.exit(0)"

CMD ["python", "-u", "/app/scheduler.py"]
