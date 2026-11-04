"""Composition root — the ONLY place that wires brain + decision + worker.

BOUNDARY:
  * nak/decision NEVER imports nak.worker / nak.brain / nak.harness.
  * nak/worker NEVER imports nak.brain / nak.decision (runtime or lazy).
  * THIS module is the single exception: it imports all three sides and
    connects them via constructor injection (duck-typed, no subclassing).

Use:
    from nak.wiring import make_worker, make_brain, make_harness, decide, run_craft
    w = make_worker("vizhi")            # brain + decider + policy injected
    w.chat("hello")                     # triage auto-runs (decision.enabled)
    h = make_harness(worker="vizhi")    # sandboxed file/directory/terminal tools
    h.run("file", {"action": "read", "path": "notes.md"})
    decide("read notes.txt", llm=False) # decider with registry loaders
    run_craft("extract invoice.pdf", w, h, approver=lambda info: True)
    run_craft("ship login", harness=h, team="core")  # leader plans
    """

from __future__ import annotations

import re

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ASSIGN_TAG = re.compile(r"\[\s*worker\s*=\s*([^\]\s]+)\s*\]", re.IGNORECASE)
_STEP_LINE = re.compile(r"^(\s*\d+[.)]\s+)(\S.*)$")


def make_brain(**kwargs: Any) -> Any:
    """Create a nak.brain.Brain (all LLM lives in NebulonMD)."""
    from nak.brain import Brain

    return Brain(**kwargs)


def make_harness(worker: Optional[str] = None,
                 user: Any = None,
                 workspace_root: Any = None,
                 registry: Any = None,
                 permissions: Any = None,
                 approver: Any = None,
                 workers_file: Any = None,
                 extra_tools: Any = None,
                 ml: Any = None,
                 mcp: Any = None) -> Any:
    """Create an ExecutionHarness bound to a worker (sandboxed tool runner).

    Thin factory for symmetry with make_brain(): session dir is created
    on construction, permissions enforced inspect-first. Pass
    approver=... (e.g. CLI y/n) or 'ask' verdicts deny with need_approval.

    extra_tools/ml/mcp are passing-only placeholders (stored on the
    engine, never executed here): nak/harness/ml/ and nak/harness/mcp/
    are empty reserves for future helpers.
    """
    from nak.harness.engine import ExecutionHarness

    return ExecutionHarness(registry=registry, workspace_root=workspace_root,
                            user=user, permissions=permissions, worker=worker,
                            approver=approver, workers_file=workers_file,
                            extra_tools=extra_tools, ml=ml, mcp=mcp)


def new_session(user: Any = None, label: Any = None, when: Any = None) -> str:
    """Mint the ONE shared session key for a team of workers.

    Hand the returned id to every participating worker's
    ``chat(..., session_id=key)`` so they read and write the SAME transcript
    doc — memory is shared worker-to-worker, never exposed over the API.
    ``user``/``label`` only seed the filename-safe prefix; ``when`` exists
    for deterministic tests. Collision-safe (see make_session_id).
    """
    from nak.harness.engine.sandbox import make_session_id

    who = str(user or label or "team").strip() or "team"
    return make_session_id(who, when=when)


def _workers_loader(workers_file: Any = None) -> Callable[[], List[Dict[str, Any]]]:
    def _load() -> List[Dict[str, Any]]:
        from nak.worker import list_workers

        return list(list_workers(workers_file) if workers_file else list_workers())

    return _load


def _tools_loader(engine: Any = None, registry: Any = None) -> Callable[[], List[str]]:
    def _load() -> List[str]:
        if engine is not None:
            try:
                return list(engine.list())
            except Exception:
                pass
        # Registry names only — never build a ToolEngine here (its
        # constructor creates a session dir on disk as a side effect).
        try:
            from nak.harness.engine.registry import load_registry

            names = [str(e.get("name", "")).strip()
                     for e in load_registry(registry) if isinstance(e, dict)]
            names = [n for n in names if n]
            if names:
                return names
        except Exception:
            pass
        return ["file"]

    return _load


def _decision_config(worker: Any, decision: Any, craft: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve a craft decision block: explicit dict > craft name > sole craft."""
    from nak.harness.craft.craft import load_craft, normalize_decision

    if isinstance(decision, dict):
        return normalize_decision(decision, "run_craft")
    if decision is False or decision == "off":
        return None
    if craft:
        return load_craft(str(craft))["decision"]
    if decision is True or decision == "auto":
        names = list((getattr(worker, "entry", {}) or {}).get("crafts") or [])
        if len(names) == 1:
            cfg = load_craft(str(names[0]))["decision"]
            return cfg if cfg.get("enabled") else None
    return None


def _set_worker_tag(line: str, name: str) -> str:
    body = _ASSIGN_TAG.sub("", str(line or "")).rstrip()
    return f"{body} [worker={str(name).strip()}]" if str(name).strip() else body


def _mark_step(line: str, label: str) -> str:
    body = _ASSIGN_TAG.sub("", str(line or "")).rstrip()
    return f"{body} [{label}]" if label and label not in body else body


def _apply_decisions(goal: str, answer: str, cfg: Dict[str, Any], worker: Any,
                     candidates: List[str], session_id: Optional[str],
                     force_parallel: Optional[bool] = None) -> tuple:
    """Route plan steps through worker.route() and rewrite [worker=] tags.

    tags_win: existing tags are untouched; decisions fill untagged steps.
    decision_only: decisions override tags (a failed call falls back to the
    existing tag rather than losing the assignment). One failed/low-confidence
    decision marks only its step and forces human approval; siblings proceed.
    The worker's own decision.parallel/max_parallel are capability defaults;
    an explicit decision_parallel override still wins. LLM mode is always
    serialized. Returns (new_answer, evidence, meta). Never raises for
    decision errors.
    """
    from nak.decision import decide_many

    try:
        if callable(getattr(worker, "decision_enabled", None)) \
                and not worker.decision_enabled():
            return answer, [], {"scope": cfg.get("scope"), "mode": cfg.get("mode"),
                                "status": "disabled (worker decision off)",
                                "items": []}
    except Exception:
        pass
    lines = str(answer or "").splitlines()
    rows: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        m = _STEP_LINE.match(line)
        if not m:
            continue
        body = m.group(2)
        tag = _ASSIGN_TAG.search(body)
        rows.append({"line": idx, "num": m.group(1).strip().rstrip(".)"),
                     "prefix": m.group(1), "body": body,
                     "text": _ASSIGN_TAG.sub("", body).strip(),
                     "tag": tag.group(1).strip() if tag else ""})
    if not rows:
        return answer, [], {"scope": cfg.get("scope"), "mode": cfg.get("mode"),
                            "status": "no numbered steps", "items": []}

    mode = str(cfg.get("mode", "auto")).lower()
    entry_dec = (getattr(worker, "entry", {}) or {}).get("decision")
    endpoint = str((entry_dec or {}).get("endpoint", "") or "").strip() \
        if isinstance(entry_dec, dict) else ""
    use_llm = mode == "llm" or (mode == "auto" and not endpoint)
    # Precedence: explicit call override > worker capability > craft policy.
    parallel = bool(cfg.get("parallel", True))
    if isinstance(entry_dec, dict) and isinstance(entry_dec.get("parallel"), bool):
        parallel = bool(entry_dec["parallel"])
    if force_parallel is not None:
        parallel = bool(force_parallel)
    if use_llm:
        parallel = False  # a shared brain is not safe to drive concurrently
    cap_raw = cfg.get("max_parallel", 1)
    if isinstance(entry_dec, dict) and entry_dec.get("max_parallel") is not None:
        cap_raw = entry_dec["max_parallel"]
    try:
        cap = max(1, min(64, int(cap_raw))) if parallel else 1
    except (TypeError, ValueError):
        cap = 1

    pending = [r for r in rows
               if not (cfg.get("assignment") == "tags_win" and r["tag"])]
    if not pending:
        return answer, [], {"scope": cfg.get("scope"), "mode": mode,
                            "status": "tags only (no decisions needed)", "items": []}
    if not candidates:
        for r in pending:
            lines[r["line"]] = _mark_step(lines[r["line"]], "decision-blocked")
        items = [{"step": r["num"], "worker": "", "source": "decision",
                  "reason": "no candidate workers"} for r in pending]
        return "\n".join(lines), items, {
            "scope": cfg.get("scope"), "mode": mode, "status": "blocked",
            "items": items, "blocked": [r["num"] for r in pending]}

    context = None
    try:
        context = worker.build_decision_input(goal, session_id=session_id)
    except Exception:
        context = None

    if cfg.get("scope") == "once":
        requests = [{"id": "once", "key": "once", "goal": goal}]
    else:
        requests = [{"id": f"step-{i}", "key": f"step:{r['text']}",
                     "goal": f"{goal}\nStep: {r['text']}"}
                    for i, r in enumerate(pending)]

    def _call(request: Dict[str, Any]) -> Dict[str, Any]:
        return worker.route(request["goal"], workers=candidates,
                            context=context, llm=use_llm,
                            session_id=session_id)

    results = decide_many(requests, _call, max_parallel=cap,
                          timeout=cfg.get("timeout"))
    by_id = {str(r.get("id", "")): r for r in results}
    on_error = str(cfg.get("on_error", "block")).lower()
    label = "decision-needs-input" if on_error == "ask" else "decision-blocked"
    items: List[Dict[str, Any]] = []
    blocked: List[str] = []

    for i, row in enumerate(pending):
        res = by_id.get("once" if cfg.get("scope") == "once" else f"step-{i}", {})
        name = str((res or {}).get("worker", "") or "").strip()
        ok = bool((res or {}).get("ok")) and bool(name) and name in candidates
        reason = str((res or {}).get("reason") or (res or {}).get("error") or "").strip()
        if ok:
            lines[row["line"]] = _set_worker_tag(lines[row["line"]], name)
            source = "decision"
        elif row["tag"]:
            lines[row["line"]] = _set_worker_tag(lines[row["line"]], row["tag"])
            name, source = row["tag"], "tag-fallback"
            reason = reason or "decision failed; kept planner tag"
        else:
            lines[row["line"]] = _mark_step(lines[row["line"]], label)
            blocked.append(row["num"])
            name, source = "", "decision"
            reason = reason or "decision failed (no worker chosen)"
        items.append({"step": row["num"], "worker": name, "source": source,
                      "reason": reason,
                      "decision": (res or {}).get("decision")})

    status = ("parallel" if parallel and len(requests) > 1 else "serial")
    status += f", {len(requests)} call(s), {len(blocked)} blocked"
    return "\n".join(lines), items, {
        "scope": cfg.get("scope"), "mode": mode, "status": status,
        "parallel": bool(parallel and len(requests) > 1 and cap > 1),
        "items": items, "blocked": blocked}


def decide(
    goal: str,
    observation: Any = None,
    workers: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    llm: bool = False,
    brain: Any = None,
    engine: Any = None,
    workers_file: Any = None,
    endpoint: Optional[str] = None,
    timeout: Optional[float] = None,
    auth_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Decide via nak.decision with registry loaders + explicit brain.

    No hidden construction: llm=True without a brain falls back to the
    endpoint scorer with a clear reason (never auto-builds a Brain —
    pass brain=make_brain() explicitly when you want LLM routing).

    endpoint/timeout/auth_token override workers.json decision block
    (None = entry value / Settings default).
    """
    from nak.decision import decide_task

    return decide_task(
        goal,
        observation=observation,
        workers=workers,
        tools=tools,
        llm=llm,
        brain=brain,
        engine=engine,
        workers_file=workers_file,
        workers_loader=_workers_loader(workers_file),
        tools_loader=_tools_loader(engine),
        brain_factory=None,
        endpoint=endpoint,
        timeout=timeout,
        auth_token=auth_token,
    )


def make_worker(
    name: str,
    brain: Any = None,
    workers_file: Any = None,
    engine: Any = None,
    tools: Optional[Dict[str, Any]] = None,
    endpoint: Optional[str] = None,
    timeout: Optional[float] = None,
    auth_token: Optional[str] = None,
    crafts: Optional[List[Dict[str, str]]] = None,
) -> Any:
    """Fully wired GenericWorker: brain + decider + policy evaluator injected.

    No hidden construction: brain=None stays None (worker loads; chat()
    raises a clear "no brain injected" error). Pass brain=make_brain()
    explicitly — the worker never builds one itself, so it always runs
    against the actual instances you wired.

    The harness is deliberately NOT a parameter: workers never receive it.
    ``run_craft`` keeps the harness in this composition root and gives the
    worker only a session id plus a plain approver callable.

    crafts: [{name, description}] craft set. None = resolved from the
    entry's own "crafts" list in workers.json (user sets it there ONLY;
    descriptions come from crafts.json/craft.md). All other deps
    (brain/decider/policy/engine) are passing-only — never read from JSON
    here. Rendered into the prompt only — never executed.

    Craft check (fail-loud): explicit crafts must be a list of
    {name, description} dicts with a non-empty name; bad shape raises
    ValueError here instead of silently rendering. Existence against
    crafts.json is validated in nak.harness.craft.load_craft().

    endpoint/timeout/auth_token bake decision-endpoint defaults into this
    worker's decider (None = entry value in workers.json). Explicit per-call
    values in triage()/route() still win over these. Per-call decision_cfg
    (the calling worker's own block) is forwarded unchanged.
    """
    from nak.decision import decide_task
    from nak.decision.policy import evaluate as evaluate_policy
    from nak.worker import GenericWorker

    if crafts is not None:
        if not isinstance(crafts, list):
            raise ValueError("'crafts' must be a list of {name, description} dicts")
        for s in crafts:
            if not isinstance(s, dict) or not str(s.get("name", "")).strip():
                raise ValueError("'crafts' entries must be {name, description} with non-empty name")

    loader = _workers_loader(workers_file)
    tools_load = _tools_loader(engine)
    _outer_engine = engine
    _outer_workers_file = workers_file
    _outer_endpoint = endpoint
    _outer_timeout = timeout
    _outer_auth_token = auth_token

    def _decider(
        goal: str,
        observation: Any = None,
        workers: Optional[List[str]] = None,
        tools: Optional[List[str]] = None,
        llm: bool = False,
        engine: Any = None,
        workers_file: Any = None,
        endpoint: Optional[str] = None,
        timeout: Optional[float] = None,
        auth_token: Optional[str] = None,
        decision_cfg: Optional[Dict[str, Any]] = None,
        **_: Any,
    ) -> Dict[str, Any]:
        # NOTE: outer engine/workers_file fall back when per-call values are None.
        # Baked make_worker() decision defaults lose to explicit per-call args.
        return decide_task(
            goal,
            observation=observation,
            workers=workers,
            tools=tools,
            llm=llm,
            brain=brain,
            engine=engine if engine is not None else _outer_engine,
            workers_file=workers_file or _outer_workers_file,
            workers_loader=loader,
            tools_loader=tools_load,
            brain_factory=None,
            endpoint=endpoint if endpoint is not None else _outer_endpoint,
            timeout=timeout if timeout is not None else _outer_timeout,
            auth_token=auth_token if auth_token is not None else _outer_auth_token,
            decision_cfg=decision_cfg,
        )

    resolved = workers_file or Path(__file__).resolve().parent / "manifests" / "workers.json"
    return GenericWorker(
        name,
        brain=brain,
        workers_file=resolved,
        tools=tools,
        decider=_decider,
        policy_evaluator=evaluate_policy,
        crafts=crafts,
    )


def run_craft(goal: str, worker: Any = None, harness: Any = None,
              approver: Any = None, brain_factory: Any = None,
              factory: Any = None,
              max_workers: Optional[int] = None,
              team: Optional[str] = None,
              parallel: Optional[bool] = None,
              decision: Any = None,
              decision_parallel: Optional[bool] = None,
              craft: Optional[str] = None,
              dispatch: bool = True) -> Dict[str, Any]:
    """Worker crafts a plan; approved assignments run as peer workers.

    Flow: worker.chat(goal, session_id=harness.session_id) (triage+policy
    run inside; the harness session doubles as the Mind session key so
    turns share one session transcript instead of a duplicate daily doc) ->
    extract numbered steps + [worker=name] assignments -> harness.start_plan()
    writes nak_plan.md (pending-approval or auto-approved per crafts
    approval/auto_steps) -> approval -> when approved and the worker has the
    "assign" craft, each named worker runs as a PEER in its own thread with
    its OWN brain (run_workers), all writing to the SAME session doc.

    Planner resolution (section 4):
      * worker given -> use it (existing behaviour);
      * worker omitted + team given -> build team.leader and use it;
      * both omitted -> ValueError "no planner and no team leader".
    The second positional may also be the harness: run_craft(goal, h, team="core").

    worker: GenericWorker (built via make_worker) or None. harness:
    ExecutionHarness with a session dir. approver: fn(info) -> bool (None =
    never auto-ok). brain_factory: callable -> brain, called per dispatched
    worker and per leader-built planner (default make_brain, i.e. one brain per
    thread). factory: callable name -> worker; when given it wins over
    brain_factory. max_workers: thread-pool cap. team: optional team name; None
    uses the worker's single default team. parallel: None (default) uses
    team.parallel (true when absent), True/False forces threaded/serial.
    Dispatch is restricted to
    team members; the planner may be outside the roster unless
    team.planner_must_be_member is true. When team.shared_memory is false the
    peers do not share the planner's session doc. dispatch=False plans only:
    the plan file is still written and approved, but no peers are run.

    decision: optional craft decision block (raw or normalized dict), a craft
    name via craft=..., or True/"auto" (sole craft in the worker's entry).
    Enabled blocks run worker.route() per plan step (or once), fill missing
    [worker=] tags (existing tags win by default), force approval when a step
    cannot be routed, and record a "## Decision" audit section in the plan.
    decision_parallel=False forces serial decisions (LLM mode is always
    serialized; endpoint batches are bounded by max_parallel/timeout).

    Returns {"planned", "path"?, "steps"?, "needs_approval"?, "approved",
    "assigned_workers", "decision"?, "workers"?, "runs"?, "skipped"?,
    "blocked"?, "team", "members", "planner", "planner_outside_team",
    "shared_memory",
    "parallel", "dispatched", "team_memory"?, "answer", "session_id",
    "reason"?}. Never raises on worker/harness/team failure — returns
    {"planned": False, ...} instead (except missing harness/planner args).
    """
    from nak.worker import GenericWorker as _GW
    from nak.worker.engine.team import (
        ensure_planner_allowed,
        resolve_team,
        split_assignments,
        team_by_name,
    )

    # run_craft(goal, h, team="core"): a non-worker second positional that
    # carries the harness API is the harness, not the planner.
    if harness is None and worker is not None and not isinstance(worker, _GW) \
            and hasattr(worker, "start_plan"):
        worker, harness = None, worker
    if harness is None:
        raise ValueError("run_craft needs a harness")
    if worker is not None and not isinstance(worker, _GW):
        raise ValueError("worker must be a GenericWorker (build via make_worker())")
    sid = getattr(harness, "session_id", None)
    workers_file = (getattr(worker, "_workers_file", None) if worker is not None
                    else (getattr(harness, "workers_file", "") or None))
    planner = (str((worker.entry or {}).get("name", "")).strip()
               if worker is not None else "")

    def _build(name: str) -> Any:
        if callable(factory):
            return factory(name)
        b = brain_factory() if callable(brain_factory) else make_brain()
        return make_worker(name, brain=b, workers_file=workers_file)

    if worker is None and not str(team or "").strip():
        raise ValueError("no planner and no team leader: pass worker= or team=")
    try:
        if worker is not None:
            team_spec = resolve_team(worker, team)
        else:
            team_spec = team_by_name(team, workers_file)
            planner = str(team_spec.get("leader", "")).strip()
            worker = _build(planner)
        planner_outside = ensure_planner_allowed(team_spec, planner)
    except ValueError as exc:
        return {"planned": False, "approved": False, "answer": "",
                "reason": f"team error: {exc}", "session_id": sid,
                "team": str(team or ""), "members": [], "planner": planner,
                "planner_outside_team": False, "shared_memory": True,
                "parallel": bool(parallel) if parallel is not None else True}
    share = True if team_spec is None else bool(team_spec.get("shared_memory", True))
    run_parallel = (bool(parallel) if parallel is not None
                    else (True if team_spec is None
                          else bool(team_spec.get("parallel", True))))
    meta = {
        "team": team_spec["name"] if team_spec else "",
        "members": list(team_spec["members"]) if team_spec else [],
        "planner": planner,
        "planner_outside_team": planner_outside,
        "shared_memory": share,
        "parallel": run_parallel,
    }
    g = str(goal or "").strip()
    if not g:
        return {"planned": False, "approved": False, "answer": "",
                "reason": "missing goal", "session_id": sid, **meta}
    try:
        resp = worker.chat(g, session_id=sid)
    except Exception as exc:
        return {"planned": False, "approved": False, "answer": "",
                "reason": f"worker chat failed: {str(exc)[:150]}",
                "session_id": sid, **meta}
    answer = str((resp or {}).get("answer", ""))
    triage = (resp or {}).get("triage") if isinstance(resp, dict) else None
    pol = (triage or {}).get("policy", {}) if isinstance(triage, dict) else {}
    if str(pol.get("action", "chat")).strip().lower() in ("clarify", "escalate"):
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "session_id": sid, **meta,
                "reason": f"policy {pol.get('action')} — no plan started"}
    steps = worker.extract_plan(answer)
    if not steps:
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "reason": "no numbered steps in reply",
                "session_id": sid, **meta}
    try:
        known = [str(e.get("name", "")).strip()
                 for e in _workers_loader(getattr(worker, "_workers_file", None))()]
    except Exception:
        known = []
    decision_meta = None
    try:
        decision_cfg = _decision_config(worker, decision, craft)
        if decision_cfg and decision_cfg.get("enabled"):
            candidates = (list(team_spec["members"]) if team_spec else list(known))
            candidates = [n for n in candidates if n and n != planner]
            answer, _items, decision_meta = _apply_decisions(
                g, answer, decision_cfg, worker, candidates, sid,
                force_parallel=decision_parallel)
            steps = worker.extract_plan(answer)
    except Exception as exc:
        decision_meta = {"scope": "", "mode": "", "status": "error",
                         "items": [], "blocked": [],
                         "reason": f"decision stage failed: {str(exc)[:150]}"}
    if not steps:
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "reason": "no numbered steps in reply",
                "session_id": sid, "decision": decision_meta, **meta}
    assigned = worker.extract_assigned(answer, known or None)
    need = worker.needs_plan_approval(len(steps))
    if decision_meta and (decision_meta.get("blocked")
                          or decision_meta.get("status") == "error"):
        need = True  # unrouted/failed decision always needs a human look
    try:
        info = harness.start_plan(g, steps, assigned_workers=assigned,
                                  needs_approval=need,
                                  decision_evidence=decision_meta)
    except Exception as exc:
        return {"planned": False, "approved": False, "answer": answer,
                "triage": triage, "session_id": sid, **meta,
                "reason": f"plan file failed: {str(exc)[:150]}"}
    approved = False
    if not need:
        approved = True  # small plan: file already says auto-approved
    elif callable(approver):
        try:
            approved = bool(approver(info))
        except Exception:
            approved = False
        try:
            harness.approve_plan(approved, by="approver",
                                 note="via run_craft")
        except Exception:
            pass
    out: Dict[str, Any] = {
        "planned": True, "path": info.get("path"), "steps": steps,
        "needs_approval": need, "approved": approved,
        "assigned_workers": assigned, "answer": answer, "triage": triage,
        "session_id": sid, "workers": [], "runs": [], "skipped": [],
        "blocked": [], "dispatched": False, "decision": decision_meta, **meta,
    }
    # Team is part of craft: approved assignments run as peer workers.
    # No plan -> no dispatch. No "assign" craft -> _enforce_autonomy has
    # already stripped [worker=] tags, so assigned is empty anyway.
    # dispatch=False keeps this planning-only (no peer chat runs).
    allowed, blocked = split_assignments(team_spec, assigned, exclude=planner)
    out["blocked"] = blocked
    if not (dispatch and approved and allowed and worker.can("assign")):
        return out
    from nak.worker.engine.dispatch import run_workers

    cap = max_workers
    if cap is None and team_spec:
        cap = team_spec.get("max_workers")
    # Shared team memory = one session doc for planner + peers. shared_memory
    # false keeps the planner's session but gives each peer its own transcript.
    dispatch_sid = sid if share else None
    try:
        fan = run_workers(answer, allowed, goal=g, factory=_build,
                          session_id=dispatch_sid, approver=approver,
                          max_workers=cap, exclude=planner,
                          parallel=run_parallel)
    except Exception as exc:
        out["reason"] = f"worker dispatch failed: {str(exc)[:150]}"
        return out
    out.update({"workers": fan["workers"], "runs": fan["runs"],
                "skipped": fan["skipped"], "dispatched": True})
    # Leader/planner read access to what the team wrote (shared mode only).
    if share and sid:
        try:
            out["team_memory"] = worker.team_memory(sid)
        except Exception:
            pass
    return out


# Backward-compatible name. Kept as a plain alias so old imports keep
# working; new code should call run_craft().
propose_plan = run_craft
