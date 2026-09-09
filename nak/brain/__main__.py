"""
CLI entry: python -m nak.brain

Commands:
    python -m nak.brain --check                  # full connectivity probe
    python -m nak.brain --health                 # just health
    python -m nak.brain --create-user alice      # create/ensure user
    python -m nak.brain --chat "hello"           # agent chat
    python -m nak.brain --search "query"         # semantic search
    python -m nak.brain --remember "text"        # store memory
    python -m nak.brain --help
"""

from __future__ import annotations

import sys
import json
import argparse

from nak.brain.client import Brain

from nak.utils.config import load_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nak.brain", description="NAK Brain CLI -- talks to NebulonMind")
    parser.add_argument("--cfg", type=str, default=None, help="Path to nebulonak.cfg")
    parser.add_argument("--base-url", type=str, default=None, help="Override NebulonMind base URL")
    parser.add_argument("--user", type=str, default=None, help="Override username")
    parser.add_argument("--check", action="store_true", help="Full connectivity probe (health + LLM + user + write/search)")
    parser.add_argument("--no-probe-write", action="store_true", help="Skip write probe in --check")
    parser.add_argument("--health", action="store_true", help="Show health (live + ready + combined)")
    parser.add_argument("--llm", action="store_true", help="Show LLM status")
    parser.add_argument("--create-user", type=str, metavar="USERNAME", help="Ensure user exists")
    parser.add_argument("--resolve-user", type=str, metavar="USERNAME", help="Resolve user (no create)")
    parser.add_argument("--chat", type=str, metavar="TEXT", help="Agent chat turn")
    parser.add_argument("--search", type=str, metavar="QUERY", help="Semantic search query")
    parser.add_argument("--remember", type=str, metavar="TEXT", help="Store memory text")
    parser.add_argument("--context", type=str, metavar="QUERY", help="Build bounded context for query")
    parser.add_argument("--top-k", type=int, default=None, help="Top-k for search/context")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args(argv)

    if args.verbose:
        import logging
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        cfg = load_config(args.cfg)
    except Exception as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    base_url = args.base_url or cfg.base_url
    user = args.user or cfg.user

    brain = Brain(base_url=base_url, user=user, cfg_path=cfg.cfg_path)

    # no args -> same as --check
    if not any([args.check, args.health, args.llm, args.create_user, args.resolve_user, args.chat, args.search, args.remember, args.context]):
        args.check = True

    def _print(obj, label: str = ""):
        if args.json:
            print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
        else:
            if label:
                print(f"\n== {label} ==")
            print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))

    exit_code = 0

    if args.check:
        probe = brain.test_connection(probe_write=not args.no_probe_write, verbose=True)
        _print(probe, "CONNECTIVITY PROBE")
        if not probe.get("ok"):
            exit_code = 1
            if not args.json:
                print("\nProbe FAILED -- check that NebulonMind is running:", file=sys.stderr)
                print(f"  NebulonMind: {brain.base_url}", file=sys.stderr)
                print(f"  NebulonDB must be up (port 6969) and NebulonMind up (port 9696)", file=sys.stderr)
                print(f"  Try: curl {brain.base_url}/health", file=sys.stderr)
        else:
            if not args.json:
                print("\nAll probes PASSED -- brain is reachable.", file=sys.stderr)

    if args.health:
        _print({"live": brain.health_live()}, "health/live")
        # ready may 503, handle via test_connection style
        try:
            _print({"ready": brain.health_ready()}, "health/ready")
        except Exception as exc:
            _print({"ready_error": str(exc)}, "health/ready")
        _print({"health": brain.health()}, "health")

    if args.llm:
        _print(brain.llm_status(), "llm/status")

    if args.create_user:
        _print(brain.ensure_user(args.create_user), f"ensure_user({args.create_user!r})")

    if args.resolve_user:
        try:
            _print(brain.resolve_user(args.resolve_user), f"resolve_user({args.resolve_user!r})")
        except Exception as exc:
            print(f"resolve failed: {exc}", file=sys.stderr)
            exit_code = 1

    if args.remember:
        _print(brain.remember(args.remember), f"remember({args.remember!r})")

    if args.search:
        _print({"query": args.search, "results": brain.search(args.search, top_k=args.top_k)}, f"search({args.search!r})")

    if args.context:
        _print(brain.context(args.context, top_k=args.top_k), f"context({args.context!r})")

    if args.chat:
        _print(brain.chat(args.chat), f"agent/chat({args.chat!r})")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
