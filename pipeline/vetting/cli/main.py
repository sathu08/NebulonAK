"""Unified command line entry point for the vetting pipeline."""

from __future__ import annotations

import argparse
import sys

from . import card, improve, run as run_cmd


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nak-vetting",
        description="Vet NebulonAK workers and build report cards with Nunnan.",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    run_parser = sub.add_parser(
        "run", help="evaluate workers with Nunnan as the impartial assessor")
    run_cmd.configure(run_parser)
    card_parser = sub.add_parser(
        "card", help="score workers on persona, behavior, tools and craft")
    card.configure(card_parser)
    improve_parser = sub.add_parser(
        "improve", help="plan, apply or discard improvement candidates")
    improve.configure(improve_parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args_in = list(sys.argv[1:] if argv is None else argv)
    if not args_in or (args_in[0].startswith("-")
                       and args_in[0] not in ("-h", "--help")):
        args_in.insert(0, "run")
    args = _parser().parse_args(args_in)
    if args.command == "run":
        return run_cmd.run(args)
    if args.command == "card":
        return card.run(args)
    if args.command == "improve":
        return improve.run(args)
    _parser().print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
