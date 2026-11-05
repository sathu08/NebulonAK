"""Persona, behavior, tool and craft checks for the report card."""
from __future__ import annotations

import time

from collections.abc import Callable
from pathlib import Path
from typing import Any

from nak_host.harness.craft.craft import (
    crafts_for_worker,
    expand_craft,
    load_craft,
)
from nak_host.harness.engine.permission import (
    load_permissions,
    load_worker_role,
    rule_for,
)
from nak_host.wiring import make_worker

from ..utils.models import Answer, Case, Judgement
from ..utils.probes import probe_tools
from ..utils.runner import (
    _configure_assessor,
    _fresh_brain,
    _judge_case,
    _reset_history,
    _run_checks,
    _vetting_user,
)
from .cases import behavior_cases, persona_cases
from .models import Check, _safe_name

_ASSESSOR = "nunnan"

def _response_text(response: Any) -> str:
    if isinstance(response, dict):
        return str(response.get("answer", "") or "")
    return str(response or "")


def _chat_with_retry(target: Any, question: str, session_id: str,
                     attempts: int = 3, delay: float = 1.5) -> tuple[str, str]:
    error = ""
    for attempt in range(1, attempts + 1):
        try:
            return _response_text(target.chat(question, session_id=session_id)), ""
        except Exception as exc:  # noqa: BLE001 — retry transient LLM failures
            error = str(exc)[:500]
            if attempt < attempts:
                time.sleep(delay * attempt)
    return "", error


def _configure_subject(target: Any) -> None:
    entry = dict(getattr(target, "entry", {}) or {})
    for key in ("decision", "memory"):
        block = dict(entry.get(key) or {})
        block["enabled"] = False
        entry[key] = block
    target.entry = entry


def _persona_suggestion(case: Case, judgement: Judgement,
                        error: str, score: float,
                        deterministic: float | None) -> str:
    if error:
        return f"Answer {case.id} without an error: {error}"
    if judgement.error:
        return f"Get a valid judgement for {case.id}: {judgement.error}"
    if score >= 0.999:
        return ""
    if score >= 0.6 and (deterministic is None or deterministic >= 1.0):
        return ""
    reason = judgement.rationale or "the answer missed the expected persona behavior."
    return f"Improve {case.id}: {reason}"


def _persona_checks(
    worker: str,
    profile: dict[str, Any],
    run_dir: Path,
    *,
    endpoint: str | None,
    timeout: float | None,
    workers_file: Any,
    brain: Any,
    nunnan_brain: Any,
    worker_factory: Callable[[str, Any], Any] | None,
    nunnan_factory: Callable[[Any], Any] | None,
    cases: int,
    answer_chars: int,
) -> list[Check]:
    if cases <= 0:
        return []
    run_id = run_dir.name
    target_brain = brain or _fresh_brain(
        user=_vetting_user(run_id, worker, "reportcard-target"),
        endpoint=endpoint, timeout=timeout)
    assessor_brain = nunnan_brain or _fresh_brain(
        user=_vetting_user(run_id, worker, "reportcard-assessor"),
        endpoint=endpoint, timeout=timeout)
    make_target = worker_factory or (
        lambda name, b: make_worker(name, brain=b, workers_file=workers_file))
    make_assessor = nunnan_factory or (
        lambda b: make_worker(_ASSESSOR, brain=b, workers_file=workers_file))
    try:
        target = make_target(worker, target_brain)
        _configure_subject(target)
        nunnan = make_assessor(assessor_brain)
        _configure_assessor(nunnan)
    except Exception as exc:  # noqa: BLE001
        return [Check(
            section="persona", name="persona", question="Can the persona suite start?",
            expected="Target and assessor start", status="error", score=0.0,
            detail=str(exc)[:300], suggestion="Fix persona worker wiring.",
        )]
    history = (run_dir / "report_card" / "evidence"
               / f"{_safe_name(worker)}_persona_history.json")
    session_id = f"reportcard-{run_id}-{_safe_name(worker)}-persona"
    checks = []
    for case in persona_cases(profile, cases):
        _reset_history(target)
        answer_text, error = _chat_with_retry(
            target, case.question, session_id)
        answer = Answer(
            case_id=case.id,
            text=answer_text[:answer_chars],
            error=error,
            chars=len(answer_text),
            truncated=len(answer_text) > answer_chars,
        )
        judgement = _judge_case(nunnan, case, answer, history, worker, root=None)
        judge_score = max(0.0, min(1.0, float(judgement.score)))
        assertions = _run_checks([case], [answer], run_dir) if case.checks else []
        case_assertions = [item for item in assertions
                           if item.get("case_id") == case.id]
        deterministic = (
            sum(1 for item in case_assertions if item.get("passed"))
            / len(case_assertions)
            if case_assertions else None)
        score = max(judge_score, deterministic or 0.0)
        status = "error" if error else ("pass" if score >= 0.6 else "fail")
        detail = f"judge={judge_score:.2f}"
        if deterministic is not None:
            detail += f"; deterministic={deterministic:.2f}"
        if judgement.rationale:
            detail += f"; judge says: {judgement.rationale}"
        elif judgement.error:
            detail += f"; judge error: {judgement.error}"
        elif error:
            detail += f"; error: {error}"
        checks.append(Check(
            section="persona", name=case.id, question=case.question,
            expected=case.expected, actual=answer_text[:answer_chars],
            score=score, status=status, detail=detail,
            suggestion=_persona_suggestion(
                case, judgement, error, score, deterministic),
        ))
    return checks


def _behavior_suggestion(case: Case, judgement: Judgement | None,
                         error: str, score: float,
                         deterministic: float | None) -> str:
    if error:
        return f"Answer {case.id} without an error: {error}"
    if judgement is None:
        return f"Get a judgement for {case.id}."
    if judgement.error:
        return f"Get a valid judgement for {case.id}: {judgement.error}"
    if score >= 0.999:
        return ""
    if score >= 0.6 and (deterministic is None or deterministic >= 1.0):
        return ""
    reason = judgement.rationale or "the answer missed the expected behavior."
    return f"Improve {case.id}: {reason}"


def _behavior_checks(
    worker: str,
    profile: dict[str, Any],
    run_dir: Path,
    *,
    case_count: int,
    endpoint: str | None,
    timeout: float | None,
    workers_file: Any,
    brain: Any,
    nunnan_brain: Any,
    worker_factory: Callable[[str, Any], Any] | None,
    nunnan_factory: Callable[[Any], Any] | None,
    answer_chars: int,
) -> list[Check]:
    if case_count <= 0:
        return []
    run_id = run_dir.name
    target_brain = brain or _fresh_brain(
        user=_vetting_user(run_id, worker, "reportcard-behavior-target"),
        endpoint=endpoint, timeout=timeout)
    assessor_brain = nunnan_brain or _fresh_brain(
        user=_vetting_user(run_id, worker, "reportcard-behavior-assessor"),
        endpoint=endpoint, timeout=timeout)
    make_target = worker_factory or (
        lambda name, b: make_worker(name, brain=b, workers_file=workers_file))
    make_assessor = nunnan_factory or (
        lambda b: make_worker(_ASSESSOR, brain=b, workers_file=workers_file))
    try:
        target = make_target(worker, target_brain)
        _configure_subject(target)
        nunnan = make_assessor(assessor_brain)
        _configure_assessor(nunnan)
    except Exception as exc:  # noqa: BLE001
        return [Check(
            section="behavior", name="behavior",
            question="Can the behavior suite start?",
            expected="Target and assessor start", status="error", score=0.0,
            detail=str(exc)[:300], suggestion="Fix behavior worker wiring.",
        )]
    history = (run_dir / "report_card" / "evidence"
               / f"{_safe_name(worker)}_behavior_history.json")
    session_id = f"reportcard-{run_id}-{_safe_name(worker)}-behavior"
    checks = []
    for case in behavior_cases(profile, case_count):
        _reset_history(target)
        answer_text, error = _chat_with_retry(
            target, case.question, session_id)
        answer = Answer(
            case_id=case.id,
            text=answer_text[:answer_chars],
            error=error,
            chars=len(answer_text),
            truncated=len(answer_text) > answer_chars,
        )
        judgement = _judge_case(nunnan, case, answer, history, worker,
                                root=run_dir)
        judge_score = max(0.0, min(1.0, float(judgement.score)))
        assertions = _run_checks([case], [answer], run_dir) if case.checks else []
        case_assertions = [item for item in assertions
                           if item.get("case_id") == case.id]
        deterministic = (
            sum(1 for item in case_assertions if item.get("passed"))
            / len(case_assertions)
            if case_assertions else None)
        score = max(judge_score, deterministic or 0.0)
        status = "error" if error else ("pass" if score >= 0.6 else "fail")
        detail = f"judge={judge_score:.2f}"
        if deterministic is not None:
            detail += f"; deterministic={deterministic:.2f}"
        if judgement.rationale:
            detail += f"; judge says: {judgement.rationale}"
        elif judgement.error:
            detail += f"; judge error: {judgement.error}"
        elif error:
            detail += f"; error: {error}"
        checks.append(Check(
            section="behavior", name=case.id, question=case.question,
            expected=case.expected, actual=answer_text[:answer_chars],
            score=score, status=status, detail=detail,
            suggestion=_behavior_suggestion(
                case, judgement, error, score, deterministic),
        ))
    return checks


def _tool_checks(
    worker: str,
    root: Path,
    *,
    workers_file: Any,
    permissions_file: Any,
) -> list[Check]:
    try:
        probes = probe_tools(worker, workers_file, root=root,
                             permissions_file=permissions_file)
    except Exception as exc:  # noqa: BLE001
        return [Check(
            section="tools", name="tools", question="Can tools be probed?",
            expected="Tool probes run", status="error", score=0.0,
            detail=str(exc)[:300], suggestion="Fix the tool probe harness.",
        )]
    checks = []
    for probe in probes:
        if probe.kind != "tool" or probe.name == "tools":
            status = probe.status if probe.status in ("deny", "skip", "error") else "fail"
            checks.append(Check(
                section="tools", name=probe.name or "tools",
                question=f"Can tool {probe.name} be probed?",
                expected="Probe runs", status=status,
                score=None if status in ("deny", "skip") else 0.0,
                detail=probe.detail,
                suggestion=(f"Fix tool probing: {probe.detail}"
                            if status == "error" else ""),
            ))
            continue
        if probe.status == "deny":
            checks.append(Check(
                section="tools", name=f"{probe.name}:permission",
                question=f"Is {probe.name} allowed by permissions?",
                expected="allow", status="deny", score=None,
                detail=probe.detail,
                suggestion=f"Grant permission for {probe.name} if the worker needs it.",
            ))
            continue
        if probe.status == "skip":
            checks.append(Check(
                section="tools", name=f"{probe.name}:permission",
                question=f"Is {probe.name} allowed by permissions?",
                expected="allow or ask", status="pass", score=1.0,
                detail=f"{probe.detail} (approval-gated)",
            ))
            continue
        executed = probe.status in ("pass", "fail")
        checks.extend([
            Check(
                section="tools", name=f"{probe.name}:permission",
                question=f"Is {probe.name} allowed by permissions?",
                expected="allow", status="pass", score=1.0,
                detail="allowed by the effective permission entry",
            ),
            Check(
                section="tools", name=f"{probe.name}:execution",
                question=f"Does {probe.name} run without a harness error?",
                expected="probe executes", status="pass" if executed else "fail",
                score=1.0 if executed else 0.0, detail=probe.detail,
                suggestion=("" if executed
                            else f"Fix {probe.name} execution: {probe.detail}"),
            ),
            Check(
                section="tools", name=f"{probe.name}:result",
                question=f"Does {probe.name} return the expected result?",
                expected="probe passes",
                status="pass" if probe.status == "pass" else "fail",
                score=1.0 if probe.status == "pass" else 0.0,
                detail=probe.detail,
                suggestion=("" if probe.status == "pass"
                            else f"Fix {probe.name} result: {probe.detail}"),
            ),
        ])
    return checks


def _craft_checks(
    worker: str,
    *,
    workers_file: Any,
    permissions_file: Any,
) -> list[Check]:
    try:
        crafts = crafts_for_worker(worker, workers_file)
    except Exception as exc:  # noqa: BLE001
        return [Check(
            section="craft", name="crafts", question="Can crafts be loaded?",
            expected="Craft list loads", status="error", score=0.0,
            detail=str(exc)[:300], suggestion="Fix the craft registry.",
        )]
    if not crafts:
        return [Check(
            section="craft", name="crafts",
            question="Does the worker declare crafts?",
            expected="At least one craft", status="skip", score=None,
            detail="no crafts declared for this worker",
            suggestion="Declare crafts if the worker should have craft capability.",
        )]
    try:
        permissions = load_permissions(permissions_file)
        role = load_worker_role(workers_file, worker)
        entry, _label = rule_for(permissions, worker, role=role)
        allowed = {
            str(name) for name, verdict in ((entry or {}).get("tools") or {}).items()
            if str(verdict).lower() in ("allow", "ask")
        }
    except Exception:  # noqa: BLE001
        allowed = set()
    checks = []
    for item in crafts:
        name = str(item.get("name", "") or "").strip()
        if not name:
            continue
        try:
            craft = load_craft(name)
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)[:300]
            checks.extend([
                Check(
                    section="craft", name=f"{name}:load",
                    question=f"Does craft {name} load?", expected="craft loads",
                    status="fail", score=0.0, detail=detail,
                    suggestion=f"Fix craft {name} registry/markdown: {detail}",
                ),
                Check(
                    section="craft", name=f"{name}:expand",
                    question=f"Does craft {name} expand?", expected="craft expands",
                    status="fail", score=0.0, detail="craft failed to load",
                    suggestion=f"Fix craft {name} before expanding it.",
                ),
                Check(
                    section="craft", name=f"{name}:tools",
                    question=f"Are craft {name} tools allowed?",
                    expected="all craft tools allowed", status="fail", score=0.0,
                    detail="craft failed to load",
                    suggestion=f"Fix craft {name} before checking its tools.",
                ),
            ])
            continue
        checks.append(Check(
            section="craft", name=f"{name}:load",
            question=f"Does craft {name} load?", expected="craft loads",
            status="pass", score=1.0, detail=str(craft.get("description", "")),
        ))
        given = {str(key): "report-card-probe"
                 for key in (craft.get("required_inputs") or [])}
        try:
            steps = expand_craft(name, given)
            ok = bool(steps)
            checks.append(Check(
                section="craft", name=f"{name}:expand",
                question=f"Does craft {name} expand?",
                expected="craft expands to at least one step",
                status="pass" if ok else "fail", score=1.0 if ok else 0.0,
                detail=f"{len(steps)} step(s)" if ok else "expanded to zero steps",
                suggestion="" if ok else f"Fix craft {name} steps/inputs.",
            ))
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)[:300]
            checks.append(Check(
                section="craft", name=f"{name}:expand",
                question=f"Does craft {name} expand?", expected="craft expands",
                status="fail", score=0.0, detail=detail,
                suggestion=f"Fix craft {name} inputs/steps: {detail}",
            ))
        bad = [str(tool) for tool in (craft.get("tools") or [])
               if str(tool) not in allowed]
        checks.append(Check(
            section="craft", name=f"{name}:tools",
            question=f"Are craft {name} tools allowed?",
            expected="all craft tools allowed or approval-gated",
            status="pass" if not bad else "fail",
            score=1.0 if not bad else 0.0,
            detail=("all allowed or approval-gated" if not bad
                    else f"not allowed: {bad}"),
            suggestion=("" if not bad
                        else f"Grant permissions or remove tools {bad} "
                             f"from craft {name}."),
        ))
    return checks

