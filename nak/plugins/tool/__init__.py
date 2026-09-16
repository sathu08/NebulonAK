"""nak.plugins.tool -- generic tools agents can call (memory + files).

    from nak.plugins.tool import NEBULONAK_TOOLS, FILE_TOOLS, execute_tool, execute_file_tool

terminal_tool (shared CLI grammar + terminal emit helpers) is imported as a
submodule -- `from nak.plugins.tool import terminal_tool` -- and is NOT part
of PLUGIN_TOOLS (it is the human boundary, not an LLM tool).
"""
from .memory_tools import (
    NEBULONAK_TOOLS,
    NEBULON_TOOLS,
    NEBULONAK_TOOL_SCHEMAS,
    NEBULON_TOOL_SCHEMAS,
    execute_tool,
    get_tool_map,
)
from .file_tool import (
    FILE_TOOLS,
    FS_TOOLS,
    read_file,
    write_file,
    execute_file_tool,
    edit_file,
    list_files,
    search_files,
    execute_fs_tool,
)
from .exec_tools import EXEC_TOOLS, shell, python_exec, run_test, run_build, detect_build_command, destructive_command_reason, execute_exec_tool
from .agent_tools import (
    AGENT_TOOLS,
    PLAN_TOOLS,
    delegate,
    create_plan,
    update_plan,
    execute_agent_tools,
)
from . import policy
from . import terminal_tool

__all__ = [
    "NEBULONAK_TOOLS",
    "NEBULON_TOOLS",
    "NEBULONAK_TOOL_SCHEMAS",
    "NEBULON_TOOL_SCHEMAS",
    "execute_tool",
    "get_tool_map",
    "FILE_TOOLS",
    "read_file",
    "write_file",
    "execute_file_tool",
    "FS_TOOLS",
    "edit_file",
    "list_files",
    "search_files",
    "execute_fs_tool",
    "EXEC_TOOLS",
    "shell",
    "python_exec",
    "run_test",
    "run_build",
    "detect_build_command",
    "destructive_command_reason",
    "execute_exec_tool",
    "AGENT_TOOLS",
    "PLAN_TOOLS",
    "delegate",
    "create_plan",
    "update_plan",
    "execute_agent_tools",
    "policy",
    "terminal_tool",
]
