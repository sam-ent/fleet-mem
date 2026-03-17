"""OpenTelemetry tracing for fleet-mem."""

import os

import xxhash
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

_ENABLED = os.environ.get("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")
_tracer: trace.Tracer | None = None


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
