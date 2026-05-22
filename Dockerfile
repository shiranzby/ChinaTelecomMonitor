FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telecom_query.py .
COPY telecom_config.example.json .

# 数据目录：挂载配置文件和 Token 缓存
VOLUME ["/app/data"]

# 默认数据目录，可通过 -e TELECOM_DATA_DIR 覆盖
ENV TELECOM_DATA_DIR=/app/data

CMD ["python", "telecom_query.py"]
