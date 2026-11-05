"""File tools — sandboxed to a workspace root.

Contract: fn(args: dict) -> JSON string. Every tool takes:
  in:  {"path": "<path inside root, required>", "root": "<sandbox dir, optional>"}
       write_file also takes {"content": "<text>", "mode": "create|overwrite|append"}
  out: '{"ok": true, ...}' or '{"ok": false, "error": ...}'

Any file type is fine as long as it opens with open() (text mode with
decoding errors replaced). Sandbox: path must resolve inside root
(no .. / absolute / symlink escapes); .env, *.key/*.pem and secrets/ +
.git/ are denied even inside root. Root defaults to NAK_WORKSPACE env
or repo workspace (the engine passes the session dir as root at run).
Permission (allow/ask/deny) is enforced by the engine inspect() BEFORE
any tool runs — the tools themselves only validate the path args.
Always returns a string, never raises — engine-safe.
"""

from __future__ import annotations

import json
import os

from pathlib import Path
from typing import Optional

try:
    from ..engine.sandbox import default_workspace, safe_join
except Exception:  # pragma: no cover — direct-path import fallback
    from nak.harness.engine.sandbox import default_workspace, safe_join  # type: ignore

from nak.utils.constant import (
    FILE_LIST_MAX_ENTRIES as MAX_ENTRIES,
    FILE_READ_MAX_CHARS as MAX_CHARS,
)


def _root(data: dict) -> str:
    return (str(data.get("root", "")).strip()
            or os.environ.get("NAK_WORKSPACE", "").strip()
            or str(default_workspace()))


def read_file(args: dict) -> str:
    """Read any file inside the sandbox root (text mode, undecodable bytes replaced)."""
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a file path"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"not found: {p}"})
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {p}"})
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    truncated = False
    if len(text) > MAX_CHARS:
        text, truncated = text[:MAX_CHARS] + "\n…(truncated)", True
    return json.dumps({"ok": True, "path": str(p), "content": text, "truncated": truncated})


def list_directory(args: dict) -> str:
    """List entries of a directory inside the sandbox root (empty path = root itself)."""
    data = args or {}
    raw = str(data.get("path", "")).strip() or "."
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"not found: {p}"})
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"not a directory: {p}"})
    try:
        items = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    entries = []
    for child in items[:MAX_ENTRIES]:
        try:
            kind = "dir" if child.is_dir() else "file"
            size = child.stat().st_size if child.is_file() else 0
        except OSError:
            kind, size = "unknown", 0
        entries.append({"name": child.name, "type": kind, "size": size})
    return json.dumps({"ok": True, "path": str(p), "entries": entries,
                       "truncated": len(items) > MAX_ENTRIES})


def write_file(args: dict) -> str:
    """Create a new file or update (overwrite/append) an existing one.

    mode: create (fail if exists) | overwrite (default: create-or-overwrite)
          | append (create-or-append). Parent dirs are created as needed.
    """
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a file path"})
    content = data.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        return json.dumps({"ok": False, "error": "content must be a string"})
    mode = str(data.get("mode", "overwrite")).strip().lower() or "overwrite"
    if mode not in ("create", "overwrite", "append"):
        return json.dumps({"ok": False, "error": f"bad mode {mode!r}: want create|overwrite|append"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    try:
        if p.exists() and p.is_dir():
            return json.dumps({"ok": False, "error": f"not a file: {p}"})
        existed = p.is_file()
        if mode == "create" and existed:
            return json.dumps({"ok": False, "error": f"already exists: {p} (use overwrite or append)"})
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and existed:
            with open(p, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(content)
        else:
            p.write_text(content, encoding="utf-8")
        written = len(content.encode("utf-8", "ignore"))
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(p), "bytes": written,
                       "created": not existed, "mode": mode})


def edit_file(args: dict) -> str:
    """Replace an exact string in one file inside the sandbox root.

    in: {"path": "<file>", "old_string": "<exact text>",
         "new_string": "<replacement>", "replace_all": false}
    The old text must occur exactly once unless replace_all is true, so a
    blind edit never changes the wrong location.
    """
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a file path"})
    old = data.get("old_string", data.get("old"))
    new = data.get("new_string", data.get("new"))
    if not isinstance(old, str) or not old:
        return json.dumps({"ok": False, "error": "missing old_string: pass the exact text to replace"})
    if not isinstance(new, str):
        return json.dumps({"ok": False, "error": "missing new_string: pass the replacement (may be empty)"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    if not p.is_file():
        return json.dumps({"ok": False, "error": f"not a file: {p}"})
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    count = text.count(old)
    if count == 0:
        return json.dumps({"ok": False, "error": "old_string not found in file"})
    replace_all = bool(data.get("replace_all", False))
    if count > 1 and not replace_all:
        return json.dumps({"ok": False,
                           "error": f"old_string occurs {count} times; pass replace_all=true or a unique string"})
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        p.write_text(updated, encoding="utf-8")
        written = len(updated.encode("utf-8", "ignore"))
    except (OSError, UnicodeError) as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(p), "bytes": written,
                       "replacements": count if replace_all else 1})


def file(args: dict) -> str:
    """Single file tool: route args.action (read|list|write|edit|delete)."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower()
    if action in ("read", "cat", "get"):
        return read_file(data)
    if action in ("list", "ls", "dir"):
        return list_directory(data)
    if action in ("write", "create", "update", "append", "put"):
        if "mode" not in data:
            data = {**data, "mode": {"create": "create", "append": "append"}.get(action, "overwrite")}
        return write_file(data)
    if action in ("edit", "replace", "patch"):
        return edit_file(data)
    if action in ("delete", "remove", "rm", "del"):
        return delete_file(data)
    return json.dumps({"ok": False,
                       "error": f"bad action {str(data.get('action', ''))!r}: want read|list|write|edit|delete"})


def delete_file(args: dict) -> str:
    """Delete a file inside the sandbox root (dirs only when empty — use dir/delete for trees)."""
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a file path"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"not found: {p}"})
    try:
        if p.is_dir():
            p.rmdir()  # only empty dirs — non-empty raises OSError below
            return json.dumps({"ok": True, "path": str(p), "deleted": "dir"})
        p.unlink()
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(p), "deleted": "file"})


def create_dir(args: dict) -> str:
    """Create a directory (parents as needed). Exists-as-dir is ok (created: false)."""
    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a directory path"})
    ok, resolved = safe_join(_root(data), raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    try:
        if p.exists():
            if not p.is_dir():
                return json.dumps({"ok": False, "error": f"not a directory: {p}"})
            return json.dumps({"ok": True, "path": str(p), "created": False})
        p.mkdir(parents=True, exist_ok=False)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(p), "created": True})


def rename_path(args: dict) -> str:
    """Rename / move a file or directory inside the sandbox root.

    in: {"path": "<source>", "new_path": "<destination>"} — both inside root.
    Destination must not exist. Never overwrites.
    """
    data = args or {}
    raw = str(data.get("path", "")).strip()
    new_raw = str(data.get("new_path", "") or data.get("to", "")).strip()
    if not raw:
        return json.dumps({"ok": False, "error": "missing path: worker must pass a source path"})
    if not new_raw:
        return json.dumps({"ok": False, "error": "missing new_path: worker must pass a destination path"})
    root = _root(data)
    ok, resolved = safe_join(root, raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    ok2, resolved2 = safe_join(root, new_raw)
    if not ok2:
        return json.dumps({"ok": False, "error": resolved2})
    src, dst = Path(resolved), Path(resolved2)
    if not src.exists():
        return json.dumps({"ok": False, "error": f"not found: {src}"})
    if dst.exists():
        return json.dumps({"ok": False, "error": f"already exists: {dst} (never overwrites)"})
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(dst), "from": str(src)})


def _tree_denied(root: str, target: Path) -> Optional[str]:
    """Pre-scan a dir tree: every nested path must itself pass safe_join (denials hold inside)."""
    try:
        base = Path(root).expanduser().resolve()
        t = target.resolve()
    except OSError as exc:
        return str(exc)[:200]
    for child in sorted(t.rglob("*")):
        try:
            rel = child.relative_to(base).as_posix()
        except ValueError:
            return f"path escapes workspace root: {child}"
        ok, reason = safe_join(root, rel)
        if not ok:
            return f"nested path blocked ({rel}): {reason}"
    return None


def delete_dir(args: dict) -> str:
    """Delete a directory tree inside the sandbox root (recursive).

    Refuses the root itself and aborts when any nested path is sandbox-denied.
    """
    import shutil

    data = args or {}
    raw = str(data.get("path", "")).strip()
    if not raw or raw in (".", "./", "/"):
        return json.dumps({"ok": False, "error": "refusing to delete the sandbox root itself"})
    root = _root(data)
    ok, resolved = safe_join(root, raw)
    if not ok:
        return json.dumps({"ok": False, "error": resolved})
    p = Path(resolved)
    if not p.exists():
        return json.dumps({"ok": False, "error": f"not found: {p}"})
    if not p.is_dir():
        return json.dumps({"ok": False, "error": f"not a directory: {p} (use file/delete for files)"})
    blocked = _tree_denied(root, p)
    if blocked:
        return json.dumps({"ok": False, "error": blocked})
    try:
        shutil.rmtree(p)
    except OSError as exc:
        return json.dumps({"ok": False, "error": str(exc)[:300]})
    return json.dumps({"ok": True, "path": str(p), "deleted": "dir"})


def directory(args: dict) -> str:
    """Single directory tool: route args.action (create|list|rename|delete) to the op above."""
    data = args or {}
    action = str(data.get("action", "")).strip().lower()
    if action in ("create", "mkdir", "makedirs", "make"):
        return create_dir(data)
    if action in ("list", "ls", "dir"):
        return list_directory(data)
    if action in ("rename", "move", "mv", "ren"):
        return rename_path(data)
    if action in ("delete", "remove", "rm", "del", "rmdir"):
        return delete_dir(data)
    return json.dumps({"ok": False,
                       "error": f"bad action {str(data.get('action', ''))!r}: want create|list|rename|delete"})
