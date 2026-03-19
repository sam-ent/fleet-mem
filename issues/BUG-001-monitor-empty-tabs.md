# Issue: Empty Monitor Tabs for New Installations

**Status:** Completed
**Type:** Bug Fix
**Priority:** High

## Description
When running `fleet-mem monitor` for the first time, several tabs (Locks, Stats, Memory, Subscriptions, Notifications) appear empty or missing data. This occurs because the database tables (`agent_locks`, `subscriptions`, etc.) are only created upon their first actual usage, but the statistics collector (`get_fleet_stats`) encounters an `OperationalError` when trying to query them if they do not exist.

## Objectives
- [x] Ensure `get_fleet_stats` is robust to non-existent tables.
- [x] Automatically create required tables in `fleet.db` and `memory.db` if they are missing.
- [x] Return zeroed counts instead of failing or swallowing errors.

## Validation Criteria
1. [x] Monitor shows `0` counts for all metrics on a fresh installation.
2. [x] No `OperationalError` or tracebacks in server logs for missing tables.
3. [x] All tabs (Agents, Stats, Locks, Memory, Subscriptions, Notifications) are accessible.

## Verification Evidence
**Test Execution:**
```bash
PYTHONPATH=. /home/sraj/.local/share/pipx/venvs/fleet-mem/bin/python -c "
import json
from fleet_mem.fleet.stats import get_fleet_stats
from fleet_mem.config import Config
cfg = Config()
stats = get_fleet_stats(cfg.chroma_path, cfg.memory_db_path, cfg.fleet_db_path, cfg.embed_cache_path, detail=True)
print(json.dumps(stats, indent=2))
"
```

**Results:**
```json
{
  "collections": {...},
  "total_chunks": 578,
  "memory_nodes": 0,
  "file_anchors": 0,
  "active_locks": 0,
  "subscriptions": 0,
  "pending_notifications": 0,
  "lock_details": [],
  "subscription_details": [],
  "notification_details": [],
  "active_agents": 4,
  ...
}
```
*Note: Previously, these keys were either missing from the output or resulted in 0 even if data existed elsewhere due to the swallowed exception.*

## Closing Notes
Modified `fleet_mem/fleet/stats.py` to include `CREATE TABLE IF NOT EXISTS` for all required tables before querying. This ensures the schema is always present for the monitoring TUI.
