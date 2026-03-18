"""Tests for agent session registry."""

import time

from fleet_mem.fleet.sessions import (
    heartbeat_agent,
    list_agents,
    refresh_statuses,
    register_agent,
)


def test_register_new_agent(tmp_path):
    db = tmp_path / "fleet.db"
    result = register_agent(db, "agent-a", "myproject", branch="main")
    assert result["status"] == "registered"
    assert result["agent_id"] == "agent-a"


def test_register_idempotent(tmp_path):
    db = tmp_path / "fleet.db"
    register_agent(db, "agent-a", "myproject", branch="main")
    register_agent(db, "agent-a", "myproject", branch="feat/new")
    agents = list_agents(db)
    assert len(agents) == 1
    assert agents[0]["branch"] == "feat/new"


def test_register_with_worktree(tmp_path):
    db = tmp_path / "fleet.db"
    register_agent(
        db,
        "agent-a",
        "myproject",
        worktree_path="/home/user/CODE/myproject/.claude/worktrees/fix-login",
        branch="fix/login",
    )
    agents = list_agents(db)
    assert agents[0]["worktree_path"] == ("/home/user/CODE/myproject/.claude/worktrees/fix-login")


def test_multiple_agents(tmp_path):
    db = tmp_path / "fleet.db"
    register_agent(db, "agent-a", "myproject", branch="fix/login")
    register_agent(db, "agent-b", "myproject", branch="feat/oauth")
    agents = list_agents(db)
    assert len(agents) == 2
    agent_ids = {a["agent_id"] for a in agents}
    assert agent_ids == {"agent-a", "agent-b"}


def test_heartbeat_updates_activity(tmp_path):
    db = tmp_path / "fleet.db"
    register_agent(db, "agent-a", "myproject")
    agents_before = list_agents(db)
    time.sleep(0.05)
    heartbeat_agent(db, "agent-a")
    agents_after = list_agents(db)
    assert agents_after[0]["last_activity_at"] >= agents_before[0]["last_activity_at"]


def test_heartbeat_noop_for_unknown_agent(tmp_path):
    db = tmp_path / "fleet.db"
    # Should not raise
    heartbeat_agent(db, "nonexistent")
    agents = list_agents(db)
    assert len(agents) == 0


def test_list_agents_empty(tmp_path):
    db = tmp_path / "fleet.db"
    agents = list_agents(db)
    assert agents == []


def test_refresh_statuses_creates_table(tmp_path):
    """refresh_statuses should not crash on a fresh DB."""
    db = tmp_path / "fleet.db"
    refresh_statuses(db)


def test_new_agent_is_active(tmp_path):
    db = tmp_path / "fleet.db"
    register_agent(db, "agent-a", "myproject")
    agents = list_agents(db)
    assert agents[0]["status"] == "active"
