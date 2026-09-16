"""nak.service -- headless API over the harness (no terminal, no chat loop).

This is the single seam for everything that talks to NebulonAK from the
outside: the thin terminal CLI, the future drag-and-drop canvas, and the
Python code builder. All functions here are pure request -> dict: no
input(), no print(), no argparse. Errors raise (BrainError / ValueError)
and the CLI layer maps them to exit codes + JSON.

Builder coordination:
- Every public method has a stable op id (OP_* / node type "nak.<name>")
  so the canvas and the code generator reference the same catalogue.
- get_node_catalog() describes draggable nodes (agents + tools + stages).
- build_python(graph) turns a node graph into runnable Python that only
  calls this package — the generated code never imports the old REPL.
"""
from .chat import NakService, create_service
from .nodes import get_node_catalog, build_python, NODE_CATALOG

__all__ = ["NakService", "create_service", "get_node_catalog", "build_python", "NODE_CATALOG"]
