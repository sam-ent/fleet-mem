"""Tests for file scanner."""

import tempfile
from pathlib import Path

from fleet_mem.splitter.file_scanner import scan_files


class TestFileScanner:
    def _make_tree(self, tmp: Path, files: dict[str, str]) -> None:
        """Create a directory tree from a dict of {relative_path: content}."""
        for rel_path, content in files.items():
            p = tmp / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    def test_finds_python_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "main.py": "print('hello')",
                    "lib/utils.py": "def util(): pass",
                },
            )
            results = list(scan_files(root))
            paths = {r[0].name for r in results}
            assert "main.py" in paths
            assert "utils.py" in paths
            # All should be python
            assert all(r[1] == "python" for r in results)

    def test_respects_gitignore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    ".gitignore": "secret.py\nbuild/\n",
                    "main.py": "print('hello')",
                    "secret.py": "PASSWORD = 'x'",
                    "build/output.py": "# build artifact",
                },
            )
            results = list(scan_files(root))
            paths = {r[0].name for r in results}
            assert "main.py" in paths
            assert "secret.py" not in paths
            assert "output.py" not in paths

    def test_skips_git_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "main.py": "print('hello')",
                    ".git/config": "[core]",
                    ".git/objects/abc.py": "# git object",
                },
            )
            results = list(scan_files(root))
            paths = {str(r[0].relative_to(root)) for r in results}
            assert not any(".git" in p for p in paths)

    def test_skips_node_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "index.js": "console.log('hi')",
                    "node_modules/pkg/index.js": "module.exports = {}",
                },
            )
            results = list(scan_files(root))
            assert len(results) == 1
            assert results[0][0].name == "index.js"

    def test_skips_pycache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "main.py": "x = 1",
                    "__pycache__/main.cpython-312.py": "cached",
                },
            )
            results = list(scan_files(root))
            assert len(results) == 1

    def test_filters_by_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "main.py": "x = 1",
                    "style.css": "body {}",
                    "data.json": "{}",
                },
            )
            # Only Python
            results = list(scan_files(root, supported_extensions={".py"}))
            assert len(results) == 1
            assert results[0][1] == "python"

    def test_yields_correct_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "app.ts": "const x = 1;",
                    "index.js": "var y = 2;",
                    "README.md": "# Hello",
                },
            )
            results = list(scan_files(root))
            lang_map = {r[0].name: r[1] for r in results}
            assert lang_map["app.ts"] == "typescript"
            assert lang_map["index.js"] == "javascript"
            assert lang_map["README.md"] == "markdown"

    def test_yields_file_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(root, {"hello.py": "print('hello world')"})
            results = list(scan_files(root))
            assert results[0][2] == "print('hello world')"

    def test_extra_ignore_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    "main.py": "x = 1",
                    "generated.py": "# auto",
                },
            )
            results = list(scan_files(root, extra_ignore_patterns=["generated.py"]))
            assert len(results) == 1
            assert results[0][0].name == "main.py"

    def test_gitignore_wildcard_pattern(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._make_tree(
                root,
                {
                    ".gitignore": "*.pyc\n",
                    "main.py": "x = 1",
                    "main.pyc": "bytecode",
                },
            )
            results = list(scan_files(root))
            paths = {r[0].name for r in results}
            assert "main.py" in paths
            # .pyc not in EXTENSION_MAP so would be skipped anyway,
            # but the ignore should also catch it
            assert "main.pyc" not in paths

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = list(scan_files(Path(tmp)))
            assert results == []
