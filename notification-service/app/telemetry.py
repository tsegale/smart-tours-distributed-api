"""JSON logs and OpenTelemetry (ADR 0017). The same file in every Python service.

Logs are JSON on stdout, always, each line with its trace_id and span_id when there is a trace. Traces, metrics and
logs go out over OTLP only when OTEL_EXPORTER_OTLP_ENDPOINT is set (the observability profile); without it this
exports nothing and costs next to nothing, and current_traceparent() returns None.

The outbox breaks a trace: the row is written in one process and published later by another. So the row keeps the W3C
traceparent of the transaction that wrote it (current_traceparent), and the relay publishes inside it (continuing).
"""

import json
import logging
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from opentelemetry import context, metrics, propagate, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.instrumentation.pika import PikaInstrumentor
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_VERSION = os.environ.get("SERVICE_VERSION", "1.0.0")
TRACEPARENT = "traceparent"
# Health checks and scrapes every few seconds would bury every real trace.
UNTRACED_URLS = "health,metrics"

_service = "unknown"
_tracer_provider: TracerProvider | None = None


class _TraceContext(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            record.trace_id = format(span.trace_id, "032x")
            record.span_id = format(span.span_id, "016x")
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, context_fields: Iterable[str] = ()) -> None:
        super().__init__()
        self._context_fields = ("trace_id", "span_id", *context_fields)

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service": _service,
            "version": SERVICE_VERSION,
        }
        for field in self._context_fields:
            value = getattr(record, field, None)
            if value is not None:
                entry[field] = value
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(
    service: str,
    level: int = logging.INFO,
    context_fields: Iterable[str] = (),
    filters: Iterable[logging.Filter] = (),
) -> None:
    global _service
    _service = service
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(context_fields))
    handler.addFilter(_TraceContext())
    for extra_filter in filters:
        handler.addFilter(extra_filter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def _process_memory(_: CallbackOptions) -> Iterator[Observation]:
    """Resident memory of this process, the Python counterpart of the JVM's memory metrics."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            yield Observation(int(statm.read().split()[1]) * os.sysconf("SC_PAGE_SIZE"))
    except OSError:
        return  # not on Linux: nothing to report


def configure_telemetry(service: str, *, database: bool = True) -> None:
    """Start exporting, if an endpoint is configured. Call once per process, after any fork.

    database=False leaves out SQL spans, for loops that poll: a relay's empty SELECT every second is not a trace.
    """
    global _tracer_provider
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or _tracer_provider is not None:
        return
    resource = Resource.create({"service.name": service, "service.version": SERVICE_VERSION})

    _tracer_provider = TracerProvider(resource=resource)
    _tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_tracer_provider)

    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())])
    )
    metrics.get_meter("smarttours").create_observable_gauge(
        "process.memory.usage", callbacks=[_process_memory], unit="By", description="Resident memory of the process"
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(LoggingHandler(logger_provider=logger_provider))

    PikaInstrumentor().instrument()
    if database:
        PsycopgInstrumentor().instrument()


def instrument_fastapi(app: FastAPI) -> None:
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        FastAPIInstrumentor.instrument_app(app, excluded_urls=UNTRACED_URLS)


def current_traceparent() -> str | None:
    """The W3C traceparent of the current span, or None outside a trace."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get(TRACEPARENT)


@contextmanager
def continuing(traceparent: str | None) -> Iterator[None]:
    """Run the block inside the trace a stored traceparent belongs to. No traceparent: leave the context alone."""
    if not traceparent:
        yield
        return
    token = context.attach(propagate.extract({TRACEPARENT: traceparent}))
    try:
        yield
    finally:
        context.detach(token)
