"""Unix domain socket server for fleet stats (read-only monitoring).

Serves JSON stats over a Unix socket with 0600 permissions.
No network exposure — only the socket owner can connect.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

from fleet_mem.fleet.stats import get_fleet_stats

_DEFAULT_SOCK = Path.home() / ".fleet-mem" / "stats.sock"


def _handle_client(conn: socket.socket, config) -> None:
    """Handle a single client request on the Unix socket."""
    try:
        data = conn.recv(4096).decode("utf-8", errors="replace")

        # Parse simple HTTP-like request
        detail = "detail=true" in data.lower() if data else False

        stats = get_fleet_stats(
            chroma_path=config.chroma_path,
            memory_db_path=config.memory_db_path,
            fleet_db_path=config.fleet_db_path,
            embed_cache_path=config.embed_cache_path,
            detail=detail,
        )

        body = json.dumps(stats)
        response = (
            f"HTTP/1.1 200 OK\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
            f"{body}"
        )
        conn.sendall(response.encode("utf-8"))
    except Exception:
        pass
    finally:
        conn.close()


def start_stats_server(config, sock_path: Path | None = None) -> Path:
    """Start the Unix domain socket stats server in a daemon thread.

    Returns the socket path.
    """
    path = sock_path or Path(os.environ.get("FLEET_STATS_SOCK", str(_DEFAULT_SOCK)))
    path.parent.mkdir(parents=True, exist_ok=True)

    # Clean up stale socket
    if path.exists():
        path.unlink()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(str(path), 0o600)
    server.listen(5)

    def _serve():
        while True:
            try:
                conn, _ = server.accept()
                t = threading.Thread(target=_handle_client, args=(conn, config), daemon=True)
                t.start()
            except OSError:
                break

    thread = threading.Thread(target=_serve, daemon=True, name="fleet-stats-sock")
    thread.start()

    return path
