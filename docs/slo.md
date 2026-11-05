# Service level objectives

What "working" means for the three things a customer would notice, measured from the telemetry the services already
emit. These are starting targets for a small service, set from what the benchmarks show the system can do
([search](benchmarks/search.md), [notifications](benchmarks/notifications.md)) and not yet validated against real
traffic. Expect to tune them.

All windows are 30 days. A target of 99.9% allows about 43 minutes of failure per 30 days.

| Journey | Indicator | Objective | Measured from |
| --- | --- | --- | --- |
| Search | Availability: requests to `/api/tours/search` that aren't a 5xx | 99.9% | tour-core `http_server_requests` |
| Search | Latency: requests served within 500 ms | 99% | tour-core `http_server_requests` |
| Booking | Availability: requests to `POST /api/bookings` that aren't a 5xx | 99.5% | booking-service `http_server_duration` |
| Booking | Latency: requests served within 1 s | 95% | booking-service `http_server_duration` |
| Email | Delivery: emails sent within 5 minutes of the booking's status change | 95% | `notification_delivery_duration` |

**Why these.** Search is the read path and is cheap to keep up, so it gets the tightest target. Booking depends on a
payment provider's checkout call, which this service doesn't control, so it allows more failure and more time. A 409
for a sold-out departure or a 4xx generally is the service working, so only 5xx counts against availability.

**Email.** A burst of 500 confirmations took up to about 6 minutes on one render process
([benchmark](benchmarks/notifications.md)), so 5 minutes at 95% tolerates a burst now and then, but not a stuck queue.
Reminders are left out: they are scheduled for later, not late.

## Alerts

Burn-rate alerts rather than plain thresholds: they page in proportion to how fast the error budget is going, and
need a short and a long window to agree, so a brief blip doesn't page and a resolved incident stops paging. A burn rate
of 14.4 over 1 hour spends 2% of a 30-day budget; both windows above it is the standard fast-burn pair from the
Google SRE workbook. Rules are code: `infra/observability/alerting/rules.yaml`.

| Alert | Fires when | For |
| --- | --- | --- |
| Search availability burn | 5xx ratio over 14.4 x 0.1%, over both 1 h and 5 min | 2 min |
| Search latency burn | Slow ratio over 14.4 x 1%, over both 1 h and 5 min | 2 min |
| Booking availability burn | 5xx ratio over 14.4 x 0.5%, over both 1 h and 5 min | 2 min |
| Email delivery burn | Late ratio over 14.4 x 5%, over both 1 h and 5 min | 2 min |
| DLQ not empty | Any `*.dlq` queue holds a message | 1 min |
| Relay lag | Oldest unpublished outbox event older than 60 s | 1 min |
| Breaker open | A provider's circuit breaker open | 5 min |
| Failed notifications | Any notification in status FAILED | 1 min |

Nothing here sends a notification yet: alerts show in Grafana. Routing them somewhere a person will see them belongs to
the deployment (Part B), since it needs an account or an endpoint.
