"""``nak-vetting card``: score workers on persona, behavior, tools and craft."""

from __future__ import annotations

import argparse
import json
import sys

from ..report_card.models import DEFAULT_WEIGHTS, SECTIONS
from ..report_card.runner import run_report_card
from .common import _names, add_common_args


def _weights(value: str | None) -> dict[str, float]:
    out = dict(DEFAULT_WEIGHTS)
    if not value:
        return out
    for part in value.split(","):
        if "=" not in part:
            raise ValueError(f"bad weight {part!r}; want section=number")
        key, raw = part.split("=", 1)
        key = key.strip().lower()
        if key not in SECTIONS:
            raise ValueError(f"unknown section {key!r}; have: {list(SECTIONS)}")
        number = float(raw)
        if number < 0:
            raise ValueError(f"weight for {key} must be >= 0")
        out[key] = number
    if sum(out.values()) <= 0:
        raise ValueError("weights must sum to more than zero")
    return out


def configure(parser: argparse.ArgumentParser) -> None:
    add_common_args(parser, answer_chars=4000)
    parser.add_argument("--persona-cases", type=int, default=10,
                        help="persona scenarios per worker (default: 10, max: 10)")
    parser.add_argument("--behavior-cases", type=int, default=10,
                        help="behavior cases per worker (default: 10)")
    parser.add_argument("--threshold", type=float, default=60.0,
                        help="passing overall score out of 100 (default: 60)")
    parser.add_argument("--weights", default=None,
                        help="section weights, e.g. "
                             "persona=25,behavior=25,tools=25,craft=25")
    parser.add_argument("--skip-persona", action="store_true",
                        help="do not run persona scenarios")
    parser.add_argument("--skip-behavior", action="store_true",
                        help="do not run the behavior suite")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nak-vetting card",
        description="Score every worker on persona, behavior, tools and craft.",
    )
    configure(parser)
    return parser


def run(args: argparse.Namespace) -> int:
    if args.persona_cases < 0 or args.persona_cases > 10:
        print("error: --persona-cases must be between 0 and 10", file=sys.stderr)
        return 2
    if args.behavior_cases < 0:
        print("error: --behavior-cases must be >= 0", file=sys.stderr)
        return 2
    if not 0.0 <= args.threshold <= 100.0:
        print("error: --threshold must be in [0, 100]", file=sys.stderr)
        return 2
    if args.answer_chars < 1:
        print("error: --answer-chars must be >= 1", file=sys.stderr)
        return 2
    try:
        weights = _weights(args.weights)
    except (TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        summary = run_report_card(
            _names(args.workers),
            run_dir=args.run_dir,
            workers_file=args.workers_file,
            permissions_file=args.permissions_file,
            endpoint=args.endpoint,
            timeout=args.timeout,
            persona_cases=0 if args.skip_persona else args.persona_cases,
            behavior_cases=0 if args.skip_behavior else args.behavior_cases,
            threshold=args.threshold,
            weights=weights,
            answer_chars=args.answer_chars,
            include_assessor=args.include_assessor,
            probe_crafts_enabled=not args.skip_crafts,
            probe_tools_enabled=not args.skip_tools,
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
        print(f"report: {summary.report_dir}/summary.md")
        if summary.error:
            print(f"error: {summary.error}")
        for card in summary.cards:
            values = []
            for name in SECTIONS:
                section = card.sections.get(name)
                values.append("n/a" if section is None or section.score is None
                              else f"{section.score:.1f}")
            verdict = "PASS" if card.passed else "FAIL"
            print(f"  {card.worker:<12} persona={values[0]} "
                  f"behavior={values[1]} tools={values[2]} craft={values[3]} "
                  f"overall={card.overall:.1f} {card.grade} {verdict}"
                  + (f"  ({card.error})" if card.error else ""))
        for skipped in summary.skipped:
            print(f"  skipped: {skipped}")
    return summary.exit_code


def main(argv: list[str] | None = None) -> int:
    return run(_parser().parse_args(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
