"""Celery: `celery -A app.celery_app worker` and `celery -A app.celery_app beat` (ADR 0014).

The broker is RabbitMQ, on a vhost of its own (CELERY_BROKER_URL ends in /celery), so task messages never share a
vhost, queue or permission with domain events.

Hardening:
- task_acks_late + task_reject_on_worker_lost: a task's message is acked only after the task finishes, and a
  worker killed mid-task (OOM, deploy) gives it back to the broker instead of losing it.
- task_acks_on_failure_or_timeout=False: a task that crashes is rejected, not acked, so it dead-letters to
  notifications.tasks.dlq rather than vanishing. Exhausted retries and permanent SMTP errors reject explicitly.
- worker_prefetch_multiplier=1: a worker holds one message per process, so a slow PDF doesn't park others behind it.
- Retries with exponential backoff and jitter, for transient errors only (app.tasks).

Three queues, so slow work never delays fast work:
- <base>.render: render_ticket only, served by its own worker pool (notification-worker in compose).
- <base>.email: send_email only, served by notification-email-worker. A burst of 500 confirmations queues 500
  renders, but each email is enqueued by its own booking's render (a chain) and then waits only for other emails.
- <base>: everything else (Beat's reminder, re-dispatch and outbox jobs, all short), also notification-email-worker.
All three dead-letter into one DLQ; a dead-lettered message's x-death header names the queue it came from.

The queues are classic durable queues with a dead-letter exchange. Quorum queues would need Celery's native
delayed delivery for countdown retries; for one worker pool on one broker that buys nothing yet.
"""

import logging

from celery import Celery
from celery.signals import setup_logging, worker_init
from kombu import Exchange, Queue

from .config import get_settings
from .logs import configure_logging

settings = get_settings()

TASK_QUEUE = settings.celery_task_queue
RENDER_QUEUE = f"{TASK_QUEUE}.render"
EMAIL_QUEUE = f"{TASK_QUEUE}.email"
DEAD_LETTER_EXCHANGE = f"{TASK_QUEUE}.dlx"
DEAD_LETTER_QUEUE = f"{TASK_QUEUE}.dlq"


def _queue(name: str) -> Queue:
    return Queue(
        name,
        Exchange(TASK_QUEUE, type="direct", durable=True),
        routing_key=name,
        durable=True,
        queue_arguments={"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE},
    )


app = Celery("notifications", broker=settings.celery_broker_url, include=["app.tasks"])
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    task_time_limit=180,
    task_soft_time_limit=120,
    task_default_queue=TASK_QUEUE,
    task_default_exchange=TASK_QUEUE,
    task_default_routing_key=TASK_QUEUE,
    task_queues=(_queue(TASK_QUEUE), _queue(RENDER_QUEUE), _queue(EMAIL_QUEUE)),
    task_routes={
        "notifications.render_ticket": {"queue": RENDER_QUEUE},
        "notifications.send_email": {"queue": EMAIL_QUEUE},
    },
    broker_connection_retry_on_startup=True,
    worker_hijack_root_logger=False,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "schedule-reminders": {"task": "notifications.schedule_reminders", "schedule": settings.reminder_interval_s},
        "redispatch-pending": {"task": "notifications.redispatch_pending", "schedule": 300.0},
        "relay-outbox": {"task": "notifications.relay_outbox", "schedule": settings.outbox_relay_interval_s},
    },
)


def declare_dead_letter_queue() -> None:
    """The DLX and DLQ the task queue dead-letters into. Not in task_queues: workers must not consume it."""
    with app.connection_for_write() as connection:
        channel = connection.default_channel
        exchange = Exchange(DEAD_LETTER_EXCHANGE, type="fanout", durable=True)
        exchange.declare(channel=channel)
        Queue(DEAD_LETTER_QUEUE, exchange, durable=True).declare(channel=channel)


@setup_logging.connect
def _logging(**_) -> None:
    configure_logging()


@worker_init.connect
def _declare_topology(**_) -> None:
    declare_dead_letter_queue()
    logging.getLogger(__name__).info("Dead-letter queue %s ready", DEAD_LETTER_QUEUE)
