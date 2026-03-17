"""Character-based text splitter for unsupported languages."""

from dataclasses import dataclass


@dataclass
class TextChunk:
    """A chunk of text with metadata."""

    content: str
    start_line: int
    end_line: int
    chunk_type: str = "text"


def split_text(
    text: str,
    chunk_size: int = 2500,
    overlap: int = 300,
) -> list[TextChunk]:
    """Split text into overlapping character-based chunks.

    Returns chunks with line number metadata.
    """
    if not text.strip():
        return []

    lines = text.split("\n")
    chunks: list[TextChunk] = []

    # Build a mapping from character offset to line number
    char_to_line: list[int] = []
    for line_idx, line in enumerate(lines):
        char_to_line.extend([line_idx + 1] * (len(line) + 1))  # +1 for newline

    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))

        # Try to break at a newline boundary
        if end < len(text):
            newline_pos = text.rfind("\n", start, end)
            if newline_pos > start:
                end = newline_pos + 1

        chunk_text = text[start:end]
        if chunk_text.strip():
            start_line = char_to_line[start] if start < len(char_to_line) else 1
            end_offset = min(end - 1, len(char_to_line) - 1)
            end_line = char_to_line[end_offset] if end_offset >= 0 else start_line

            chunks.append(
                TextChunk(
                    content=chunk_text,
                    start_line=start_line,
                    end_line=end_line,
                )
            )

        # Move forward, accounting for overlap
        start = end - overlap if end < len(text) else end

    return chunks
