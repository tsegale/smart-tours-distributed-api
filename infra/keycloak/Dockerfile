# Keycloak built for this deployment, the way its container guide recommends: the build step runs here, so the
# server starts with --optimized and skips it at every start.
# https://www.keycloak.org/server/containers
FROM quay.io/keycloak/keycloak:26.8.1 AS builder
ENV KC_DB=postgres
ENV KC_HEALTH_ENABLED=true
ENV KC_METRICS_ENABLED=true
RUN ["/opt/keycloak/bin/kc.sh", "build"]

FROM quay.io/keycloak/keycloak:26.8.1
COPY --from=builder /opt/keycloak/ /opt/keycloak/
ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
