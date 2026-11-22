"""Branching-pipeline validation (DAG: one start, conditions branch true/false).

Rules (v1):
  * 1..30 nodes, ids unique, type in NODE_TYPES (prompt/tool/craft/condition/agent/verify)
  * per-type config: prompt.text, tool.tool, craft.craft, agent.{worker,goal},
    verify.expect, condition.contains (all non-empty strings)
  * edges reference existing nodes, no self-loops, acyclic
  * exactly 1 start (indegree 0); >= 1 end; all nodes reachable from start
  * condition: exactly 2 outgoing edges labelled "true" + "false" (one each)
  * other nodes: at most 1 outgoing edge, no true/false label
  * every node: at most 1 incoming edge (no joins in v1)
  * x/y coordinates are free metadata (canvas layout), ignored here

Old single-chain definitions (no labels, no x/y) validate unchanged.
"""

from __future__ import annotations

import re
from typing import Any

from nak_host.flow.models import NODE_TYPES

_SLUG_RX = re.compile(r"^[a-z0-9-]{1,64}$")
_MAX_NODES = 30


def validate_slug(slug: str) -> str | None:
    """Return error string or None when ok. 'vetting' is reserved (builtin)."""
    s = str(slug or "").strip().lower()
    if not s:
        return "slug must be non-empty"
    if s == "vetting":
        return "slug 'vetting' is reserved (builtin template)"
    if not _SLUG_RX.match(s):
        return "slug must match ^[a-z0-9-]{1,64}$"
    if ".." in s or "/" in s or "\\" in s:
        return "slug must not contain path separators"
    return None


def _norm_label(raw: Any) -> str:
    return str(raw or "").strip().lower()


def validate_definition(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a raw definition dict. Returns {ok, errors[], ordered[]}.

    ordered[] is a DFS execution order from the start (true-branch first).
    Per-type config is checked lightly (non-empty); tool/craft/agent names
    are NOT resolved here so offline UI still saves.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return {"ok": False, "errors": ["definition must be an object"], "ordered": []}
    name = str(raw.get("name", "") or "").strip()
    if not name:
        errors.append("name must be non-empty")
    if len(name) > 120:
        errors.append("name must be <= 120 chars")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append(f"nodes must be a non-empty list (1..{_MAX_NODES})")
        nodes = []
    if len(nodes) > _MAX_NODES:
        errors.append(f"nodes must be <= {_MAX_NODES}")
    edges = raw.get("edges")
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []

    by_id: dict[str, dict[str, Any]] = {}
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"nodes[{i}] must be an object")
            continue
        nid = str(n.get("id", "")).strip()
        ntype = str(n.get("type", "")).strip()
        if not nid:
            errors.append(f"nodes[{i}].id must be non-empty")
            continue
        if nid in by_id:
            errors.append(f"duplicate node id {nid!r}")
        if ntype not in NODE_TYPES:
            errors.append(f"nodes[{nid}].type must be one of {list(NODE_TYPES)}")
        cfg = n.get("config")
        if not isinstance(cfg, dict):
            errors.append(f"nodes[{nid}].config must be an object")
        else:
            if ntype == "prompt" and not str(cfg.get("text", "") or "").strip():
                errors.append(f"nodes[{nid}] prompt.config.text must be non-empty")
            if ntype == "tool" and not str(cfg.get("tool", "") or "").strip():
                errors.append(f"nodes[{nid}] tool.config.tool must be non-empty")
            if ntype == "craft" and not str(cfg.get("craft", "") or "").strip():
                errors.append(f"nodes[{nid}] craft.config.craft must be non-empty")
            if ntype == "agent" and not str(cfg.get("goal", "") or "").strip():
                errors.append(f"nodes[{nid}] agent.config.goal must be non-empty")
            if ntype == "agent" and not str(cfg.get("worker", "") or "").strip():
                errors.append(f"nodes[{nid}] agent.config.worker must be non-empty")
            if ntype == "verify" and not str(cfg.get("expect", "") or "").strip():
                errors.append(f"nodes[{nid}] verify.config.expect must be non-empty")
            if ntype == "condition" and not str(cfg.get("contains", "") or "").strip():
                errors.append(f"nodes[{nid}] condition.config.contains must be non-empty")
        by_id[nid] = n

    # Edge shape + DAG degree checks (no joins in v1).
    indeg: dict[str, int] = {nid: 0 for nid in by_id}
    out_edges: dict[str, list[tuple[str, str]]] = {nid: [] for nid in by_id}
    for i, e in enumerate(edges):
        if not isinstance(e, dict):
            errors.append(f"edges[{i}] must be an object")
            continue
        a, b = str(e.get("from_id", "")).strip(), str(e.get("to_id", "")).strip()
        label = _norm_label(e.get("label"))
        if not a or not b:
            errors.append(f"edges[{i}] from_id/to_id must be non-empty")
            continue
        if a not in by_id or b not in by_id:
            errors.append(f"edges[{i}] references unknown node {a!r}->{b!r}")
            continue
        if a == b:
            errors.append(f"edges[{i}] self-loop {a!r} not allowed")
            continue
        if label and label not in ("true", "false"):
            errors.append(f"edges[{i}] label must be 'true' or 'false' (got {label!r})")
            continue
        atype = str(by_id[a].get("type", ""))
        if atype == "condition":
            if not label:
                errors.append(f"edges[{i}] from condition {a!r} needs label 'true'/'false'")
                continue
        elif label:
            errors.append(f"edges[{i}] only condition nodes may use true/false labels")
            continue
        indeg[b] += 1
        if indeg[b] > 1:
            errors.append(f"node {b!r} has >1 incoming edge (no joins in v1)")
        out_edges[a].append((b, label))

    for nid, outs in out_edges.items():
        ntype = str(by_id[nid].get("type", ""))
        if ntype == "condition":
            labels = sorted(lbl for _, lbl in outs)
            if labels != ["false", "true"]:
                errors.append(
                    f"condition {nid!r} needs exactly one 'true' and one 'false' edge (got {labels or 'none'})"
                )
        elif len(outs) > 1:
            errors.append(f"node {nid!r} has >1 outgoing edge (only condition may branch)")

    ordered: list[str] = []
    if not errors and by_id:
        starts = [nid for nid, d in indeg.items() if d == 0]
        if len(starts) != 1:
            if not starts:
                errors.append("no start node found (possible cycle — every node has an incoming edge)")
            else:
                errors.append(f"pipeline needs exactly 1 start node (found {len(starts)})")
        else:
            # Pre-order DFS from start: cycle check + reachability + display order (true first).
            visiting: set[str] = set()
            visited: set[str] = set()
            order: list[str] = []

            def _dfs(nid: str) -> bool:
                if nid in visiting:
                    return False
                if nid in visited:
                    return True
                visiting.add(nid)
                visited.add(nid)
                order.append(nid)

                def _rank(edge: tuple[str, str]) -> tuple[int, str]:
                    _to, _lbl = edge
                    return (0 if _lbl == "true" else 1 if _lbl == "false" else 2, _to)

                outs = sorted(out_edges[nid], key=_rank)
                for nxt, _ in outs:
                    if not _dfs(nxt):
                        return False
                visiting.discard(nid)
                return True

            if not _dfs(starts[0]):
                errors.append("cycle detected (pipeline must be acyclic)")
            elif len(visited) != len(by_id):
                missing = sorted(set(by_id) - visited)
                errors.append(f"nodes unreachable from start: {missing}")
            else:
                ordered = order

    if errors:
        return {"ok": False, "errors": errors, "ordered": []}
    return {"ok": True, "errors": [], "ordered": ordered}


def order_chain(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return node dicts in DFS execution order (raises ValueError when invalid)."""
    res = validate_definition(raw)
    if not res["ok"]:
        raise ValueError("; ".join(res["errors"][:5]))
    by_id = {str(n.get("id")): n for n in (raw.get("nodes") or []) if isinstance(n, dict)}
    return [by_id[nid] for nid in res["ordered"]]
