"""
nak.plugins.tool.file_tool -- generic file + filesystem tools for agents.

Merged module (absorbs legacy fs_tools.py):
    read_file / write_file  single-file content (all types)
    edit_file / list_files / search_files  filesystem ops (same sandbox)

    from nak.plugins.tool import FILE_TOOLS, FS_TOOLS, execute_file_tool, execute_fs_tool

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
    - all paths sandboxed under `root` (default: repo root); `..`
      escapes outside root are rejected; modes create/overwrite/append
"""
from __future__ import annotations

import csv
import fnmatch
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

_DEFAULT_MAX_BYTES = 200_000
_DEFAULT_MAX_ROWS = 200
_DEFAULT_MAX_RESULTS = 50

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
    """Resolve `path` (absolute or root-relative) and sandbox it under root.

    root=None falls back to the ambient harness workspace (when an agent runs
    under AgentExecutor) and finally to the repo root (legacy behaviour).
    """
    if root is None:
        try:
            from .workspace import current_workspace_root

            ambient = current_workspace_root()
            if ambient is not None:
                root = ambient
        except Exception:
            pass
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
            # Alias tolerance (like edit_file): LLMs often guess `file`/
            # `filename`/`filepath`. Canonical `path` wins when present.
            res = read_file(args.get("path") or args.get("file")
                            or args.get("filename") or args.get("filepath") or "",
                            max_bytes=int(args.get("max_bytes") or _DEFAULT_MAX_BYTES),
                            max_rows=int(args.get("max_rows") or _DEFAULT_MAX_ROWS))
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        if name == "write_file":
            res = write_file(args.get("path") or args.get("file")
                             or args.get("filename") or args.get("filepath") or "",
                             args.get("content", ""),
                             mode=str(args.get("mode") or "create"))
            return json.dumps(res, ensure_ascii=False, default=str)
        return json.dumps({"error": f"unknown file tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


# -- filesystem ops (merged from legacy fs_tools.py) ---------------------------


def edit_file(
    path: str,
    old_text: str,
    new_text: str,
    *,
    root: Optional[str | Path] = None,
    replace_all: bool = False,
) -> Dict[str, Any]:
    """Replace old_text with new_text inside a text file."""
    if not old_text:
        raise ValueError("old_text must be non-empty")
    target = _resolve(path, root)
    if not target.exists():
        raise FileNotFoundError(f"no such file: {path!r}")
    if target.is_dir():
        raise ValueError(f"path is a directory: {path!r}")
    original = target.read_text(encoding="utf-8", errors="replace")
    if old_text not in original:
        raise ValueError(f"old_text not found in {path!r}")
    updated = (
        original.replace(old_text, new_text)
        if replace_all
        else original.replace(old_text, new_text, 1)
    )
    target.write_text(updated, encoding="utf-8")
    return {
        "path": str(target),
        "replacements": original.count(old_text) if replace_all else 1,
        "size_bytes": target.stat().st_size,
    }


def list_files(
    directory: str = ".",
    *,
    root: Optional[str | Path] = None,
    pattern: str = "*",
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """List files under `directory` (root-relative), optional glob pattern."""
    base = _resolve(directory or ".", root)
    if not base.exists():
        raise FileNotFoundError(f"no such directory: {directory!r}")
    if not base.is_dir():
        raise ValueError(f"not a directory: {directory!r}")
    entries: List[str] = []
    truncated = False
    for p in sorted(base.rglob("*")):
        rel = p.relative_to(base).as_posix()
        if not fnmatch.fnmatch(rel, pattern) and not fnmatch.fnmatch(
            p.name, pattern
        ):
            continue
        entries.append(rel + ("/" if p.is_dir() else ""))
        if len(entries) >= max_results:
            truncated = True
            break
    return {
        "directory": str(base),
        "pattern": pattern,
        "entries": entries,
        "count": len(entries),
        "truncated": truncated,
    }


def search_files(
    pattern: str,
    *,
    root: Optional[str | Path] = None,
    directory: str = ".",
    file_pattern: str = "*",
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> Dict[str, Any]:
    """Regex search across text files. Returns [{file, line, text}]."""
    if not pattern:
        raise ValueError("pattern must be non-empty")
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}")
    base = _resolve(directory or ".", root)
    if not base.exists():
        raise FileNotFoundError(f"no such directory: {directory!r}")
    matches: List[Dict[str, Any]] = []
    files_scanned = 0
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        if not fnmatch.fnmatch(p.name, file_pattern):
            continue
        files_scanned += 1
        try:
            if p.stat().st_size > _DEFAULT_MAX_BYTES:
                continue
            with p.open("rb") as f:
                head = f.read(4096)
            if b"\x00" in head:
                continue  # skip binaries
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                matches.append(
                    {
                        "file": p.relative_to(base).as_posix(),
                        "line": i,
                        "text": line[:300],
                    }
                )
                if len(matches) >= max_results:
                    return {
                        "pattern": pattern,
                        "matches": matches,
                        "files_scanned": files_scanned,
                        "truncated": True,
                    }
    return {
        "pattern": pattern,
        "matches": matches,
        "files_scanned": files_scanned,
        "truncated": False,
    }


FS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace text inside a file. Use to patch code/config without rewriting the whole file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Root-relative or absolute file path"},
                    "old_text": {"type": "string", "description": "Exact existing text to replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                    "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false = first only)"},
                },
                "required": ["path", "old_text", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use to explore project layout before reading/editing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "description": "Directory to list (default '.')"},
                    "pattern": {"type": "string", "description": "Glob pattern (default '*')"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Regex-search file contents. Use to find code, symbols, or TODOs across a project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "directory": {"type": "string", "description": "Directory to search (default '.')"},
                    "file_pattern": {"type": "string", "description": "Glob for filenames (default '*')"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["pattern"],
                "additionalProperties": False,
            },
        },
    },
]


def execute_fs_tool(
    name: str,
    arguments: str | Dict[str, Any],
    *,
    root: Optional[str | Path] = None,
) -> str:
    """Route an fs tool call. Returns JSON string (errors as {"error": ...})."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    # harness may inject workspace root via `_root` without polluting the schema
    effective_root = args.pop("_root", root)
    try:
        if name == "edit_file":
            # Alias tolerance: LLMs often guess `old`/`new`/`file`. Canonical
            # keys win when both are present; schema still documents canonical.
            path = args.get("path") or args.get("file") or args.get("filename") or ""
            old = args.get("old_text")
            if old is None:
                old = args.get("old", "")
            new = args.get("new_text")
            if new is None:
                new = args.get("new", "")
            res = edit_file(
                path,
                old,
                new,
                root=effective_root,
                replace_all=bool(args.get("replace_all", False)),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        if name == "list_files":
            res = list_files(
                args.get("directory", "."),
                root=effective_root,
                pattern=str(args.get("pattern") or "*"),
                max_results=int(args.get("max_results") or _DEFAULT_MAX_RESULTS),
            )
            return json.dumps(res, ensure_ascii=False, default=str)
        if name == "search_files":
            res = search_files(
                args.get("pattern", ""),
                root=effective_root,
                directory=str(args.get("directory") or "."),
                file_pattern=str(args.get("file_pattern") or "*"),
                max_results=int(args.get("max_results") or _DEFAULT_MAX_RESULTS),
            )
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        return json.dumps({"error": f"unknown fs tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


def _rows_preview(rows: List[List[Any]], n: int = 3) -> List[List[Any]]:
    """Small helper for logs/tests: first n CSV rows."""
    return rows[:n]


__all__ = [
    "FILE_TOOLS",
    "FS_TOOLS",
    "read_file",
    "write_file",
    "execute_file_tool",
    "edit_file",
    "list_files",
    "search_files",
    "execute_fs_tool",
]
