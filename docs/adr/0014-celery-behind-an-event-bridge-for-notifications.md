# 14. Celery behind an event bridge for notifications

Date: 2026-11-04 · Status: Accepted

## Context
Phase 4 sends customers email for every booking lifecycle event (confirmed with a PDF ticket, cancelled, refunded,
hold expired) and a reminder the day before departure. The work is slow and failure-prone in ways request handling
isn't: rendering a PDF takes a second of CPU, SMTP servers defer and refuse, and a reminder is a scheduled job. It
needs a worker pool with retries, backoff, rate limits, a dead-letter path and a scheduler. booking-service already
publishes the lifecycle events through its outbox (ADR 0008); this ADR is about how they become emails.

## Decision
A new **notification-service** (Python 3.12, its own Postgres, Alembic, ADR 0001) with four processes from one
image: a FastAPI app (health, metrics, admin), an **event bridge**, **Celery workers**, and **Celery Beat**.

    booking-service outbox ──► smarttours.events (vhost /) ──► notifications.booking-events (quorum, retry, DLQ)
                                                                          │ event bridge: record, commit, enqueue
                                                                          ▼
                                       Celery broker: RabbitMQ vhost "celery" ──► notifications.tasks (DLX → DLQ)
                                                                          │ workers: render_ticket, send_email
                                                                          ▼
                                                     MinIO (ticket PDFs)  ·  SMTP (Mailpit in dev)

### Why not consume domain events with Celery directly
- **Different contracts.** Domain events are a published, versioned interface shared by several services
  (docs/events.md), routed by a topic exchange to consumer-owned queues with their own retry and DLQ (ADR 0005).
  Celery task messages are an internal implementation detail: Celery's own envelope, its own exchange and queue
  conventions, its own retry semantics. Pointing Celery at `smarttours.events` would make Celery's message format
  part of other services' contract, or force booking-service to publish Celery tasks.
- **Separate failure domains.** Celery's broker is the same RabbitMQ on **its own vhost** (`celery`): its queues,
  permissions and churn (a retry is a new message) never touch the domain exchange, and the vhost can move to its
  own broker later without changing anyone else.
- **The bridge is where idempotency belongs.** Deciding "do we owe this customer an email" is a database
  transaction; running it is a task. The bridge does the first and hands the second to Celery.

### Three idempotency layers, one transaction
The bridge records each event in one transaction, then commits, then enqueues, then acks:
1. `processed_events` (unique `eventId`): the same event delivered twice is a no-op the second time.
2. `booking_snapshots`, updated only by a **newer** event (`occurredAt`): an overtaken event (a confirmation that
   arrives after its cancellation, possible across relay batches) changes nothing and sends nothing.
3. `notifications` unique `(booking_id, type)`: one confirmation per booking, whatever creates it. The same
   constraint makes reminders idempotent: an overlapping window, or a second Beat instance, creates nothing.

Enqueueing after the commit can fail (the Celery broker is down). The notification then stays `PENDING`, and the
`redispatch_pending` Beat job enqueues it again once it is five minutes old. Enqueueing before the commit would
risk a task for a row that rolled back; this way the worst case is a delay.

### At-least-once email, and what keeps it to once
- `send_email` locks the notification row and checks it is still `PENDING`; it commits `SENT` after the SMTP
  server accepts the message. A second copy of the task (redelivery after a worker crash, a re-dispatch) waits on
  the lock and then finds it sent. The lock waits rather than skips: the delivery tests caught a version that
  skipped a row still locked by the dispatcher, and so silently dropped the email.
- What remains: a worker that dies **after** the SMTP server accepted the message but **before** the commit sends
  it again on redelivery. Every attempt carries the same deterministic `Message-ID`
  (`<confirmation.18.st-7k3q9p2m@…>`), so mail clients and providers that deduplicate on it show one. This is
  at-least-once delivery, by design: losing a ticket email is worse than a rare duplicate.
- A notification that no longer applies when its turn comes (a confirmation for a booking cancelled since) is
  marked `SKIPPED`.

### Celery hardening
- `task_acks_late` + `task_reject_on_worker_lost`: a task's message is acked after the task finishes; a worker
  killed mid-task returns it to the broker.
- `task_acks_on_failure_or_timeout = False`: a task that crashes is rejected into the dead-letter queue, not acked
  into oblivion. `worker_prefetch_multiplier = 1`: a slow PDF doesn't park other tasks behind it.
- Retries with exponential backoff and full jitter, **for transient errors only**: SMTP 4xx, connection errors,
  object-storage errors. SMTP 5xx (a refused recipient) fails the notification at once. After `max_retries` the
  notification is `FAILED` and the task message is rejected into `notifications.tasks.dlq`. An operator can put a
  failed notification back with `POST /admin/notifications/{id}/retry`.
- A per-worker rate limit on `send_email` (`SEND_RATE_LIMIT`, 10/s by default), the knob a provider's sending quota
  needs.
- Structured JSON logs carrying `event_id`, `booking_id`, `notification_id` and the Celery `task_id`.
- The task queue is a classic durable queue with a dead-letter exchange. Quorum queues would need Celery's native
  delayed delivery for countdown retries; with one broker node that buys nothing yet.

### Beat
Every 15 minutes: reminders for confirmed departures 24 h ± 20 min ahead (the window is wider than half the
interval, so no departure falls between runs; the unique constraint absorbs the overlap), shown in the tour's time
zone. Every 5 minutes: re-dispatch stuck notifications. Every 30 s: the ticket outbox relay (ADR 0015).
**Beat must be a singleton**: two Beat processes enqueue every job twice. Because every job is idempotent, an
overlap during a deploy sends nothing twice, but the compose service must never be scaled.

### Email
An `EmailSender` protocol with one implementation, SMTP (Mailpit in development, any relay in production).
Multipart text + HTML from Jinja2 templates (HTML escaped, text not), the ticket attached to confirmations. An SES or
SendGrid adapter implements the same protocol and is chosen by configuration.

## Consequences
- Four new processes and a database, behind the `notifications` compose profile; the default stack is unchanged
  (memory: docs/benchmarks/notifications.md).
- Email is eventually consistent with bookings: measured in docs/benchmarks/notifications.md.
- Two RabbitMQ consumers' worth of topology to operate (the bridge's queue, retry queue and DLQ, and Celery's
  queue and DLQ), all visible in the management UI and in Flower (`--profile flower`).
- Duplicate emails remain possible in one narrow window (above) and are made recognisable, not impossible.

## Update (Phase 5): renders and emails on separate queues
The Phase 4 benchmark put every task on one FIFO queue: 500 confirmations queued 500 renders, each render's email
went in behind the remaining renders, and the median email arrived 344 s after its event. Since Phase 5 there are
three queues on the same broker, each dead-lettering into the same DLQ:

| Queue | Tasks | Worker |
| --- | --- | --- |
| `notifications.tasks.render` | `render_ticket` | `notification-worker` (the only process with the signing key) |
| `notifications.tasks.email` | `send_email` | `notification-email-worker` |
| `notifications.tasks` | Beat's reminder, re-dispatch and outbox jobs | `notification-email-worker` |

A confirmation is still a chain (render, then send) per booking, so its email is published when its own ticket is
rendered and then waits only for other emails. Renders scale with `notification-worker`, sends with
`notification-email-worker`, independently. Numbers before and after: docs/benchmarks/notifications.md.
