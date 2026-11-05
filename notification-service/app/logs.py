"""Structured logs: JSON lines (app.telemetry), with the ids that tie a line to an event, a booking and a task.

Pass ids as `extra={"event_id": ..., "booking_id": ..., "notification_id": ...}`; the Celery task id is added
automatically inside a task, and trace_id and span_id whenever there is a trace.
"""

import logging

from .telemetry import configure_logging as configure_json_logging

CONTEXT_FIELDS = ("event_id", "booking_id", "notification_id", "task_id", "task_name")


class _TaskContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from celery import current_task  # optional at import time: the bridge and API log without Celery

        task = current_task
        if task and task.request and task.request.id and not hasattr(record, "task_id"):
            record.task_id = task.request.id
            record.task_name = task.name
        return True


def configure_logging(service: str = "notification-service", level: int = logging.INFO) -> None:
    configure_json_logging(service, level, CONTEXT_FIELDS, (_TaskContext(),))
