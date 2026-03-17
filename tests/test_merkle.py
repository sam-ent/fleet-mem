"""Tests for MerkleDAG and hierarchical comparison logic."""

import xxhash

from fleet_mem.sync.merkle import MerkleDAG


class TestMerkleDAG:
    def test_add_node_returns_hash(self):
        dag = MerkleDAG()
        content = b"hello world"
        h = dag.add_node("file.txt", content)
        assert h == xxhash.xxh3_64(content).hexdigest()

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
        assert dag.root_hash == xxhash.xxh3_64(b"").hexdigest()


class TestHierarchicalTree:
    def test_add_file_creates_nested_structure(self):
        dag = MerkleDAG()
        dag.add_file("src/auth/login.py", "hash1")
        tree = dag.get_tree()
        assert "src" in tree["dirs"]
        assert "auth" in tree["dirs"]["src"]["dirs"]
        assert tree["dirs"]["src"]["dirs"]["auth"]["files"]["login.py"] == "hash1"

    def test_root_level_files(self):
        dag = MerkleDAG()
        dag.add_file("README.md", "hash_readme")
        tree = dag.get_tree()
        assert tree["files"]["README.md"] == "hash_readme"

    def test_tree_hashes_computed(self):
        dag = MerkleDAG()
        dag.add_file("src/a.py", "h1")
        dag.add_file("src/b.py", "h2")
        tree = dag.get_tree()
        assert tree["hash"] != ""
        assert tree["dirs"]["src"]["hash"] != ""

    def test_nodes_flat_matches_tree(self):
        dag = MerkleDAG()
        dag.add_file("src/auth/login.py", "h1")
        dag.add_file("README.md", "h2")
        dag.add_file("src/main.py", "h3")
        nodes = dag.nodes
        assert nodes == {
            "src/auth/login.py": "h1",
            "README.md": "h2",
            "src/main.py": "h3",
        }

    def test_root_hash_deterministic_with_dirs(self):
        dag1 = MerkleDAG()
        dag1.add_file("src/a.py", "h1")
        dag1.add_file("lib/b.py", "h2")

        dag2 = MerkleDAG()
        dag2.add_file("lib/b.py", "h2")
        dag2.add_file("src/a.py", "h1")

        assert dag1.root_hash == dag2.root_hash


class TestHierarchicalCompare:
    def _make_tree(self, files_dict: dict[str, str]) -> dict:
        """Helper: build a tree from {path: hash} dict."""
        dag = MerkleDAG()
        for path, h in files_dict.items():
            dag.add_file(path, h)
        return dag.get_tree()

    def test_unchanged_dir_skipped(self):
        tree = self._make_tree({"src/a.py": "h1", "src/b.py": "h2", "lib/c.py": "h3"})
        # Modify only lib/c.py
        tree2 = self._make_tree({"src/a.py": "h1", "src/b.py": "h2", "lib/c.py": "h3_mod"})
        diff = MerkleDAG.compare(tree, tree2)
        assert diff["modified"] == {"lib/c.py"}
        assert diff["added"] == set()
        assert diff["removed"] == set()

    def test_changed_file_in_nested_dir(self):
        old = self._make_tree({"src/auth/login.py": "v1", "src/auth/jwt.py": "v2"})
        new = self._make_tree({"src/auth/login.py": "v1_mod", "src/auth/jwt.py": "v2"})
        diff = MerkleDAG.compare(old, new)
        assert diff["modified"] == {"src/auth/login.py"}

    def test_new_directory_all_files_added(self):
        old = self._make_tree({"src/a.py": "h1"})
        new = self._make_tree({"src/a.py": "h1", "lib/utils.py": "h2", "lib/helpers.py": "h3"})
        diff = MerkleDAG.compare(old, new)
        assert diff["added"] == {"lib/utils.py", "lib/helpers.py"}
        assert diff["removed"] == set()
        assert diff["modified"] == set()

    def test_removed_directory_all_files_removed(self):
        old = self._make_tree({"src/a.py": "h1", "lib/utils.py": "h2", "lib/helpers.py": "h3"})
        new = self._make_tree({"src/a.py": "h1"})
        diff = MerkleDAG.compare(old, new)
        assert diff["removed"] == {"lib/utils.py", "lib/helpers.py"}
        assert diff["added"] == set()

    def test_mixed_changes_across_dirs(self):
        old = self._make_tree(
            {
                "src/a.py": "h1",
                "src/b.py": "h2",
                "lib/c.py": "h3",
                "docs/readme.md": "h4",
            }
        )
        new = self._make_tree(
            {
                "src/a.py": "h1_mod",  # modified
                "src/b.py": "h2",  # unchanged
                # lib/ removed entirely
                "docs/readme.md": "h4",  # unchanged
                "tests/test_a.py": "h5",  # new dir + file
            }
        )
        diff = MerkleDAG.compare(old, new)
        assert diff["modified"] == {"src/a.py"}
        assert diff["removed"] == {"lib/c.py"}
        assert diff["added"] == {"tests/test_a.py"}

    def test_no_changes(self):
        tree = self._make_tree({"a.py": "h1", "src/b.py": "h2"})
        diff = MerkleDAG.compare(tree, tree)
        assert diff == {"added": set(), "removed": set(), "modified": set()}

    def test_both_empty_trees(self):
        empty = {"hash": "", "files": {}, "dirs": {}}
        diff = MerkleDAG.compare(empty, empty)
        assert diff == {"added": set(), "removed": set(), "modified": set()}


class TestFlatCompareBackwardCompat:
    """Ensure flat dict comparison still works for legacy snapshots."""

    def test_flat_added_files(self):
        old = {"a.py": "hash_a"}
        new = {"a.py": "hash_a", "b.py": "hash_b"}
        diff = MerkleDAG.compare(old, new)
        assert diff["added"] == {"b.py"}

    def test_flat_removed_files(self):
        old = {"a.py": "hash_a", "b.py": "hash_b"}
        new = {"a.py": "hash_a"}
        diff = MerkleDAG.compare(old, new)
        assert diff["removed"] == {"b.py"}

    def test_flat_modified_files(self):
        old = {"a.py": "hash_v1"}
        new = {"a.py": "hash_v2"}
        diff = MerkleDAG.compare(old, new)
        assert diff["modified"] == {"a.py"}

    def test_flat_mixed_changes(self):
        old = {"a.py": "h1", "b.py": "h2", "c.py": "h3"}
        new = {"a.py": "h1_mod", "c.py": "h3", "d.py": "h4"}
        diff = MerkleDAG.compare(old, new)
        assert diff["added"] == {"d.py"}
        assert diff["removed"] == {"b.py"}
        assert diff["modified"] == {"a.py"}

    def test_flat_both_empty(self):
        diff = MerkleDAG.compare({}, {})
        assert diff == {"added": set(), "removed": set(), "modified": set()}
