"""ChromaDB vector store implementation."""

from pathlib import Path

import chromadb

from fleet_mem.vectordb.base import VectorDatabase
from fleet_mem.vectordb.types import VectorDocument


class ChromaDBStore(VectorDatabase):
    """ChromaDB-backed vector store using pre-computed embeddings."""

    def __init__(self, path: Path):
        self._client = chromadb.PersistentClient(path=str(path))

    def create_collection(self, name: str, dimension: int) -> None:
        self._client.get_or_create_collection(
            name=name,
            metadata={"dimension": dimension, "hnsw:space": "l2"},
        )

    def has_collection(self, name: str) -> bool:
        names = [c.name for c in self._client.list_collections()]
        return name in names

    def list_collections(self) -> list[str]:
        return [c.name for c in self._client.list_collections()]

    def insert(self, collection: str, documents: list[VectorDocument]) -> None:
        missing = [d.id for d in documents if d.vector is None]
        if missing:
            raise ValueError(
                f"Documents missing pre-computed vectors: {missing}. "
                "Embed documents before inserting."
            )

        col = self._client.get_collection(name=collection)
        col.upsert(
            ids=[d.id for d in documents],
            documents=[d.content for d in documents],
            embeddings=[d.vector for d in documents],
            metadatas=[d.metadata if d.metadata else None for d in documents],
        )

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
        where: dict | None = None,
    ) -> list[dict]:
        col = self._client.get_collection(name=collection)
        kwargs: dict = {
            "query_embeddings": [vector],
            "n_results": limit,
        }
        if where:
            kwargs["where"] = where

        results = col.query(**kwargs)

        out: list[dict] = []
        ids = results["ids"][0]
        documents = results["documents"][0]
        distances = results["distances"][0]
        metadatas = results["metadatas"][0]

        for i, doc_id in enumerate(ids):
            out.append(
                {
                    "id": doc_id,
                    "content": documents[i],
                    "score": 1.0 / (1.0 + distances[i]),
                    "metadata": metadatas[i],
                }
            )
        return out

    def delete(self, collection: str, ids: list[str]) -> None:
        col = self._client.get_collection(name=collection)
        col.delete(ids=ids)

    def drop_collection(self, name: str) -> None:
        self._client.delete_collection(name=name)

    def count(self, collection: str) -> int:
        col = self._client.get_collection(name=collection)
        return col.count()

    def delete_by_metadata(self, collection: str, key: str, value: str) -> None:
        col = self._client.get_collection(name=collection)
        col.delete(where={key: value})
