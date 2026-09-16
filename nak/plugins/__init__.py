"""nak.plugins -- plugin folders for NAK (tool today, mcp planned).

    from nak.plugins import PLUGIN_TOOLS, execute_plugin

PLUGIN_TOOLS = memory (need Brain) + file + fs + exec + orchestration
(delegate / create_plan / update_plan). execute_plugin(brain, name,
arguments) routes to the right executor so LLM loops and future agents need
only one tool list + one call.
"""
from .tool import (
    NEBULONAK_TOOLS,
    NEBULON_TOOLS,
    execute_tool,
    get_tool_map,
    FILE_TOOLS,
    execute_file_tool,
    FS_TOOLS,
    execute_fs_tool,
    EXEC_TOOLS,
    execute_exec_tool,
    AGENT_TOOLS,
    PLAN_TOOLS,
    execute_agent_tools,
)

PLUGIN_TOOLS = (
    list(NEBULONAK_TOOLS) + list(FILE_TOOLS) + list(FS_TOOLS)
    + list(EXEC_TOOLS) + list(AGENT_TOOLS) + list(PLAN_TOOLS)
)

# Back-compat alias: harness extended set == PLUGIN_TOOLS (memory + file + fs + exec).
EXTENDED_TOOLS = list(PLUGIN_TOOLS)
HARNESS_TOOLS = list(PLUGIN_TOOLS)


def execute_plugin(brain, name: str, arguments, *, _root=None, _cwd=None) -> str:
    """Route any plugin tool call. File/fs tools ignore `brain`.

    Workspace scoping (explicit args win, else ambient harness workspace,
    else legacy repo root / process cwd) works without changing the
    LLM-visible schemas.
    """
    from .tool.workspace import current_workspace_root

    file_names = {spec["function"]["name"] for spec in FILE_TOOLS}
    fs_names = {spec["function"]["name"] for spec in FS_TOOLS}
    exec_names = {spec["function"]["name"] for spec in EXEC_TOOLS}
    orchestration_names = ({spec["function"]["name"] for spec in AGENT_TOOLS}
                           | {spec["function"]["name"] for spec in PLAN_TOOLS})
    ambient = current_workspace_root()
    root = _root if _root is not None else (str(ambient) if ambient else None)
    cwd = _cwd if _cwd is not None else (str(ambient) if ambient else None)
    if name in file_names:
        if root is not None:
            # file_tool honours the ambient root itself, but an explicit
            # _root must win -> scope it for this call only.
            from .tool.workspace import set_current_workspace

            with set_current_workspace(root):
                return execute_file_tool(name, arguments)
        return execute_file_tool(name, arguments)
    if name in fs_names:
        return execute_fs_tool(name, arguments, root=root)
    if name in exec_names:
        return execute_exec_tool(name, arguments, cwd=cwd)
    if name in orchestration_names:
        # delegate needs brain; plan tools use the ambient turn state
        return execute_agent_tools(brain, name, arguments)
    # custom tools auto-loaded via tools.json (create-tool / upload-tool):
    # one-time fallback so no __init__ edit is ever needed per tool.
    try:
        from .tool.manifest import execute_manifest_tool

        _custom = execute_manifest_tool(name, arguments, root=root, cwd=cwd)
        if _custom is not None:
            return _custom
    except Exception:
        pass
    return execute_tool(brain, name, arguments)


__all__ = [
    "PLUGIN_TOOLS",
    "EXTENDED_TOOLS",
    "HARNESS_TOOLS",
    "execute_plugin",
    "NEBULONAK_TOOLS",
    "NEBULON_TOOLS",
    "execute_tool",
    "get_tool_map",
    "FILE_TOOLS",
    "execute_file_tool",
    "FS_TOOLS",
    "execute_fs_tool",
    "EXEC_TOOLS",
    "execute_exec_tool",
    "AGENT_TOOLS",
    "PLAN_TOOLS",
    "execute_agent_tools",
]
