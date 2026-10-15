FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36 AS app-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY app ./app
COPY agents ./agents
COPY scripts ./scripts
COPY data/knowledge ./data/knowledge
COPY data/structured ./data/structured

# OCR/PDF binaries exist only in the offline preparation target. They are not
# copied into or installed in the public evaluation runtime.
FROM app-base AS tools

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*

FROM app-base AS runtime

RUN addgroup --system --gid 10001 agent \
    && adduser --system --uid 10001 --ingroup agent agent \
    && chmod -R a=rX /app/data/knowledge /app/data/structured \
    && mkdir -p /app/data/runtime \
    && chown -R agent:agent /app/data/runtime

USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=3).read()"

# One worker keeps the in-process single-flight lock authoritative. The evaluator
# sends requests sequentially; completed retries are persisted in SQLite WAL.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log", "--timeout-graceful-shutdown", "300"]
