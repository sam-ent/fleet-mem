"""OpenTelemetry tracing and structured logging for fleet-mem."""

import logging
import os

import structlog
import xxhash
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_ENABLED = os.environ.get("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
_tracer: trace.Tracer | None = None
_logging_configured = False


def get_tracer() -> trace.Tracer:
    """Get or create the fleet-mem tracer. No-op when OTEL_ENABLED is false."""
    global _tracer
    if _tracer is not None:
        return _tracer

    if _ENABLED:
        provider = TracerProvider()
        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if endpoint:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint)
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("fleet-mem")
    else:
        _tracer = trace.get_tracer("fleet-mem")

    return _tracer


def hash_content(text: str) -> str:
    """Hash content for privacy-safe span attributes."""
    return xxhash.xxh3_64(text.encode()).hexdigest()


def _add_trace_context(logger: logging.Logger, method_name: str, event_dict: dict) -> dict:
    """Inject trace_id and span_id from the current OpenTelemetry context."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def configure_logging() -> None:
    """Configure structlog with trace context injection.

    JSON output when OTEL_ENABLED, human-readable console otherwise.
    Idempotent — safe to call multiple times.
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    renderer = structlog.processors.JSONRenderer() if _ENABLED else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            _add_trace_context,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
