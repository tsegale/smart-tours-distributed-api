# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 app
USER app
EXPOSE 8082
# Migrate, then serve. With more than one replica, run migrations as a separate one-off job instead.
# Workers come from WEB_CONCURRENCY. Keep-alive outlives the gateway's 30 s pool idle time, so the
# gateway always retires a connection before uvicorn closes it. The supervisor kills a worker that misses a
# health check; at the 5 s default it killed busy-but-healthy workers under the contention benchmark on a
# CPU-starved host, dropping their in-flight requests (docs/benchmarks/booking-locking.md, Phase 2). A worker
# that is really hung is still replaced, 30 s later.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8082 --proxy-headers --timeout-keep-alive 75 --timeout-worker-healthcheck 30"]
