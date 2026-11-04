"""Shared helpers and arguments for the vetting CLI."""

from __future__ import annotations

import argparse


def _names(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    out: list[str] = []
    for value in values:
        for part in str(value).split(","):
            name = part.strip()
            if name and name not in out:
                out.append(name)
    return out or None


def add_common_args(parser: argparse.ArgumentParser, *,
                    answer_chars: int = 8000) -> None:
    parser.add_argument("--workers", nargs="+", default=None,
                        help="worker names to test (space or comma separated; default: all)")
    parser.add_argument("--include-assessor", action="store_true",
                        help="also test the assessor (nunnan) during an auto-run")
    parser.add_argument("--run-dir", default=None,
                        help="output directory (default: pipeline/vetting/runs/<stamp>)")
    parser.add_argument("--endpoint", default=None,
                        help="NebulonMD base URL for the brain "
                             "(e.g. http://localhost:9696/api/NebulonMind)")
    parser.add_argument("--timeout", type=float, default=None,
                        help="brain request timeout in seconds")
    parser.add_argument("--workers-file", default=None,
                        help="override workers.json")
    parser.add_argument("--permissions-file", default=None,
                        help="override permissions.json")
    parser.add_argument("--answer-chars", type=int, default=answer_chars,
                        help="maximum answer characters kept for judging "
                             f"(default: {answer_chars})")
    parser.add_argument("--skip-crafts", action="store_true",
                        help="do not probe crafts")
    parser.add_argument("--skip-tools", action="store_true",
                        help="do not probe tools")
    parser.add_argument("--json", action="store_true",
                        help="print the summary JSON instead of a table")
    parser.add_argument("--quiet", action="store_true",
                        help="only print the run directory")
