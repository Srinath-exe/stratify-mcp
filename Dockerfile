# Stratify MCP backend.
#
# Runs the FastAPI/JSON-RPC service only. It holds NO market data: every query goes to
# ClickHouse over the internal Docker network, and only aggregates come back. That is the
# whole architecture -- results leave, data does not -- so this image is safe to rebuild,
# move between hosts, and eventually open-source.
FROM python:3.11-slim

# curl is the healthcheck; nothing else is added, to keep the attack surface small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so a code change does not reinstall numpy and pandas.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ ./engine/
COPY server/ ./server/

# The service database (accounts, keys, usage) is mounted at runtime, never baked in.
# Writable by the unprivileged user below.
RUN mkdir -p /app/server/state \
 && useradd --system --uid 10001 --home /app stratify \
 && chown -R stratify:stratify /app
USER stratify

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    STRATIFY_SERVICE_DB=/app/server/state/service.sqlite \
    CLICKHOUSE_HOST=stratify_clickhouse \
    CLICKHOUSE_HTTP_PORT=8123 \
    PORT=8792

EXPOSE 8792

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

# run.sh refuses to start without STRATIFY_KEY_PEPPER and sizes workers to the box.
CMD ["./server/run.sh"]
