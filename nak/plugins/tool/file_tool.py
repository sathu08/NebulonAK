"""
nak.plugins.tool.file_tool -- generic file read/write tools for agents.

Created agents currently use NO tools (plain Brain.chat proxy). This module
gives them (and any LLM loop) two generic tools with OpenAI-compatible
schemas, following the memory_tools.py convention:

    from nak.plugins.tool import FILE_TOOLS, execute_file_tool

    result_json = execute_file_tool("read_file", {"path": "notes/todo.md"})
    result_json = execute_file_tool("write_file", {"path": "out.md", "content": "..."})

read_file handles, by extension:
    text/code  .txt .md .py .js .ts .jsonl .log .yaml/.yml .toml .cfg .ini
               .html .css .sh and anything else tried as text
    .json      parsed JSON (returned as data + raw text)
    .csv/.tsv  parsed rows (capped)
    .pdf       via optional `pypdf` (clear error if not installed)
    .xlsx/.xls via optional `openpyxl` (clear error if not installed)
    .docx      via optional `python-docx` (clear error if not installed)

Safety (production):
    - binary sniff (null bytes) -> refused with a message, never dumped
    - size caps (max_bytes / max_rows) with truncated flag + total size
    - write_file is sandboxed under `root` (default: repo root); `..`
      escapes outside root are rejected; modes create/overwrite/append
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_MAX_BYTES = 200_000
_DEFAULT_MAX_ROWS = 200

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".py", ".js", ".ts", ".tsx", ".jsx",
    ".jsonl", ".ndjson", ".log", ".yaml", ".yml", ".toml", ".cfg",
    ".ini", ".html", ".htm", ".css", ".sh", ".bash", ".sql", ".xml",
    ".rst", ".c", ".h", ".cpp", ".java", ".go", ".rs", ".r", ".scala",
}

_PDF_EXTS = {".pdf"}
_EXCEL_EXTS = {".xlsx", ".xls"}
_WORD_EXTS = {".docx"}


def _repo_root() -> Path:
    # nak/plugins/tool/file_tool.py -> tool -> plugins -> nak -> NebulonAK
    return Path(__file__).resolve().parents[3]


def _resolve(path: str, root: Optional[str | Path] = None) -> Path:
    """Resolve `path` (absolute or root-relative) and sandbox it under root."""
    base = Path(root).expanduser().resolve() if root else _repo_root()
    p = Path(path).expanduser()
    target = p.resolve() if p.is_absolute() else (base / p).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise ValueError(f"path escapes sandbox root {base}: {path!r}")
    return target


def _read_text(p: Path, max_bytes: int) -> Dict[str, Any]:
    size = p.stat().st_size
    with p.open("rb") as f:
        raw = f.read(max_bytes + 1)
    if b"\x00" in raw:
        raise ValueError(f"binary file refused (null bytes): {p.name} ({size} bytes)")
    truncated = len(raw) > max_bytes
    text = raw[:max_bytes].decode("utf-8", errors="replace")
    return {"type": "text", "content": text, "truncated": truncated, "size_bytes": size}


def read_file(
    path: str,
    *,
    root: Optional[str | Path] = None,
    encoding: str = "utf-8",  # kept for schema compat; decode uses errors="replace"
    max_bytes: int = _DEFAULT_MAX_BYTES,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    """Read a file of any common type. Returns a JSON-serializable dict."""
    _ = encoding  # reserved: decoding is utf-8 with replacement (never crashes)
    target = _resolve(path, root)
    if not target.exists():
        raise FileNotFoundError(f"no such file: {path!r}")
    if target.is_dir():
        raise ValueError(f"path is a directory, not a file: {path!r}")
    ext = target.suffix.lower()
    out: Dict[str, Any] = {"path": str(target), "extension": ext}

    if ext == ".json":
        text_info = _read_text(target, max_bytes)
        try:
            out.update({"type": "json", "data": json.loads(text_info["content"]),
                        "truncated": text_info["truncated"], "size_bytes": text_info["size_bytes"]})
        except json.JSONDecodeError as exc:
            # truncated JSON won't parse — return raw text with the error
            out.update({"type": "json_unparsed", "content": text_info["content"],
                        "parse_error": str(exc)[:200], "truncated": text_info["truncated"],
                        "size_bytes": text_info["size_bytes"]})
        return out

    if ext in (".csv", ".tsv"):
        size = target.stat().st_size
        delim = "\t" if ext == ".tsv" else ","
        with target.open("r", encoding="utf-8", errors="replace", newline="") as f:
            rows = list(csv.reader(f, delimiter=delim))
        truncated = len(rows) > max_rows
        out.update({"type": "csv", "rows": rows[:max_rows], "row_count": len(rows),
                    "truncated": truncated, "size_bytes": size})
        return out

    if ext in _PDF_EXTS:
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            raise ValueError("PDF support needs `pip install pypdf` (file refused, not guessed)")
        reader = PdfReader(str(target))
        pages = [ (pg.extract_text() or "") for pg in reader.pages[:50] ]
        text = "\n".join(pages)[:max_bytes]
        out.update({"type": "pdf", "content": text, "pages": len(reader.pages),
                    "truncated": len(reader.pages) > 50 or len("\n".join(pages)) > max_bytes})
        return out

    if ext in _EXCEL_EXTS:
        try:
            from openpyxl import load_workbook  # type: ignore
        except ImportError:
            raise ValueError("Excel support needs `pip install openpyxl` (file refused, not guessed)")
        wb = load_workbook(str(target), read_only=True, data_only=True)
        sheets: Dict[str, Any] = {}
        for ws in wb.worksheets[:10]:
            rows = [[c.value for c in row] for row in ws.iter_rows(max_row=max_rows)]
            sheets[ws.title] = {"rows": rows, "max_row": ws.max_row, "max_column": ws.max_column}
        out.update({"type": "excel", "sheets": sheets, "sheet_names": wb.sheetnames})
        return out

    if ext in _WORD_EXTS:
        try:
            import docx  # type: ignore
        except ImportError:
            raise ValueError("Word support needs `pip install python-docx` (file refused, not guessed)")
        doc = docx.Document(str(target))
        text = "\n".join(p.text for p in doc.paragraphs)[:max_bytes]
        out.update({"type": "docx", "content": text, "paragraphs": len(doc.paragraphs)})
        return out

    # text-like extensions, extensionless files, or anything else: try as text
    info = _read_text(target, max_bytes)
    info["path"] = str(target)
    info["extension"] = ext
    return info


def write_file(
    path: str,
    content: str,
    *,
    root: Optional[str | Path] = None,
    mode: str = "create",
    make_parents: bool = True,
) -> Dict[str, Any]:
    """Write content to a file inside the sandbox root.

    Modes: create (fail if exists), overwrite, append.
    """
    if mode not in ("create", "overwrite", "append"):
        raise ValueError(f"invalid mode {mode!r}: use create|overwrite|append")
    target = _resolve(path, root)
    if target.exists() and target.is_dir():
        raise ValueError(f"path is a directory: {path!r}")
    if mode == "create" and target.exists():
        raise FileExistsError(f"file already exists (use overwrite|append): {path!r}")
    if make_parents:
        target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append":
        with target.open("a", encoding="utf-8") as f:
            f.write(content)
    else:
        target.write_text(content, encoding="utf-8")
    size = target.stat().st_size
    return {"path": str(target), "mode": mode, "size_bytes": size, "chars_written": len(content)}


FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file (text, JSON, CSV/TSV, PDF, Excel, Word). Use when the agent needs file contents to answer or plan.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative or absolute file path"},
                    "max_bytes": {"type": "integer", "description": "Max text bytes to return", "minimum": 100, "maximum": 1000000},
                    "max_rows": {"type": "integer", "description": "Max CSV/Excel rows to return", "minimum": 1, "maximum": 1000},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file inside the sandbox root. Use to save plans, reports, or generated files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative or absolute file path"},
                    "content": {"type": "string", "description": "Full content to write"},
                    "mode": {"type": "string", "enum": ["create", "overwrite", "append"]},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_file_tool(name: str, arguments: str | Dict[str, Any]) -> str:
    """Route a file tool call. Returns JSON string for the tool message."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    try:
        if name == "read_file":
            res = read_file(args.get("path", ""),
                            max_bytes=int(args.get("max_bytes") or _DEFAULT_MAX_BYTES),
                            max_rows=int(args.get("max_rows") or _DEFAULT_MAX_ROWS))
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        if name == "write_file":
            res = write_file(args.get("path", ""), args.get("content", ""),
                             mode=str(args.get("mode") or "create"))
            return json.dumps(res, ensure_ascii=False, default=str)
        return json.dumps({"error": f"unknown file tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


__all__ = ["FILE_TOOLS", "read_file", "write_file", "execute_file_tool"]


def _rows_preview(rows: List[List[Any]], n: int = 3) -> List[List[Any]]:
    """Small helper for logs/tests: first n CSV rows."""
    return rows[:n]
