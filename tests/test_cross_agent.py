"""Tests for cross-agent memory sharing."""

from fleet_mem.fleet.cross_agent import (
    _notify_subscribers,
    memory_feed,
    memory_notifications,
    memory_subscribe,
)
from fleet_mem.memory.engine import MemoryEngine


class TestMemoryFeed:
    def test_returns_other_agents_memories(self, tmp_path):
        db = tmp_path / "memory.db"
        with MemoryEngine(db) as engine:
            engine.insert_node("n1", "insight", "content1", agent_id="alpha")
            engine.insert_node("n2", "insight", "content2", agent_id="bravo")
            engine.insert_node("n3", "insight", "content3", agent_id="alpha")

        results = memory_feed(db, agent_id="alpha")
        ids = [r["id"] for r in results]
        assert "n2" in ids
        assert "n1" not in ids
        assert "n3" not in ids

    def test_respects_since_minutes(self, tmp_path):
        db = tmp_path / "memory.db"
        with MemoryEngine(db) as engine:
            engine.insert_node("n1", "insight", "recent", agent_id="bravo")
            # Backdate one node
            engine.conn.execute(
                "UPDATE memory_nodes SET created_at = ? WHERE id = ?",
                ("2020-01-01 00:00:00", "n1"),
            )
            engine.conn.commit()
            engine.insert_node("n2", "insight", "fresh", agent_id="bravo")

        results = memory_feed(db, since_minutes=60, agent_id="alpha")
        ids = [r["id"] for r in results]
        assert "n2" in ids
        assert "n1" not in ids


class TestMemorySubscribe:
    def test_creates_subscription(self, tmp_path):
        db = tmp_path / "fleet.db"
        result = memory_subscribe(db, "alpha", "/proj", ["*.py"])
        assert len(result) == 1
        assert result[0]["file_pattern"] == "*.py"

    def test_idempotent_on_duplicate(self, tmp_path):
        db = tmp_path / "fleet.db"
        memory_subscribe(db, "alpha", "/proj", ["*.py"])
        result = memory_subscribe(db, "alpha", "/proj", ["*.py"])
        assert len(result) == 0


class TestNotifications:
    def test_notification_created_on_match(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_subscribe(fleet_db, "bravo", "/proj", ["src/*.py"])

        _notify_subscribers(
            fleet_db_path=fleet_db,
            memory_id="m1",
            memory_summary="changed engine",
            file_path="src/engine.py",
            author_agent_id="alpha",
        )

        notifs = memory_notifications(fleet_db, "bravo")
        assert len(notifs) == 1
        assert notifs[0]["memory_id"] == "m1"
        assert notifs[0]["author_agent_id"] == "alpha"

    def test_notifications_marks_as_read(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_subscribe(fleet_db, "bravo", "/proj", ["*.py"])
        _notify_subscribers(fleet_db, "m1", "summary", "foo.py", "alpha")

        first = memory_notifications(fleet_db, "bravo")
        assert len(first) == 1

        second = memory_notifications(fleet_db, "bravo")
        assert len(second) == 0

    def test_no_self_notification(self, tmp_path):
        fleet_db = tmp_path / "fleet.db"
        memory_subscribe(fleet_db, "alpha", "/proj", ["*.py"])

        _notify_subscribers(fleet_db, "m1", "summary", "foo.py", "alpha")

        notifs = memory_notifications(fleet_db, "alpha")
        assert len(notifs) == 0
