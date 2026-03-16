"""Tests for merge impact preview and post-merge notifications."""

from src.fleet.cross_agent import memory_notifications, memory_subscribe
from src.fleet.lock_registry import lock_acquire
from src.fleet.merge_impact import merge_impact, notify_merge
from src.memory.engine import MemoryEngine


class TestMergeImpact:
    def _setup_dbs(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_db = tmp_path / "memory.db"
        return fleet_db, memory_db

    def test_detects_locked_agents(self, tmp_path):
        fleet_db, memory_db = self._setup_dbs(tmp_path)

        lock_acquire(fleet_db, "agent-alpha", "myproj", ["src/auth/*"], "feat-1")

        result = merge_impact(
            project="myproj",
            files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["locked_agents"]) == 1
        assert result["locked_agents"][0]["agent_id"] == "agent-alpha"

    def test_detects_subscribed_agents(self, tmp_path):
        fleet_db, memory_db = self._setup_dbs(tmp_path)

        memory_subscribe(fleet_db, "agent-beta", "myproj", ["src/auth/*"])

        result = merge_impact(
            project="myproj",
            files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["subscribed_agents"]) == 1
        assert result["subscribed_agents"][0]["agent_id"] == "agent-beta"

    def test_detects_stale_memories(self, tmp_path):
        fleet_db, memory_db = self._setup_dbs(tmp_path)

        with MemoryEngine(memory_db) as engine:
            engine.insert_node("m1", "insight", "auth logic", project_path="/proj")
            engine.insert_file_anchor("a1", "m1", "src/auth/login.py", "abc123")

        result = merge_impact(
            project="myproj",
            files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["stale_memories"]) == 1
        assert result["stale_memories"][0]["memory_id"] == "m1"
        assert result["stale_memories"][0]["file_path"] == "src/auth/login.py"

    def test_combined_detection(self, tmp_path):
        fleet_db, memory_db = self._setup_dbs(tmp_path)

        lock_acquire(fleet_db, "agent-alpha", "myproj", ["src/auth/*"], "feat-1")
        memory_subscribe(fleet_db, "agent-beta", "myproj", ["src/auth/*"])

        with MemoryEngine(memory_db) as engine:
            engine.insert_node("m1", "insight", "auth logic", project_path="/proj")
            engine.insert_file_anchor("a1", "m1", "src/auth/login.py", "abc123")

        result = merge_impact(
            project="myproj",
            files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["locked_agents"]) == 1
        assert len(result["subscribed_agents"]) == 1
        assert len(result["stale_memories"]) == 1

    def test_unrelated_files_returns_empty(self, tmp_path):
        fleet_db, memory_db = self._setup_dbs(tmp_path)

        lock_acquire(fleet_db, "agent-alpha", "myproj", ["src/auth/*"], "feat-1")
        memory_subscribe(fleet_db, "agent-beta", "myproj", ["src/auth/*"])

        with MemoryEngine(memory_db) as engine:
            engine.insert_node("m1", "insight", "auth logic", project_path="/proj")
            engine.insert_file_anchor("a1", "m1", "src/auth/login.py", "abc123")

        result = merge_impact(
            project="myproj",
            files=["src/utils/helpers.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["locked_agents"]) == 0
        assert len(result["subscribed_agents"]) == 0
        assert len(result["stale_memories"]) == 0


class TestNotifyMerge:
    def test_creates_notifications_for_subscribers(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_db = tmp_path / "memory.db"
        # Init memory db so it exists
        with MemoryEngine(memory_db):
            pass

        memory_subscribe(fleet_db, "agent-beta", "myproj", ["src/auth/*"])

        result = notify_merge(
            project="myproj",
            branch="feat-login",
            merged_files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert result["notifications_created"] >= 1

        # Verify agent-beta can read the notification
        notifs = memory_notifications(fleet_db, "agent-beta")
        assert len(notifs) == 1
        assert "feat-login" in notifs[0]["memory_summary"]

    def test_returns_stale_anchors(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_db = tmp_path / "memory.db"

        with MemoryEngine(memory_db) as engine:
            engine.insert_node("m1", "insight", "auth logic", project_path="/proj")
            engine.insert_file_anchor("a1", "m1", "src/auth/login.py", "abc123")

        result = notify_merge(
            project="myproj",
            branch="feat-login",
            merged_files=["src/auth/login.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )
        assert len(result["stale_anchors"]) == 1
        assert result["stale_anchors"][0]["file_path"] == "src/auth/login.py"

    def test_no_notification_for_unsubscribed(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_db = tmp_path / "memory.db"
        with MemoryEngine(memory_db):
            pass

        memory_subscribe(fleet_db, "agent-beta", "myproj", ["src/auth/*"])

        notify_merge(
            project="myproj",
            branch="feat-utils",
            merged_files=["src/utils/helpers.py"],
            fleet_db_path=fleet_db,
            memory_db_path=memory_db,
        )

        notifs = memory_notifications(fleet_db, "agent-beta")
        assert len(notifs) == 0
