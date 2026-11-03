# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# WeasyPrint renders with Pango and HarfBuzz; DejaVu is the ticket's font.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 app

# Tests need the same system libraries (PDF rendering), so they run in this image: docker build --target test
FROM base AS test
COPY requirements-dev.txt pyproject.toml ./
RUN pip install -r requirements-dev.txt
COPY tests ./tests
USER app
CMD ["pytest", "-q"]

# The runtime image, last so that a plain `docker build` (compose, CI) produces it.
FROM base AS runtime
USER app
EXPOSE 8084
# The same image runs four processes, chosen by the compose command: the API (this default), the event bridge
# (python -m app.bridge), Celery workers (celery -A app.celery_app worker) and Beat (celery -A app.celery_app beat).
# The API migrates first; with more than one replica, run migrations as a separate one-off job instead.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8084 --proxy-headers"]

