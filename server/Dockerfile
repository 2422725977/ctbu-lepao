FROM python:3.12-slim

ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

RUN pip install --no-cache-dir pycryptodome flask requests

# 复制乐跑核心模块
COPY lepaorun/lepao/ /app/lepao/
COPY lepaorun/driver.py /app/driver.py

# 复制服务端
COPY server/server.py /app/server.py
COPY server/templates/ /app/templates/

RUN mkdir -p /app/data/state /app/data/logs

VOLUME ["/app/data"]

EXPOSE 8080

HEALTHCHECK --interval=60s --timeout=5s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/status')"

CMD ["python", "-u", "/app/server.py"]
