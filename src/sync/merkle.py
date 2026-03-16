"""Merkle DAG for tracking file content hashes."""

import hashlib


class MerkleDAG:
    """A content-addressable DAG where each node is a file path mapped to its SHA-1 hash."""

    def __init__(self):
        self._nodes: dict[str, str] = {}

    def add_node(self, path: str, content: bytes) -> str:
        """Add a file node. Returns the SHA-1 hash of the content."""
        h = hashlib.sha1(content).hexdigest()
        self._nodes[path] = h
        return h

    @property
    def nodes(self) -> dict[str, str]:
        return dict(self._nodes)

    @property
    def root_hash(self) -> str:
        """SHA-1 of sorted concatenated child hashes."""
        if not self._nodes:
            return hashlib.sha1(b"").hexdigest()
        combined = "".join(self._nodes[k] for k in sorted(self._nodes))
        return hashlib.sha1(combined.encode()).hexdigest()

    @staticmethod
    def compare(old: dict[str, str], new: dict[str, str]) -> dict[str, set[str]]:
        """Compare two snapshots (path->hash dicts).

        Returns dict with keys: added, removed, modified (sets of paths).
        """
        old_keys = set(old)
        new_keys = set(new)
        added = new_keys - old_keys
        removed = old_keys - new_keys
        modified = {k for k in old_keys & new_keys if old[k] != new[k]}
        return {"added": added, "removed": removed, "modified": modified}
