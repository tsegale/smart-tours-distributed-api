# Postgres with both pgvector and PostGIS: hybrid search filters on distance and ranks on similarity in one statement
# (ADR 0019). Neither official image has both. PostGIS comes from the PostgreSQL project's own apt repository,
# already configured in the base image.

# The base image's gosu is built on Go 1.24.6, flagged CRITICAL for crypto/tls (CVE-2025-68121). gosu does no TLS, so it
# isn't reachable, and gosu's latest release is built on the same Go. Rebuilding that release's source on a current
# toolchain removes the finding without an exception that could also hide a real one later. The flags are gosu's own.
FROM golang:1.27.3-bookworm AS gosu
RUN CGO_ENABLED=0 go install -trimpath -ldflags '-d -w' github.com/tianon/gosu@29b939aa5bd4dc588af765b8248f6e39150d2565

FROM pgvector/pgvector:0.8.7-pg16
COPY --from=gosu /go/bin/gosu /usr/local/bin/gosu
RUN gosu nobody true \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
