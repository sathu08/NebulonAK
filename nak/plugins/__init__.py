"""nak.plugins -- plugin folders for NAK (tool today, mcp planned).

    from nak.plugins import PLUGIN_TOOLS, execute_plugin

PLUGIN_TOOLS = memory tools (need Brain) + file tools (no Brain).
execute_plugin(brain, name, arguments) routes to the right executor so
LLM loops and future agents need only one tool list + one call.
"""
from .tool import (
    NEBULONAK_TOOLS,
    NEBULON_TOOLS,
    execute_tool,
    get_tool_map,
    FILE_TOOLS,
    execute_file_tool,
)

PLUGIN_TOOLS = list(NEBULONAK_TOOLS) + list(FILE_TOOLS)


def execute_plugin(brain, name: str, arguments) -> str:
    """Route any plugin tool call. File tools ignore `brain`."""
    file_names = {spec["function"]["name"] for spec in FILE_TOOLS}
    if name in file_names:
        return execute_file_tool(name, arguments)
    return execute_tool(brain, name, arguments)


__all__ = [
    "PLUGIN_TOOLS",
    "execute_plugin",
    "NEBULONAK_TOOLS",
    "NEBULON_TOOLS",
    "execute_tool",
    "get_tool_map",
    "FILE_TOOLS",
    "execute_file_tool",
]
