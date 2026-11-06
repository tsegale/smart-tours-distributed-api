# syntax=docker/dockerfile:1
FROM python:3.12-slim
# Plain HTTP for model downloads: the Xet transport stalled here for 20 minutes at no bytes (ADR 0020).
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models HF_HUB_DISABLE_XET=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app
COPY datasets/gazetteer/places.csv ./datasets/gazetteer/places.csv

RUN useradd --system --uid 10001 app && mkdir -p /models && chown app /models
USER app
EXPOSE 8083
# The API container runs the migrations; ai-worker reuses this image with `python -m app.worker`.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8083 --proxy-headers"]
