"""Vector document types."""

from dataclasses import dataclass, field


@dataclass
class VectorDocument:
    """A document with optional pre-computed vector."""

    id: str
    content: str
    metadata: dict = field(default_factory=dict)
    vector: list[float] | None = None
