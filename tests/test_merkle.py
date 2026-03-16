"""Tests for MerkleDAG and comparison logic."""

import hashlib

from src.sync.merkle import MerkleDAG


class TestMerkleDAG:
    def test_add_node_returns_sha1(self):
        dag = MerkleDAG()
        content = b"hello world"
        h = dag.add_node("file.txt", content)
        assert h == hashlib.sha1(content).hexdigest()

    def test_nodes_property(self):
        dag = MerkleDAG()
        dag.add_node("a.py", b"aaa")
        dag.add_node("b.py", b"bbb")
        assert set(dag.nodes.keys()) == {"a.py", "b.py"}

    def test_root_hash_deterministic(self):
        dag1 = MerkleDAG()
        dag1.add_node("a.py", b"aaa")
        dag1.add_node("b.py", b"bbb")

        dag2 = MerkleDAG()
        dag2.add_node("b.py", b"bbb")
        dag2.add_node("a.py", b"aaa")

        assert dag1.root_hash == dag2.root_hash

    def test_root_hash_changes_on_content_change(self):
        dag1 = MerkleDAG()
        dag1.add_node("a.py", b"v1")

        dag2 = MerkleDAG()
        dag2.add_node("a.py", b"v2")

        assert dag1.root_hash != dag2.root_hash

    def test_empty_dag_root_hash(self):
        dag = MerkleDAG()
        assert dag.root_hash == hashlib.sha1(b"").hexdigest()


class TestMerkleCompare:
    def test_added_files(self):
        old = {"a.py": "hash_a"}
        new = {"a.py": "hash_a", "b.py": "hash_b"}
        diff = MerkleDAG.compare(old, new)
        assert diff["added"] == {"b.py"}
        assert diff["removed"] == set()
        assert diff["modified"] == set()

    def test_removed_files(self):
        old = {"a.py": "hash_a", "b.py": "hash_b"}
        new = {"a.py": "hash_a"}
        diff = MerkleDAG.compare(old, new)
        assert diff["removed"] == {"b.py"}
        assert diff["added"] == set()

    def test_modified_files(self):
        old = {"a.py": "hash_v1"}
        new = {"a.py": "hash_v2"}
        diff = MerkleDAG.compare(old, new)
        assert diff["modified"] == {"a.py"}
        assert diff["added"] == set()
        assert diff["removed"] == set()

    def test_mixed_changes(self):
        old = {"a.py": "h1", "b.py": "h2", "c.py": "h3"}
        new = {"a.py": "h1_mod", "c.py": "h3", "d.py": "h4"}
        diff = MerkleDAG.compare(old, new)
        assert diff["added"] == {"d.py"}
        assert diff["removed"] == {"b.py"}
        assert diff["modified"] == {"a.py"}

    def test_no_changes(self):
        snap = {"a.py": "h1", "b.py": "h2"}
        diff = MerkleDAG.compare(snap, snap)
        assert diff["added"] == set()
        assert diff["removed"] == set()
        assert diff["modified"] == set()

    def test_both_empty(self):
        diff = MerkleDAG.compare({}, {})
        assert diff == {"added": set(), "removed": set(), "modified": set()}
