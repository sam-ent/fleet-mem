"""Fleet-Mem monitoring TUI built with Textual."""

from __future__ import annotations

from collections import deque

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Sparkline,
    TabbedContent,
    TabPane,
    Tree,
)

from fleet_mem import __version__ as monitor_version
from fleet_mem.monitor.client import fetch_stats

# Max history for sparklines
_HISTORY_LEN = 60

# Status display colors
_STATUS_COLORS = {
    "active": "green",
    "idle": "yellow",
    "disconnected": "red",
}


class DashboardPanel(Container):
    """Dashboard with key metrics, conflict alerts, and activity summary."""

    def compose(self) -> ComposeResult:
        yield Label("Connecting...", id="stats-summary")
        yield Label("", id="conflict-banner")
        with Horizontal(classes="sparkline-row"):
            with Container(classes="sparkline-box"):
                yield Label("Agents [bold]0[/]", id="gauge-agents", classes="sparkline-label")
                yield Sparkline([], id="spark-agents")
            with Container(classes="sparkline-box"):
                yield Label("Locks [bold]0[/]", id="gauge-locks", classes="sparkline-label")
                yield Sparkline([], id="spark-locks")
            with Container(classes="sparkline-box"):
                yield Label("Notifs [bold]0[/]", id="gauge-notifs", classes="sparkline-label")
                yield Sparkline([], id="spark-notifs")
            with Container(classes="sparkline-box"):
                yield Label("Memory [bold]0[/]", id="gauge-memory", classes="sparkline-label")
                yield Sparkline([], id="spark-memory")


class FleetMonitorApp(App):
    """btop-style TUI for fleet-mem coordination health."""

    TITLE = f"fleet-mem monitor v{monitor_version}"
    CSS = """
    Screen {
        background: $surface;
    }
    #filter-input {
        dock: top;
        width: 100%;
        margin: 0 1;
    }
    .sparkline-row {
        height: 5;
        margin: 1 0;
    }
    .sparkline-box {
        width: 1fr;
        margin: 0 1;
    }
    .sparkline-label {
        text-align: center;
        color: $text-muted;
    }
    #stats-summary {
        text-align: center;
        padding: 1;
        color: $success;
    }
    #conflict-banner {
        text-align: center;
        padding: 0 1;
    }
    DataTable {
        height: 1fr;
    }
    Tree {
        height: 1fr;
    }
    RichLog {
        height: 1fr;
        border: solid $primary-background;
    }
    .error-msg {
        color: $error;
        text-align: center;
        padding: 2;
    }
    TabPane {
        padding: 0 1;
    }
    #log-pane {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("f", "focus_filter", "Filter"),
        Binding("d", "toggle_disconnected", "Disconnected"),
        Binding("x", "prune_disconnected", "Prune"),
    ]

    agent_filter = reactive("")
    show_disconnected = reactive(False)

    def __init__(self, sock_path: str = "", interval: float = 2.0):
        super().__init__()
        self._sock_path = sock_path
        self._interval = interval
        self._agent_history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._lock_history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._notif_history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._memory_history: deque[float] = deque(maxlen=_HISTORY_LEN)
        self._last_stats: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            placeholder="Filter by agent ID... (d=toggle disconnected, x=prune)",
            id="filter-input",
        )
        with TabbedContent():
            with TabPane("Dashboard", id="tab-dashboard"):
                yield DashboardPanel()
            with TabPane("Agents", id="tab-agents"):
                yield DataTable(id="agents-table")
            with TabPane("Locks", id="tab-locks"):
                with Vertical():
                    yield Label("", id="overlap-summary")
                    yield DataTable(id="locks-table")
            with TabPane("Subscriptions", id="tab-subs"):
                yield Tree("Subscriptions", id="subs-tree")
            with TabPane("Memory", id="tab-memory"):
                yield DataTable(id="memory-table")
            with TabPane("Notifications", id="tab-notifs"):
                yield DataTable(id="notifs-table")
            with TabPane("Log", id="tab-log"):
                yield RichLog(markup=True, auto_scroll=True, max_lines=500, id="live-log")
        yield Footer()

    def on_mount(self) -> None:
        # Agents table
        agents = self.query_one("#agents-table", DataTable)
        agents.add_columns(
            "Agent",
            "Project",
            "Worktree",
            "Branch",
            "Connected",
            "Last Activity",
            "Status",
        )

        # Locks table
        locks = self.query_one("#locks-table", DataTable)
        locks.add_columns("Agent", "Project", "Files", "Branch", "Acquired", "Expires")

        # Subs tree
        tree = self.query_one("#subs-tree", Tree)
        tree.show_root = False

        # Notifs table
        notifs = self.query_one("#notifs-table", DataTable)
        notifs.add_columns("Subscriber", "Author", "Summary", "File", "Created", "Read")

        # Memory table
        memory = self.query_one("#memory-table", DataTable)
        memory.add_columns("Metric", "Value")

        # Start polling
        self.set_interval(self._interval, self._poll)
        self._poll()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.agent_filter = event.value

    def watch_agent_filter(self, value: str) -> None:
        self._render_data(self._last_stats)

    def watch_show_disconnected(self, value: bool) -> None:
        self._render_data(self._last_stats)

    def action_focus_filter(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def action_refresh(self) -> None:
        self._poll()

    def action_toggle_disconnected(self) -> None:
        self.show_disconnected = not self.show_disconnected
        self._log(
            "TOGGLE",
            f"Disconnected agents: {'shown' if self.show_disconnected else 'hidden'}",
            "cyan",
        )

    def action_prune_disconnected(self) -> None:
        """Delete disconnected sessions from the DB."""
        import sqlite3
        from pathlib import Path

        db_path = Path.home() / ".local" / "share" / "fleet-mem" / "fleet.db"
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("DELETE FROM agent_sessions WHERE status = 'disconnected'")
            count = cursor.rowcount
            conn.commit()
            conn.close()
            self._log("PRUNE", f"Removed {count} disconnected session(s)", "yellow")
            self._poll()
        except Exception as e:
            self._log("ERROR", f"Prune failed: {e}", "red")

    def _log(self, level: str, message: str, color: str = "white") -> None:
        """Append a line to the live log."""
        import datetime

        now = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            log = self.query_one("#live-log", RichLog)
            log.write(f"[dim]{now}[/] [{color}]{level:>6}[/]  {message}")
        except Exception:
            pass

    def _poll(self) -> None:
        stats = fetch_stats(sock_path=self._sock_path, detail=True)
        self._last_stats = stats

        if "_waiting" in stats:
            try:
                self.query_one("#stats-summary", Label).update(
                    "[bold yellow]Waiting for fleet-mem server...[/]"
                )
            except Exception:
                pass
            return

        if "_error" in stats:
            try:
                self.query_one("#stats-summary", Label).update(f"[bold red]{stats['_error']}[/]")
            except Exception:
                pass
            return

        # Update sparkline history
        self._agent_history.append(float(stats.get("active_agents", 0)))
        self._lock_history.append(float(stats.get("active_locks", 0)))
        self._notif_history.append(float(stats.get("pending_notifications", 0)))
        self._memory_history.append(float(stats.get("memory_nodes", 0)))

        self._render_data(stats)

    def _render_data(self, stats: dict) -> None:
        if not stats or "_error" in stats or "_waiting" in stats:
            return

        agent_filter = self.agent_filter.strip().lower()

        # === Dashboard ===
        try:
            summary = self.query_one("#stats-summary", Label)
            server_ver = stats.get("server_version", "unknown")
            summary.update(
                f"Agents: [bold green]{stats.get('active_agents', 0)}[/]  "
                f"Chunks: [bold cyan]{stats.get('total_chunks', 0)}[/]  "
                f"Memory: [bold cyan]{stats.get('memory_nodes', 0)}[/]  "
                f"Locks: [bold cyan]{stats.get('active_locks', 0)}[/]  "
                f"Subs: [bold cyan]{stats.get('subscriptions', 0)}[/]  "
                f"Pending: [bold yellow]"
                f"{stats.get('pending_notifications', 0)}[/]  "
                f"Cache: [bold cyan]{stats.get('cached_embeddings', 0)}[/]  "
                f"Server: [bold magenta]v{server_ver}[/]"
            )
        except Exception:
            pass

        # Conflict banner
        try:
            conflicts = stats.get("conflicts", [])
            banner = self.query_one("#conflict-banner", Label)
            if conflicts:
                lines = []
                for c in conflicts:
                    files = ", ".join(c["overlapping_files"][:3])
                    extra = len(c["overlapping_files"]) - 3
                    suffix = f" +{extra} more" if extra > 0 else ""
                    lines.append(
                        f"[bold red]CONFLICT[/] {c['agent_a']} ↔ {c['agent_b']}: {files}{suffix}"
                    )
                banner.update("\n".join(lines))
            else:
                banner.update("")
        except Exception:
            pass

        # Sparklines + gauges
        try:
            for name, history in [
                ("agents", self._agent_history),
                ("locks", self._lock_history),
                ("notifs", self._notif_history),
                ("memory", self._memory_history),
            ]:
                data = list(history)
                current = int(data[-1]) if data else 0
                label = name.title()
                color = "green" if current > 0 else "dim"
                self.query_one(f"#gauge-{name}", Label).update(
                    f"{label} [bold {color}]{current}[/]"
                )
                self.query_one(f"#spark-{name}", Sparkline).data = data
        except Exception:
            pass

        # === Agents table (with row coloring) ===
        try:
            agents_table = self.query_one("#agents-table", DataTable)
            agents_table.clear()

            # Build set of agents with locks for coloring
            locked_agents = {lock.get("agent_id") for lock in stats.get("lock_details", [])}

            for agent in stats.get("agent_details", []):
                aid = agent.get("agent_id", "")
                status = agent.get("status", "unknown")

                # Filter
                if agent_filter and agent_filter not in aid.lower():
                    continue
                if not self.show_disconnected and status == "disconnected":
                    continue

                # Row color
                if status == "active" and aid in locked_agents:
                    row_color = "green"
                elif status == "active":
                    row_color = "bright_white"
                elif status == "idle":
                    row_color = "yellow"
                else:
                    row_color = "bright_black"

                worktree = agent.get("worktree_path", "") or ""
                if len(worktree) > 40:
                    worktree = "..." + worktree[-37:]

                agents_table.add_row(
                    Text(aid, style=row_color),
                    Text(agent.get("project", ""), style=row_color),
                    Text(worktree, style=row_color),
                    Text(agent.get("branch", "") or "", style=row_color),
                    Text(agent.get("connected_at", "")[:19], style=row_color),
                    Text(agent.get("last_activity_at", "")[:19], style=row_color),
                    Text(status, style=_STATUS_COLORS.get(status, "white")),
                )
        except Exception:
            pass

        # === Locks table + overlap summary ===
        try:
            locks_table = self.query_one("#locks-table", DataTable)
            locks_table.clear()
            for lock in stats.get("lock_details", []):
                if agent_filter and agent_filter not in lock.get("agent_id", "").lower():
                    continue
                patterns = lock.get("file_patterns", [])
                file_str = f"{len(patterns)} files"
                locks_table.add_row(
                    lock.get("agent_id", ""),
                    lock.get("project", ""),
                    file_str,
                    lock.get("branch", ""),
                    lock.get("acquired_at", "")[:19],
                    lock.get("expires_at", "")[:19],
                )

            # Overlap summary
            conflicts = stats.get("conflicts", [])
            overlap_label = self.query_one("#overlap-summary", Label)
            if conflicts:
                lines = []
                for c in conflicts:
                    for f in c["overlapping_files"][:5]:
                        lines.append(f"  [red]●[/] {f} → {c['agent_a']} ↔ {c['agent_b']}")
                overlap_label.update(
                    f"[bold red]{len(conflicts)} conflict(s):[/]\n" + "\n".join(lines)
                )
            else:
                overlap_label.update("[green]No lock conflicts[/]")
        except Exception:
            pass

        # === Subscriptions tree (grouped by agent) ===
        try:
            tree = self.query_one("#subs-tree", Tree)
            tree.clear()

            # Group by agent
            subs_by_agent: dict[str, list[str]] = {}
            for sub in stats.get("subscription_details", []):
                aid = sub.get("agent_id", "")
                if agent_filter and agent_filter not in aid.lower():
                    continue
                subs_by_agent.setdefault(aid, []).append(sub.get("file_pattern", ""))

            for aid, patterns in sorted(subs_by_agent.items(), key=lambda x: -len(x[1])):
                node = tree.root.add(
                    f"{aid} ({len(patterns)} files)",
                    expand=False,
                    allow_expand=True,
                )
                for p in sorted(patterns):
                    node.add_leaf(p)
        except Exception:
            pass

        # === Memory table ===
        try:
            mem_table = self.query_one("#memory-table", DataTable)
            mem_table.clear()
            mem_table.add_row("Memory Nodes", str(stats.get("memory_nodes", 0)))
            mem_table.add_row("File Anchors", str(stats.get("file_anchors", 0)))
            mem_table.add_row("Cached Embeddings", str(stats.get("cached_embeddings", 0)))
            mem_table.add_row("Total Chunks", str(stats.get("total_chunks", 0)))
            for name, count in stats.get("collections", {}).items():
                mem_table.add_row(f"  {name}", str(count))
        except Exception:
            pass

        # === Notifications table ===
        try:
            notifs_table = self.query_one("#notifs-table", DataTable)
            notifs_table.clear()
            for notif in stats.get("notification_details", []):
                sub_id = notif.get("subscriber_agent_id", "")
                author_id = notif.get("author_agent_id", "")
                if agent_filter and (
                    agent_filter not in sub_id.lower() and agent_filter not in author_id.lower()
                ):
                    continue
                read_style = "dim" if notif.get("read") else "bold"
                notifs_table.add_row(
                    Text(sub_id, style=read_style),
                    Text(author_id, style=read_style),
                    Text(notif.get("memory_summary", "")[:60], style=read_style),
                    Text(notif.get("file_path", ""), style=read_style),
                    Text(notif.get("created_at", "")[:19], style=read_style),
                    Text("yes" if notif.get("read") else "no", style=read_style),
                )
        except Exception:
            pass
