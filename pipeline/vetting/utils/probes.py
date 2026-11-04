"""Deterministic craft and tool probes for a worker under test."""

from __future__ import annotations

import json

from pathlib import Path
from typing import Any

from nak.harness.craft.craft import crafts_for_worker, expand_craft, load_craft
from nak.harness.engine.permission import (
    load_permissions,
    load_worker_role,
    rule_for,
)

from .models import ProbeResult

_MARKER = "vetting-probe-line"


def probe_crafts(worker_name: str, workers_file: Any = None) -> list[ProbeResult]:
    """Load and expand every craft the worker is allowed to see."""
    try:
        crafts = crafts_for_worker(worker_name, workers_file)
    except Exception as exc:  # noqa: BLE001 — probes never break a run
        return [ProbeResult("crafts", "craft", "error", str(exc)[:300])]
    if not crafts:
        return [ProbeResult("crafts", "craft", "skip",
                            "no crafts declared for this worker")]
    out: list[ProbeResult] = []
    for item in crafts:
        name = str(item.get("name", "") or "").strip()
        try:
            craft = load_craft(name)
            given = {key: "vetting-probe" for key in craft.get("required_inputs") or []}
            steps = expand_craft(name, given)
            if not steps:
                out.append(ProbeResult(name, "craft", "fail",
                                       "craft expanded to zero steps"))
                continue
            out.append(ProbeResult(
                name, "craft", "pass",
                f"{len(steps)} step(s); tools={craft.get('tools')}; "
                f"approval={craft.get('approval')}",
                {"tools": craft.get("tools"), "approval": craft.get("approval"),
                 "max_steps": craft.get("max_steps"),
                 "required_inputs": craft.get("required_inputs")},
            ))
        except Exception as exc:  # noqa: BLE001 — one bad craft is evidence
            out.append(ProbeResult(name, "craft", "fail", str(exc)[:300]))
    return out


def _json(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"non-JSON tool result: {str(value)[:200]}"}
    return data if isinstance(data, dict) else {"ok": False, "error": "non-object result"}


def _probe_call(harness: Any, tool: str) -> dict[str, Any]:
    calls = {
        "file": ("file", {"action": "read", "path": "workspace/probe.txt"}),
        "directory": ("directory", {"action": "list", "path": "workspace"}),
        "terminal": ("terminal", {"action": "exec", "command": ["echo", _MARKER],
                                  "workdir": "workspace"}),
        "observe": ("observe", {"action": "capture", "path": "workspace/probe.txt"}),
        "assertion": ("assertion", {"action": "check", "checks": [
            {"id": "probe", "op": "eq", "expected": "ok", "actual": "ok"}]}),
        "trace": ("trace", {"action": "grep", "path": "workspace/probe.txt",
                            "pattern": "vetting"}),
        "scorecard": ("scorecard", {"action": "score", "inputs": {"quality": 1.0},
                                    "rules": [{"id": "probe", "metric": "quality",
                                               "weight": 1, "min": 0, "max": 1}]}),
        "report": ("report", {"action": "write", "format": "md",
                              "title": "Vetting probe",
                              "sections": {"probe": "ok"},
                              "path": "output/probe.md"}),
    }
    if tool not in calls:
        return {"ok": False, "error": f"no safe probe for tool {tool!r}"}
    name, args = calls[tool]
    return _json(harness.run(name, args))


def _verdict(data: dict[str, Any], tool: str) -> tuple:
    if not data.get("ok"):
        return "fail", str(data.get("error", "tool call failed"))[:300]
    if tool == "file" and _MARKER not in str(data.get("content", "")):
        return "fail", "file read returned no probe content"
    if tool == "directory":
        names = {str(e.get("name", "")) for e in data.get("entries") or []
                 if isinstance(e, dict)}
        if "probe.txt" not in names:
            return "fail", "directory list missed probe.txt"
    if tool == "terminal" and _MARKER not in str(data.get("stdout", "")):
        return "fail", "terminal stdout missed the probe marker"
    if tool == "observe" and _MARKER not in str(data.get("content", "")):
        return "fail", "observe returned no probe content"
    if tool == "assertion" and not data.get("all_passed"):
        return "fail", "assertion probe did not pass"
    if tool == "trace" and int(data.get("matched") or 0) < 1:
        return "fail", "trace found no probe line"
    if tool == "scorecard" and abs(float(data.get("score", 0)) - 1.0) > 1e-6:
        return "fail", "scorecard probe did not score 1.0"
    return "pass", ""


def probe_tools(worker_name: str, workers_file: Any = None,
                root: str | Path = "", permissions_file: Any = None) -> list[ProbeResult]:
    """Exercise each tool the worker's effective permission entry allows.

    Probes are read-only/deterministic except the report write, which lands
    inside the throwaway probe session under ``root``. Denied tools are
    recorded as ``deny`` (expected policy, not a failure); ask/skip tools are
    not executed.
    """
    base = Path(root or ".")
    try:
        perms = load_permissions(permissions_file)
        role = load_worker_role(workers_file, worker_name)
        entry, _label = rule_for(perms, worker_name, role=role)
    except Exception as exc:  # noqa: BLE001
        return [ProbeResult("tools", "tool", "error", str(exc)[:300])]
    if not isinstance(entry, dict):
        return [ProbeResult("tools", "tool", "deny",
                            "no effective permission entry (fail-closed)")]
    tools = sorted(str(name) for name in (entry.get("tools") or {}) if str(name).strip())
    if not tools:
        return [ProbeResult("tools", "tool", "skip",
                            "no tools declared in the effective permission entry")]

    from nak.wiring import make_harness

    try:
        harness = make_harness(
            worker=worker_name,
            user=f"vetting_{worker_name}",
            workspace_root=str(base),
            permissions=permissions_file,
            workers_file=workers_file,
            approver=lambda _info: True,
        )
    except Exception as exc:  # noqa: BLE001
        return [ProbeResult("tools", "tool", "error",
                            f"probe harness failed: {str(exc)[:250]}")]
    probe_dir = Path(harness.session_dir) / "workspace"
    probe_dir.mkdir(parents=True, exist_ok=True)
    (probe_dir / "probe.txt").write_text(f"{_MARKER}\n", encoding="utf-8")

    out: list[ProbeResult] = []
    for tool in tools:
        verdict = str((entry.get("tools") or {}).get(tool, "deny")).lower()
        if verdict == "deny":
            out.append(ProbeResult(tool, "tool", "deny",
                                   "permission denies this tool"))
            continue
        if verdict != "allow":
            out.append(ProbeResult(tool, "tool", "skip",
                                   f"permission verdict {verdict!r}; not executed"))
            continue
        try:
            data = _probe_call(harness, tool)
        except Exception as exc:  # noqa: BLE001
            out.append(ProbeResult(tool, "tool", "error", str(exc)[:300]))
            continue
        status, detail = _verdict(data, tool)
        out.append(ProbeResult(tool, "tool", status, detail, data))
    return out
