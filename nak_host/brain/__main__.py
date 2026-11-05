"""CLI: python -m nak_host.brain  (installed as `nak-brain`).

All commands talk to NebulonMD through Brain — including --chat,
which runs Mind's LLM loop server-side.
"""

from __future__ import annotations

import argparse
import json
import sys

from nak_host.brain.client import Brain
from nak_host.brain.config import load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="nak-brain", description="NAK Brain CLI — talks to NebulonMD"
    )
    parser.add_argument("--cfg", default=None, help="Path to nebulonak.cfg")
    parser.add_argument("--base-url", default=None, help="Override Mind base URL")
    parser.add_argument("--user", default=None, help="Override username")
    parser.add_argument("--check", action="store_true", help="Full probe: health + LLM + user + write/search")
    parser.add_argument("--no-probe-write", action="store_true", help="Skip write probe in --check")
    parser.add_argument("--health", action="store_true", help="Show live + ready + health")
    parser.add_argument("--llm", action="store_true", help="Show Mind LLM status (read-only)")
    parser.add_argument("--create-user", metavar="USERNAME", help="Ensure user exists")
    parser.add_argument("--resolve-user", metavar="USERNAME", help="Resolve user (no create)")
    parser.add_argument("--chat", metavar="TEXT", help="Agent chat turn (via Mind LLM)")
    parser.add_argument("--search", metavar="QUERY", help="Semantic search")
    parser.add_argument("--remember", metavar="TEXT", help="Store memory text")
    parser.add_argument("--context", metavar="QUERY", help="Build bounded context (in Mind)")
    parser.add_argument("--top-k", type=int, default=None, help="Top-k for search/context")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    if args.verbose:
        import logging

        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        cfg = load_config(args.cfg)
    except Exception as exc:  # noqa: BLE001
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    problems = cfg.validate()
    if problems:
        print("config invalid:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    brain = Brain(base_url=args.base_url or cfg.base_url, user=args.user or cfg.user,
                  cfg_path=cfg.cfg_path)

    if not any([args.check, args.health, args.llm, args.create_user,
                args.resolve_user, args.chat, args.search, args.remember, args.context]):
        args.check = True

    def _print(obj, label: str = ""):
        if label and not args.json:
            print(f"\n== {label} ==")
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))

    exit_code = 0

    if args.check:
        probe = brain.test_connection(probe_write=not args.no_probe_write, verbose=True)
        _print(probe, "CONNECTIVITY PROBE")
        if not probe.get("ok"):
            exit_code = 1
            if not args.json:
                print("\nProbe FAILED — is NebulonMD up?", file=sys.stderr)
                print(f"  Mind: {brain.base_url}  (NebulonDB :6969 first, then Mind :9696)",
                      file=sys.stderr)
                print(f"  Try: curl {brain.base_url}/health", file=sys.stderr)
        elif not args.json:
            print("\nAll probes PASSED.", file=sys.stderr)

    if args.health:
        _print({"live": brain.health_live()}, "health/live")
        try:
            _print({"ready": brain.health_ready()}, "health/ready")
        except Exception as exc:  # noqa: BLE001
            _print({"ready_error": str(exc)}, "health/ready")
            exit_code = 1
        _print({"health": brain.health()}, "health")

    if args.llm:
        _print(brain.llm_status(), "llm/status (Mind-owned)")

    if args.create_user:
        _print(brain.ensure_user(args.create_user), f"ensure_user({args.create_user!r})")

    if args.resolve_user:
        try:
            _print(brain.resolve_user(args.resolve_user), f"resolve_user({args.resolve_user!r})")
        except Exception as exc:  # noqa: BLE001
            print(f"resolve failed: {exc}", file=sys.stderr)
            exit_code = 1

    if args.remember or args.search or args.context or args.chat:
        # Hard gate: Mind down (or Mind without DB) -> refuse, do nothing.
        try:
            brain.require_mind()
        except Exception as exc:  # noqa: BLE001
            print(f"refused: {exc}", file=sys.stderr)
            return 1

    if args.remember:
        _print(brain.remember(args.remember), f"remember({args.remember!r})")

    if args.search:
        _print({"query": args.search,
                "results": brain.search(args.search, top_k=args.top_k)},
               f"search({args.search!r})")

    if args.context:
        _print(brain.context(args.context, top_k=args.top_k), f"context({args.context!r})")

    if args.chat:
        _print(brain.chat(args.chat), f"agent/chat({args.chat!r})")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
