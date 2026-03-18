"""CLI entry point for fleet-mem commands."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="fleet-mem",
        description="Fleet-Mem: shared code intelligence for agent fleets",
    )
    sub = parser.add_subparsers(dest="command")

    # monitor subcommand
    mon = sub.add_parser("monitor", help="Launch the fleet monitoring TUI")
    mon.add_argument(
        "--sock",
        default="",
        help="Unix socket path (default: ~/.fleet-mem/stats.sock)",
    )
    mon.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Poll interval in seconds (default: 2.0)",
    )

    args = parser.parse_args()

    if args.command == "monitor":
        try:
            from fleet_mem.monitor.app import FleetMonitorApp
        except ImportError:
            print(
                "Monitor extras not installed. Run:\n  pip install fleet-mem[monitor]",
                file=sys.stderr,
            )
            sys.exit(1)

        app = FleetMonitorApp(sock_path=args.sock, interval=args.interval)
        app.run()
    else:
        parser.print_help()
        sys.exit(1)
