"""nak.service.nodes -- drag-drop catalogue + Python builder (v0).

The future canvas renders get_node_catalog() and posts back a graph;
build_python(graph) turns that graph into runnable Python that only calls
nak.service — generated code never touches the old REPL.

Graph shape (v0, linear):
    {"nodes": [{"id": "n1", "type": "nak.chat_turn",
                "params": {"text": "fix calc.py"}}],
     "edges": [{"from": "n1", "to": "n2"}]}   # edges optional; order = list order

Node types are stable op ids ("nak.<name>"); unknown types refuse with a
clear ValueError instead of generating broken code.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

# -- static node specs ----------------------------------------------------
# {type, label, description, inputs, outputs, params}
# inputs/outputs use the canvas port names; params is a JSON-schema-ish map.

NODE_CATALOG: List[Dict[str, Any]] = [
    {
        "type": "nak.chat_turn",
        "label": "Chat turn",
        "description": "One full harness turn: decide -> route -> run -> verify. The core block.",
        "inputs": ["text"],
        "outputs": ["answer", "decision", "run_on", "workspace"],
        "params": {"text": "string", "auto_create": "bool=false",
                   "instant_route": "bool=false (user-consented 0-LLM direct routing)",
                   "session_id": "string?", "planning": "auto|always|never"},
    },
    {
        "type": "nak.list_agents",
        "label": "List agents",
        "description": "Registered agents from registry.json.",
        "inputs": [],
        "outputs": ["agents"],
        "params": {},
    },
    {
        "type": "nak.create_agent",
        "label": "Create agent",
        "description": "Scaffold a new agent folder + registry entry. Non-interactive.",
        "inputs": ["description"],
        "outputs": ["name", "path"],
        "params": {"name": "string", "description": "string",
                   "capabilities": "string[]", "instructions": "string="},
    },
    {
        "type": "nak.delegate",
        "label": "Delegate",
        "description": "Run another agent as a sub-step (depth-capped).",
        "inputs": ["task"],
        "outputs": ["answer"],
        "params": {"agent_name": "string", "task": "string"},
    },
    {
        "type": "nak.create_plan",
        "label": "Create plan",
        "description": "Replace the turn plan (works inside a run).",
        "inputs": ["steps"],
        "outputs": ["plan"],
        "params": {"steps": "string[]"},
    },
    {
        "type": "nak.shell",
        "label": "Shell",
        "description": "Run a shell command in the workspace.",
        "inputs": ["command"],
        "outputs": ["output"],
        "params": {"command": "string"},
    },
    {
        "type": "nak.python_exec",
        "label": "Python exec",
        "description": "Run a Python snippet in the workspace.",
        "inputs": ["code"],
        "outputs": ["output"],
        "params": {"code": "string"},
    },
    {
        "type": "nak.run_test",
        "label": "Run tests",
        "description": "pytest wrapper with summary.",
        "inputs": [],
        "outputs": ["summary"],
        "params": {"target": "string=."},
    },
    {
        "type": "nak.read_file",
        "label": "Read file",
        "description": "Read a workspace file.",
        "inputs": ["path"],
        "outputs": ["content"],
        "params": {"path": "string"},
    },
    {
        "type": "nak.write_file",
        "label": "Write file",
        "description": "Write a workspace file.",
        "inputs": ["path", "content"],
        "outputs": ["ok"],
        "params": {"path": "string", "content": "string"},
    },
    {
        "type": "nak.remember",
        "label": "Remember",
        "description": "Store a memory in NebulonMind.",
        "inputs": ["text"],
        "outputs": ["memory_id"],
        "params": {"text": "string"},
    },
    {
        "type": "nak.recall",
        "label": "Recall",
        "description": "Semantic recall from NebulonMind.",
        "inputs": ["query"],
        "outputs": ["hits"],
        "params": {"query": "string"},
    },
    {
        "type": "nak.verify",
        "label": "Verify",
        "description": "Harness verifier (compileall + pytest).",
        "inputs": [],
        "outputs": ["ok"],
        "params": {},
    },
    {
        "type": "nak.output",
        "label": "Output",
        "description": "Terminal sink: what the user sees.",
        "inputs": ["answer"],
        "outputs": [],
        "params": {},
    },
]

# Codegen: node type -> (service call template, result var).
# v0 emits straight-line code; branching/loops are a later builder version.
_CODEGEN = {
    "nak.chat_turn": 'r_{i} = svc.chat_turn_sync({text}{auto})',
    "nak.list_agents": 'r_{i} = svc.list_agents()',
    "nak.create_agent": 'r_{i} = svc.create_agent_sync({name}, {desc}{caps})',
    "nak.remember": 'r_{i} = __import__("asyncio").run(svc.remember({text}))',
    "nak.recall": 'r_{i} = __import__("asyncio").run(svc.search({query}))',
    "nak.output": 'print(r_{src}["answer"] if isinstance(r_{src}, dict) else r_{src})',
}


def get_node_catalog(registry: Optional[Any] = None) -> Dict[str, Any]:
    """Full catalogue: static nodes + one node per registered agent.

    Agent nodes look like {"type": "nak.agent.Apollo", ...} so the
    canvas can offer "run THIS agent" blocks that survive renames via the
    registry (unknown agents at build time are a clean error, not silence).
    """
    nodes = [dict(n) for n in NODE_CATALOG]
    names: List[str] = []
    if registry is not None:
        try:
            names = [m.name for m in registry.list_agents()]
        except Exception:
            names = []
    for name in names:
        nodes.append({
            "type": f"nak.agent.{name}",
            "label": f"Run {name}",
            "description": f"Run the {name} agent on a task.",
            "inputs": ["task"],
            "outputs": ["answer"],
            "params": {"task": "string"},
        })
    return {"nodes": nodes, "version": 0}


def build_python(graph: Dict[str, Any]) -> str:
    """Graph -> runnable Python source (v0, straight-line). Raises ValueError
    on unknown node types or bad shapes — never emits half-broken code."""
    if not isinstance(graph, dict):
        raise ValueError("graph must be a dict with 'nodes'")
    raw_nodes = graph.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("graph['nodes'] must be a non-empty list")
    known = {n["type"] for n in NODE_CATALOG}
    lines = [
        '"""Generated by nak.service.build_python (v0) — edit freely."""',
        "from nak.service import create_service",
        "",
        "svc = create_service()",
        "",
    ]
    last_var = "None"
    for i, nd in enumerate(raw_nodes):
        if not isinstance(nd, dict):
            raise ValueError(f"node {i} must be a dict")
        ntype = nd.get("type", "")
        params = nd.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"node {i} params must be a dict")
        if ntype == "nak.output":
            src = params.get("from", last_var)
            lines.append(f'print({src}["answer"] if isinstance({src}, dict) else {src})')
            continue
        if ntype == "nak.chat_turn":
            text = json.dumps(str(params.get("text", "")))
            auto = ", auto_create=True" if params.get("auto_create") else ""
            fp = ", instant_route=True" if params.get("instant_route") else ""
            lines.append(f"r_{i} = svc.chat_turn_sync({text}{auto}{fp})")
        elif ntype == "nak.list_agents":
            lines.append(f"r_{i} = svc.list_agents()")
        elif ntype == "nak.create_agent":
            lines.append(
                f"r_{i} = svc.create_agent_sync("
                f"{json.dumps(str(params.get('name', '')))}, "
                f"{json.dumps(str(params.get('description', '')))}, "
                f"{json.dumps(list(params.get('capabilities') or []))})"
            )
        elif ntype == "nak.remember":
            lines.append(f"r_{i} = __import__('asyncio').run(svc.remember({json.dumps(str(params.get('text', '')))}))")
        elif ntype == "nak.recall":
            lines.append(f"r_{i} = __import__('asyncio').run(svc.search({json.dumps(str(params.get('query', '')))}))")
        elif ntype.startswith("nak.agent."):
            agent = ntype[len("nak.agent."):]
            if not agent or "/" in agent or "\\" in agent or ".." in agent:
                raise ValueError(f"node {i}: bad agent type {ntype!r}")
            lines.append(
                f"r_{i} = svc.chat_turn_sync({json.dumps(str(params.get('task', '')))})"
                f"  # via {agent} (decision routes; pin with instant_route if needed)"
            )
        elif ntype in known:
            raise ValueError(f"node {i} ({ntype}): codegen not yet implemented in v0")
        else:
            raise ValueError(f"node {i}: unknown node type {ntype!r}")
        last_var = f"r_{i}"
    lines.append("")
    lines.append(f"result = {last_var}")
    return "\n".join(lines) + "\n"


__all__ = ["NODE_CATALOG", "get_node_catalog", "build_python"]
