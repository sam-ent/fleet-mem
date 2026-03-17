"""Tests for config module: XDG defaults, env var overrides, fleet_db_path."""

import os
from pathlib import Path
from unittest.mock import patch


class TestConfigDefaults:
    """Config uses XDG-standard defaults."""

    def test_chroma_path_xdg_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ["HOME"] = str(Path.home())
            from importlib import reload

            import fleet_mem.config

            reload(fleet_mem.config)
            config = fleet_mem.config.Config()
            expected = Path.home() / ".local" / "share" / "fleet-mem" / "chroma"
            assert config.chroma_path == expected

    def test_memory_db_path_xdg_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ["HOME"] = str(Path.home())
            from importlib import reload

            import fleet_mem.config

            reload(fleet_mem.config)
            config = fleet_mem.config.Config()
            expected = Path.home() / ".local" / "share" / "fleet-mem" / "memory.db"
            assert config.memory_db_path == expected

    def test_fleet_db_path_xdg_default(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ["HOME"] = str(Path.home())
            from importlib import reload

            import fleet_mem.config

            reload(fleet_mem.config)
            config = fleet_mem.config.Config()
            expected = Path.home() / ".local" / "share" / "fleet-mem" / "fleet.db"
            assert config.fleet_db_path == expected

    def test_fleet_db_path_exists_as_field(self):
        from fleet_mem.config import Config

        config = Config()
        assert hasattr(config, "fleet_db_path")
        assert isinstance(config.fleet_db_path, Path)

    def test_merkle_path_relative_to_chroma(self):
        from fleet_mem.config import Config

        config = Config()
        assert config.merkle_path == config.chroma_path / "merkle"


class TestConfigEnvOverrides:
    """Config reads from environment variables."""

    def test_chroma_path_from_env(self, tmp_path):
        with patch.dict(os.environ, {"CHROMA_PATH": str(tmp_path / "custom-chroma")}):
            from importlib import reload

            import fleet_mem.config

            reload(fleet_mem.config)
            config = fleet_mem.config.Config()
            assert config.chroma_path == tmp_path / "custom-chroma"

    def test_memory_db_path_from_env(self, tmp_path):
        with patch.dict(os.environ, {"MEMORY_DB_PATH": str(tmp_path / "custom.db")}):
            from fleet_mem.config import Config

            config = Config()
            assert config.memory_db_path == tmp_path / "custom.db"

    def test_fleet_db_path_from_env(self, tmp_path):
        with patch.dict(os.environ, {"FLEET_DB_PATH": str(tmp_path / "fleet-custom.db")}):
            from fleet_mem.config import Config

            config = Config()
            assert config.fleet_db_path == tmp_path / "fleet-custom.db"

    def test_ollama_host_from_env(self):
        with patch.dict(os.environ, {"OLLAMA_HOST": "http://ollama:11434"}):
            from fleet_mem.config import Config

            config = Config()
            assert config.ollama_host == "http://ollama:11434"

    def test_ollama_host_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OLLAMA_HOST", None)
            from fleet_mem.config import Config

            config = Config()
            assert config.ollama_host == "http://localhost:11434"
