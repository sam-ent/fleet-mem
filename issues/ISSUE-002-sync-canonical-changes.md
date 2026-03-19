# Issue: Sync Monitor Fix to Canonical

**Status:** Open
**Type:** Sync/Merge
**Priority:** Medium

## Description
The fix for "Empty Monitor Tabs" was implemented locally in `~/CODE/fleet-mem/fleet_mem/fleet/stats.py`. This needs to be synced/merged into the main branch of the canonical repository to ensure it persists in future builds.

## Objectives
- [ ] Merge the updated `get_fleet_stats` function to the main branch.
- [ ] Verify that the change works across different environments (pipx vs. local dev).
- [ ] Re-install `fleet-mem` via pipx to confirm the fix is persistent in the global installation.

## Validation Criteria
- [ ] `pipx install --force ~/CODE/fleet-mem` results in a working monitor with all tabs.
- [ ] Regression tests for stats collection pass.

## Tasks
- [ ] Create a feature branch for the sync.
- [ ] Push and merge.
- [ ] Perform a clean install test.
