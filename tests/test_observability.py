"""Tests for observability module."""

from fleet_mem.observability import get_tracer, hash_content


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
