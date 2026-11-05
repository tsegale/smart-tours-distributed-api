# Threat model

A short STRIDE pass over the parts of the system most exposed or most trusted. Each threat ends in a mitigation that
exists today, or an accepted risk with its reason. "Tested" means a test in this repo fails if the mitigation goes away.
Written for the local stack and CI; Part B's deployment closes the risks marked **deployment**.

Trust boundaries: the internet reaches only the gateway. Everything behind it trusts the gateway's `X-User-*` headers
(ADR 0004), and the compose network is what makes that safe.

## API gateway

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| S | Forged token | RS256 from Keycloak's JWKS, or HS256 dev tokens only when enabled; each issuer locked to its algorithm and key, so the HS256/public-key confusion attack fails. Audience required. Tested (`MultiIssuerTest`). |
| S | Dev tokens accepted in production | The `prod` profile refuses to start with them on. Tested, and checked on a real container (ADR 0016). |
| S | Client sends `X-User-Id` to act as someone else | Stripped from every request before routing, then set from the verified token. Tested (`UserContextHeadersFilterTest`, `TicketRouteTest`). |
| T | A browser page on another origin calls the API | CORS allows only configured origins, none by default, without credentials. Bearer tokens aren't sent automatically, so CSRF doesn't apply. Tested (`EdgeHardeningTest`). |
| R | A caller denies a request | JSON access to traces and logs with trace ids (ADR 0017), but no dedicated audit log. **Accepted**: nothing here is regulated; add one before handling more than test payments. |
| I | Traffic read in transit | **Deployment**: plain HTTP locally. TLS at the ingress in Part B. |
| I | Responses cached or framed | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, CSP `default-src 'none'`. Tested. Swagger UI runs without CSP, since it needs its own scripts. **Accepted**: it serves only public API docs. |
| D | Oversized or endless request bodies | 1 MB by default, tighter on some routes. `RequestSize` only reads `Content-Length`, so chunked bodies without one get a 411. Tested. |
| D | One caller floods the API | Redis token buckets per subject or IP, fail-closed for seat holds (ADR 0010). Tested. A distributed flood isn't stopped here: **accepted** locally, needs an edge provider in production. |
| E | A customer calls operator endpoints | Route rules at the gateway, roles from the token only. Tested, including against a real Keycloak. |

## Payments webhook

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| S | A forged "payment succeeded" | Stripe's signature over the raw body, with a timestamp tolerance. The gateway forwards the body byte for byte and adds no identity (ADR 0006). Tested (`WebhookRouteTest`, `test_webhooks.py`). |
| T | Body altered in transit | Same signature check. |
| R | Disputed payment | The provider's event id and the booking's status changes are stored, and each change is published as an event. |
| I | The webhook endpoint reveals bookings | It answers only with an acknowledgement. |
| D | Webhook flood | Not rate limited, on purpose: a 429 would only delay real payments. 256 KB body limit, and a bad signature is refused before any database work. **Accepted**. |
| D, T | Replayed event | Deduplicated by the provider's event id; replaying a valid event changes nothing. Tested. |
| E | Paying for less than the price | The amount comes from the server's price snapshot, never the client. |

## Check-in tokens

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| S | A forged ticket | Ed25519 signature; booking-service holds public keys only, from the JWKS. Only the render worker can read the private key. Tested. |
| S | A retired or attacker's key | Unknown `kid` triggers a refetch at most every 30 s, then a refusal. Retired keys leave the JWKS (runbook: `docs/runbooks/ticket-key-rotation.md`). Tested. |
| T | Ticket edited | Signature. |
| R | "I never checked in" | Each check-in records the operator, the time and the booking. |
| I | A photographed QR code | The token holds only a booking reference. It works once: **accepted**, whoever scans first boards, as with any paper ticket. |
| D | Scanner floods, or the JWKS endpoint is down | Rate limit per operator. A short outage serves cached keys; beyond that, unknown keys answer 503. |
| E | A customer scans tickets | Check-in needs OPERATOR or ADMIN. Tested. |

## Outbox and messaging

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| S | Anyone on the network publishes fake events | Broker credentials, and it isn't reachable from outside. **Accepted**: services share one RabbitMQ user locally. **Deployment**: per-service users and NetworkPolicies. |
| T | A row altered before relay | Needs database access, which each service alone has (ADR 0001). |
| R | Which change caused this event? | `eventId`, `occurredAt`, and now the `traceparent` of the transaction that wrote the row (ADR 0017). |
| I | Personal data in events | `booking.*` carries the contact email by design (ADR 0014), and outbox rows live 7 days. **Accepted** on one private broker; revisit for GDPR before real customers. |
| I | Trace context leaks data | `traceparent` holds only random ids. SQL spans carry statements with placeholders, not values. |
| D | Poison message blocks a queue | Retry, then DLQ, and an alert on DLQ depth > 0. Tested. |
| D | Broker down | Events wait in the outbox and go out afterwards, without loss. Relay lag has an alert. Tested. |
| E | A consumer acts on a forged event | Same answer as the first row. Consumers validate events and dedupe by id, but don't authenticate the sender. |

## Admin endpoints

Keycloak's admin console, notification-service's operator API, RabbitMQ's UI, Grafana, Flower, MinIO's console.

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| S | Default or leaked credentials | Generated secrets outside git (`scripts/keycloak_secrets.py`, `.env`), gitleaks over the whole history in CI. Grafana's image defaults to `admin`/`admin`: **accepted** only because it binds to 127.0.0.1; change it before exposing it. |
| T | An admin action changes state unnoticed | Actuator is read-only (ADR 0012). Re-queuing a notification needs OPERATOR or ADMIN and is logged. |
| R | Who changed the realm? | Keycloak's admin events are off by default. **Accepted** locally; turn them on in production. |
| I | Consoles reachable from elsewhere | All published on 127.0.0.1 only; the application services aren't published at all. **Deployment**: NetworkPolicies and no public route. |
| D | Metrics scrapes abused | `/metrics` and `/actuator/prometheus` aren't routed by the gateway. |
| E | Container escape after a compromise | Non-root, no capabilities, no-new-privileges, read-only root filesystem and memory-backed `/tmp` on application containers. Data stores keep the few capabilities their entrypoints need. Their limits are in compose. |

## Supply chain

| | Threat | Mitigation or accepted risk |
| --- | --- | --- |
| T | A vulnerable dependency or base image | Trivy on every image in CI, failing on a CRITICAL with a fix available. Dependabot weekly for Maven, pip, Docker, compose and Actions. An SBOM per image, kept as a build artifact. |
| T | A compromised CI action | Third-party actions with job access are pinned by commit SHA. First-party `actions/*` and `docker/*` follow major tags: **accepted**, for readability and because Dependabot tracks them. |
| I | A secret committed by accident | Gitleaks over the full history in CI; reviewed findings are listed by exact fingerprint in `.gitleaksignore`, so a new one still fails. |
