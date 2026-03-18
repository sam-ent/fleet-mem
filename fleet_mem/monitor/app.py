"""Fleet-Mem monitoring TUI built with Textual."""

from __future__ import annotations

from collections import deque

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Sparkline,
    Static,
    TabbedContent,
    TabPane,
)

from fleet_mem.monitor.client import fetch_stats

# Max history for sparklines
_HISTORY_LEN = 60

# Status display colors
_STATUS_COLORS = {
    "active": "green",
    "idle": "yellow",
    "disconnected": "red",
}


class StatsPanel(Static):
    """Displays aggregate fleet statistics."""

    def compose(self) -> ComposeResult:
        yield Label("Connecting...", id="stats-summary")
        with Horizontal(classes="sparkline-row"):
            with Container(classes="sparkline-box"):
                yield Label("Agents", classes="sparkline-label")
                yield Sparkline([], id="spark-agents")
            with Container(classes="sparkline-box"):
                yield Label("Locks", classes="sparkline-label")
                yield Sparkline([], id="spark-locks")
            with Container(classes="sparkline-box"):
                yield Label("Notifications", classes="sparkline-label")
                yield Sparkline([], id="spark-notifs")
            with Container(classes="sparkline-box"):
                yield Label("Memory", classes="sparkline-label")
                yield Sparkline([], id="spark-memory")


class FleetMonitorApp(App):
    """btop-style TUI for fleet-mem coordination health."""

    TITLE = "fleet-mem monitor"
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
    DataTable {
        height: 1fr;
    }
    .error-msg {
        color: $error;
        text-align: center;
        padding: 2;
    }
    TabPane {
        padding: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("f", "focus_filter", "Filter"),
    ]

    agent_filter = reactive("")

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
        yield Input(placeholder="Filter by agent ID...", id="filter-input")
        with TabbedContent():
            with TabPane("Agents", id="tab-agents"):
                yield DataTable(id="agents-table")
            with TabPane("Stats", id="tab-stats"):
                yield StatsPanel()
            with TabPane("Locks", id="tab-locks"):
                yield DataTable(id="locks-table")
            with TabPane("Memory", id="tab-memory"):
                yield DataTable(id="memory-table")
            with TabPane("Subscriptions", id="tab-subs"):
                yield DataTable(id="subs-table")
            with TabPane("Notifications", id="tab-notifs"):
                yield DataTable(id="notifs-table")
        yield Footer()

    def on_mount(self) -> None:
        # Set up tables
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

        locks = self.query_one("#locks-table", DataTable)
        locks.add_columns(
            "Agent",
            "Project",
            "Patterns",
            "Branch",
            "Acquired",
            "Expires",
        )

        subs = self.query_one("#subs-table", DataTable)
        subs.add_columns("Agent", "Project", "Pattern", "Created")

        notifs = self.query_one("#notifs-table", DataTable)
        notifs.add_columns(
            "Subscriber",
            "Author",
            "Summary",
            "File",
            "Created",
            "Read",
        )

        memory = self.query_one("#memory-table", DataTable)
        memory.add_columns("Metric", "Value")

        # Start polling
        self.set_interval(self._interval, self._poll)
        # Immediate first fetch
        self._poll()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.agent_filter = event.value

    def watch_agent_filter(self, value: str) -> None:
        self._render_data(self._last_stats)

    def action_focus_filter(self) -> None:
        self.query_one("#filter-input", Input).focus()

    def action_refresh(self) -> None:
        self._poll()

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
        if not stats or "_error" in stats:
            return

        agent_filter = self.agent_filter.strip().lower()

        # Stats summary
        try:
            summary = self.query_one("#stats-summary", Label)
            summary.update(
                f"Agents: [bold green]{stats.get('active_agents', 0)}[/]  "
                f"Chunks: [bold cyan]{stats.get('total_chunks', 0)}[/]  "
                f"Memory: [bold cyan]{stats.get('memory_nodes', 0)}[/]  "
                f"Locks: [bold cyan]{stats.get('active_locks', 0)}[/]  "
                f"Subs: [bold cyan]{stats.get('subscriptions', 0)}[/]  "
                f"Pending: [bold yellow]"
                f"{stats.get('pending_notifications', 0)}[/]  "
                f"Cache: [bold cyan]{stats.get('cached_embeddings', 0)}[/]"
            )
        except Exception:
            pass

        # Sparklines
        try:
            self.query_one("#spark-agents", Sparkline).data = list(self._agent_history)
            self.query_one("#spark-locks", Sparkline).data = list(self._lock_history)
            self.query_one("#spark-notifs", Sparkline).data = list(self._notif_history)
            self.query_one("#spark-memory", Sparkline).data = list(self._memory_history)
        except Exception:
            pass

        # Agents table
        try:
            agents_table = self.query_one("#agents-table", DataTable)
            agents_table.clear()
            for agent in stats.get("agent_details", []):
                aid = agent.get("agent_id", "")
                if agent_filter and agent_filter not in aid.lower():
                    continue
                status = agent.get("status", "unknown")
                color = _STATUS_COLORS.get(status, "white")
                worktree = agent.get("worktree_path", "") or ""
                # Shorten worktree path for display
                if len(worktree) > 40:
                    worktree = "..." + worktree[-37:]
                agents_table.add_row(
                    aid,
                    agent.get("project", ""),
                    worktree,
                    agent.get("branch", "") or "",
                    agent.get("connected_at", "")[:19],
                    agent.get("last_activity_at", "")[:19],
                    f"[{color}]{status}[/]",
                )
        except Exception:
            pass

        # Locks table
        try:
            locks_table = self.query_one("#locks-table", DataTable)
            locks_table.clear()
            for lock in stats.get("lock_details", []):
                if agent_filter and agent_filter not in (lock.get("agent_id", "").lower()):
                    continue
                patterns = ", ".join(lock.get("file_patterns", []))
                locks_table.add_row(
                    lock.get("agent_id", ""),
                    lock.get("project", ""),
                    patterns,
                    lock.get("branch", ""),
                    lock.get("acquired_at", "")[:19],
                    lock.get("expires_at", "")[:19],
                )
        except Exception:
            pass

        # Memory table
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

        # Subscriptions table
        try:
            subs_table = self.query_one("#subs-table", DataTable)
            subs_table.clear()
            for sub in stats.get("subscription_details", []):
                if agent_filter and agent_filter not in (sub.get("agent_id", "").lower()):
                    continue
                subs_table.add_row(
                    sub.get("agent_id", ""),
                    sub.get("project", ""),
                    sub.get("file_pattern", ""),
                    sub.get("created_at", "")[:19],
                )
        except Exception:
            pass

        # Notifications table
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
                notifs_table.add_row(
                    sub_id,
                    author_id,
                    notif.get("memory_summary", "")[:60],
                    notif.get("file_path", ""),
                    notif.get("created_at", "")[:19],
                    "yes" if notif.get("read") else "no",
                )
        except Exception:
            pass
