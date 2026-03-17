"""Merkle DAG for tracking file content hashes with hierarchical directory support."""

import xxhash


def _collect_all_files(node: dict, prefix: str) -> set[str]:
    """Collect all file paths under a tree node with a path prefix."""
    result = set()
    for name in node.get("files", {}):
        result.add(f"{prefix}/{name}")
    for dir_name, dir_node in node.get("dirs", {}).items():
        result |= _collect_all_files(dir_node, f"{prefix}/{dir_name}")
    return result


def _compute_dir_hash(node: dict) -> str:
    """Compute hash for a directory node from its children."""
    parts = []
    for name in sorted(node.get("files", {})):
        parts.append(f"f:{name}:{node['files'][name]}")
    for name in sorted(node.get("dirs", {})):
        parts.append(f"d:{name}:{node['dirs'][name]['hash']}")
    return xxhash.xxh3_64("".join(parts).encode()).hexdigest()


class MerkleDAG:
    """Hierarchical Merkle tree for directory-level change detection."""

    def __init__(self):
        self._tree: dict = {"hash": "", "files": {}, "dirs": {}}

    def add_file(self, rel_path: str, content_hash: str) -> None:
        """Add a file at its directory position in the tree."""
        parts = rel_path.replace("\\", "/").split("/")
        node = self._tree
        for dir_part in parts[:-1]:
            if dir_part not in node["dirs"]:
                node["dirs"][dir_part] = {"hash": "", "files": {}, "dirs": {}}
            node = node["dirs"][dir_part]
        node["files"][parts[-1]] = content_hash

    def add_node(self, path: str, content: bytes) -> str:
        """Add a file node (backward-compatible). Returns the xxh3_64 hash."""
        h = xxhash.xxh3_64(content).hexdigest()
        self.add_file(path, h)
        return h

    def _recompute_hashes(self, node: dict | None = None) -> str:
        """Recursively compute directory hashes bottom-up."""
        if node is None:
            node = self._tree
        for dir_name in node["dirs"]:
            self._recompute_hashes(node["dirs"][dir_name])
        node["hash"] = _compute_dir_hash(node)
        return node["hash"]

    @property
    def root_hash(self) -> str:
        """Compute root hash from directory tree."""
        if not self._tree["files"] and not self._tree["dirs"]:
            return xxhash.xxh3_64(b"").hexdigest()
        return self._recompute_hashes()

    @property
    def nodes(self) -> dict[str, str]:
        """Flat path->hash dict (backward-compatible)."""
        result: dict[str, str] = {}
        self._collect_flat(self._tree, "", result)
        return result

    def _collect_flat(self, node: dict, prefix: str, out: dict[str, str]) -> None:
        for name, h in node["files"].items():
            key = f"{prefix}{name}" if not prefix else f"{prefix}/{name}"
            out[key] = h
        for dir_name, dir_node in node["dirs"].items():
            sub = f"{prefix}{dir_name}" if not prefix else f"{prefix}/{dir_name}"
            self._collect_flat(dir_node, sub, out)

    def get_tree(self) -> dict:
        """Return the nested tree structure for serialization."""
        self._recompute_hashes()
        return self._tree

    @staticmethod
    def compare(old: dict, new: dict) -> dict[str, set[str]]:
        """Compare two trees. Supports both hierarchical and flat formats.

        Hierarchical: dicts with "files", "dirs", "hash" keys.
        Flat (legacy): plain {path: hash} dicts.
        """
        if _is_hierarchical(old) and _is_hierarchical(new):
            return _compare_trees(old, new)
        # Flat comparison (legacy or mixed)
        old_flat = _flatten(old) if _is_hierarchical(old) else old
        new_flat = _flatten(new) if _is_hierarchical(new) else new
        old_keys = set(old_flat)
        new_keys = set(new_flat)
        added = new_keys - old_keys
        removed = old_keys - new_keys
        modified = {k for k in old_keys & new_keys if old_flat[k] != new_flat[k]}
        return {"added": added, "removed": removed, "modified": modified}


def _is_hierarchical(d: dict) -> bool:
    """Check if a dict is a hierarchical tree node."""
    return "files" in d and "dirs" in d


def _flatten(node: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a hierarchical tree to {path: hash}."""
    result: dict[str, str] = {}
    for name, h in node.get("files", {}).items():
        key = f"{prefix}/{name}" if prefix else name
        result[key] = h
    for dir_name, dir_node in node.get("dirs", {}).items():
        sub = f"{prefix}/{dir_name}" if prefix else dir_name
        result.update(_flatten(dir_node, sub))
    return result


def _compare_trees(old: dict, new: dict, prefix: str = "") -> dict[str, set[str]]:
    """Compare two hierarchical trees, skipping unchanged subtrees."""
    added: set[str] = set()
    removed: set[str] = set()
    modified: set[str] = set()

    old_files = old.get("files", {})
    new_files = new.get("files", {})

    # Compare files at this level
    for name, h in new_files.items():
        path = f"{prefix}/{name}" if prefix else name
        old_h = old_files.get(name)
        if old_h is None:
            added.add(path)
        elif old_h != h:
            modified.add(path)
    for name in old_files:
        if name not in new_files:
            path = f"{prefix}/{name}" if prefix else name
            removed.add(path)

    old_dirs = old.get("dirs", {})
    new_dirs = new.get("dirs", {})

    # Compare subdirectories
    for name, new_dir in new_dirs.items():
        sub_prefix = f"{prefix}/{name}" if prefix else name
        if name not in old_dirs:
            added |= _collect_all_files(new_dir, sub_prefix)
        elif old_dirs[name].get("hash") != new_dir.get("hash"):
            sub = _compare_trees(old_dirs[name], new_dir, sub_prefix)
            added |= sub["added"]
            removed |= sub["removed"]
            modified |= sub["modified"]
        # else: hash matches, skip entirely

    for name in old_dirs:
        if name not in new_dirs:
            sub_prefix = f"{prefix}/{name}" if prefix else name
            removed |= _collect_all_files(old_dirs[name], sub_prefix)

    return {"added": added, "removed": removed, "modified": modified}
