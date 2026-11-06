# Postgres with both pgvector and PostGIS: hybrid search filters on distance and ranks on similarity in one statement
# (ADR 0019). Neither official image has both. PostGIS comes from the PostgreSQL project's own apt repository,
# already configured in the base image.
FROM pgvector/pgvector:0.8.7-pg16
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
