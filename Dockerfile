FROM python:3.12-slim

WORKDIR /app

# 安装系统依赖（电信脚本无需 Playwright，依赖很轻）
RUN apt-get update && \
    apt-get install -y \
        curl \
        cron \
        && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制脚本文件
COPY telecom_query.py .
COPY telecom_config.example.json ./telecom_config.example.json

# 创建数据目录（Token 缓存、失败计数）
RUN mkdir -p /app/data

# 配置文件和数据目录挂载点
VOLUME ["/app/data"]

# 默认命令：运行查询
CMD ["python", "telecom_query.py"]
