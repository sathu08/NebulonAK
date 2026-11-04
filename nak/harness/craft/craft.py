"""nak.harness.craft — craft library (registry + markdown crafts).

Layout (flat, reviewable units):
  nak/manifests/crafts.json  registry: craft profiles (plan, assign, ...)
    single truth for tools + approval + limits (max_steps/auto_steps)
  nak/harness/craft/<mode>/craft.md  one craft: frontmatter (name/version/
    description only) + body (Purpose/Inputs/Steps/...)

craft.py is COMMON code only: discover + parse + validate + expand.
It NEVER executes anything and NEVER imports the engine, tools, worker,
brain or decision packages (raw JSON reads only — same boundary rule as
nak.decision). Execution stays in harness.run() per step; proposal stays
in nak.wiring.run_craft().

Flat-only rule: craft steps are plain tool steps. A step containing a
[worker=...] tag or a Craft: reference is refused at load — no nesting,
no cross-naming. Crafts declare; permissions.json + inspect() decide.

Fail-closed: discovery skips bad crafts (see craft_gaps()); explicit
load_craft()/expand_craft() raise ValueError with the reason.
"""

from __future__ import annotations

import json
import re

from pathlib import Path
from typing import Any, Dict, List, Optional

def _cfg_path(attr: str) -> Optional[str]:
    """cfg-backed path for a harness setting (None when no cfg is found)."""
    try:
        from nak.utils.settings import load_settings

        settings = load_settings()
        if settings.cfg_path is None:
            return None
        return str(getattr(settings, attr)() or "") or None
    except Exception:
        return None


def _default_registry() -> Path:
    """crafts.json path: env > cfg [harness] crafts > bundled default."""
    import os as _os

    cand = (_os.environ.get("NAK_CRAFTS", "")
            or _os.environ.get("NAK_CRAFTS_FILE", "") or "").strip()
    if cand:
        return Path(cand).expanduser()
    cfg = _cfg_path("crafts_path")
    if cfg:
        return Path(cfg).expanduser()
    return Path(__file__).resolve().parents[2] / "manifests" / "crafts.json"


def _craft_root() -> Path:
    """Craft markdown root: env > cfg [harness] crafts_prompt > bundled."""
    import os as _os

    cand = (_os.environ.get("NAK_CRAFTS_PROMPT", "") or "").strip()
    if cand:
        return Path(cand).expanduser()
    cfg = _cfg_path("crafts_prompt_path")
    return Path(cfg).expanduser() if cfg else BASE


REGISTRY = _default_registry()
BASE = Path(__file__).resolve().parent

from nak.utils.constant import (
    CRAFT_VALID_APPROVALS as _VALID_APPROVALS,
    DECISION_ASSIGNMENTS as _DECISION_ASSIGNMENTS,
    DECISION_DEFAULTS as _DECISION_DEFAULTS,
    DECISION_MODES as _DECISION_MODES,
    DECISION_ON_ERROR as _DECISION_ON_ERROR,
    DECISION_SCOPES as _DECISION_SCOPES,
)
_WORKER_TAG = re.compile(r"\[\s*worker\s*=", re.IGNORECASE)
_CRAFT_REF = re.compile(r"(?m)^\s*craft\s*:", re.IGNORECASE)
_STEP_LINE = re.compile(r"^\s*(\d+)[.)]\s+(\S.*)$")
_TOKEN = re.compile(r"<([A-Za-z0-9_.\-]+)>")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _registry() -> Dict[str, Any]:
    # Refresh REGISTRY each call so NAK_CRAFTS env wins without reimport.
    try:
        reg = _default_registry()
        globals()["REGISTRY"] = reg
    except Exception:
        reg = globals().get("REGISTRY", REGISTRY)
    raw = _read_json(reg)
    return raw if isinstance(raw, dict) else {}


def _tools_registry() -> Path:
    """tools.json path: NAK_REGISTRY env > nak/manifests/tools.json."""
    import os as _os

    cand = (_os.environ.get("NAK_REGISTRY", "") or "").strip()
    if cand:
        return Path(cand).expanduser()
    return Path(__file__).resolve().parents[2] / "manifests" / "tools.json"


def _workers_path(explicit: Any = None) -> Path:
    """workers.json path: explicit > NAK_WORKERS_FILE env > nak/manifests/workers.json."""
    import os as _os

    if explicit:
        try:
            return Path(explicit).expanduser()
        except Exception:
            pass
    cand = (_os.environ.get("NAK_WORKERS_FILE", "") or "").strip()
    if cand:
        return Path(cand).expanduser()
    return Path(__file__).resolve().parents[2] / "manifests" / "workers.json"


def _tool_names() -> List[str]:
    """Tool names from tools.json (raw read — no tool imports here)."""
    raw = _read_json(_tools_registry())
    if not isinstance(raw, list):
        return []
    return sorted({str(e.get("name", "")).strip() for e in raw
                   if isinstance(e, dict) and str(e.get("name", "")).strip()})


def _split_frontmatter(text: str) -> tuple:
    """(meta lines, body). Raises ValueError when the --- block is missing."""
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("craft.md must start with a --- frontmatter block")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("craft.md frontmatter never closes (missing ---)")
    return lines[1:end], "\n".join(lines[end + 1:])


def _parse_frontmatter(meta_lines: List[str]) -> Dict[str, Any]:
    """Strict subset: scalar name/version/description only.

    tools + approval live ONLY in crafts.json pins (single truth) —
    craft.md carries no machine fields beyond identity.
    """
    meta: Dict[str, Any] = {}
    for ln in meta_lines:
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith(("*", "-")):
            continue
        if ":" in s:
            key, val = s.split(":", 1)
            key, val = key.strip().lower(), val.strip()
            if key in ("name", "version", "description"):
                meta[key] = val
    return meta


def _section(body: str, title: str) -> str:
    """Markdown under '## <title>' until the next '##' (or end)."""
    lines = str(body or "").splitlines()
    buf: List[str] = []
    inside = False
    for ln in lines:
        if re.match(r"^##\s+", ln):
            if inside:
                break
            if ln.strip("# ").strip().lower() == title.lower():
                inside = True
            continue
        if inside:
            buf.append(ln)
    return "\n".join(buf).strip()


def _steps_from_body(body: str) -> List[str]:
    steps: List[str] = []
    for ln in _section(body, "steps").splitlines():
        m = _STEP_LINE.match(ln)
        if m:
            steps.append(m.group(2).strip())
    return steps


def _required_inputs(body: str) -> List[str]:
    """Required inputs from ## Inputs: <angle> or `code` tokens on lines
    marked Required (case-insensitive). Matches the house md style."""
    req: List[str] = []
    for ln in _section(body, "inputs").splitlines():
        if "required" not in ln.lower():
            continue
        for tok in _TOKEN.findall(ln) + re.findall(r"`([^`\s]+)`", ln):
            if tok not in req:
                req.append(tok)
    return req


def _craft_pins() -> List[Dict[str, Any]]:
    reg = _registry()
    items = reg.get("crafts") if isinstance(reg.get("crafts"), list) else []
    if not items and isinstance(reg.get("craft"), list):
        items = reg.get("craft")  # backward compat: old singular key
    out: List[Dict[str, Any]] = []
    for e in items:
        if not isinstance(e, dict):
            continue
        mode = str(e.get("mode", "") or e.get("name", "")).strip()
        if not mode:
            continue
        d = dict(e)
        d["name"] = mode
        d["mode"] = mode
        if not str(d.get("path", "")).strip():
            d["path"] = mode
        out.append(d)
    return out


def _decision_pin(pin: Dict[str, Any], name: str) -> Dict[str, Any]:
    """Normalize the optional crafts.json "decision" block (fail-loud).

    Absent = disabled defaults. Values are validated here, not at runtime:
    a typo must break load_craft()/craft_gaps(), never silently no-op.
    """
    raw = pin.get("decision")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"craft {name!r}: decision must be an object")
    d = dict(_DECISION_DEFAULTS)
    d.update(raw)
    d["enabled"] = bool(d.get("enabled"))

    def _choice(key: str, valid: tuple) -> str:
        val = str(d.get(key, "") or "").strip().lower()
        if val not in valid:
            raise ValueError(f"craft {name!r}: bad decision.{key} "
                             f"{d.get(key)!r} (want one of {list(valid)})")
        d[key] = val
        return val

    _choice("scope", _DECISION_SCOPES)
    _choice("assignment", _DECISION_ASSIGNMENTS)
    _choice("mode", _DECISION_MODES)
    _choice("on_error", _DECISION_ON_ERROR)
    d["parallel"] = bool(d.get("parallel"))
    try:
        d["max_parallel"] = max(1, min(64, int(d.get("max_parallel"))))
    except (TypeError, ValueError):
        raise ValueError(f"craft {name!r}: decision.max_parallel must be an int") from None
    try:
        d["timeout"] = float(d.get("timeout"))
    except (TypeError, ValueError):
        raise ValueError(f"craft {name!r}: decision.timeout must be a number") from None
    if d["timeout"] <= 0:
        raise ValueError(f"craft {name!r}: decision.timeout must be > 0")
    questions = d.get("questions")
    d["questions"] = list(questions) if isinstance(questions, list) else None
    return d


def normalize_decision(raw: Any, name: str = "decision") -> Dict[str, Any]:
    """Public wrapper: validate a raw decision block with the same rules."""
    return _decision_pin({"decision": raw}, name)


def load_craft(name: str) -> Dict[str, Any]:
    """Parse + fully validate one craft. Raises ValueError with the reason.

    Checks: registry pin exists; pin tools non-empty and all known; pin
    approval known; folder craft.md exists; folder name == frontmatter
    name; version == pin; frontmatter description present; >=1 step;
    flat-only (no [worker=] tags, no Craft: references).
    tools + approval + limits + optional decision phase come from the
    crafts.json pin (single truth); the md supplies identity
    (name/version/description) + body.
    """
    want = str(name or "").strip()
    if not want:
        raise ValueError("craft name must be non-empty")
    pin = next((s for s in _craft_pins() if s.get("name") == want), None)
    if pin is None:
        raise ValueError(f"unknown craft {want!r} (not in crafts.json)")
    raw_tools = pin.get("tools", [])
    tools = [str(t).strip() for t in (raw_tools if isinstance(raw_tools, list) else [])
             if str(t).strip()]
    if not tools:
        raise ValueError(f"craft {want!r}: registry pin needs a non-empty tools list")
    known_tools = _tool_names()
    bad_tools = [t for t in tools if t not in known_tools]
    if bad_tools:
        raise ValueError(f"craft {want!r}: unknown tools {bad_tools} "
                         f"(have: {known_tools})")
    approval = str(pin.get("approval", "") or "").strip().lower()
    if approval not in _VALID_APPROVALS:
        raise ValueError(f"craft {want!r}: bad approval "
                         f"{pin.get('approval')!r}")
    folder = str(pin.get("path", "") or want).strip()
    if not folder or folder.startswith(("/", "~")) or ".." in folder:
        raise ValueError(f"craft {want!r}: bad path {folder!r}")
    md = (_craft_root() / folder / "craft.md")
    try:
        text = md.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"craft {want!r} unreadable: {exc}") from exc
    meta_lines, body = _split_frontmatter(text)
    meta = _parse_frontmatter(meta_lines)
    if folder != str(meta.get("name", "")).strip():
        raise ValueError(f"craft {want!r}: folder {folder!r} != frontmatter "
                         f"name {meta.get('name')!r}")
    if str(meta.get("version", "")).strip() != str(pin.get("version", "")).strip():
        raise ValueError(f"craft {want!r}: md version {meta.get('version')!r} != "
                         f"registry pin {pin.get('version')!r}")
    if not str(meta.get("description", "")).strip():
        raise ValueError(f"craft {want!r}: frontmatter missing 'description'")
    steps = _steps_from_body(body)
    if not steps:
        raise ValueError(f"craft {want!r}: no numbered steps under ## Steps")
    for i, step in enumerate(steps, 1):
        if _WORKER_TAG.search(step):
            raise ValueError(f"craft {want!r} step {i}: [worker=] tags refused "
                             f"(flat steps only — workers assign, crafts don't nest)")
        if _CRAFT_REF.match(step):
            raise ValueError(f"craft {want!r} step {i}: Craft: references refused "
                             f"(flat steps only — no nesting)")
    return {"name": want, "mode": want,
            "version": str(pin.get("version", "")).strip(),
            "description": str(meta.get("description", "")).strip(),
            "tools": tools, "approval": approval,
            "required_inputs": _required_inputs(body), "steps": steps,
            "max_steps": pin.get("max_steps"),
            "auto_steps": pin.get("auto_steps"),
            "modes": [str(m).strip().lower() for m in (pin.get("modes") or [])
                      if str(m).strip()],
            "roles": list(pin.get("roles") or []),
            "workers": list(pin.get("workers") or []),
            "decision": _decision_pin(pin, want)}


def list_crafts(worker: Optional[str] = None,
                role: Optional[str] = None,
                worker_modes: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Validated crafts, filtered by worker/role/modes allowlists.

    Modes gate: a craft with a non-empty modes pin is offered only when
    the worker's modes intersect it (a plan-only worker never sees an
    assign-only craft). worker_modes=None skips the modes check.
    Bad crafts are skipped (see craft_gaps()). Never raises.
    """
    want_modes = ([str(m).strip().lower() for m in (worker_modes or [])
                   if str(m).strip()] if worker_modes is not None else None)
    out: List[Dict[str, Any]] = []
    for pin in _craft_pins():
        try:
            craft = load_craft(str(pin.get("name", "")))
        except ValueError:
            continue
        if worker and craft["workers"] and worker not in craft["workers"]:
            continue
        if role and craft["roles"] and role not in craft["roles"]:
            continue
        if want_modes is not None and craft["modes"] and \
                not set(want_modes) & set(craft["modes"]):
            continue
        out.append(craft)
    return out


def craft_gaps() -> Dict[str, str]:
    """Pins that refuse to load: {craft name: reason}. Empty = all healthy."""
    gaps: Dict[str, str] = {}
    for pin in _craft_pins():
        name = str(pin.get("name", "")).strip() or "(unnamed pin)"
        try:
            load_craft(str(pin.get("name", "")))
        except ValueError as exc:
            gaps[name] = str(exc)[:200]
    return gaps


def expand_craft(name: str, inputs: Optional[Dict[str, Any]] = None) -> List[str]:
    """Steps with <placeholders> filled. Raises ValueError when a required
    input is missing. Unfilled optional tokens stay literal."""
    craft = load_craft(name)
    given = {str(k): str(v) for k, v in (inputs or {}).items()}
    missing = [k for k in craft["required_inputs"] if k not in given]
    if missing:
        raise ValueError(f"craft {craft['name']!r} missing inputs: {missing}")

    def _fill(step: str) -> str:
        def _rep(m: "re.Match") -> str:
            return given.get(m.group(1), m.group(0))
        return _TOKEN.sub(_rep, step)

    return [_fill(s) for s in craft["steps"]]


from nak.utils.constant import (
    CRAFT_DEFAULT_MODES as _DEFAULT_MODES,
    CRAFT_WORKER_MODES as _WORKER_MODES,
)


def _registry_modes() -> List[str]:
    """Valid craft modes from the crafts.json registry + implicit "assist".

    Registry is truth (a new pin needs no Python edit); _WORKER_MODES
    constant is fallback-only for unreadable registries. Never raises.
    """
    try:
        pins = _craft_pins()
        modes = [str(p.get("name", "")).strip().lower() for p in pins
                 if str(p.get("name", "")).strip()]
        if modes:
            return ["assist"] + [m for m in modes if m != "assist"]
    except Exception:
        pass
    return [str(m).strip().lower() for m in _WORKER_MODES if str(m).strip()]


def _worker_modes(entry: Any) -> List[str]:
    """Worker's modes from its crafts allowlist (no nak.worker import).

    crafts is the ONLY source of truth (no autonomy block): entry "crafts"
    ["plan", "build"] -> modes ["plan", "build"]; missing key = all
    crafts -> default modes; explicit [] = assist-only.
    Names are kept only when present in the registry (see _registry_modes).
    """
    if not isinstance(entry, dict):
        return list(_DEFAULT_MODES)
    raw_crafts = entry.get("crafts")
    if raw_crafts is None:
        return list(_DEFAULT_MODES)
    if isinstance(raw_crafts, str):
        raw_crafts = [raw_crafts]
    if not isinstance(raw_crafts, list):
        return list(_DEFAULT_MODES)
    valid = set(_registry_modes())
    clean = [str(m).strip().lower() for m in raw_crafts
             if str(m).strip().lower() in valid]
    if not clean:
        # Explicit [] (or only unknown names) = assist (answer directly).
        return ["assist"] if isinstance(entry.get("crafts"), list) else list(_DEFAULT_MODES)
    return clean


def crafts_for_worker(worker_name: str,
                      workers_file: Any = None) -> List[Dict[str, str]]:
    """[{name, description}] for a worker (crafts allowlist + role/modes applied).

    Reads workers.json raw (never `from nak.worker import ...` — same
    boundary rule as nak.decision). A worker entry with a non-empty "crafts"
    list only sees those crafts; missing/empty = all crafts. Unknown
    worker -> []. Never raises.
    """
    name = str(worker_name or "").strip()
    if not name:
        return []
    try:
        cand = _workers_path(workers_file)
        raw = json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    role = ""
    modes: List[str] = []
    allowed: Optional[List[str]] = None
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict) and str(e.get("name", "")).strip() == name:
                ident = e.get("identity") or {}
                role = str(ident.get("role", "")).strip() if isinstance(ident, dict) else ""
                modes = _worker_modes(e)
                raw_crafts = e.get("crafts")
                if isinstance(raw_crafts, list):
                    allowed = [str(c or "").strip().lower()
                               for c in raw_crafts if str(c or "").strip()]
                break
    if not role:
        # Unknown worker: still offer crafts with no role allowlist.
        pass
    out: List[Dict[str, str]] = []
    try:
        for craft in list_crafts(worker=name or None, role=role or None,
                                 worker_modes=modes or None):
            if allowed is not None and craft["name"].strip().lower() not in allowed:
                continue
            out.append({"name": craft["name"],
                        "description": craft["description"]})
    except Exception:
        return []
    return out
