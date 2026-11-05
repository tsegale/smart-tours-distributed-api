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
| Breaker open | A provider's circuit breaker open over 80% of the last 5 minutes | 1 min |
| Failed notifications | Any notification in status FAILED | 1 min |

## Fire drill

An alert nobody has seen fire may be unable to. `scripts/alert_fire_drill.py` causes what each rule watches for and
waits until Grafana reports it firing. Every rule fired on 2026-11-05, on the Docker VM described in
[the memory budget](benchmarks/notifications.md#memory-budget).

| Alert | Fault caused | Fired at (UTC) | After | Labelled |
| --- | --- | --- | ---: | --- |
| DLQ not empty | Poison message on `availability.changed` | 18:35:07 | 116 s | `core.availability-summary.dlq` |
| Outbox relay lag | `booking-outbox-relay` stopped, then a departure created | 18:38:15 | 163 s | `booking-service` |
| Search availability burn | `tours-db` stopped under search traffic | 18:45:13 | 363 s | |
| Search latency burn | Same outage | 18:45:13 | active from 18:41:00 | |
| Booking availability burn | `bookings-db` stopped under booking traffic: answered 500 | 18:52:44 | active from 18:49:00, see below | |
| Circuit breaker open | Provider stubs answering errors, weather requests continuing | 20:04:41 | 321 s | `open-meteo-archive` |
| Email delivery burn | Email worker paused, five confirmations, their status change moved 10 minutes back | 21:23:41 | 176 s | |
| Notifications failed | Mailpit refusing recipients with 550 | 21:25:41 | 91 s | |

**Two rules couldn't fire, or nearly:**
- **Circuit breaker open** was `state="open" == 1` for 5 minutes. A breaker under sustained failure goes
  `OPEN -> HALF_OPEN -> OPEN` every 30 s here, which reset that timer every time, so it could never fire on the fault it
  exists for. Counting open or half-open as "not recovered" fired, but then kept firing on a healthy provider, see
  below. The rule now measures time spent open: over 80% of the last 5 minutes.
- **Search latency burn** first matched `le="500.0"`, which no series has: this one was found before the drill by querying
  real data. The label is `le="500"`. `le="300"` on the email rule and `task` on the Celery panel were checked against
  real series and are right.

**What to know when reading these:**
- *Booking* fired 3 s into its recorded run, because it was already firing from an earlier attempt at the same outage,
  whose script run failed on a Grafana timeout after the fault was on. Its active time, 18:49:00, is in that outage.
- *Email* used a shortcut: rather than wait 10 real minutes, the booking's status change was moved back in the database
  while the worker was paused. Everything after that is the real pipeline and metric.
- *A first event in a new process is invisible to `rate()`*, which starts from the first sample. Burn rates need a
  second late email or failed request to move, so the drill sends one on-time email first and pauses the worker rather
  than restarting it. A sustained problem still fires. One isolated failure right after a restart doesn't.
- *A breaker stuck half-open must not alert.* A breaker stays half-open until a call probes it, so a provider that
  recovered while nothing called it looked unrecovered indefinitely. The drill's first version of this rule fired on
  `open-meteo-archive` for exactly that reason. Measuring time spent open doesn't have that problem.
- *The drill found a behaviour to fix:* during the `tours-db` outage all 144 searches got no answer within the client's
  15 s. Search now fails in about 2 s with a retryable 503 (`DatabaseOutageIntegrationTest`), booking likewise, and the
  gateway answers 504 for a hung service.
- Permanent SMTP failures also dead-letter, so *Notifications failed* comes with *DLQ not empty* on
  `notifications.tasks.dlq`. The drill leaves that message there; purge it after looking at it.

## Not done yet

Nothing here sends a notification yet: alerts show in Grafana. Routing them somewhere a person will see them belongs to
the deployment (Part B), since it needs an account or an endpoint.
