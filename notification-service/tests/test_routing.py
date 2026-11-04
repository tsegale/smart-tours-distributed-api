"""Which queue each task goes to: renders and emails never share one, so emails never wait behind a render backlog."""

import pytest

from app.celery_app import EMAIL_QUEUE, RENDER_QUEUE, TASK_QUEUE
from app.celery_app import app as celery_app


def queue_of(task_name: str) -> str:
    return celery_app.amqp.router.route({}, task_name)["queue"].name


def test_renders_and_emails_have_queues_of_their_own():
    assert queue_of("notifications.render_ticket") == RENDER_QUEUE
    assert queue_of("notifications.send_email") == EMAIL_QUEUE
    assert len({TASK_QUEUE, RENDER_QUEUE, EMAIL_QUEUE}) == 3


@pytest.mark.parametrize(
    "task_name",
    ["notifications.schedule_reminders", "notifications.redispatch_pending", "notifications.relay_outbox"],
)
def test_the_beat_jobs_use_the_default_queue(task_name):
    assert queue_of(task_name) == TASK_QUEUE


def test_a_confirmation_chain_sends_its_email_on_the_email_queue():
    """The email is published by its own render when that finishes, so it waits for that render only."""
    from app.tasks import render_ticket, send_email

    chain = render_ticket.si(1) | send_email.si(1)
    first, second = chain.tasks
    assert queue_of(first.task) == RENDER_QUEUE
    assert queue_of(second.task) == EMAIL_QUEUE


def test_every_queue_dead_letters():
    for queue in celery_app.conf.task_queues:
        assert queue.queue_arguments["x-dead-letter-exchange"] == f"{TASK_QUEUE}.dlx"
