# RAG Service Docker 镜像 — 部署指南 §11
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY service/ ./service/
COPY config/ ./config/
COPY knowledge_base/ ./knowledge_base/
COPY README.md .

RUN mkdir -p /data/payment-rag/indexes

EXPOSE 8020

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8020/health')"

CMD ["uvicorn", "service.main:app", "--host", "0.0.0.0", "--port", "8020"]
