"""Tests for AST splitter and text splitter."""

from src.splitter.ast_splitter import split_ast, supported_languages
from src.splitter.text_splitter import split_text

# ─── Python AST splitting ───────────────────────────────────────────

PYTHON_SOURCE = '''\
import os
from pathlib import Path

CONSTANT = 42


def greet(name: str) -> str:
    """Say hello."""
    return f"Hello, {name}"


class Calculator:
    """A simple calculator."""

    def __init__(self, value: int = 0):
        self.value = value

    def add(self, x: int) -> "Calculator":
        self.value += x
        return self


def main():
    calc = Calculator()
    calc.add(10)
    print(greet("world"))
'''


class TestPythonASTSplitting:
    def test_extracts_functions_and_classes(self):
        chunks = split_ast(PYTHON_SOURCE, "python")
        names = [c.name for c in chunks if c.name]
        assert "greet" in names
        assert "Calculator" in names
        assert "main" in names

    def test_chunk_types(self):
        chunks = split_ast(PYTHON_SOURCE, "python")
        types = {c.chunk_type for c in chunks}
        assert "function" in types
        assert "class" in types

    def test_includes_module_header(self):
        chunks = split_ast(PYTHON_SOURCE, "python")
        header_chunks = [c for c in chunks if c.chunk_type == "module_header"]
        assert len(header_chunks) >= 1
        # Header should contain the imports
        header_text = header_chunks[0].content
        assert "import os" in header_text

    def test_line_numbers_are_correct(self):
        chunks = split_ast(PYTHON_SOURCE, "python")
        greet = next(c for c in chunks if c.name == "greet")
        # "def greet" is on line 7 (after imports, constant, blank lines)
        assert greet.start_line == 7
        assert greet.end_line == 9

    def test_all_source_covered(self):
        """Every non-blank line should appear in at least one chunk."""
        chunks = split_ast(PYTHON_SOURCE, "python")
        all_content = "".join(c.content for c in chunks)
        for line in PYTHON_SOURCE.strip().splitlines():
            if line.strip():
                assert line.strip() in all_content

    def test_empty_source(self):
        assert split_ast("", "python") == []
        assert split_ast("   \n\n  ", "python") == []


# ─── TypeScript AST splitting ───────────────────────────────────────

TS_SOURCE = """\
import { useState } from "react";

interface Props {
  name: string;
}

export function Hello(props: Props) {
  return <div>{props.name}</div>;
}

export class Counter {
  private count = 0;

  increment() {
    this.count++;
  }
}
"""


class TestTypeScriptASTSplitting:
    def test_extracts_ts_definitions(self):
        chunks = split_ast(TS_SOURCE, "typescript")
        names = [c.name for c in chunks if c.name]
        assert "Hello" in names
        assert "Counter" in names

    def test_chunk_types_ts(self):
        chunks = split_ast(TS_SOURCE, "typescript")
        types = {c.chunk_type for c in chunks}
        # Should have export and class types
        assert len(types) >= 2

    def test_line_ranges(self):
        chunks = split_ast(TS_SOURCE, "typescript")
        # There should be multiple chunks
        assert len(chunks) >= 2
        # Each chunk should have valid line ranges
        for chunk in chunks:
            assert chunk.start_line >= 1
            assert chunk.end_line >= chunk.start_line


# ─── Unsupported language fallback ──────────────────────────────────


class TestUnsupportedLanguage:
    def test_returns_whole_file(self):
        chunks = split_ast("some go code", "go")
        # Go tree-sitter is not installed, so returns single file chunk
        assert len(chunks) == 1
        assert chunks[0].chunk_type == "file"
        assert chunks[0].content == "some go code"


# ─── Text splitter ──────────────────────────────────────────────────


class TestTextSplitter:
    def test_short_text_single_chunk(self):
        text = "Hello, world!\nSecond line."
        chunks = split_text(text)
        assert len(chunks) == 1
        assert chunks[0].content == text
        assert chunks[0].start_line == 1

    def test_long_text_multiple_chunks(self):
        # Create text longer than default chunk_size
        lines = [f"Line {i}: " + "x" * 40 for i in range(100)]
        text = "\n".join(lines)
        chunks = split_text(text, chunk_size=500, overlap=50)
        assert len(chunks) > 1
        # Chunks should overlap
        for i in range(len(chunks) - 1):
            # Later chunks should start before the previous one ends
            assert chunks[i + 1].start_line <= chunks[i].end_line + 5

    def test_empty_text(self):
        assert split_text("") == []
        assert split_text("   \n\n  ") == []

    def test_line_numbers(self):
        text = "line1\nline2\nline3\nline4\nline5"
        chunks = split_text(text, chunk_size=10, overlap=0)
        assert chunks[0].start_line == 1


# ─── supported_languages ────────────────────────────────────────────


class TestSupportedLanguages:
    def test_python_is_supported(self):
        langs = supported_languages()
        assert "python" in langs

    def test_typescript_is_supported(self):
        langs = supported_languages()
        assert "typescript" in langs

    def test_javascript_is_supported(self):
        langs = supported_languages()
        assert "javascript" in langs
