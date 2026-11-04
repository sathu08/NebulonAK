"""nak.plugins.tool.web_tool -- weblink fetch tools for agents.

New links never touch file_tool.py: this module downloads a URL into the
task workspace (sandboxed) and then reuses file_tool.read_file() so every
format parses exactly like a local file:

    text/html/md  -> text (raw, truncated)
    .json         -> parsed data
    .csv/.tsv     -> parsed rows
    .pdf/.xlsx/.docx -> via optional deps (pypdf / openpyxl / python-docx)

    from nak.plugins.tool import WEB_TOOLS, execute_web_tool

    result = execute_web_tool("read_url", {"url": "https://.../guide.md"})
    result = execute_web_tool("download_file", {"url": "...", "path": "paper.pdf"})

Safety (production):
    - http/https only (file://, ftp://, localhost/private hosts refused)
    - timeout 30s max, download cap 10MB (configurable, truncated flag)
    - saves land under `root` (harness workspace); `..` escapes rejected
    - stdlib only (urllib) — no new dependencies
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_BYTES = 10_000_000
_DEFAULT_READ_BYTES = 200_000
_DEFAULT_MAX_ROWS = 200

# content-type (prefix match, lowercased, without params) -> save extension
_CONTENT_EXTS = {
    "application/pdf": ".pdf",
    "application/json": ".json",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/plain": ".txt",
}


def _repo_root() -> Path:
    # nak/plugins/tool/web_tool.py -> tool -> plugins -> nak -> NebulonAK
    return Path(__file__).resolve().parents[3]


def _resolve_save(path: str, root: Optional[str | Path] = None) -> Path:
    """Sandbox a save path under root (same rule as file_tool._resolve)."""
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


def _check_url(url: str) -> urllib.parse.ParseResult:
    """Allow http/https only; refuse localhost + private IPs (SSRF guard)."""
    parts = urllib.parse.urlparse((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"only http/https URLs allowed, got {url!r}"[:200])
    host = (parts.hostname or "").lower()
    if not host or host in ("localhost",):
        raise ValueError(f"refused local host {host!r} — fetch it yourself in a terminal")
    if host.endswith((".localhost", ".local", ".internal")):
        raise ValueError(f"refused internal host {host!r}")
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError(f"refused private host {host!r} ({ip})")
    except ValueError:
        raise
    except Exception:
        pass  # DNS failed -> let urlopen raise the real error
    return parts


def _ext_for(content_type: str, url_path: str) -> str:
    """Pick a save extension: content-type first, URL suffix fallback."""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype in _CONTENT_EXTS:
        return _CONTENT_EXTS[ctype]
    suffix = Path(url_path or "").suffix.lower()
    if suffix and len(suffix) <= 6:
        return suffix
    if ctype.startswith("text/"):
        return ".txt"
    return ".html"


def _fetch(url: str, timeout: int = _DEFAULT_TIMEOUT,
           max_bytes: int = _DEFAULT_MAX_BYTES) -> Dict[str, Any]:
    """Download a URL. Returns {content, content_type, final_url, truncated}."""
    parts = _check_url(url)
    timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), 60))
    req = urllib.request.Request(
        parts.geturl(),
        headers={"User-Agent": "NebulonAK-webtool/1", "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            chunks: list[bytes] = []
            total = 0
            truncated = False
            while True:
                buf = resp.read(65536)
                if not buf:
                    break
                total += len(buf)
                if total > max_bytes:
                    over = total - max_bytes
                    chunks.append(buf[: len(buf) - over])
                    truncated = True
                    break
                chunks.append(buf)
            return {"content": b"".join(chunks), "content_type": ctype,
                    "final_url": resp.geturl(), "truncated": truncated,
                    "size_bytes": total}
    except urllib.error.HTTPError as exc:
        raise ValueError(f"HTTP {exc.code} for {url!r}"[:200])
    except urllib.error.URLError as exc:
        raise ValueError(f"fetch failed for {url!r}: {exc.reason}"[:200])


def read_url(
    url: str,
    *,
    root: Optional[str | Path] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    max_bytes: int = _DEFAULT_READ_BYTES,
    max_rows: int = _DEFAULT_MAX_ROWS,
) -> Dict[str, Any]:
    """Fetch a weblink and parse it like a local file (all formats).

    Downloads into the workspace sandbox (hashed filename + real extension),
    then delegates parsing to file_tool.read_file(). Returns the parsed dict
    plus {url, final_url, content_type}.
    """
    fetched = _fetch(url, timeout=timeout, max_bytes=_DEFAULT_MAX_BYTES)
    parts = urllib.parse.urlparse(fetched["final_url"] or url)
    ext = _ext_for(fetched["content_type"], parts.path)
    digest = hashlib.sha256(fetched["content"]).hexdigest()[:12]
    save_name = f"url_{digest}{ext}"
    target = _resolve_save(save_name, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fetched["content"][: _DEFAULT_MAX_BYTES])

    from .file_tool import read_file

    try:
        parsed = read_file(str(target), root=target.parent,
                           max_bytes=max_bytes, max_rows=max_rows)
    except Exception as exc:
        raise ValueError(f"downloaded but unparseable ({ext}): {exc}"[:300])
    parsed["url"] = url
    parsed["final_url"] = fetched["final_url"]
    parsed["content_type"] = fetched["content_type"]
    parsed["download_truncated"] = fetched["truncated"]
    return parsed


def download_file(
    url: str,
    path: str,
    *,
    root: Optional[str | Path] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Save a weblink to `path` inside the sandbox root. Returns metadata."""
    if not path or not path.strip():
        raise ValueError("path must be non-empty")
    fetched = _fetch(url, timeout=timeout, max_bytes=_DEFAULT_MAX_BYTES)
    target = _resolve_save(path.strip(), root)
    if target.exists() and target.is_dir():
        raise ValueError(f"path is a directory: {path!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fetched["content"])
    return {"path": str(target), "url": url, "final_url": fetched["final_url"],
            "content_type": fetched["content_type"],
            "size_bytes": target.stat().st_size,
            "truncated": fetched["truncated"]}


WEB_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_url",
            "description": "Fetch a weblink (md, html, text, JSON, CSV, PDF, Excel, Word) and parse it. Use when the user shares a URL to read or summarize.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL to fetch"},
                    "max_bytes": {"type": "integer", "description": "Max text bytes to return", "minimum": 100, "maximum": 1000000},
                    "max_rows": {"type": "integer", "description": "Max CSV/Excel rows to return", "minimum": 1, "maximum": 1000},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "Download a weblink to a file inside the sandbox root. Use to save a linked file for later reading.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http(s) URL to download"},
                    "path": {"type": "string", "description": "Sandbox-relative save path"},
                },
                "required": ["url", "path"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_SCOPES = {"read_url": "file", "download_file": "file"}


def execute_web_tool(name: str, arguments: str | Dict[str, Any], *,
                     root: Optional[str | Path] = None) -> str:
    """Route a web tool call. Returns JSON string (errors as {"error": ...})."""
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except json.JSONDecodeError:
            return json.dumps({"error": f"invalid JSON arguments: {arguments[:200]}"})
    else:
        args = dict(arguments or {})
    effective_root = args.pop("_root", root)
    try:
        if name == "read_url":
            res = read_url(args.get("url") or args.get("link") or args.get("href") or "",
                           root=effective_root,
                           timeout=int(args.get("timeout") or _DEFAULT_TIMEOUT),
                           max_bytes=int(args.get("max_bytes") or _DEFAULT_READ_BYTES),
                           max_rows=int(args.get("max_rows") or _DEFAULT_MAX_ROWS))
            return json.dumps(res, ensure_ascii=False, default=str)[:20000]
        if name == "download_file":
            res = download_file(args.get("url") or args.get("link") or "",
                                args.get("path") or args.get("file") or "",
                                root=effective_root,
                                timeout=int(args.get("timeout") or _DEFAULT_TIMEOUT))
            return json.dumps(res, ensure_ascii=False, default=str)
        return json.dumps({"error": f"unknown web tool {name!r}"})
    except Exception as exc:  # keep LLM loop alive
        return json.dumps({"error": str(exc)[:500]})


__all__ = ["WEB_TOOLS", "TOOL_SCOPES", "read_url", "download_file", "execute_web_tool"]
