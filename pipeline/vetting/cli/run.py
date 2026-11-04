"""``nak-vetting run``: evaluate workers with Nunnan as assessor."""

from __future__ import annotations

import argparse
import json
import sys

from ..utils.runner import run_vetting
from .common import _names, add_common_args


def configure(parser: argparse.ArgumentParser) -> None:
    add_common_args(parser, answer_chars=8000)
    parser.add_argument("--cases", type=int, default=6,
                        help="test cases per worker "
                             "(default: 6 = 2 easy, 2 mid, 2 hard)")
    parser.add_argument("--threshold", type=float, default=0.6,
                        help="passing score in [0,1] (default: 0.6)")
    parser.add_argument("--target-decision", action="store_true",
                        default=False,
                        help="keep the target worker's triage/policy layer "
                             "enabled (default: disabled for capability vetting)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nak-vetting run",
        description="Evaluate NebulonAK workers with Nunnan as the impartial assessor.",
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.cases < 1:
        print("error: --cases must be >= 1", file=sys.stderr)
        return 2
    if not 0.0 <= args.threshold <= 1.0:
        print("error: --threshold must be in [0, 1]", file=sys.stderr)
        return 2
    if args.answer_chars < 1:
        print("error: --answer-chars must be >= 1", file=sys.stderr)
        return 2
    try:
        summary = run_vetting(
            _names(args.workers),
            run_dir=args.run_dir,
            workers_file=args.workers_file,
            permissions_file=args.permissions_file,
            endpoint=args.endpoint,
            timeout=args.timeout,
            cases=args.cases,
            threshold=args.threshold,
            answer_chars=args.answer_chars,
            probe_crafts_enabled=not args.skip_crafts,
            probe_tools_enabled=not args.skip_tools,
            include_assessor=args.include_assessor,
            target_decision=args.target_decision,
        )
    except Exception as exc:  # noqa: BLE001 — CLI must fail cleanly
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.quiet:
        print(summary.run_dir)
    elif args.json:
        print(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False,
                         sort_keys=True))
    else:
        print(f"run: {summary.run_dir}")
        if summary.error:
            print(f"error: {summary.error}")
        for worker in summary.workers:
            verdict = ("INCONCLUSIVE" if worker.status == "inconclusive"
                       else "PASS" if worker.passed else "FAIL")
            score = ("  n/a  " if worker.status == "inconclusive"
                     else f"{worker.score:.3f}")
            print(f"  {worker.name:<12} {score}  {verdict}"
                  + (f"  ({worker.error})" if worker.error else ""))
        for skipped in summary.skipped:
            print(f"  skipped: {skipped}")
        print(f"summary: {summary.run_dir}/summary.md")
    return summary.exit_code


def main(argv: list[str] | None = None) -> int:
    return run(_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
