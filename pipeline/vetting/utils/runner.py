"""Vetting runner: Nunnan plans/judges, targets answer, reports are written."""

from __future__ import annotations

import json
import re
import secrets
import time

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from collections.abc import Callable

from nak.harness.craft.craft import crafts_for_worker
from nak.harness.engine.permission import (
    load_permissions,
    load_worker_names,
    load_worker_role,
    resolve_workers_file,
    rule_for,
)
from nak.harness.tools.assertion import assertion
from nak.harness.tools.scorecard import scorecard
from nak.harness.tools.trace import trace
from nak.wiring import make_brain, make_worker

from .models import (
    Answer,
    Case,
    Improvement,
    Judgement,
    RunSummary,
    WorkerResult,
)
from .probes import probe_crafts, probe_tools
from .prompting import (
    build_judge_prompt,
    build_plan_prompt,
    build_plan_retry_prompt,
    parse_json_object,
    validate_judgement,
    validate_plan,
)
from ..report import write_summary, write_worker_report

_ASSESSOR = "nunnan"
_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")
_PLANNER_KEYS = ("name", "role", "description", "worker_visible")
_CONTAINS_CLEAN = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_name(name: str) -> str:
    clean = _SLUG.sub("_", str(name or "").strip()).strip("._")
    return clean or "worker"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _load_entry(workers_file: Any, name: str) -> dict[str, Any]:
    path = resolve_workers_file(workers_file)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, list):
        return {}
    for entry in raw:
        if isinstance(entry, dict) and str(entry.get("name", "")).strip() == name:
            return entry
    return {}


def build_profile(worker_name: str, workers_file: Any = None,
                  permissions_file: Any = None) -> dict[str, Any]:
    entry = _load_entry(workers_file, worker_name)
    identity = entry.get("identity") if isinstance(entry.get("identity"), dict) else {}
    try:
        perms = load_permissions(permissions_file)
        role = load_worker_role(workers_file, worker_name)
        effective, _label = rule_for(perms, worker_name, role=role)
    except Exception:  # noqa: BLE001 — profile is best-effort
        effective = None
    tools = sorted((effective or {}).get("tools") or {})
    try:
        crafts = crafts_for_worker(worker_name, workers_file)
    except Exception:  # noqa: BLE001
        crafts = []
    decision = entry.get("decision") if isinstance(entry.get("decision"), dict) else {}
    memory = entry.get("memory") if isinstance(entry.get("memory"), dict) else {}
    try:
        from nak.worker.engine.autonomy import _autonomy_from_crafts
        autonomy = _autonomy_from_crafts(
            entry.get("crafts"), resolve_workers_file(workers_file), worker_name)
    except Exception:  # noqa: BLE001 — profile is best-effort
        autonomy = {}
    visible = {
        "role": str(identity.get("role", "") or ""),
        "description": str(entry.get("description", "") or ""),
        "personality": identity.get("personality") or [],
        "tone": str(identity.get("tone", "") or ""),
        "formality": str(identity.get("formality", "") or ""),
        "humor": str(identity.get("humor", "") or ""),
        "gender": str(identity.get("gender", "") or ""),
        "values": identity.get("values") or [],
        "do_not": identity.get("do_not") or [],
        "language": str(identity.get("language", "") or ""),
        "crafts": crafts,
        "autonomy": autonomy,
    }
    return {
        "name": worker_name,
        **visible,
        "worker_visible": visible,
        "tools": tools,
        "tool_verdicts": dict((effective or {}).get("tools") or {}),
        "capabilities": sorted((effective or {}).get("capabilities") or {}),
        "decision": {"enabled": bool(decision.get("enabled")),
                     "model": str(decision.get("model", "") or "")},
        "memory": {"enabled": bool(memory.get("enabled"))},
    }


def planner_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Only the facts the worker can actually see, for plan generation.

    The assessor must not turn hidden metadata (decision model, tool
    verdicts, permissions) into recall questions. The worker-visible block is
    the shared contract between what the planner reads and what the target
    receives.
    """
    if not isinstance(profile, dict):
        return {}
    return {key: profile.get(key) for key in _PLANNER_KEYS if key in profile}


def _empty_triage(triage: Any) -> bool:
    """True when the decision layer answered but selected no decision."""
    return (isinstance(triage, dict) and triage.get("ok") is True
            and not triage.get("decision"))


def _normalize_contains(value: Any) -> str:
    """Normalize prose for deterministic contains checks.

    Markdown ticks, punctuation and case must not decide a semantic check:
    "`plan` and `build`" and "plan and build" are the same phrase.
    """
    text = str(value or "").lower()
    return _CONTAINS_CLEAN.sub(" ", text).strip()


def _append_transcript(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _append_history(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = []
    if not isinstance(data, list):
        data = []
    data.append(record)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    tmp.replace(path)


def _configure_assessor(nunnan: Any) -> None:
    """Determinism: Nunnan's own triage/memory must never alter a verdict."""
    entry = dict(getattr(nunnan, "entry", {}) or {})
    for key in ("decision", "memory"):
        block = dict(entry.get(key) or {})
        block["enabled"] = False
        entry[key] = block
    nunnan.entry = entry


def _configure_target(target: Any) -> None:
    """Disable the subject's triage/policy for capability vetting.

    Empty or high-risk laya decisions otherwise replace the worker's answer
    with an escalation note. Policy behaviour can still be tested by passing
    target_decision=True.
    """
    entry = dict(getattr(target, "entry", {}) or {})
    block = dict(entry.get("decision") or {})
    block["enabled"] = False
    entry["decision"] = block
    target.entry = entry


def _attach_record(exc: Exception, record: dict[str, Any]) -> None:
    exc.record = record  # type: ignore[attr-defined]


def _reset_history(worker: Any) -> None:
    """Keep every plan/judge turn independent so ids never bleed across cases."""
    history = getattr(worker, "history", None)
    if isinstance(history, list):
        history.clear()


def _call_with_timeout(fn: Callable[[], Any],
                       timeout: float | None) -> Any:
    """Run fn, raising TimeoutError when it exceeds timeout seconds."""
    if not timeout or timeout <= 0:
        return fn()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn).result(timeout=timeout)
    except FuturesTimeoutError as exc:
        raise TimeoutError(f"timed out after {timeout:g}s") from exc
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _nunnan_json(nunnan: Any, prompt: str, history_path: Path, target: str,
                 phase: str, parser: Callable[[str], Any],
                 case_id: str = "", timeout: float | None = None) -> Any:
    record: dict[str, Any] = {
        "timestamp": _now(),
        "target": target,
        "phase": phase,
        "case_id": case_id,
        "prompt": prompt,
        "raw_response": "",
        "parsed": None,
        "error": "",
        "latency_ms": 0,
    }
    started = time.perf_counter()
    try:
        response = _call_with_timeout(lambda: nunnan.chat(prompt), timeout)
    except Exception as exc:  # noqa: BLE001 — recorded, then surfaced
        record["latency_ms"] = int((time.perf_counter() - started) * 1000)
        record["error"] = str(exc)[:500]
        _append_history(history_path, record)
        _attach_record(exc, record)
        raise
    record["latency_ms"] = int((time.perf_counter() - started) * 1000)
    if isinstance(response, dict):
        answer = str(response.get("answer", "") or "")
        record["triage"] = response.get("triage") or {}
    else:
        answer = str(response or "")
    record["raw_response"] = answer
    try:
        parsed = parser(answer)
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)[:500]
        _append_history(history_path, record)
        _attach_record(exc, record)
        raise
    record["parsed"] = _jsonable(parsed)
    _append_history(history_path, record)
    return parsed


def _chat_target(target: Any, case: Case, session_id: str,
                 answer_chars: int) -> Answer:
    started = time.perf_counter()
    answer = Answer(case_id=case.id)
    try:
        response = target.chat(case.question, session_id=session_id)
    except Exception as exc:  # noqa: BLE001 — one bad answer is evidence
        answer.error = str(exc)[:500]
        answer.latency_ms = int((time.perf_counter() - started) * 1000)
        return answer
    answer.latency_ms = int((time.perf_counter() - started) * 1000)
    if not isinstance(response, dict):
        response = {"answer": str(response or "")}
    text = str(response.get("answer", "") or "")
    triage = response.get("triage")
    answer.triage = triage if isinstance(triage, dict) else {}
    policy = answer.triage.get("policy") if isinstance(answer.triage, dict) else {}
    answer.policy_action = str((policy or {}).get("action", "chat")).lower()
    answer.blocked = (answer.policy_action in ("clarify", "escalate")
                      or bool(response.get("needs_approve")))
    answer.blocked_by_policy = answer.blocked
    if _empty_triage(answer.triage):
        answer.inconclusive = True
        answer.inconclusive_reason = (
            "decision layer returned an empty triage; not scored")
    answer.chars = len(text)
    answer.truncated = answer.chars > answer_chars
    answer.text = text[:answer_chars] if answer.truncated else text
    return answer


def _judge_case(nunnan: Any, case: Case, answer: Answer, history_path: Path,
                target: str, root: Path | None = None,
                timeout: float | None = None) -> Judgement:
    policy = None
    if answer.blocked or answer.policy_action in ("clarify", "escalate"):
        policy = {
            "action": answer.policy_action,
            "blocked": answer.blocked,
            "blocked_by_policy": answer.blocked_by_policy,
            "triage": answer.triage,
        }
    # Deterministic checks stay in the report for humans, but they are not
    # sent to the judge: live runs showed brittle `contains` checks anchoring
    # an otherwise correct answer to 0.0. The rubric/expected text is the
    # scoring contract; checks must never override it.
    prompt = build_judge_prompt(case, answer.text, answer.error, policy)
    last_error = ""
    for attempt in range(2):
        attempt_timeout = timeout * (attempt + 1) if timeout else None
        _reset_history(nunnan)
        try:
            return _nunnan_json(
                nunnan, prompt, history_path, target, "judge",
                lambda text: validate_judgement(parse_json_object(text), case),
                case_id=case.id,
                timeout=attempt_timeout,
            )
        except Exception as exc:  # noqa: BLE001 — retry once, then record
            last_error = str(exc)[:500]
            if attempt == 0 and not isinstance(exc, TimeoutError):
                prompt += (
                    "\n\nYour previous reply was not valid JSON. Reply again "
                    "with ONE JSON object and quote every string value."
                )
    return Judgement(case_id=case.id, score=0.0, rationale="",
                     error=last_error)


def _run_checks(cases: list[Case], answers: list[Answer], root: Path) -> list[dict[str, Any]]:
    by_id = {a.case_id: a for a in answers}
    out: list[dict[str, Any]] = []
    for case in cases:
        if not case.checks:
            continue
        answer = by_id.get(case.id)
        actual = answer.text if answer else ""
        checks = []
        for check in case.checks:
            op = check.get("op", "")
            expected = check.get("expected")
            if op == "contains":
                expected = _normalize_contains(expected)
                actual_for_check = _normalize_contains(actual)
            else:
                actual_for_check = actual
            checks.append({"id": check.get("id", ""), "op": op,
                           "expected": expected, "actual": actual_for_check})
        try:
            data = json.loads(assertion({"action": "check", "checks": checks,
                                         "root": str(root)}))
        except (TypeError, ValueError) as exc:
            out.append({"case_id": case.id, "id": "assertion", "op": "",
                        "passed": False, "error": str(exc)[:300]})
            continue
        if not data.get("ok"):
            out.append({"case_id": case.id, "id": "assertion", "op": "",
                        "passed": False, "error": str(data.get("error", ""))[:300]})
            continue
        for item in data.get("results") or []:
            if isinstance(item, dict):
                out.append({"case_id": case.id, **item})
    return out


def _case_score(cases: list[Case], judgements: list[Judgement],
                answers: list[Answer]) -> float | None:
    """Weighted case score, excluding cases the environment could not run.

    An empty decision-layer triage is not a worker answer: counting it as 0
    would fail the worker for a service failure. If every case is
    inconclusive, return None so the run can be reported as inconclusive.
    """
    if not cases:
        return None
    skipped = {a.case_id for a in answers if a.inconclusive}
    by_id = {j.case_id: j for j in judgements}
    eligible = [
        case for case in cases
        if case.id not in skipped
        and not (by_id.get(case.id) and by_id[case.id].error)
    ]
    if not eligible:
        return None
    total = sum(case.weight for case in eligible)
    if total <= 0:
        return None
    got = sum(case.weight * float(by_id.get(case.id, Judgement(case.id)).score)
              for case in eligible)
    return max(0.0, min(1.0, got / total))


def _pass_ratio(probes: list[Any]) -> float:
    scored = [p for p in probes if p.status in ("pass", "fail", "error")]
    if not scored:
        return 1.0
    return sum(1 for p in scored if p.status == "pass") / len(scored)


def _score_with_tool(case_score: float, craft_score: float | None,
                     tool_score: float | None, trace_data: dict[str, Any]) -> float:
    inputs: dict[str, Any] = {"cases": case_score}
    rules = [{"id": "cases", "metric": "cases", "weight": 85,
              "min": 0, "max": 1}]
    if craft_score is not None:
        inputs["crafts"] = craft_score
        rules.append({"id": "crafts", "metric": "crafts", "weight": 7.5,
                      "min": 0, "max": 1})
    if tool_score is not None:
        inputs["tools"] = tool_score
        rules.append({"id": "tools", "metric": "tools", "weight": 7.5,
                      "min": 0, "max": 1})
    try:
        data = json.loads(scorecard({"action": "score", "inputs": inputs,
                                     "rules": rules}))
    except (TypeError, ValueError) as exc:
        trace_data["scorecard"] = {"ok": False, "error": str(exc)[:300]}
        return case_score
    trace_data["scorecard"] = data
    if data.get("ok"):
        return float(data.get("score", case_score))
    return case_score


def _workers_using_craft(craft: str, workers_file: Any) -> list[str]:
    """Workers whose craft allowlist includes ``craft`` (best effort)."""
    out: list[str] = []
    try:
        names = load_worker_names(workers_file)
    except Exception:  # noqa: BLE001 — suggestions must never break a run
        return out
    for name in names:
        if name == _ASSESSOR:
            continue
        try:
            crafts = crafts_for_worker(name, workers_file)
        except Exception:  # noqa: BLE001
            continue
        if any(str(c.get("name", "")).strip() == craft for c in crafts):
            out.append(name)
    return out


def _build_improvements(result: WorkerResult,
                        workers_file: Any = None) -> list[Improvement]:
    """Turn failures into concrete, scoped improvement suggestions."""
    out: list[Improvement] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(**kw: Any) -> None:
        worker = str(kw.pop("worker", result.name))
        target = str(kw.get("target", ""))
        suggestion = str(kw.get("suggestion", ""))
        key = (worker, str(kw.get("source", "")), target, suggestion)
        if key in seen:
            return
        seen.add(key)
        out.append(Improvement(
            id=f"{_safe_name(worker)}-{len(out) + 1}", worker=worker, **kw))

    threshold = result.threshold or 0.6
    judgements = {j.case_id: j for j in result.judgements}
    for case in result.cases:
        judgement = judgements.get(case.id)
        if judgement is None:
            continue
        if judgement.error:
            add(source="judge", section="case", severity="high",
                target=case.id,
                problem=f"Judge failed for case {case.id}.",
                evidence=str(judgement.error)[:500],
                suggestion=("Re-run the judge; if it keeps failing, fix the "
                            "judge prompt/timeout before changing the worker."))
            continue
        if float(judgement.score) < threshold:
            add(source="case", section=case.difficulty,
                severity="high" if judgement.score < 0.3 else "medium",
                target=case.id,
                problem=(f"Case {case.id} scored {judgement.score:.2f} "
                         f"(threshold {threshold:.2f})."),
                evidence=str(judgement.evidence or judgement.rationale or "")[:500],
                suggestion=f"Improve the answer for: {case.expected}")

    for check in result.checks:
        if check.get("passed"):
            continue
        target = str(check.get("case_id", ""))
        evidence = check.get("error") or (
            f"expected={check.get('expected')!r} "
            f"actual={check.get('actual')!r}")
        add(source="check", section="case", target=target,
            problem=f"Deterministic check {check.get('id', '')} failed.",
            evidence=str(evidence)[:500],
            suggestion=("Make the answer satisfy the check, or relax the "
                        "check if the rubric accepts a paraphrase."))

    for probe in result.craft_probes:
        if probe.status not in ("fail", "error"):
            continue
        add(source="craft", section="craft", scope="craft",
            severity="high" if probe.status == "error" else "medium",
            target=probe.name,
            affected_workers=_workers_using_craft(probe.name, workers_file),
            problem=f"Craft {probe.name} probe {probe.status}.",
            evidence=str(probe.detail)[:500],
            suggestion=(f"Fix craft {probe.name} in a staged copy, test with "
                        "the core team, then re-vet affected workers."))

    for probe in result.tool_probes:
        if probe.status not in ("fail", "error"):
            continue
        add(source="tool", section="tool", scope="tool",
            severity="high" if probe.status == "error" else "medium",
            target=probe.name,
            problem=f"Tool {probe.name} probe {probe.status}.",
            evidence=str(probe.detail)[:500],
            suggestion=(f"Fix tool {probe.name} or its registry entry, then "
                        "re-run the vetting probes."))

    if result.status == "inconclusive":
        add(source="status", section="run", severity="high",
            target=result.name,
            problem="Worker run was inconclusive.",
            evidence=str(result.error)[:500],
            suggestion=("Fix the plan/judge/environment before re-testing "
                        "the worker."))
    return out[:12]


def _finalize(result: WorkerResult, reports_dir: Path,
              transcript_path: Path, worker_dir: Path,
              workers_file: Any = None) -> None:
    result.finished_at = _now()
    result.improvements = _build_improvements(result, workers_file)
    case_score = _case_score(result.cases, result.judgements, result.answers)
    inconclusive_cases = sum(1 for a in result.answers if a.inconclusive)
    judge_errors = sum(1 for j in result.judgements if j.error)
    craft_scored = [p for p in result.craft_probes
                    if p.status in ("pass", "fail", "error")]
    tool_scored = [p for p in result.tool_probes
                   if p.status in ("pass", "fail", "error")]
    craft_score = _pass_ratio(result.craft_probes)
    tool_score = _pass_ratio(result.tool_probes)
    structural_failures = any(
        p.status in ("fail", "error")
        for p in (*result.craft_probes, *result.tool_probes)
    )
    inconclusive = result.status == "inconclusive" or case_score is None
    if inconclusive:
        result.score = 0.0
        result.passed = False
        result.status = "inconclusive"
        if not result.error:
            result.error = (
                "no scorable cases: the assessor produced no plan"
                if not result.cases else
                "no scorable cases: decision layer returned empty triage")
    else:
        final = _score_with_tool(
            case_score,
            craft_score if craft_scored else None,
            tool_score if tool_scored else None,
            result.trace,
        )
        result.score = round(max(0.0, min(1.0, final)), 6)
        result.passed = (result.score >= result.threshold
                         and not result.error
                         and not structural_failures)
        result.status = "pass" if result.passed else "fail"
    result.structural = {
        "case_score": (round(case_score, 6)
                       if case_score is not None else None),
        "craft_score": round(craft_score, 6),
        "tool_score": round(tool_score, 6),
        "inconclusive_cases": inconclusive_cases,
        "judge_errors": judge_errors,
        "final": result.score,
    }
    try:
        write_worker_report(result, reports_dir)
        _append_transcript(transcript_path, {
            "timestamp": _now(), "type": "report", "target": result.name,
            "md": result.report_md, "json": result.report_json,
        })
    except Exception as exc:  # noqa: BLE001 — report failure is recorded
        note = f"report failed: {str(exc)[:300]}"
        result.error = f"{result.error}; {note}" if result.error else note
        result.passed = False
        result.status = "inconclusive"
    try:
        result.trace["transcript"] = json.loads(trace({
            "action": "grep", "path": "output/evidence/transcript.jsonl",
            "pattern": '"type": "judgement"', "root": str(worker_dir),
        }))
    except Exception as exc:  # noqa: BLE001 — trace is evidence, not a gate
        result.trace["transcript"] = {"ok": False, "error": str(exc)[:300]}


def _run_one(name: str, run_id: str, run_dir: Path, *,
             workers_file: Any, permissions_file: Any,
             assessor_brain: Any, target_brain: Any,
             nunnan_factory: Callable[[Any], Any],
             worker_factory: Callable[[str, Any], Any],
             cases: int, threshold: float, answer_chars: int,
             probe_crafts_enabled: bool, probe_tools_enabled: bool,
             target_decision: bool = False) -> WorkerResult:
    worker_dir = run_dir / _safe_name(name)
    evidence = worker_dir / "output" / "evidence"
    reports = worker_dir / "output" / "reports"
    evidence.mkdir(parents=True, exist_ok=True)
    reports.mkdir(parents=True, exist_ok=True)
    transcript_path = evidence / "transcript.jsonl"
    history_path = evidence / "nunnan_history.json"

    result = WorkerResult(
        name=name, threshold=threshold, started_at=_now(),
        session_id=worker_dir.name,
        transcript_path=str(transcript_path),
        nunnan_history_path=str(history_path),
    )
    try:
        result.profile = build_profile(name, workers_file, permissions_file)
    except Exception as exc:  # noqa: BLE001
        result.error = f"profile failed: {str(exc)[:300]}"

    if probe_crafts_enabled:
        result.craft_probes = probe_crafts(name, workers_file)
    if probe_tools_enabled:
        result.tool_probes = probe_tools(name, workers_file,
                                         root=worker_dir / "probes",
                                         permissions_file=permissions_file)

    try:
        nunnan = nunnan_factory(assessor_brain)
        _configure_assessor(nunnan)
        _reset_history(nunnan)
        assessor_profile = planner_profile(result.profile)
        plan_prompt = build_plan_prompt(assessor_profile, cases)
        plan_attempts = 3
        for attempt in range(1, plan_attempts + 1):
            try:
                _reset_history(nunnan)
                result.cases = _nunnan_json(
                    nunnan, plan_prompt, history_path, name, "plan",
                    lambda text: validate_plan(parse_json_object(text), cases),
                )
                break
            except Exception as exc:  # noqa: BLE001 — retry a bad plan
                record = getattr(exc, "record", {}) or {}
                previous = str(record.get("raw_response", "") or "")
                if attempt >= plan_attempts:
                    raise
                if not previous and attempt >= 2:
                    raise
                if previous:
                    plan_prompt = build_plan_retry_prompt(
                        assessor_profile, cases, previous, str(exc))
        _append_transcript(transcript_path, {
            "timestamp": _now(), "type": "plan", "target": name,
            "cases": [case.to_dict() for case in result.cases],
        })
    except Exception as exc:  # noqa: BLE001 — no plan means no answers
        result.error = f"plan failed: {str(exc)[:400]}"
        result.status = "inconclusive"
        _finalize(result, reports, transcript_path, worker_dir, workers_file)
        return result

    try:
        target = worker_factory(name, target_brain)
        if not target_decision:
            _configure_target(target)
    except Exception as exc:  # noqa: BLE001
        result.error = f"target worker failed: {str(exc)[:300]}"
        result.status = "inconclusive"
        _finalize(result, reports, transcript_path, worker_dir, workers_file)
        return result

    session_id = f"vetting-{run_id}-{_safe_name(name)}"
    for case in result.cases:
        answer = _chat_target(target, case, session_id, answer_chars)
        result.answers.append(answer)
        _append_transcript(transcript_path, {
            "timestamp": _now(), "type": "answer", "target": name,
            "case_id": case.id, "prompt": case.question,
            "answer": answer.text, "error": answer.error,
            "latency_ms": answer.latency_ms, "chars": answer.chars,
            "truncated": answer.truncated, "triage": answer.triage,
            "policy_action": answer.policy_action, "blocked": answer.blocked,
            "blocked_by_policy": answer.blocked_by_policy,
            "inconclusive": answer.inconclusive,
            "inconclusive_reason": answer.inconclusive_reason,
        })
        if answer.inconclusive:
            judgement = Judgement(
                case_id=case.id, score=0.0,
                error=answer.inconclusive_reason or "inconclusive answer")
        else:
            judgement = _judge_case(
                nunnan, case, answer, history_path, name, root=worker_dir,
                timeout=60)
        result.judgements.append(judgement)
        _append_transcript(transcript_path, {
            "timestamp": _now(), "type": "judgement", "target": name,
            "case_id": case.id, "score": judgement.score,
            "rationale": judgement.rationale, "evidence": judgement.evidence,
            "error": judgement.error, "inconclusive": answer.inconclusive,
        })

    result.checks = _run_checks(result.cases, result.answers, worker_dir)
    _finalize(result, reports, transcript_path, worker_dir, workers_file)
    return result


def _default_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (Path(__file__).resolve().parent.parent / "runs"
            / f"{stamp}_{secrets.token_hex(3)}")


def _vetting_user(run_id: str, target: str, role: str) -> str:
    """A fresh Mind user per target/role so prior runs cannot bleed in.

    Mind grounds every /agent/chat turn in the user's stored memories, even
    when the worker's own memory flag is off. Sharing the default user made
    Nunnan retrieve an earlier target's plan (e.g. vizhi's) while planning
    for a different worker. A unique user per target/role keeps the assessor
    and each subject in their own memory space.
    """
    raw = f"vetting_{run_id}_{target}_{role}"
    return re.sub(r"[^A-Za-z0-9_]+", "_", raw)[:80]


def _fresh_brain(*, user: str, endpoint: str | None,
                 timeout: float | None) -> Any:
    brain = make_brain(base_url=endpoint, timeout=timeout, user=user)
    ensure = getattr(brain, "ensure_user", None)
    if callable(ensure):
        ensure()
    return brain


def run_vetting(
    workers: list[str] | None = None,
    run_dir: str | Path | None = None,
    *,
    workers_file: Any = None,
    permissions_file: Any = None,
    endpoint: str | None = None,
    timeout: float | None = None,
    cases: int = 6,
    threshold: float = 0.6,
    answer_chars: int = 8000,
    brain: Any = None,
    nunnan_brain: Any = None,
    worker_factory: Callable[[str, Any], Any] | None = None,
    nunnan_factory: Callable[[Any], Any] | None = None,
    probe_crafts_enabled: bool = True,
    probe_tools_enabled: bool = True,
    include_assessor: bool = False,
    target_decision: bool = False,
) -> RunSummary:
    """Run one vetting pass and return the summary (also written to disk)."""
    if isinstance(workers, str):
        workers = [part.strip() for part in workers.split(",") if part.strip()]
    run_path = Path(run_dir) if run_dir else _default_run_dir()
    run_path.mkdir(parents=True, exist_ok=True)
    run_id = run_path.name
    started = _now()
    summary = RunSummary(run_dir=str(run_path), started_at=started,
                         cases=cases, threshold=threshold)

    known = load_worker_names(workers_file)
    if workers:
        names = list(dict.fromkeys(
            str(w).strip() for w in workers if str(w).strip()))
        unknown = [w for w in names if known and w not in known]
        if unknown:
            summary.error = f"unknown worker(s): {unknown}; have: {known}"
            summary.exit_code = 2
            summary.finished_at = _now()
            write_summary(summary)
            return summary
    else:
        names = list(known)
        if not include_assessor:
            if _ASSESSOR in names:
                summary.skipped.append(
                    f"{_ASSESSOR}: assessor excluded from auto-run "
                    f"(pass --include-assessor to test it)")
            names = [n for n in names if n != _ASSESSOR]
    if not names:
        summary.error = "no workers to vet"
        summary.exit_code = 2
        summary.finished_at = _now()
        write_summary(summary)
        return summary

    def _make_target_brain(target: str) -> Any:
        if brain is not None:
            return brain
        return _fresh_brain(
            user=_vetting_user(run_path.name, target, "target"),
            endpoint=endpoint, timeout=timeout)

    def _make_assessor_brain(target: str) -> Any:
        if nunnan_brain is not None:
            return nunnan_brain
        if brain is not None:
            return brain
        return _fresh_brain(
            user=_vetting_user(run_path.name, target, "assessor"),
            endpoint=endpoint, timeout=timeout)

    make_target = worker_factory or (
        lambda name, b: make_worker(name, brain=b, workers_file=workers_file))
    make_assessor = nunnan_factory or (
        lambda b: make_worker(_ASSESSOR, brain=b, workers_file=workers_file))

    for name in names:
        try:
            result = _run_one(
                name, run_id, run_path,
                workers_file=workers_file,
                permissions_file=permissions_file,
                assessor_brain=_make_assessor_brain(name),
                target_brain=_make_target_brain(name),
                nunnan_factory=make_assessor,
                worker_factory=make_target,
                cases=cases,
                threshold=threshold,
                answer_chars=answer_chars,
                probe_crafts_enabled=probe_crafts_enabled,
                probe_tools_enabled=probe_tools_enabled,
                target_decision=target_decision,
            )
        except Exception as exc:  # noqa: BLE001 — one worker never kills the run
            result = WorkerResult(
                name=name, threshold=threshold, started_at=_now(),
                finished_at=_now(), status="inconclusive",
                error=f"unexpected failure: {str(exc)[:400]}",
            )
        summary.workers.append(result)

    summary.finished_at = _now()
    real_failures = [w for w in summary.workers
                     if not w.passed and w.status != "inconclusive"]
    inconclusive = [w for w in summary.workers
                    if w.status == "inconclusive"]
    if real_failures:
        summary.exit_code = 1
    elif inconclusive:
        summary.exit_code = 3
    else:
        summary.exit_code = 0
    write_summary(summary)
    return summary
