"""Language-aware AST splitting using tree-sitter."""

from __future__ import annotations

from dataclasses import dataclass

from tree_sitter import Language, Parser


@dataclass
class ASTChunk:
    """A chunk extracted from an AST node."""

    content: str
    start_line: int
    end_line: int
    chunk_type: str  # e.g. "function", "class", "method", "module_header"
    name: str | None = None


# Node types to extract per language
_EXTRACTABLE_TYPES: dict[str, set[str]] = {
    "python": {
        "function_definition",
        "class_definition",
        "decorated_definition",
    },
    "typescript": {
        "function_declaration",
        "class_declaration",
        "method_definition",
        "lexical_declaration",  # top-level const/let arrow functions
        "export_statement",
    },
    "javascript": {
        "function_declaration",
        "class_declaration",
        "method_definition",
        "lexical_declaration",
        "export_statement",
    },
    "go": {
        "function_declaration",
        "method_declaration",
        "type_declaration",
    },
    "rust": {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
    },
}

# Map language name to the callable that returns the language pointer
_LANGUAGE_LOADERS: dict[str, tuple[str, str]] = {
    "python": ("tree_sitter_python", "language"),
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "javascript": ("tree_sitter_javascript", "language"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
}

# Cache loaded languages
_language_cache: dict[str, Language] = {}
_parser_cache: dict[str, Parser] = {}


def _get_parser(lang_name: str) -> Parser | None:
    """Get or create a tree-sitter parser for the given language."""
    if lang_name in _parser_cache:
        return _parser_cache[lang_name]

    loader = _LANGUAGE_LOADERS.get(lang_name)
    if loader is None:
        return None

    module_name, func_name = loader
    try:
        import importlib

        mod = importlib.import_module(module_name)
        lang_fn = getattr(mod, func_name)
        language = Language(lang_fn())
    except (ImportError, AttributeError):
        return None

    parser = Parser(language)
    _parser_cache[lang_name] = parser
    _language_cache[lang_name] = language
    return parser


def _node_name(node) -> str | None:
    """Extract the name of a definition node."""
    for child in node.children:
        if child.type in ("identifier", "type_identifier", "name"):
            return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
        # For decorated definitions / export statements, recurse into inner definition
        if child.type in (
            "function_definition",
            "class_definition",
            "function_declaration",
            "class_declaration",
            "export_statement",
            "decorated_definition",
            "lexical_declaration",
        ):
            return _node_name(child)
        # For property_identifier (JS/TS method names)
        if child.type == "property_identifier":
            return child.text.decode("utf-8") if isinstance(child.text, bytes) else child.text
    return None


def _chunk_type_from_node(node_type: str) -> str:
    """Map tree-sitter node type to a simpler chunk_type label."""
    if "class" in node_type:
        return "class"
    if "function" in node_type or "method" in node_type:
        return "function"
    if "impl" in node_type:
        return "impl"
    if "struct" in node_type:
        return "struct"
    if "enum" in node_type:
        return "enum"
    if "type" in node_type:
        return "type"
    if "export" in node_type:
        return "export"
    if "lexical" in node_type:
        return "variable"
    return "definition"


def _text_of(node, source_bytes: bytes) -> str:
    """Extract the source text for a node."""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def split_ast(
    source: str,
    language: str,
    *,
    max_chunk_size: int = 5000,
) -> list[ASTChunk]:
    """Split source code into AST-aware chunks.

    Extracts top-level definitions (functions, classes, etc.) as individual
    chunks. Code between definitions is grouped as "module_header" or
    "module_body" chunks. Falls back to returning the whole file as a single
    chunk if the language is not supported or parsing fails.

    Args:
        source: The source code to split.
        language: Language name (python, typescript, javascript, go, rust).
        max_chunk_size: Maximum character size for any chunk. Large definitions
            are kept whole (not sub-split) to preserve AST coherence.

    Returns:
        List of ASTChunk with content, line ranges, and type metadata.
    """
    if not source.strip():
        return []

    parser = _get_parser(language)
    if parser is None:
        # Unsupported language: return whole file as one chunk
        line_count = source.count("\n") + 1
        return [
            ASTChunk(
                content=source,
                start_line=1,
                end_line=line_count,
                chunk_type="file",
            )
        ]

    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node

    extractable = _EXTRACTABLE_TYPES.get(language, set())
    chunks: list[ASTChunk] = []
    last_end_byte = 0

    for child in root.children:
        if child.type in extractable:
            # Capture any "gap" text before this node (imports, comments, etc.)
            if child.start_byte > last_end_byte:
                gap_text = source_bytes[last_end_byte : child.start_byte].decode(
                    "utf-8", errors="replace"
                )
                if gap_text.strip():
                    gap_start = source_bytes[:last_end_byte].count(b"\n") + 1
                    gap_end = source_bytes[: child.start_byte].count(b"\n") + 1
                    chunks.append(
                        ASTChunk(
                            content=gap_text,
                            start_line=gap_start,
                            end_line=gap_end,
                            chunk_type="module_header",
                        )
                    )

            node_text = _text_of(child, source_bytes)
            # start_point is (row, col), rows are 0-indexed
            start_line = child.start_point[0] + 1
            end_line = child.end_point[0] + 1

            chunks.append(
                ASTChunk(
                    content=node_text,
                    start_line=start_line,
                    end_line=end_line,
                    chunk_type=_chunk_type_from_node(child.type),
                    name=_node_name(child),
                )
            )
            last_end_byte = child.end_byte

    # Trailing code after last definition
    if last_end_byte < len(source_bytes):
        trailing = source_bytes[last_end_byte:].decode("utf-8", errors="replace")
        if trailing.strip():
            trail_start = source_bytes[:last_end_byte].count(b"\n") + 1
            trail_end = source.count("\n") + 1
            chunks.append(
                ASTChunk(
                    content=trailing,
                    start_line=trail_start,
                    end_line=trail_end,
                    chunk_type="module_body",
                )
            )

    # If no extractable nodes found, return whole file
    if not chunks:
        line_count = source.count("\n") + 1
        return [
            ASTChunk(
                content=source,
                start_line=1,
                end_line=line_count,
                chunk_type="file",
            )
        ]

    return chunks


def supported_languages() -> list[str]:
    """Return list of languages with tree-sitter support installed."""
    supported = []
    for lang in _LANGUAGE_LOADERS:
        if _get_parser(lang) is not None:
            supported.append(lang)
    return supported
