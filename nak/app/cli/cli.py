"""nak.plugins.tool.terminal_tool -- shared terminal boundary for ALL pipelines.

One file, every pipeline reuses it: the CLI grammar lives here (not copied
per pipeline), and each pipeline adds only the command groups it needs:

    from nak.plugins.tool import terminal_tool

    parser = terminal_tool.build_parser(
        prog="pipeline.chatagent",
        include=("chat", "agents", "memory", "builder"),
    )
    # another pipeline adds only what it needs, plus its own:
    #   parser = terminal_tool.build_parser(prog="pipeline.other", include=("chat",))
    #   terminal_tool.add_custom(sub, ...)  # its own subcommands

Groups:
    chat     ask (one harness turn) + --instant-route consent flag
    agents   agents / create-agent / suggest-name
    memory   health / remember / search / session-new
    builder  catalog (drag-drop nodes) / build (graph -> python)

Output helpers (emit_human / emit_json) keep stdout conventions identical
across pipelines so the terminal parses one format. Nothing here imports any
pipeline — the caller maps parsed args -> its own service calls.

NOTE: this is NOT an LLM-callable tool (it is the human/terminal boundary),
so it stays OUT of PLUGIN_TOOLS on purpose.
"""

from __future__ import annotations

import json
import argparse
from typing import Any, Sequence

CHAT = "chat"
AGENTS = "agents"
MEMORY = "memory"
BUILDER = "builder"

ALL_GROUPS = (CHAT, AGENTS, MEMORY, BUILDER)


def add_common_args(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Global flags every pipeline CLI shares."""
    p.add_argument("--cfg", default=None, help="Path to nebulonak.cfg")
    p.add_argument("--base-url", default=None, help="Override NebulonMind base URL")
    p.add_argument("--user", default=None, help="Override username")
    p.add_argument("--session", default=None, help="Reuse an existing Mind session id")
    p.add_argument("--no-session", action="store_true", help="Disable auto session creation")
    p.add_argument("--planning", default=None, choices=["auto", "always", "never"],
                   help="Pre-execution planning step (also: NAK_CHAT_PLANNING env)")
    p.add_argument("--json", action="store_true", help="Emit full JSON result")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    p.add_argument("--once", default=None, help="(deprecated) single turn text; use: ask \"text\"")
    return p


def add_chat_commands(sub) -> None:
    a = sub.add_parser("ask", help="One harness turn: decide -> route -> run -> verify")
    a.add_argument("text", nargs="+", help="User message")
    a.add_argument("--auto-create", action="store_true",
                   help="Auto-create the proposed agent instead of returning needs_confirm")
    a.add_argument("--instant-route", action="store_true",
                   help="User-consented token-saving mode: explicit agent-name mention "
                        "is matched, decided AND executed inside Polaris (0 LLM calls)")


def add_agent_commands(sub) -> None:
    sub.add_parser("agents", help="List registered agents")
    c = sub.add_parser("create-agent", help="Non-interactive agent creation")
    c.add_argument("--desc", required=True, help="What the agent should do")
    c.add_argument("--name", default=None, help="Agent name (default: suggested from --desc)")
    c.add_argument("--caps", default=None, help="Comma-separated capabilities")
    c.add_argument("--instructions", default="", help="Custom instructions baked into the agent")
    c.add_argument("--yes", action="store_true",
                   help="Required: confirms creation (replaces the old terminal prompt)")
    n = sub.add_parser("suggest-name", help="Suggest an agent name for a description (offline)")
    n.add_argument("desc", nargs="+", help="Free-text description")


def add_memory_commands(sub) -> None:
    sub.add_parser("health", help="Brain health + LLM status + user")
    sub.add_parser("session-new", help="Mint a fresh Mind session")
    r = sub.add_parser("remember", help="Store a memory directly")
    r.add_argument("text", nargs="+", help="Text to remember")
    s = sub.add_parser("search", help="Semantic search memories")
    s.add_argument("query", nargs="+", help="Search query")
    s.add_argument("--top-k", type=int, default=5, help="Max hits (default 5)")


def add_builder_commands(sub) -> None:
    sub.add_parser("catalog", help="Drag-drop node catalogue (static + registered agents)")
    b = sub.add_parser("build", help="Node graph JSON file -> runnable Python source")
    b.add_argument("graph", help="Path to graph JSON {nodes:[{id,type,params}]}")
    c = sub.add_parser("create-tool", help="Scaffold nak/plugins/tool/<name>_tools.py + reload tools.json")
    c.add_argument("name", help="Tool base name (e.g. mytool -> mytool_tools.py)")
    c.add_argument("--scope", default="file", choices=["file", "exec", "none"],
                   help="Workspace scope for auto-loaded tools (default file)")
    u = sub.add_parser("upload-tool", help="Copy a *_tools.py file into nak/plugins/tool/ + reload tools.json")
    u.add_argument("src", help="Path to the *_tools.py file to upload")


_GROUP_BUILDERS = {
    CHAT: add_chat_commands,
    AGENTS: add_agent_commands,
    MEMORY: add_memory_commands,
    BUILDER: add_builder_commands,
}


def build_parser(prog: str = "nak",
                 include: Sequence[str] = ALL_GROUPS,
                 description: str = "NebulonAK headless CLI — one service call per invocation (no REPL).",
                 ) -> argparse.ArgumentParser:
    """Common grammar. Pipelines pick groups via `include`; anything else
    they add on the returned parser's subparsers themselves."""
    p = argparse.ArgumentParser(prog=prog, description=description)
    add_common_args(p)
    sub = p.add_subparsers(dest="cmd")
    for group in include:
        try:
            _GROUP_BUILDERS[group](sub)
        except KeyError:
            raise ValueError(f"unknown terminal group {group!r}; use one of {ALL_GROUPS}")
    # stash the subparsers handle so pipelines can add custom commands:
    #   sub = parser.get_default("subparsers") ...
    p.set_defaults(_subparsers=sub)
    return p


def emit_human(data: Any) -> None:
    """Human line(s) for terminal display (answer text or compact JSON)."""
    if isinstance(data, dict) and "answer" in data:
        print(data.get("answer", ""))
    else:
        print(json.dumps(data, ensure_ascii=False, default=str)[:2000])


def emit_json(data: Any) -> None:
    """Full JSON for terminal parsing (--json mode)."""
    print(json.dumps(data, ensure_ascii=False, default=str, indent=2))


# Canvas node: the terminal as a sink block (stdin -> pipeline -> stdout).
TERMINAL_NODE_SPEC = {
    "type": "nak.terminal",
    "label": "Terminal",
    "description": "Human boundary: shows the answer, collects the next message. "
                   "Owns the chat loop; calls one pipeline turn per message.",
    "inputs": ["answer"],
    "outputs": ["text"],
    "params": {"json": "bool=false"},
}


# -- generic machine boundary -----------------------------------------------
# execute_tool_call() lets builders / delegate() / the canvas call ANY
# pipeline as one tool node. The pipeline only supplies a sync turn runner:
#   run_turn(text, *, auto_create=False, instant_route=None) -> dict
# Loop-safe JSON errors: never raises.

def execute_tool_call(run_turn, name: str, arguments) -> str:
    """Run one pipeline turn as a tool call. Never raises."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    try:
        if name == "pipeline_ask":
            text = str(args.get("text", "") or "")
            if not text.strip():
                raise ValueError("text must be non-empty")
            fp = args.get("instant_route", None)
            fp = None if fp is None else bool(fp)
            res = run_turn(text.strip(),
                           auto_create=bool(args.get("auto_create")),
                           instant_route=fp)
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        return json.dumps({"error": f"unknown pipeline tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


# -- generic human boundary ---------------------------------------------------
# One handler per command group; main() dispatches argv -> handler. Pipelines
# reuse these by passing create_service + include; a pipeline needing custom
# behaviour passes handlers={...} overrides (e.g. its own "ask").

def _emit(data, as_json: bool) -> int:
    if as_json:
        emit_json(data)
    else:
        emit_human(data)
    return 0


def handle_ask(args, svc) -> int:
    import sys as _sys

    text = " ".join(args.text or []).strip()
    if not text:
        print("usage: ask \"message\"", file=_sys.stderr)
        return 1
    res = svc.chat_turn_sync(text, auto_create=args.auto_create,
                             instant_route=True if args.instant_route else None)
    if args.json:
        return _emit(res, True)
    decision = res.get("decision", {})
    print(f"[decision] action={decision.get('action')} "
          f"agent={decision.get('agent_name')} "
          f"conf={decision.get('confidence', 0.0):.2f}")
    print(f"[Running on: {res.get('run_on') or res.get('routed_via')}]")
    if res.get("workspace"):
        print(f"[workspace: {res['workspace']}]")
    for v in res.get("verification") or []:
        print("[verify: OK]" if v.get("ok")
              else f"[verify: FAILED ({', '.join(map(str, v.get('failures') or ['unknown']))})]")
    print(res.get("answer", ""))
    if res.get("needs_confirm"):
        print(f"-- needs confirm: re-run with create-agent (agent={decision.get('agent_name')})",
              file=_sys.stderr)
    return 0


def handle_agents(args, svc) -> int:
    return _emit(svc.list_agents(), args.json)


def handle_health(args, svc) -> int:
    import asyncio as _asyncio

    return _emit(_asyncio.run(svc.health()), args.json)


def handle_session_new(args, svc) -> int:
    return _emit(svc.session_new(), args.json)


def handle_remember(args, svc) -> int:
    import asyncio as _asyncio

    text = " ".join(args.text or []).strip()
    return _emit(_asyncio.run(svc.remember(text)), args.json)


def handle_search(args, svc) -> int:
    import asyncio as _asyncio

    return _emit(
        _asyncio.run(svc.search(" ".join(args.query or []), top_k=args.top_k)),
        args.json,
    )


def handle_create_agent(args, svc) -> int:
    import sys as _sys

    from nak.service.suggest import (
        capability_options as _capability_options,
        suggest_agent_name as _suggest_agent_name,
    )

    if not args.yes:
        print("refusing: pass --yes to confirm non-interactive creation",
              file=_sys.stderr)
        return 1
    name = args.name or _suggest_agent_name(args.desc)
    caps = [c.strip() for c in (args.caps or "").split(",") if c.strip()]
    if not caps:
        caps = _capability_options(args.desc)[0][1]
    res = svc.create_agent_sync(name, args.desc, caps, args.instructions or "")
    print(f"created {res['name']}: {res['path']}")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, default=str))
    return 0


def handle_suggest_name(args, svc) -> int:
    return _emit(svc.suggest_name(" ".join(args.desc or [])), args.json)


def handle_catalog(args, svc) -> int:
    from nak.service import get_node_catalog

    return _emit(get_node_catalog(svc.pipe.registry), True)


def handle_build(args, svc) -> int:
    from pathlib import Path as _Path

    from nak.service import build_python

    graph = json.loads(_Path(args.graph).read_text(encoding="utf-8"))
    print(build_python(graph))
    return 0


_CREATE_TEMPLATE = '''"""{name} custom tools (auto-loaded into tools.json).

Convention: expose *_TOOLS specs + an execute_* function.
No __init__ edit needed — manifest discovery picks this file up.
"""
MY_TOOLS = [{{
    "type": "function",
    "function": {{
        "name": "{name}",
        "description": "Custom tool {name}.",
        "parameters": {{
            "type": "object",
            "properties": {{
                "path": {{"type": "string", "description": "Root-relative file path"}},
            }},
            "required": ["path"],
            "additionalProperties": False,
        }},
    }},
}}]

TOOL_SCOPES = {{"{name}": "{scope}"}}


def execute_my_tool(tool_name, arguments, *, root=None, cwd=None):
    """Route custom calls. Loop-safe JSON errors."""
    import json as _json
    from .file_tool import read_file
    args = dict(arguments or {{}})
    if tool_name != "{name}":
        return _json.dumps({{"error": "unknown custom tool %r" % tool_name}})
    try:
        data = read_file(args.get("path", ""), root=root)
        return _json.dumps(data, ensure_ascii=False, default=str)[:20000]
    except Exception as exc:
        return _json.dumps({{"error": str(exc)[:500]}})
'''


def handle_create_tool(args, svc) -> int:
    from pathlib import Path as _Path

    from nak.plugins.tool.manifest import refresh_manifest

    safe = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in args.name.strip().lower())
    if not safe or safe.startswith("_"):
        print(f"error: invalid tool name {args.name!r}", file=__import__("sys").stderr)
        return 1
    dest = _Path(__file__).resolve().parent / f"{safe}_tools.py"
    if dest.exists():
        print(f"error: already exists: {dest}", file=__import__("sys").stderr)
        return 1
    dest.write_text(_CREATE_TEMPLATE.format(name=safe, scope=args.scope), encoding="utf-8")
    info = refresh_manifest()
    print(f"created {dest} -> tools.json {info['count']} tools ({', '.join(info['names'])})")
    if args.json:
        emit_json({"created": str(dest), "manifest": info})
    return 0


def handle_upload_tool(args, svc) -> int:
    import shutil as _shutil
    from pathlib import Path as _Path

    from nak.plugins.tool.manifest import refresh_manifest, validate_manifest

    src = _Path(args.src).expanduser().resolve()
    if not src.exists() or src.suffix != ".py" or not src.name.endswith("_tools.py"):
        print(f"error: src must be an existing *_tools.py file: {args.src!r}",
              file=__import__("sys").stderr)
        return 1
    dest = _Path(__file__).resolve().parent / src.name
    _shutil.copyfile(src, dest)
    try:
        info = refresh_manifest()
    except Exception as exc:
        dest.unlink(missing_ok=True)
        print(f"error: uploaded file broke discovery ({exc}); removed",
              file=__import__("sys").stderr)
        return 1
    check = validate_manifest()
    if not check.get("ok"):
        print(f"uploaded {dest} but manifest invalid: {check['issues']}",
              file=__import__("sys").stderr)
        return 1
    print(f"uploaded {src} -> {dest} -> tools.json {info['count']} tools")
    if args.json:
        emit_json({"uploaded": str(dest), "manifest": info})
    return 0


DEFAULT_HANDLERS = {
    "ask": handle_ask,
    "agents": handle_agents,
    "health": handle_health,
    "session-new": handle_session_new,
    "remember": handle_remember,
    "search": handle_search,
    "create-agent": handle_create_agent,
    "suggest-name": handle_suggest_name,
    "catalog": handle_catalog,
    "build": handle_build,
    "create-tool": handle_create_tool,
    "upload-tool": handle_upload_tool,
}


def main(argv=None, *, prog: str = "nak", include=ALL_GROUPS,
         create_service=None, handlers=None) -> int:
    """Generic terminal entry any pipeline reuses.

    prog/include shape the grammar; create_service(argv-namespace) builds the
    pipeline's service; handlers maps cmd -> handler(args, svc) and defaults
    to DEFAULT_HANDLERS (override per pipeline as needed).
    Returns the process exit code.
    """
    import logging as _logging
    import sys as _sys

    from nak.brain.client import BrainError as _BrainError

    if create_service is None:
        raise ValueError("create_service factory is required")
    handlers = DEFAULT_HANDLERS if handlers is None else handlers

    args = build_parser(prog=prog, include=include).parse_args(argv)
    if args.verbose:
        _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")

    # Backward compat: bare `--once "text"` with no subcommand behaves like ask.
    if args.cmd is None and args.once:
        args.cmd = "ask"
        args.text = [args.once]
        args.auto_create = False
        args.instant_route = False
    if args.cmd is None:
        build_parser(prog=prog, include=include).print_help(_sys.stderr)
        print("hint: this CLI is single-shot (no REPL). Use: ask \"...\" [--json]",
              file=_sys.stderr)
        return 1

    try:
        svc = create_service(
            session_id=args.session,
            auto_session=not args.no_session and args.session is None,
            planning=args.planning,
            cfg_path=args.cfg,
            base_url=args.base_url,
            user=args.user,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"service init failed: {exc}", file=_sys.stderr)
        return 1

    try:
        fn = handlers.get(args.cmd)
        if fn is None:
            print(f"unknown command {args.cmd!r}", file=_sys.stderr)
            return 1
        return int(fn(args, svc))
    except _BrainError as exc:
        print(json.dumps({"error": str(exc), "status": exc.status}), file=_sys.stderr)
        return 2
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=_sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=_sys.stderr)
        return 1


__all__ = [
    "CHAT",
    "AGENTS",
    "MEMORY",
    "BUILDER",
    "ALL_GROUPS",
    "TERMINAL_NODE_SPEC",
    "DEFAULT_HANDLERS",
    "add_common_args",
    "add_chat_commands",
    "add_agent_commands",
    "add_memory_commands",
    "add_builder_commands",
    "build_parser",
    "emit_human",
    "emit_json",
    "execute_tool_call",
    "handle_ask",
    "handle_agents",
    "handle_health",
    "handle_session_new",
    "handle_remember",
    "handle_search",
    "handle_create_agent",
    "handle_suggest_name",
    "handle_catalog",
    "handle_build",
    "handle_create_tool",
    "handle_upload_tool",
    "main",
]
