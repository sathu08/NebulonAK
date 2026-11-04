"""CLI: python -m nak.worker (installed as `nak-worker`).

Individual worker: each worker runs standalone via one Brain call.
Tools/harness are placeholders — pass values in code, not bundled here.

  --list                  all worker names
  --worker NAME --show    entry + identity (+ placeholders if passed/stored)
  --worker NAME --prompt  rendered prompt block
  --worker NAME --chat T  one turn (single Brain call)
  --worker NAME --craft GOAL  plan -> write nak_plan.md -> approve when
                          needed -> assigned workers run as peers in threads
                          (--approve auto-accepts, --no-parallel is serial)
  --team-name NAME --craft GOAL  omit --worker: the team leader plans
  --worker NAME --team GOAL  deprecated alias for --craft
  --worker NAME --session SID  show a shared session (ownership, turns,
                          injected context) — read-only, for debugging
  --set KEY=VALUE         in-memory identity patch (repeatable)

All LLM goes via Brain -> NebulonMD.
"""

from __future__ import annotations

import argparse
import json
import sys

from nak.worker.engine.worker import list_workers


def _prompt_template(entry: dict) -> str:
    """Minimal prompt file for a --create worker ({{...}} rendered at runtime)."""
    name = str(entry.get("name", "worker"))
    return (
        "# {{name}} — {{role}}\n"
        "\n"
        "> Human-readable identity view for the worker entry in\n"
        "> `workers.json`. Edit the wording freely; double-brace values render from\n"
        "> the entry's inline identity at runtime. Mapped via\n"
        f"> `\"prompt_file\": \"prompts/{name}.md\"` in `workers.json`.\n"
        "\n"
        "You are {{name}}, {{role}}.\n"
        "\n"
        "- Personality: {{personality}}\n"
        "- Tone: {{tone}}\n"
        "- Formality: {{formality}}\n"
        "- Humor: {{humor}}\n"
        "- Gender: {{gender}}\n"
        "- Language: {{language}}\n"
        "- Values: {{values}}\n"
        "- Never: {{do_not}}\n"
        "- Today is {{today}} (UTC).\n"
        "\n"
        "## How you act\n"
        "\n"
        "- Answer the user's message directly; keep it short unless detail is asked.\n"
        "- Ask at most one follow-up question, only when it genuinely helps.\n"
        "\n"
        "## Crafts\n"
        "\n"
        "{{crafts}}\n"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nak-worker", description="Run any JSON-defined worker")
    parser.add_argument("--worker", default=None, help="Worker name in workers.json (required unless --list)")
    parser.add_argument("--list", action="store_true", help="List all workers and exit")
    parser.add_argument("--show", action="store_true", help="Show entry + identity")
    parser.add_argument("--prompt", action="store_true", help="Print rendered prompt block")
    parser.add_argument("--chat", metavar="TEXT", help="Run one turn")
    parser.add_argument("--craft", metavar="GOAL", help="Run one craft: plan -> write nak_plan.md -> approve -> run assigned workers as peers")
    parser.add_argument("--approve", action="store_true", help="With --craft: auto-approve a plan that needs approval")
    parser.add_argument("--team", metavar="GOAL", help="Deprecated alias for --craft (team is part of craft)")
    parser.add_argument("--team-name", default=None, metavar="NAME", help="With --craft: team roster to plan for; when --worker is omitted the team leader plans")
    parser.add_argument("--max-workers", type=int, default=None, metavar="N", help="With --craft: cap the worker thread pool")
    parser.add_argument("--no-parallel", dest="parallel", action="store_false", help="With --craft: run assigned workers one at a time (default: team.parallel)")
    parser.add_argument("--decision", action="store_true",
                        help="With --craft: enable per-step decision routing (sole craft)")
    parser.add_argument("--decision-craft", default=None, metavar="NAME",
                        help="With --craft: use this craft's decision block to route plan steps")
    parser.add_argument("--no-decision-parallel", dest="decision_parallel",
                        action="store_false",
                        help="With --craft: decide steps one at a time")
    parser.set_defaults(parallel=None, decision_parallel=None)
    parser.add_argument("--session", metavar="SID", help="Show a shared session: ownership, turns, injected context (read-only)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="Patch identity field")
    parser.add_argument("--create", metavar="NAME", help="Print new workers.json entry + deny-all permissions.json entry (add --apply to write files)")
    parser.add_argument("--apply", action="store_true", help="With --create: append entry to workers.json + permission to permissions.json + create prompt file (never overwrites)")
    parser.add_argument("--caps", default="", help="Extra capability names for --create (comma-separated, stored as-is)")
    parser.add_argument("--role", default="Assistant", help="Role for --create (default Assistant)")
    parser.add_argument("--gender", default="unspecified", help="Gender for --create (default unspecified)")
    parser.add_argument("--json", action="store_true", help="Emit JSON only")
    args = parser.parse_args(argv)

    def _print(obj, label: str = ""):
        if label and not args.json:
            print(f"\n== {label} ==")
        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))

    if args.list:
        _print([{"name": e["name"], "type": e["type"],
                 "capabilities": e.get("capabilities", [])} for e in list_workers()], "WORKERS")
        return 0

    if args.create:
        from pathlib import Path

        from nak.utils.settings import (
            default_permission_entry, default_worker_entry, load_settings,
        )

        settings = load_settings()
        extra_caps = [c.strip() for c in str(args.caps or "").split(",") if c.strip()]
        entry = default_worker_entry(args.create, role=args.role,
                                     gender=args.gender, s=settings,
                                     capabilities=extra_caps or None)
        perm = default_permission_entry(entry.get("name", args.create),
                                        entry.get("capabilities", []), s=settings)
        if not args.apply:
            _print(entry, "NEW WORKER (append to workers.json — or re-run with --apply)")
            _print({entry.get("name", args.create): perm},
                   "NEW PERMISSION (append under workers in permissions.json — or re-run with --apply)")
            return 0
        # --apply: write files (fail loud on duplicates, never overwrite).
        wpath = Path(settings.workers_path()).expanduser()
        ppath = Path(settings.permissions_path()).expanduser()
        try:
            workers_raw = json.loads(wpath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"--apply: workers file unreadable {wpath}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(workers_raw, list):
            print(f"--apply: {wpath} must be a JSON list", file=sys.stderr)
            return 2
        have = [str(e.get("name", "")).strip() for e in workers_raw if isinstance(e, dict)]
        if entry["name"] in have:
            print(f"--apply: worker {entry['name']!r} already exists in {wpath}",
                  file=sys.stderr)
            return 2
        try:
            perms_raw = json.loads(ppath.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"--apply: permissions file unreadable {ppath}: {exc}", file=sys.stderr)
            return 2
        if not isinstance(perms_raw, dict) or not isinstance(perms_raw.get("workers"), dict):
            print(f"--apply: {ppath} must have a workers object", file=sys.stderr)
            return 2
        if entry["name"] in perms_raw["workers"]:
            print(f"--apply: permission {entry['name']!r} already in {ppath}",
                  file=sys.stderr)
            return 2
        # Prompt file: first existing prompts dir in loader order
        # (workers.json parent, then nak/worker/prompts fallback).
        rel = str(entry.get("prompt_file") or f"prompts/{entry['name']}.md")
        cands = [wpath.parent / rel,
                 wpath.parent.parent / "worker" / rel]
        target = next((c for c in cands if c.parent.is_dir()), cands[0])
        if target.is_file():
            print(f"--apply: prompt file exists, not overwriting {target}",
                  file=sys.stderr)
            return 2
        try:
            workers_raw.append(entry)
            wpath.write_text(json.dumps(workers_raw, indent=2, ensure_ascii=False),
                             encoding="utf-8")
            perms_raw["workers"][entry["name"]] = perm
            ppath.write_text(json.dumps(perms_raw, indent=2, ensure_ascii=False),
                             encoding="utf-8")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_prompt_template(entry), encoding="utf-8")
        except OSError as exc:
            print(f"--apply: write failed: {exc}", file=sys.stderr)
            return 2
        _print({"worker": entry["name"], "workers_file": str(wpath),
                "permissions_file": str(ppath), "prompt_file": str(target),
                "crafts": entry.get("crafts", [])}, "APPLIED")
        return 0

    craft_goal = (args.craft or args.team or "").strip()
    team_name = (args.team_name or "").strip()
    name = (args.worker or "").strip()
    if not name and team_name and craft_goal:
        # Section 4: no --worker -> the team leader plans for the roster.
        from nak.worker.engine.team import team_by_name

        try:
            name = str(team_by_name(team_name).get("leader", "")).strip()
        except ValueError as exc:
            print(f"team error: {exc}", file=sys.stderr)
            return 2
    if not name:
        print("missing --worker NAME (or --team-name with --craft). "
              "See --list.", file=sys.stderr)
        return 2

    # Explicit brain, only when a network call is actually needed (--chat).
    # --list/--show/--prompt stay offline: no hidden Brain() construction.
    brain = None
    if args.chat or args.session or craft_goal:
        try:
            from nak.wiring import make_brain

            brain = make_brain()
        except Exception as exc:
            print(f"brain unavailable (is NebulonMD up?): {exc}", file=sys.stderr)
            return 2

    try:
        from nak.wiring import make_worker  # composition root: brain+decider injected

        worker = make_worker(name, brain=brain)
    except ValueError as exc:
        print(f"worker error: {exc}", file=sys.stderr)
        return 2

    for item in args.set:
        if "=" not in item:
            print(f"--set expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        key, value = key.strip(), value.strip()
        if key in ("personality", "values", "do_not") and "," in value:
            worker.identity[key] = [v.strip() for v in value.split(",") if v.strip()]
        elif key in ("memory", "memory.enabled", "enabled") and value:
            worker.entry["memory"]["enabled"] = value.lower() not in ("0", "false", "no", "off", "")
        else:
            worker.identity[key] = value

    if args.prompt:
        print(worker.prompt())
        return 0
    if craft_goal:
        from nak.wiring import make_harness, run_craft

        try:
            harness = make_harness(worker=name)
        except Exception as exc:
            print(f"harness unavailable: {exc}", file=sys.stderr)
            return 2
        approver = (lambda info: True) if args.approve else None
        _print(run_craft(craft_goal, worker, harness, approver=approver,
                         max_workers=args.max_workers,
                         team=team_name or None,
                         parallel=args.parallel,
                         decision=True if args.decision else None,
                         decision_parallel=args.decision_parallel,
                         craft=args.decision_craft or None), "craft")
        return 0
    if args.session:
        _print(worker.session_report(args.session), "session")
        return 0
    if args.chat:
        _print(worker.chat(args.chat), "chat")
        return 0
    _print(worker.show(), "WORKER")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
