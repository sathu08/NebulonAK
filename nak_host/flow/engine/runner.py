"""Run a branching pipeline via nak_host.wiring (lazy imports only)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _as_text(output: Any, limit: int = 20000) -> str:
    """Best-effort text of a step output (for contains-checks)."""
    if isinstance(output, str):
        text = output
    else:
        try:
            text = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(output)
    return text[:limit]


def _with_root(args: dict[str, Any], ws: str) -> dict[str, Any]:
    """Resolve a tool path: absolute path -> root+name, else workspace root."""
    import os as _os

    from pathlib import Path as _Path

    if str(args.get("root", "")).strip():
        return args
    p = str(args.get("path", "") or "").strip()
    if not p:
        return {**args, "root": ws} if ws else args
    # Windows C:\... or POSIX /... absolute -> parent dir is root.
    is_abs = p.startswith("/") or (len(p) > 2 and p[1] == ":" and p[2] in ("\\", "/"))
    if is_abs or _os.path.isabs(p):
        try:
            parent = str(_Path(p).expanduser().parent)
            return {**args, "root": parent, "path": _Path(p).expanduser().name or p}
        except OSError:
            return args
    return {**args, "root": ws} if ws else args


def _run_tool_step(
    tool: str, base_args: dict[str, Any], harness: Any, approver: Any, ws: str
) -> tuple[bool, dict[str, Any]]:
    """Run one tool node (all configured actions). Returns (ok, entry_fields)."""
    raw_actions = base_args.pop("actions", None)
    if isinstance(raw_actions, list) and raw_actions:
        actions = [str(a).strip() for a in raw_actions if str(a).strip()]
    else:
        actions = [str(base_args.get("action", "") or "read").strip() or "read"]
    sub_results: list[dict[str, Any]] = []
    step_ok = True
    for act in actions:
        args = _with_root({**base_args, "action": act}, ws)
        raw_out = harness.run(tool, args, approver=approver)
        try:
            parsed: Any = json.loads(raw_out) if isinstance(raw_out, str) else raw_out
        except (ValueError, TypeError):
            parsed = {"raw": str(raw_out)[:2000]}
        # Friendly hints for the common sandbox mistakes.
        if isinstance(parsed, dict) and not parsed.get("ok", True):
            err = str(parsed.get("error", ""))
            if "escapes workspace root" in err:
                parsed["hint"] = (
                    "absolute path split to root+name; "
                    "still denied? grant it in permissions.json "
                    "or use a workspace file."
                )
            if "not found:" in err:
                try:
                    from pathlib import Path as _HP

                    _base = _HP(str(ws)).expanduser() if ws else None
                    if _base is not None and _base.is_dir():
                        _names = sorted([p.name for p in _base.iterdir() if not p.name.startswith(".")][:20])
                        parsed["hint"] = (
                            "file not in workspace. Workspace has: "
                            + (", ".join(_names) if _names else "(empty — only session dirs)")
                            + ". Fix path, or add a write step first to create it, "
                            "or use action=list to browse."
                        )
                except OSError:
                    parsed["hint"] = "file not found. Fix path or add a write step first."
            if parsed.get("need_approval"):
                parsed["hint"] = (
                    "agent waited for approval and was denied/timed out. "
                    "Approve in Builder popup or re-run with Auto-approve."
                )
        sub_results.append(
            {"action": act, "ok": bool(parsed.get("ok", True)) if isinstance(parsed, dict) else True, "output": parsed}
        )
        if isinstance(parsed, dict) and not parsed.get("ok", True):
            step_ok = False
            break  # stop multi-action on first failure
    if len(sub_results) == 1:
        return step_ok, {"output": sub_results[0]["output"], "actions": [sub_results[0]["action"]]}
    return step_ok, {"output": {"results": sub_results}, "actions": [s["action"] for s in sub_results]}


def _run_agent_step(agent_worker: str, goal: str) -> tuple[bool, dict[str, Any]]:
    """Delegate one goal to another worker (depth-1 call, own brain)."""
    try:
        from nak_host import wiring as W
    except Exception as exc:
        return False, {"output": {"error": f"wiring import failed: {exc!s}"[:200]}}
    try:
        out = W.call_worker(str(agent_worker).strip(), str(goal or ""))
    except Exception as exc:  # noqa: BLE001 — per-step failure, not fatal
        return False, {"output": {"error": f"agent {agent_worker!r} failed: {str(exc)[:300]}"}}
    try:
        result = out.get("result") if isinstance(out, dict) else None
        summary = getattr(result, "summary", None) or str(result or out)[:2000]
        task = out.get("task") if isinstance(out, dict) else None
        task_id = getattr(task, "id", "") if task is not None else ""
        ok = True
    except Exception as exc:  # noqa: BLE001
        return False, {"output": {"error": f"agent result unreadable: {str(exc)[:200]}"}}
    return ok, {"output": {"worker": str(agent_worker), "task_id": str(task_id or ""), "summary": str(summary)[:2000]}}


def run_definition(
    raw: dict[str, Any], worker: str = "viyan", dry: bool = False, auto_approve: bool = False
) -> dict[str, Any]:
    """Execute a branching pipeline. Never raises on step failure.

    prompt   -> worker.chat(text)
    tool     -> harness.run(tool, args) (multi-action, workspace/absolute roots)
    craft    -> wiring.run_craft(goal, worker, harness, dispatch=False)
    agent    -> wiring.call_worker(agent_worker, goal)
    verify   -> previous output contains expect (no LLM/tool calls)
    condition-> previous output contains `contains` ? follow 'true' edge : 'false' edge

    dry=True only validates + plans (no LLM/tool calls). auto_approve=True
    auto-ok's ask-verdict tools, else the run waits for a Builder approval.
    Untaken branches are reported as skipped steps (not failures).
    Result is persisted to <pipeline_dir>/<slug>/runs/<stamp>/result.json.
    """
    from nak_host.flow.engine.chain import order_chain, validate_definition

    check = validate_definition(raw if isinstance(raw, dict) else {})
    if not check["ok"]:
        return {"ok": False, "errors": check["errors"]}
    ordered = order_chain(raw)
    nodes = {str(n.get("id")): n for n in ordered}
    slug = str(raw.get("slug") or raw.get("name") or "pipeline").strip().lower()[:64] or "pipeline"
    # Worker: explicit arg wins, else definition.worker, else viyan.
    worker = str(worker or raw.get("worker") or "viyan").strip() or "viyan"
    team = str(raw.get("team") or "").strip()
    stamp = _stamp()
    steps: list[dict[str, Any]] = []

    out_edges: dict[str, list[tuple[str, str]]] = {nid: [] for nid in nodes}
    indeg: dict[str, int] = {nid: 0 for nid in nodes}
    for e in raw.get("edges") or []:
        if not isinstance(e, dict):
            continue
        a, b = str(e.get("from_id", "")).strip(), str(e.get("to_id", "")).strip()
        label = str(e.get("label", "") or "").strip().lower()
        if a in out_edges and b in nodes:
            out_edges[a].append((b, label))
            indeg[b] += 1
    starts = [nid for nid, d in indeg.items() if d == 0]
    start = starts[0] if starts else next(iter(nodes))

    if dry:
        for n in ordered:
            steps.append(
                {
                    "id": str(n.get("id")),
                    "type": str(n.get("type")),
                    "label": str(n.get("label", "")),
                    "ok": True,
                    "dry": True,
                }
            )
        return {"ok": True, "slug": slug, "stamp": stamp, "worker": worker, "team": team, "dry": True, "steps": steps}

    try:
        from nak_host import wiring as W
    except Exception as exc:
        return {"ok": False, "slug": slug, "errors": [f"wiring import failed: {exc!s}"[:200]]}

    harness = None
    worker_obj = None
    try:
        harness = W.make_harness(worker=worker)
        worker_obj = W.make_worker(worker, brain=W.make_brain())
    except Exception as exc:
        return {"ok": False, "slug": slug, "stamp": stamp, "errors": [f"setup failed: {exc!s}"[:300]]}

    approver: Any = (lambda *_a, **_k: True) if auto_approve else None
    if not auto_approve:
        from nak_host.flow.engine import approvals as _appr

        def _waiting_approver(tool: str, args: dict[str, Any], worker_label: str, what: str) -> bool:
            aid = _appr.create(tool, args, worker_label, what)
            return _appr.wait(aid)

        approver = _waiting_approver
    try:
        from nak_host.utils.settings import load_settings

        _ws = load_settings().workspace_path() or str(getattr(harness, "workspace_root", "") or "")
    except Exception:
        _ws = str(getattr(harness, "workspace_root", "") or "")

    all_ok, last_text, visited = True, "", set()
    cur: str | None = start
    while cur:
        n = nodes[cur]
        nid, ntype = str(n.get("id")), str(n.get("type"))
        cfg = n.get("config") if isinstance(n.get("config"), dict) else {}
        entry: dict[str, Any] = {"id": nid, "type": ntype, "label": str(n.get("label", "") or "")}
        visited.add(nid)
        nxt: str | None = None
        try:
            if ntype == "prompt":
                text = str(cfg.get("text", "") or "")
                ans = worker_obj.chat(text, session_id=getattr(harness, "session_id", None))
                entry.update({"ok": True, "output": str(ans)[:2000]})
                last_text = _as_text(ans)
            elif ntype == "tool":
                tool = str(cfg.get("tool", "") or "")
                base_args = dict(cfg.get("args") or {}) if isinstance(cfg.get("args"), dict) else {}
                base_args.pop("location", None)
                step_ok, fields = _run_tool_step(tool, base_args, harness, approver, _ws)
                entry.update({"ok": step_ok, **fields})
                if not step_ok:
                    all_ok = False
                else:
                    last_text = _as_text(entry.get("output"))
            elif ntype == "craft":
                goal = str(cfg.get("goal", "") or cfg.get("text", "") or "")
                craft_name = str(cfg.get("craft", "") or "").strip() or None
                out = W.run_craft(
                    goal or f"run pipeline {slug}",
                    worker_obj,
                    harness,
                    craft=craft_name,
                    dispatch=False,
                    approver=approver,
                )
                entry.update(
                    {
                        "ok": bool(out.get("planned", False)),
                        "output": {
                            "planned": out.get("planned"),
                            "path": out.get("path"),
                            "steps": out.get("steps"),
                            "reason": out.get("reason", ""),
                        },
                    }
                )
                if not out.get("planned"):
                    all_ok = False
                else:
                    last_text = _as_text(entry.get("output"))
            elif ntype == "agent":
                agent_worker = str(cfg.get("worker", "") or worker).strip() or worker
                goal = str(cfg.get("goal", "") or "")
                step_ok, fields = _run_agent_step(agent_worker, goal)
                entry.update({"ok": step_ok, **fields})
                if not step_ok:
                    all_ok = False
                else:
                    last_text = _as_text(entry.get("output"))
            elif ntype == "verify":
                expect = str(cfg.get("expect", "") or "")
                matched = bool(expect) and expect.lower() in last_text.lower()
                entry.update(
                    {
                        "ok": matched,
                        "output": {
                            "matched": matched,
                            "expect": expect,
                            "evidence": last_text[:500],
                            **(
                                {}
                                if matched
                                else {
                                    "hint": "previous output lacks the expected text; "
                                    "check the step above or loosen 'expect'"
                                }
                            ),
                        },
                    }
                )
                if not matched:
                    all_ok = False
            elif ntype == "condition":
                needle = str(cfg.get("contains", "") or "")
                matched = bool(needle) and needle.lower() in last_text.lower()
                branch = "true" if matched else "false"
                nxt = next((to_id for to_id, lbl in out_edges.get(nid, []) if lbl == branch), None)
                if nxt is None:
                    entry.update({"ok": False, "output": {"error": f"condition has no {branch!r} edge"}})
                    all_ok = False
                else:
                    entry.update(
                        {
                            "ok": True,
                            "branch": branch,
                            "output": {"branch": branch, "matched": matched, "contains": needle},
                        }
                    )
            else:
                entry.update({"ok": False, "error": f"unknown type {ntype!r}"})
                all_ok = False
        except Exception as exc:  # noqa: BLE001 — per-step failure, not fatal
            entry.update({"ok": False, "error": str(exc)[:300]})
            all_ok = False
        steps.append(entry)
        if not entry.get("ok", True):
            break  # stop on first failure
        if nxt is None and ntype != "condition":
            outs = [to_id for to_id, _ in out_edges.get(nid, [])]
            nxt = outs[0] if outs else None
        cur = nxt

    for nid in ordered:
        nid = str(nid.get("id")) if isinstance(nid, dict) else str(nid)
        if nid not in visited and nid in nodes:
            n = nodes[nid]
            steps.append(
                {
                    "id": nid,
                    "type": str(n.get("type")),
                    "label": str(n.get("label", "") or ""),
                    "ok": None,
                    "skipped": True,
                    "output": {"skipped": True, "reason": "branch not taken"},
                }
            )

    result = {"ok": all_ok, "slug": slug, "stamp": stamp, "worker": worker, "team": team, "steps": steps}
    # Persist (best-effort, never fail the run on IO).
    try:
        from nak_host.flow.store import custom_root

        if slug != "vetting":
            out_dir = custom_root() / slug / "runs" / f"{stamp}"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "result.json").write_text(json.dumps(result, indent=2)[:200000], encoding="utf-8")
            result["path"] = str(out_dir)
    except OSError:
        pass
    return result
