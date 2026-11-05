"""nak_host server CLI: start|stop|restart|status. No TUI, no prompts.

Usage:
    python nebulonak.py start [--foreground] [--force]
    python nebulonak.py stop [--force]
    python nebulonak.py restart [--foreground] [--force]
    python nebulonak.py status
"""

from __future__ import annotations

import argparse
import sys

from nak_host.cli import ops


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nebulonak",
        description="Host the NebulonAK API + web console (no interactive UI).",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    p_start = sub.add_parser("start", help="start the server (background by default)")
    p_start.add_argument("--foreground", "-f", action="store_true",
                         help="run in the foreground (logs to terminal)")
    p_start.add_argument("--force", action="store_true",
                         help="start even if the port looks busy (no-op guard)")
    p_stop = sub.add_parser("stop", help="stop the server")
    p_stop.add_argument("--force", action="store_true",
                        help="SIGKILL instead of SIGTERM")
    p_restart = sub.add_parser("restart", help="stop (if running) then start")
    p_restart.add_argument("--foreground", "-f", action="store_true")
    p_restart.add_argument("--force", action="store_true")
    sub.add_parser("status", help="pid alive? port open? host/port/pid file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "start":
        return 0 if ops.start(foreground=args.foreground) else 1
    if args.command == "stop":
        return 0 if ops.stop(force=args.force) else 1
    if args.command == "restart":
        return 0 if ops.restart(foreground=args.foreground,
                                force=args.force) else 1
    if args.command == "status":
        info = ops.describe()
        print(f"pid: {info['pid']} (alive: {info['pid_alive']})")
        print(f"listening: {info['host']}:{info['port']} "
              f"(open: {info['port_open']})")
        print(f"pid_file: {info['pid_file']}")
        print(f"logs_dir: {info['logs_dir']}")
        return 0 if info["pid_alive"] or info["port_open"] else 1
    _parser().print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
