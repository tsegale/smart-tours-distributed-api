"""Structured logs: one JSON object per line, with the ids that tie a line to an event, a booking and a task.

Pass ids as `extra={"event_id": ..., "booking_id": ..., "notification_id": ...}`; the Celery task id is added
automatically inside a task. A log pipeline (Phase 5) can then follow one booking from event to sent email.
"""

import json
import logging
from datetime import UTC, datetime

CONTEXT_FIELDS = ("event_id", "booking_id", "notification_id", "task_id", "task_name")


class _TaskContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        from celery import current_task  # optional at import time: the bridge and API log without Celery

        task = current_task
        if task and task.request and task.request.id and not hasattr(record, "task_id"):
            record.task_id = task.request.id
            record.task_name = task.name
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(_TaskContext())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
