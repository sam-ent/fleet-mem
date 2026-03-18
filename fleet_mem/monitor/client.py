"""Client for the fleet-mem stats Unix socket."""

from __future__ import annotations

import json
import socket
from pathlib import Path

_DEFAULT_SOCK = Path.home() / ".fleet-mem" / "stats.sock"


def fetch_stats(sock_path: str = "", detail: bool = True) -> dict:
    """Fetch stats from the Unix domain socket server.

    Returns parsed JSON dict, or an error dict on failure.
    """
    path = sock_path or str(_DEFAULT_SOCK)

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(path)

        query = "?detail=true" if detail else ""
        request = f"GET /stats{query} HTTP/1.1\r\nHost: localhost\r\n\r\n"
        sock.sendall(request.encode("utf-8"))

        # Read response
        chunks = []
        while True:
            data = sock.recv(65536)
            if not data:
                break
            chunks.append(data)
        sock.close()

        raw = b"".join(chunks).decode("utf-8", errors="replace")

        # Parse past HTTP headers
        if "\r\n\r\n" in raw:
            body = raw.split("\r\n\r\n", 1)[1]
        else:
            body = raw

        return json.loads(body)
    except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError, OSError):
        # Stale socket or server not running — clean up stale socket if possible
        sock_file = Path(path)
        if sock_file.exists():
            try:
                sock_file.unlink()
            except OSError:
                pass
        return {"_waiting": True}
    except json.JSONDecodeError as exc:
        return {"_error": f"Invalid JSON from stats socket: {exc}"}
    except Exception as exc:
        return {"_error": str(exc)}
