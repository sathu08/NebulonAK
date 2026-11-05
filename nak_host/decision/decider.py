"""Task decider — goal + observation -> {worker, tool, arguments}.

Two paths (same return shape):
  llm=True  -> injected brain.chat() to NebulonMD (no local LLM, no keys here).
  llm=False -> HTTP POST to workers.json decision.endpoint (laya System-1
               scorer, NOT an LLM — typed choice/score/noul answers).
               HTTP failure -> deterministic fallback (never raises).

BOUNDARY (strict): this module NEVER imports nak.worker / nak.brain /
nak.harness, and NEVER imports any local decision lib. Workers/tools/brain
arrive injected (params) or via a caller-supplied loader callable, or are
read as raw JSON from disk. The decision endpoint comes from the
workers.json decision block (or Settings defaults for new workers).
The ONLY place that imports all three sides together is nak/wiring.py
(the composition root). Keep it that way.

Workers: explicit `workers=[...]` wins; else `workers_loader()`; else
raw workers.json read; else ["vizhi"]. Same pattern for tools/decision.
Per-worker `decision` block in workers.json drives endpoint + questions:
  {"enabled": true, "model": "laya",
   "endpoint": "http://localhost:8000/api/NebulonAgentKit/laya", "timeout": 10.0,
   "questions": [{"name": "priority", "type": "score"},
                 {"name": "risk", "type": "score"},
                 {"name": "needs_clarification", "type": "boolean"}]}

Return (never raises — endpoint/LLM failure falls back to first worker/tool):
  {"ok": True, "worker": "vizhi", "tool": "file",
   "arguments": {"action": "read", "path": "notes.txt"}, "reason": ...,
   "llm": True/False, "model": "laya"|None,
   "decision": {"priority": ..., "risk": ..., "needs_clarification": ...},
   "goal": ...}
"""

from __future__ import annotations

import json
import re
import threading
import time

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from nak.utils.constant import (
    DECISION_TIMEOUT as _DECISION_TIMEOUT,
    DECISION_TIMEOUT_FLOOR as _DECISION_TIMEOUT_FLOOR,
    DEFAULT_DECISION_QUESTIONS as _DEFAULT_DECISION_QUESTIONS,
    DEFAULT_WORKERS_JSON as _DEFAULT_WORKERS_JSON,
    ENDPOINT_LIVE_TTL as _ENDPOINT_LIVE_TTL,
    ENDPOINT_PROBE_TIMEOUT as _ENDPOINT_PROBE_TIMEOUT,
    ENDPOINT_PROBE_TIMEOUT_FLOOR as _ENDPOINT_PROBE_TIMEOUT_FLOOR,
    FALLBACK_TOOLS as _FALLBACK_TOOLS,
    FALLBACK_WORKERS as _FALLBACK_WORKERS,
    TRIAGE_CACHE_MAX as _TRIAGE_CACHE_MAX,
    TRIAGE_CACHE_TTL as _TRIAGE_CACHE_TTL,
)

# --- Keep-alive HTTP session PER THREAD -------------------------------- #
# requests.Session is not documented as thread-safe. Parallel decision
# batches therefore get one session per thread (still pooled per thread,
# never shared across workers/decisions running concurrently).
_TLS = threading.local()


def _http() -> Any:
    """Thread-local requests.Session (keep-alive). Falls back to requests module."""
    sess = getattr(_TLS, "session", None)
    if sess is None:
        try:
            import requests

            sess = requests.Session()
            _TLS.session = sess
        except Exception:
            sess = None
    if sess is None:
        import requests as _rq  # last resort: plain module (no pooling)

        return _rq
    return sess

# --- Triage result cache (per-goal; never raises) ---------------------- #
# Repeat decisions (retries, multi-step plans re-asking the same goal)
# skip inference entirely. Key includes goal + observation + workers +
# tools + path + endpoint, so changed context never gets a stale answer.
# Only ok-results cached; errors/fallbacks always recompute. TTL from constant.py.
_TRIAGE_CACHE: Dict[str, tuple] = {}
_TRIAGE_LOCK = threading.Lock()


def _triage_key(goal: str, observation: Any,
                workers: List[str], tools: List[str],
                llm: bool, endpoint: Any,
                decision_cfg: Any = None) -> str:
    import hashlib

    try:
        obs = observation if isinstance(observation, str) else json.dumps(
            observation, sort_keys=True, default=str)
    except Exception:
        obs = str(observation)
    cfg = decision_cfg if isinstance(decision_cfg, dict) else {}
    try:
        cfg_key = json.dumps(
            {"model": cfg.get("model"), "endpoint": cfg.get("endpoint"),
             "questions": cfg.get("questions")},
            sort_keys=True, default=str)
    except Exception:
        cfg_key = str(cfg)[:1000]
    raw = json.dumps({"g": str(goal or ""), "o": str(obs)[:2000],
                      "w": list(workers or []), "t": list(tools or []),
                      "l": bool(llm), "e": str(endpoint or ""),
                      "d": cfg_key},
                     sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _triage_cached(key: str) -> Optional[Dict[str, Any]]:
    try:
        with _TRIAGE_LOCK:
            hit = _TRIAGE_CACHE.get(key)
            if hit is None:
                return None
            result, stamp = hit
            if time.monotonic() - stamp >= _TRIAGE_CACHE_TTL:
                _TRIAGE_CACHE.pop(key, None)
                return None
            import copy as _copy

            return _copy.deepcopy(result)
    except Exception:
        return None


def _triage_store(key: str, result: Dict[str, Any]) -> None:
    try:
        if not isinstance(result, dict) or not result.get("ok"):
            return  # errors recompute; only solid answers cache
        if result.get("endpoint_fallback") or result.get("llm_fallback"):
            return  # degraded answers recompute once backends recover
        import copy as _copy

        with _TRIAGE_LOCK:
            if len(_TRIAGE_CACHE) >= _TRIAGE_CACHE_MAX:
                _TRIAGE_CACHE.pop(next(iter(_TRIAGE_CACHE)), None)
            _TRIAGE_CACHE[key] = (_copy.deepcopy(result), time.monotonic())
    except Exception:
        pass


# --- Endpoint liveness (cached probe; never raises) ------------------ #
_ENDPOINT_LIVE_CACHE: Dict[str, tuple] = {}


def endpoint_live(url: Any, timeout: float = _ENDPOINT_PROBE_TIMEOUT,
                  ttl: float = _ENDPOINT_LIVE_TTL) -> bool:
    """True when the decision endpoint answers HTTP (any status — even 400).

    Probe is POST {} (rejected by validation before inference, so it costs
    almost nothing server-side). Connection refused / timeout / DNS failure
    -> False. Result cached `ttl` seconds so every chat() doesn't pay a
    probe. Never raises — unconfigured/bad URL is simply not live.
    """
    u = str(url or "").strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    try:
        now = time.monotonic()
        hit = _ENDPOINT_LIVE_CACHE.get(u)
        if hit is not None and now - hit[1] < float(ttl):
            return bool(hit[0])
    except Exception:
        pass
    live = False
    try:
        _http().post(u, json={}, timeout=max(_ENDPOINT_PROBE_TIMEOUT_FLOOR, float(timeout)))
        live = True  # any HTTP response (400/422 included) = server alive
    except Exception:
        live = False
    try:
        _ENDPOINT_LIVE_CACHE[u] = (live, time.monotonic())
    except Exception:
        pass
    return live


def _down_notice(url: str) -> str:
    """User-facing line when decision.enabled but the URL is not active."""
    return (f"decision endpoint {url} is not active — "
            f"using LLM (Brain) to decide instead.")


def _read_workers_json(workers_file: Any = None) -> List[Dict[str, Any]]:
    """Raw JSON read — filesystem only, NEVER `from nak.worker import ...`."""
    cand = Path(workers_file).expanduser() if workers_file else _DEFAULT_WORKERS_JSON
    try:
        raw = json.loads(cand.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return raw if isinstance(raw, list) else []


def _all_workers(
    workers: Optional[List[str]] = None,
    workers_file: Any = None,
    workers_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> List[str]:
    """Resolve worker names. No nak.worker import — injected or raw JSON."""
    if workers:
        ws = [str(w).strip() for w in workers if str(w).strip()]
        if ws:
            return ws
    if workers_loader is not None:
        try:
            entries = workers_loader()
            names = [str(e.get("name", "")).strip() for e in entries or [] if isinstance(e, dict)]
            names = [n for n in names if n]
            if names:
                return names
        except Exception:
            pass
    try:
        raw = _read_workers_json(workers_file)
        names = [str(e.get("name", "")).strip() for e in raw if isinstance(e, dict)]
        names = [n for n in names if n]
        if names:
            return names
    except Exception:
        pass
    return list(_FALLBACK_WORKERS)


def _all_tools(
    tools: Optional[List[str]] = None,
    engine: Any = None,
    tools_loader: Optional[Callable[[], List[str]]] = None,
) -> List[str]:
    """Resolve tool names. No nak.harness import — injected engine/loader only."""
    if tools:
        ts = [str(t).strip() for t in tools if str(t).strip()]
        if ts:
            return ts
    if engine is not None:
        try:
            names = list(engine.list())
            names = [str(t).strip() for t in names if str(t).strip()]
            if names:
                return names
        except Exception:
            pass
    if tools_loader is not None:
        try:
            names = list(tools_loader() or [])
            names = [str(t).strip() for t in names if str(t).strip()]
            if names:
                return names
        except Exception:
            pass
    return list(_FALLBACK_TOOLS)


def _is_decision_input(obs: Any) -> bool:
    return isinstance(obs, dict) and any(
        k in obs for k in ("situation", "user_intent", "worker_role", "history")
    )


def _state_from_decision_input(d: Dict[str, Any], goal: str) -> str:
    parts = [f"Goal: {goal}"]
    if str(d.get("situation", "")).strip():
        parts.append(f"Situation: {str(d['situation'])[:800]}")
    if str(d.get("user_intent", "")).strip():
        parts.append(f"Intent: {str(d['user_intent'])[:800]}")
    if str(d.get("worker_role", "")).strip():
        parts.append(f"Role: {str(d['worker_role'])[:500]}")
    if str(d.get("history", "")).strip():
        parts.append(f"History: {str(d['history'])[:800]}")
    acts = d.get("available_actions") or []
    if isinstance(acts, list) and acts:
        parts.append(f"Actions: {', '.join(map(str, acts))[:300]}")
    return "\n".join(parts)[:2000]


def _obs_text(observation: Any) -> str:
    if observation is None:
        return ""
    if isinstance(observation, str):
        return observation[:2000]
    if isinstance(observation, dict):
        if _is_decision_input(observation):
            return _state_from_decision_input(observation, str(observation.get("user_intent", "")))
        for k in ("content", "text", "summary"):
            v = observation.get(k)
            if isinstance(v, str) and v.strip():
                return v[:2000]
        try:
            return json.dumps(observation, ensure_ascii=False, default=str)[:2000]
        except Exception:
            return str(observation)[:2000]
    return str(observation)[:2000]


# ------------------------------------------------------------------ #
# Decision via HTTP endpoint (llm=False). URL comes from workers.json
# entry["decision"]["endpoint"], e.g.:
#   {"enabled": true, "model": "laya",
#    "endpoint": "http://localhost:8000/api/NebulonAgentKit/laya",
#    "questions": [{"name": "priority", "type": "score"}, ...]}
# Explicit `endpoint` arg wins over workers.json. No TCP probe, no
# in-process scorer here (BOUNDARY: never import nak.api / nak.worker).
# Failure -> deterministic fallback (never raises).
# Defaults (_DEFAULT_DECISION_QUESTIONS etc.) live in nak/utils/constant.py.
# ------------------------------------------------------------------ #


def _load_decision(
    workers_file: Any = None,
    workers_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """First enabled decision block, else defaults. No nak.worker import."""
    entries: List[Any] = []
    if workers_loader is not None:
        try:
            entries = list(workers_loader() or [])
        except Exception:
            entries = []
    if not entries:
        entries = _read_workers_json(workers_file)
    for e in entries:
        if not isinstance(e, dict):
            continue
        dec = e.get("decision")
        if isinstance(dec, dict) and dec.get("enabled", True):
            return dec
    return {"enabled": True, "model": "laya", "endpoint": "",
            "timeout": _DECISION_TIMEOUT,
            "questions": list(_DEFAULT_DECISION_QUESTIONS)}


def _build_laya_questions(
    workers: List[str], tools: List[str], decision: Dict[str, Any]
) -> Dict[str, Any]:
    """Worker/tool choice + your priority/risk/needs_clarification scores."""
    qs: Dict[str, Any] = {
        "worker": {
            "type": "choice",
            "instructions": "Which worker should handle this goal?",
            "criteria": {w: f"worker named {w}" for w in workers},
        },
        "tool": {
            "type": "choice",
            "instructions": "Which tool should handle this goal?",
            "criteria": {t: f"tool named {t}" for t in tools},
        },
    }
    wanted = decision.get("questions") if isinstance(decision, dict) else None
    if not isinstance(wanted, list) or not wanted:
        wanted = _DEFAULT_DECISION_QUESTIONS
    for q in wanted:
        if not isinstance(q, dict):
            continue
        name = str(q.get("name", "")).strip() or "q"
        qtype = str(q.get("type", "")).strip().lower()
        if name in qs:
            continue
        if qtype == "score" and "priority" in name.lower():
            qs[name] = {
                "type": "score",
                "instructions": str(q.get("instructions") or "How urgent/important is this goal?"),
                "criteria": list(q.get("criteria") or ["low", "medium", "high", "critical"]),
            }
        elif qtype == "score":
            qs[name] = {
                "type": "score",
                "instructions": str(q.get("instructions") or f"How risky is this goal ({name})?"),
                "criteria": list(q.get("criteria") or ["safe", "caution", "risky", "dangerous"]),
            }
        elif qtype in ("boolean", "noul"):
            # laya calls yes/no "noul" — "boolean" in workers.json maps here.
            qs[name] = {
                "type": "noul",
                "instructions": str(
                    q.get("instructions") or "Does this goal need clarification before acting?"
                ),
            }
    return qs


def _pick_file_arg(goal: str, observation: Any, tool: str) -> Dict[str, Any]:
    """Still needed: laya picks worker/tool, but filename comes from text."""
    if tool != "file":
        return {}
    m = re.search(r"'([^']+\.txt)'|\"([^\"]+\.txt)\"|(\S+\.txt)", goal)
    fname = next((x for x in (m.groups() if m else []) if x), None) if m else None
    if not fname and isinstance(observation, dict):
        p = str(observation.get("path", "") or "").strip()
        if p and not p.startswith("/"):
            fname = p.split("/")[-1]
    return {"action": "read", "path": (fname or "notes.txt").strip()}


def _fallback_pick(ws: List[str], ts: List[str], goal: str,
                   observation: Any, note: str,
                   notice: str = "", endpoint_live_flag: Optional[bool] = None) -> Dict[str, Any]:
    """Never-crash fallback when the decision endpoint is unreachable.

    Fail-closed: ok=False + fallback=True so callers never mistake the
    first-worker/tool guess for a real decision. worker/tool/arguments
    are still filled (best-effort guess) + decision={} (policy treats
    empty triage as escalate).
    """
    worker = ws[0] if ws else "vizhi"
    tool = ts[0] if ts else "file"
    out: Dict[str, Any] = {"ok": False, "fallback": True,
            "worker": worker, "tool": tool,
            "arguments": _pick_file_arg(goal, observation, tool),
            "reason": f"decision endpoint unavailable, fallback -> {worker}/{tool} ({note[:100]})",
            "llm": False, "model": "endpoint", "endpoint_fallback": True,
            "decision": {}, "goal": goal}
    if notice:
        out["notice"] = notice[:300]
    if endpoint_live_flag is not None:
        out["endpoint_live"] = bool(endpoint_live_flag)
    return out


def local_rule(
    goal: str,
    workers: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    observation: Any = None,
    engine: Any = None,
    workers_file: Any = None,
    workers_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tools_loader: Optional[Callable[[], List[str]]] = None,
    endpoint: Optional[str] = None,
    timeout: Optional[float] = None,
    auth_token: Optional[str] = None,
    decision_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Decision via HTTP endpoint (llm=False). Workers/tools injected.

    URL source (first wins):
      1. explicit `endpoint` arg
      2. `decision_cfg` (the calling worker's block)
      3. first enabled entry in workers.json (via _load_decision)
    No probe, no in-process scorer — one POST {state, questions} -> answers.
    HTTP failure / missing URL -> deterministic first-worker/tool fallback
    (never raises; fallback returns ok=False + fallback=True).
    endpoint/timeout/auth_token override the decision block (None = entry value).
    decision_cfg=None falls back to the first enabled block (backward compat);
    per-worker callers pass their own block so transports never leak across
    workers.
    BOUNDARY: no nak.worker / nak.brain / nak.harness / nak.api imports here.
    """
    g = str(goal or "").strip()
    if not g:
        return {"ok": False, "error": "missing goal", "llm": False}
    ws = _all_workers(workers, workers_file, workers_loader)
    ts = _all_tools(tools, engine, tools_loader)
    if not ws or not ts:
        return {"ok": False, "error": "no workers/tools", "llm": False}

    cfg = (decision_cfg if isinstance(decision_cfg, dict)
           else _load_decision(workers_file, workers_loader))
    # decision_input (from GenericWorker) already carries situation/history/
    # intent/role — format it richly; plain observation stays Goal+Observation.
    if _is_decision_input(observation):
        state = _state_from_decision_input(observation, g)
    else:
        state = f"Goal: {g}\nObservation: {_obs_text(observation)[:1200]}"
    questions = _build_laya_questions(ws, ts, cfg)

    # URL from the calling worker's block (or explicit override) — nothing else.
    endpoint_url = (str(endpoint or "").strip()
                    or str(cfg.get("endpoint", "") or "").strip())
    if not (endpoint_url.startswith("http://")
            or endpoint_url.startswith("https://")):
        return _fallback_pick(ws, ts, g, observation,
                              f"missing/invalid decision.endpoint in workers.json: {endpoint_url!r}"[:150],
                              notice="no decision endpoint configured — using deterministic fallback.",
                              endpoint_live_flag=False)
    try:
        to = float(timeout) if timeout is not None else float(cfg.get("timeout", _DECISION_TIMEOUT))
        to = max(_DECISION_TIMEOUT_FLOOR, to)
    except (TypeError, ValueError):
        to = _DECISION_TIMEOUT
    token = (str(auth_token).strip() if auth_token is not None
             else str(cfg.get("auth_token", "") or "").strip())

    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = _http().post(endpoint_url,
                            json={"state": state, "questions": questions},
                            headers=headers, timeout=to)
        try:
            body = resp.json()
        except ValueError as exc:
            raise RuntimeError(f"endpoint non-JSON ({resp.status_code})") from exc
        if resp.status_code >= 400:
            msg = body.get("message") if isinstance(body, dict) else None
            raise RuntimeError(
                f"endpoint HTTP {resp.status_code}: {str(msg or resp.text)[:150]}"
            )
        # Accept {"answers"}, {"data": {"answers"}}, or raw scorer dict.
        if isinstance(body, dict) and isinstance(body.get("answers"), dict):
            answers = body["answers"]
        elif (isinstance(body, dict) and isinstance(body.get("data"), dict)
                and isinstance(body["data"].get("answers"), dict)):
            answers = body["data"]["answers"]
        elif isinstance(body, dict):
            answers = body
        else:
            raise RuntimeError("endpoint returned non-object")
    except Exception as exc:  # HTTP failed -> fallback
        return _fallback_pick(ws, ts, g, observation, str(exc),
                              notice=_down_notice(endpoint_url),
                              endpoint_live_flag=False)

    try:
        w_ans = answers.get("worker", {}) if isinstance(answers, dict) else {}
        t_ans = answers.get("tool", {}) if isinstance(answers, dict) else {}
        worker = str(w_ans.get("choice", "")).strip() if isinstance(w_ans, dict) else ""
        tool = str(t_ans.get("choice", "")).strip() if isinstance(t_ans, dict) else ""
        if worker not in ws:
            worker = ws[0]
        if tool not in ts:
            tool = ts[0]

        triage: Dict[str, Any] = {}
        for key in ("priority", "risk", "needs_clarification"):
            a = answers.get(key) if isinstance(answers, dict) else None
            if not isinstance(a, dict):
                continue
            if "score" in a:
                triage[key] = a["score"]
            elif "noul" in a:
                triage[key] = bool(a["noul"] > 0.5) if isinstance(a["noul"], (int, float)) else a["noul"]
            if isinstance(a.get("answer_confidence"), (int, float)):
                triage[f"{key}_confidence"] = round(float(a["answer_confidence"]), 3)

        model = str(cfg.get("model", "endpoint") or "endpoint")[:50]
        return {"ok": True, "worker": worker, "tool": tool,
                "arguments": _pick_file_arg(g, observation, tool),
                "reason": f"endpoint(http): {worker}/{tool} triage={triage}",
                "llm": False, "model": model, "decision": triage,
                "endpoint": endpoint_url, "endpoint_live": True,
                "via": "http", "goal": g}
    except Exception as exc:
        return _fallback_pick(ws, ts, g, observation, str(exc),
                              notice=_down_notice(endpoint_url),
                              endpoint_live_flag=False)


def _decide_prompt_dirs(workers_file: Any = None) -> List[Path]:
    """Where prompts/decide.md may live (first hit wins). Filesystem only."""
    dirs: List[Path] = []
    if str(workers_file or "").strip():
        try:
            wf_parent = Path(workers_file).expanduser().parent
            dirs.append(wf_parent / "prompts")
            # workers.json moved to nak/manifests/; prompts stay in nak/worker/prompts/
            dirs.append(wf_parent.parent / "worker" / "prompts")
        except Exception:
            pass
    dirs.append(_DEFAULT_WORKERS_JSON.parent / "prompts")
    dirs.append(Path(__file__).resolve().parent.parent / "worker" / "prompts")
    return dirs


def _render_decide_template(template: str, goal: str, observation: str,
                            workers: List[str], tools: List[str]) -> str:
    """Fill {{goal}}/{{workers}}/{{tools}}/{{observation}}. No new deps."""
    out = str(template)
    out = out.replace("{{goal}}", str(goal or "").strip())
    out = out.replace("{{workers}}", ", ".join(workers))
    out = out.replace("{{tools}}", ", ".join(tools))
    out = out.replace("{{observation}}", str(observation or "")[:1200])
    return out.strip()


def _llm_prompt(goal: str, observation: Any, workers: List[str], tools: List[str],
               workers_file: Any = None) -> str:
    """Decision prompt for llm=True (shared by any LLM provider).

    Loads prompts/decide.md next to workers.json (same {{...}} convention
    as nak/worker/prompts/*.md) so every LLM runs the same process;
    missing/unreadable file -> built-in template below.
    BOUNDARY: raw file read only — never `from nak.worker import ...`.
    """
    obs = _obs_text(observation)
    for d in _decide_prompt_dirs(workers_file):
        try:
            text = (d / "decide.md").read_text(encoding="utf-8")
        except OSError:
            continue
        if text.strip():
            return _render_decide_template(text, str(goal or ""), obs,
                                           workers, tools)
    return (
        f"Goal: {str(goal or '').strip()}\n"
        f"Workers: {', '.join(workers)}\n"
        f"Tools: {', '.join(tools)}\n"
        f"Observation: {obs[:1200]}\n\n"
        "Reply JSON only: {\"worker\": \"<one of Workers>\", "
        "\"tool\": \"<one of Tools>\", \"arguments\": {...}, "
        "\"priority\": <0-3>, \"risk\": <0-3>, "
        "\"needs_clarification\": <true|false>, \"confidence\": <0-1>, "
        "\"reason\": \"<short>\"}"
    )


def _num(value: Any) -> Optional[float]:
    """Coerce to float; None for booleans/strings/non-numbers (LLM output)."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_llm_answer(ans: str, workers: List[str], tools: List[str]) -> Dict[str, Any]:
    """Parse the router JSON, including the policy triage fields.

    Extras are optional and clamped to the documented ranges: priority/risk
    0..3, confidence 0..1. Invalid shapes are dropped, never raised.
    """
    t = str(ans or "").strip()
    try:
        start, end = t.index("{"), t.rindex("}") + 1
        data = json.loads(t[start:end])
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, Any] = {}
    if str(data.get("worker", "")).strip() in workers:
        out["worker"] = str(data["worker"]).strip()
    if str(data.get("tool", "")).strip() in tools:
        out["tool"] = str(data["tool"]).strip()
    args = data.get("arguments")
    if isinstance(args, dict):
        out["arguments"] = args
    if isinstance(data.get("reason"), str) and data["reason"].strip():
        out["reason"] = data["reason"].strip()[:300]
    for key in ("priority", "risk"):
        val = _num(data.get(key))
        if val is not None:
            out[key] = max(0.0, min(3.0, val))
    nc = data.get("needs_clarification")
    if isinstance(nc, bool):
        out["needs_clarification"] = nc
    elif isinstance(nc, str) and nc.strip().lower() in ("true", "yes", "1"):
        out["needs_clarification"] = True
    elif isinstance(nc, str) and nc.strip().lower() in ("false", "no", "0"):
        out["needs_clarification"] = False
    conf = _num(data.get("confidence"))
    if conf is not None:
        conf = max(0.0, min(1.0, conf))
        out["worker_confidence"] = conf
        out["tool_confidence"] = conf
    return out


def decide_task(
    goal: str,
    observation: Any = None,
    workers: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    llm: bool = True,
    brain: Any = None,
    engine: Any = None,
    workers_file: Any = None,
    workers_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tools_loader: Optional[Callable[[], List[str]]] = None,
    brain_factory: Optional[Callable[[], Any]] = None,
    endpoint: Optional[str] = None,
    timeout: Optional[float] = None,
    auth_token: Optional[str] = None,
    decision_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Goal -> worker + tool + arguments.

    BOUNDARY: brain/workers/tools/endpoint are INJECTED. This module never
    does `from nak.worker / nak.brain / nak.harness import ...` and never
    imports any local decision lib — see nak/wiring.py.

    Args:
      goal: what the user needs todo (chat text, required).
      observation: dict/str context (decision_input or plain) or None.
      workers: allowed worker names (None = resolve via loader/raw JSON).
      tools: allowed tool names (None = resolve via engine/loader).
      llm: True -> injected brain.chat() to Mind (prompt: prompts/decide.md
        next to workers.json, builtin fallback); False -> endpoint local_rule().
      brain: injected Brain client (duck-typed: .chat/.require_mind).
      engine: injected ToolEngine (duck-typed: .list()).
      workers_file: raw JSON path fallback (no module import).
      workers_loader/tools_loader: callables for custom registries.
      brain_factory: optional () -> brain, used only when brain is None.
      endpoint/timeout/auth_token: decision endpoint override
        (None = decision block / Settings default).
      decision_cfg: the calling worker's raw decision block. None = first
        enabled block in workers.json (backward compatible); explicit blocks
        keep per-worker endpoint/model/questions separate.
    """
    g = str(goal or "").strip()
    if not g:
        return {"ok": False, "error": "missing goal", "llm": bool(llm)}

    ws = _all_workers(workers, workers_file, workers_loader)
    ts = _all_tools(tools, engine, tools_loader)

    # Per-goal cache: identical repeats skip inference entirely.
    ckey = _triage_key(g, observation, ws, ts, llm, endpoint, decision_cfg)
    hit = _triage_cached(ckey)
    if hit is not None:
        return hit

    if not llm:
        res = local_rule(g, ws, ts, observation, engine, workers_file,
                         workers_loader, tools_loader, endpoint, timeout,
                         auth_token, decision_cfg=decision_cfg)
        _triage_store(ckey, res)
        return res

    # llm=True — via INJECTED brain only (Golden Rule: no local LLM, no keys).
    if brain is None and brain_factory is not None:
        try:
            brain = brain_factory()
        except Exception as exc:  # factory failed -> endpoint fallback, never crash
            fb = local_rule(g, ws, ts, observation, engine, workers_file,
                            workers_loader, tools_loader, endpoint, timeout,
                            auth_token, decision_cfg=decision_cfg)
            fb["reason"] += f" (brain factory: {str(exc)[:100]})"
            fb["llm_fallback"] = True
            return fb
    if brain is None:  # no direct `from nak.brain import Brain` here by design
        fb = local_rule(g, ws, ts, observation, engine, workers_file,
                        workers_loader, tools_loader, endpoint, timeout,
                        auth_token, decision_cfg=decision_cfg)
        fb["reason"] += " (no brain injected — wire via nak/wiring.py)"
        fb["llm_fallback"] = True
        return fb

    preflight = getattr(brain, "require_mind", None)
    try:
        if callable(preflight):
            preflight()
        res = brain.chat(_llm_prompt(g, observation, ws, ts, workers_file))
        parsed = _parse_llm_answer(str(res.get("answer", "")), ws, ts)
    except Exception as exc:  # Mind down / bad JSON -> endpoint fallback
        fb = local_rule(g, ws, ts, observation, engine, workers_file,
                        workers_loader, tools_loader, endpoint, timeout,
                        auth_token, decision_cfg=decision_cfg)
        fb["reason"] += f" (llm fallback: {str(exc)[:100]})"
        fb["llm_fallback"] = True
        return fb

    worker = parsed.get("worker", ws[0] if ws else "vizhi")
    tool = parsed.get("tool", ts[0] if ts else "file")
    out: Dict[str, Any] = {"ok": True, "worker": worker, "tool": tool,
            "arguments": parsed.get("arguments", {} if tool != "file" else {"action": "read", "path": "notes.txt"}),
            "decision": parsed,
            "reason": parsed.get("reason", "llm decision"),
            "llm": True, "goal": g}
    # decision.enabled=true guard: report whether the scoring URL is live.
    # Down -> user notice that the LLM (not the endpoint) decided this call.
    try:
        cfg = (decision_cfg if isinstance(decision_cfg, dict)
               else _load_decision(workers_file, workers_loader))
        ep = (str(endpoint or "").strip()
              or str(cfg.get("endpoint", "") or "").strip())
        if ep.startswith("http://") or ep.startswith("https://"):
            live = endpoint_live(ep)
            out["endpoint"] = ep
            out["endpoint_live"] = live
            if not live:
                out["notice"] = _down_notice(ep)
    except Exception:
        pass
    _triage_store(ckey, out)
    return out
