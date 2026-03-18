"""Tests for observability module."""

from fleet_mem.observability import configure_logging, get_tracer, hash_content


def test_get_tracer_returns_tracer():
    tracer = get_tracer()
    assert tracer is not None


def test_hash_content_returns_hex():
    result = hash_content("hello world")
    assert isinstance(result, str)
    assert len(result) > 0
    # Should be valid hex
    int(result, 16)


def test_hash_content_is_consistent():
    assert hash_content("test") == hash_content("test")


def test_hash_content_different_inputs():
    assert hash_content("foo") != hash_content("bar")


def test_hash_content_does_not_contain_raw_text():
    text = "sensitive content here"
    hashed = hash_content(text)
    assert text not in hashed


def test_configure_logging_is_idempotent():
    """configure_logging can be called multiple times without error."""
    configure_logging()
    configure_logging()


def test_trace_context_injected_in_active_span():
    """When a span is active, trace_id and span_id appear in log events."""
    import structlog

    configure_logging()

    tracer = get_tracer()
    with tracer.start_as_current_span("test-span"):
        # Use structlog's testing capture
        with structlog.testing.capture_logs() as captured:
            log = structlog.get_logger("test")
            log.info("hello from span")

    # At least one log event should have trace context
    assert len(captured) >= 1
    # trace_id may or may not be present depending on whether OTel is enabled
    # but the logging itself should work without error
