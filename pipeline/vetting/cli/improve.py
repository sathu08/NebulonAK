"""``nak-vetting improve``: plan, apply or discard improvement candidates."""

from __future__ import annotations

import argparse
import json
import sys

from ..improve import (
    apply_improvements,
    discard_candidate,
    plan_improvements,
)
from .common import _names


def configure(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="action", required=True)

    plan = sub.add_parser(
        "plan", help="show the staged-craft plan for an existing run")
    plan.add_argument("run_dir", help="vetting run directory")
    plan.add_argument("--crafts-prompt", default=None,
                      help="override the crafts prompt root")
    plan.add_argument("--workspace-root", default=None,
                      help="override the workspace root")
    plan.add_argument("--quiet", action="store_true",
                      help="only print the run directory")

    apply = sub.add_parser(
        "apply", help="stage candidates, test with core, then re-vet")
    apply.add_argument("run_dir", help="vetting run directory")
    apply.add_argument("--workers", nargs="+", default=None,
                       help="workers to re-vet during apply "
                            "(default: affected workers)")
    apply.add_argument("--workers-file", default=None,
                       help="override workers.json")
    apply.add_argument("--crafts-prompt", default=None,
                       help="override the crafts prompt root")
    apply.add_argument("--workspace-root", default=None,
                       help="override the workspace root")
    apply.add_argument("--threshold", type=float, default=0.6,
                       help="passing score in [0,1] (default: 0.6)")
    apply.add_argument("--cases", type=int, default=6,
                       help="test cases per worker during verify (default: 6)")
    apply.add_argument("--endpoint", default=None,
                       help="NebulonMD base URL for the brain")
    apply.add_argument("--timeout", type=float, default=None,
                       help="brain request timeout in seconds")
    apply.add_argument("--no-core", action="store_true",
                       help="skip the core-team test during apply")
    apply.add_argument("--no-verify", action="store_true",
                       help="skip the Nunnan re-vet during apply")
    apply.add_argument("--quiet", action="store_true",
                       help="only print the staged candidate directory")

    discard = sub.add_parser(
        "discard", help="remove a staged candidate tree (history is kept)")
    discard.add_argument("run_id", help="run id or candidate directory name")
    discard.add_argument("--workspace-root", default=None,
                         help="override the workspace root")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nak-vetting improve",
        description="Turn vetting findings into staged craft candidates.",
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.action == "plan":
        try:
            result = plan_improvements(
                args.run_dir,
                crafts_prompt=args.crafts_prompt,
                workspace_root=args.workspace_root,
            )
        except Exception as exc:  # noqa: BLE001 — CLI must fail cleanly
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.quiet:
            print(result["run_dir"])
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False,
                             sort_keys=True))
        return 0

    if args.action == "apply":
        if args.cases < 1:
            print("error: --cases must be >= 1", file=sys.stderr)
            return 2
        if not 0.0 <= args.threshold <= 1.0:
            print("error: --threshold must be in [0, 1]", file=sys.stderr)
            return 2
        try:
            result = apply_improvements(
                args.run_dir,
                workers=_names(args.workers),
                workers_file=args.workers_file,
                crafts_prompt=args.crafts_prompt,
                workspace_root=args.workspace_root,
                threshold=args.threshold,
                cases=args.cases,
                endpoint=args.endpoint,
                timeout=args.timeout,
                test_core=not args.no_core,
                verify=not args.no_verify,
            )
        except Exception as exc:  # noqa: BLE001 — CLI must fail cleanly
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.quiet:
            print(result.get("candidate_root") or result.get("run_dir"))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False,
                             sort_keys=True))
        return 0 if result.get("status") in (
            "staged", "tested", "verified") else 1

    try:
        removed = discard_candidate(args.workspace_root, args.run_id)
    except Exception as exc:  # noqa: BLE001 — CLI must fail cleanly
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": args.run_id, "discarded": removed},
                     indent=2, sort_keys=True))
    return 0 if removed else 1


def main(argv: list[str] | None = None) -> int:
    return run(_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
