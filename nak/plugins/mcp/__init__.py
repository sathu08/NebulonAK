"""nak.plugins.mcp -- Model Context Protocol integration (PLAN, not implemented).

Planned layout (deferred):
    nak/plugins/mcp/server.py  expose PLUGIN_TOOLS (memory + file) as MCP tools
    nak/plugins/mcp/client.py  call external MCP servers from agents/pipelines
    registration                merge MCP tool specs into PLUGIN_TOOLS so
                                Polaris can route them like local tools

Status: placeholder package so `import nak.plugins.mcp` never breaks.
No runtime behavior yet — implement per plan above when needed.
"""

__all__: list = []
