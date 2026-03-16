"""Memory embedding and hybrid search via ChromaDB + FTS5."""

import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path

from src.embedding.base import Embedding
from src.memory.engine import MemoryEngine
from src.vectordb.base import VectorDatabase
from src.vectordb.types import VectorDocument

MEMORY_COLLECTION = "memory"


@dataclass
class MemoryResult:
    """A single result from hybrid memory search."""

    id: str
    node_type: str
    content: str
    summary: str | None
    score: float
    file_path: str | None = None


@dataclass
class StaleAnchor:
    """A file anchor whose file has changed since storage."""

    memory_id: str
    anchor_id: str
    file_path: str
    stored_hash: str
    current_hash: str


class MemoryEmbedder:
    """Hybrid search over agent memory using FTS5 + ChromaDB."""

    def __init__(
        self,
        engine: MemoryEngine,
        embedding: Embedding,
        vectordb: VectorDatabase,
    ):
        self._engine = engine
        self._embedding = embedding
        self._vectordb = vectordb
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if not self._vectordb.has_collection(MEMORY_COLLECTION):
            dim = self._embedding.get_dimension()
            self._vectordb.create_collection(MEMORY_COLLECTION, dimension=dim)

    def memory_store(
        self,
        node_type: str,
        content: str,
        summary: str | None = None,
        keywords: list[str] | None = None,
        file_path: str | None = None,
        line_range: str | None = None,
        source: str = "agent",
        project_path: str | None = None,
        agent_id: str | None = None,
        fleet_db_path: str | None = None,
    ) -> str:
        node_id = uuid.uuid4().hex

        keywords_str = ",".join(keywords) if keywords else None
        self._engine.insert_node(
            node_id=node_id,
            node_type=node_type,
            content=content,
            summary=summary,
            keywords=keywords_str,
            file_path=file_path,
            line_range=line_range,
            source=source,
            project_path=project_path,
            agent_id=agent_id,
        )

        # Embed and insert into ChromaDB
        vector = self._embedding.embed(content)
        doc = VectorDocument(
            id=node_id,
            content=content,
            metadata={"node_type": node_type, "source": source},
            vector=vector,
        )
        self._vectordb.insert(MEMORY_COLLECTION, [doc])

        # Create file anchor if file_path provided
        if file_path:
            file_hash = _sha1_file(file_path)
            anchor_id = uuid.uuid4().hex
            line_start = None
            line_end = None
            if line_range:
                parts = line_range.split("-")
                if len(parts) == 2:
                    line_start = int(parts[0])
                    line_end = int(parts[1])
            self._engine.insert_file_anchor(
                anchor_id=anchor_id,
                memory_id=node_id,
                file_path=file_path,
                file_hash=file_hash,
                line_start=line_start,
                line_end=line_end,
            )

        # Notify subscribers if file_path matches their patterns
        if file_path and agent_id and fleet_db_path:
            from src.fleet.cross_agent import _notify_subscribers

            _notify_subscribers(
                fleet_db_path=Path(fleet_db_path),
                memory_id=node_id,
                memory_summary=summary or content[:200],
                file_path=file_path,
                author_agent_id=agent_id,
            )

        return node_id

    def memory_search(
        self,
        query: str,
        top_k: int = 10,
        node_type: str | None = None,
    ) -> list[MemoryResult]:
        # FTS5 keyword search
        fts_rows = self._engine.search_fts(query, limit=top_k)
        fts_results: dict[str, float] = {}
        for rank, row in enumerate(fts_rows):
            fts_results[row["id"]] = 1.0 / (rank + 1)

        # ChromaDB semantic search
        vector = self._embedding.embed(query)
        where = {"node_type": node_type} if node_type else None
        chroma_results = self._vectordb.search(
            MEMORY_COLLECTION,
            vector=vector,
            limit=top_k,
            where=where,
        )
        semantic_scores: dict[str, float] = {}
        for rank, hit in enumerate(chroma_results):
            semantic_scores[hit["id"]] = 1.0 / (rank + 1)

        # Reciprocal rank fusion
        all_ids = set(fts_results) | set(semantic_scores)
        scored: list[tuple[str, float]] = []
        for nid in all_ids:
            rrf = fts_results.get(nid, 0.0) + semantic_scores.get(nid, 0.0)
            scored.append((nid, rrf))
        scored.sort(key=lambda x: x[1], reverse=True)

        results: list[MemoryResult] = []
        for nid, score in scored[:top_k]:
            row = self._engine.get_node(nid)
            if row is None:
                continue
            if node_type and row["node_type"] != node_type:
                continue
            results.append(
                MemoryResult(
                    id=nid,
                    node_type=row["node_type"],
                    content=row["content"],
                    summary=row["summary"],
                    score=score,
                    file_path=row["file_path"],
                )
            )
        return results

    def memory_promote(self, memory_id: str, target_scope: str | None = None) -> None:
        """Promote a project-scoped memory to global scope."""
        self._engine.update_node_project_path(memory_id, target_scope)

    def stale_check(self, project_path: str | None = None) -> list[StaleAnchor]:
        anchors = self._engine.get_all_file_anchors(project_path=project_path)
        stale: list[StaleAnchor] = []
        for anchor in anchors:
            fp = anchor["file_path"]
            stored_hash = anchor["file_hash"]
            try:
                current_hash = _sha1_file(fp)
            except (FileNotFoundError, OSError):
                current_hash = "<missing>"
            if current_hash != stored_hash:
                stale.append(
                    StaleAnchor(
                        memory_id=anchor["memory_id"],
                        anchor_id=anchor["id"],
                        file_path=fp,
                        stored_hash=stored_hash,
                        current_hash=current_hash,
                    )
                )
        return stale


def _sha1_file(file_path: str) -> str:
    h = hashlib.sha1()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
