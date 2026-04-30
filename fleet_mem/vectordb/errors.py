"""Vector-database specific exceptions."""

from __future__ import annotations


class DimMismatchError(ValueError):
    """Raised when an embed model's vector dimension doesn't match an existing
    ChromaDB collection's stored dimension.

    Indicates either: (a) the user switched the configured embed model
    (e.g. ``OLLAMA_EMBED_MODEL``) between models with different output
    dimensions, or (b) the model itself was re-pulled with a new version
    that changed output dim (rare but possible).

    Recovery: either revert the model change, OR drop the affected
    collection and re-index with the new model. ChromaDB collections are
    dim-locked at creation; mixed-dim collections are not supported.

    Attributes are exposed for programmatic recovery (e.g. an automated
    re-indexer that catches this error and decides whether to drop +
    rebuild the collection).
    """

    def __init__(
        self,
        *,
        model_name: str,
        model_dim: int,
        collection_name: str,
        collection_dim: int,
    ):
        self.model_name = model_name
        self.model_dim = model_dim
        self.collection_name = collection_name
        self.collection_dim = collection_dim
        super().__init__(
            f"Embed model {model_name!r} produces {model_dim}-dim vectors, "
            f"but collection {collection_name!r} stores {collection_dim}-dim "
            f"vectors. ChromaDB collections are dim-locked at creation. "
            f"Recovery: either revert the model change, OR drop the affected "
            f"collection (e.g. via drop_collection) and re-index with the "
            f"model that produces {model_dim}-dim vectors."
        )
