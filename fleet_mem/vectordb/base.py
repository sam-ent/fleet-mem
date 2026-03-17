"""Vector database abstract base class."""

from abc import ABC, abstractmethod

from fleet_mem.vectordb.types import VectorDocument


class VectorDatabase(ABC):
    """Abstract vector database."""

    @abstractmethod
    def create_collection(self, name: str, dimension: int) -> None:
        """Create or get a collection."""

    @abstractmethod
    def has_collection(self, name: str) -> bool:
        """Check if a collection exists."""

    @abstractmethod
    def list_collections(self) -> list[str]:
        """List all collection names."""

    @abstractmethod
    def insert(self, collection: str, documents: list[VectorDocument]) -> None:
        """Insert/upsert documents with pre-computed vectors."""

    @abstractmethod
    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        """Search by vector. Returns list of {id, content, score, metadata}."""

    @abstractmethod
    def delete(self, collection: str, ids: list[str]) -> None:
        """Delete documents by ID."""

    @abstractmethod
    def drop_collection(self, name: str) -> None:
        """Drop a collection entirely."""

    @abstractmethod
    def count(self, collection: str) -> int:
        """Return the number of documents in a collection."""

    @abstractmethod
    def delete_by_metadata(self, collection: str, key: str, value: str) -> None:
        """Delete documents matching a metadata key-value pair."""
