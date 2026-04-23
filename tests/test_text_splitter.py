from unittest.mock import MagicMock, patch

from fleet_mem.splitter.text_splitter import TextChunk, split_text


def test_split_text_empty_input():
    """Verify that empty or whitespace-only input returns an empty list."""
    assert split_text("") == []
    assert split_text("   ") == []
    assert split_text("\n\n") == []


def test_split_text_single_chunk():
    """Verify that text smaller than chunk_size produces a single chunk."""
    text = "Hello world"
    chunks = split_text(text, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0].content == "Hello world"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 1


def test_split_text_multiple_lines():
    """Verify that multiple lines are correctly mapped to line numbers."""
    text = "Line 1\nLine 2\nLine 3"
    # Small chunk size to force splits
    chunks = split_text(text, chunk_size=10, overlap=0)

    assert len(chunks) >= 2
    assert chunks[0].content.startswith("Line 1")
    assert chunks[0].start_line == 1

    # Find the chunk containing Line 2
    line2_chunk = next(c for c in chunks if "Line 2" in c.content)
    assert line2_chunk.start_line == 2


def test_split_text_newline_boundary():
    """Verify that the splitter prefers breaking at newline boundaries."""
    text = "First line.\nSecond line."
    # chunk_size (15) would cut "Second line." in the middle,
    # but it should snap to the newline at index 11.
    chunks = split_text(text, chunk_size=15, overlap=0)

    assert chunks[0].content == "First line.\n"
    assert chunks[1].content == "Second line."


def test_split_text_with_overlap():
    """Verify that overlapping chunks contain the expected duplicated content."""
    text = "abcdefghij"
    # chunk 1: 0-5 "abcde"
    # overlap 2: next start = 5 - 2 = 3
    # chunk 2: 3-8 "defgh"
    chunks = split_text(text, chunk_size=5, overlap=2)

    assert chunks[0].content == "abcde"
    assert chunks[1].content == "defgh"
    assert chunks[2].content == "ghij"


def test_split_text_large_text_no_newlines():
    """Verify behavior when no newline is available to break on."""
    text = "a" * 100
    chunks = split_text(text, chunk_size=30, overlap=10)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.content) <= 30
        assert chunk.start_line == 1
        assert chunk.end_line == 1


def test_text_chunk_dataclass_defaults():
    """Verify the TextChunk dataclass properties."""
    chunk = TextChunk(content="test", start_line=5, end_line=10)
    assert chunk.content == "test"
    assert chunk.start_line == 5
    assert chunk.end_line == 10
    assert chunk.chunk_type == "text"


@patch("fleet_mem.splitter.text_splitter.TextChunk")
def test_mock_dependency_injection(mock_chunk):
    """Example of mocking the TextChunk class if it had complex logic."""
    mock_instance = MagicMock()
    mock_chunk.return_value = mock_instance

    text = "some text"
    split_text(text, chunk_size=100)

    assert mock_chunk.called


def test_split_text_edge_case_end_of_mapping():
    """Verify line mapping doesn't index out of bounds on last character."""
    text = "line1"
    # Forces char_to_line access at the very end
    chunks = split_text(text, chunk_size=len(text), overlap=0)
    assert len(chunks) == 1
    assert chunks[0].end_line == 1


def test_split_text_long_overlap_handling():
    """Verify that overlap < chunk_size still produces correct chunks."""
    text = "abcdefghijklmnop"
    # overlap is close to chunk_size but still smaller — should not loop forever
    chunks = split_text(text, chunk_size=6, overlap=4)
    assert len(chunks) > 0
    # Each chunk should have content
    for chunk in chunks:
        assert len(chunk.content) > 0


def test_split_text_terminates_on_short_prefix_long_body():
    """Regression: short first chunk followed by long no-newline body must terminate.

    Previously converged to a fixed point (start=-300, end=0) and hung forever.
    Reaching the assertion at all is the regression signal.
    """
    text = "a\n" + "x" * 50000
    chunks = split_text(text, chunk_size=2500, overlap=300)
    # Asserting that we return at all is the primary regression signal.
    assert len(chunks) >= 1
    # Reasonable upper bound — advance is at least (chunk_size - overlap) per iter.
    assert len(chunks) <= (len(text) // (2500 - 300)) + 2


def test_split_text_start_stays_nonnegative_after_small_first_chunk():
    """Regression: short newline early forces end <= overlap, which would previously
    drive start negative and trigger Python's negative-index rfind wrap.

    If start went negative, earlier versions hung. Reaching this assertion = bug fixed.
    """
    text = "a\n" + "x" * 3000
    chunks = split_text(text, chunk_size=2500, overlap=300)
    assert len(chunks) >= 1
    # Concatenated non-empty chunks should cover the full text (rough sanity).
    assert sum(len(c.content) for c in chunks) >= len(text)
