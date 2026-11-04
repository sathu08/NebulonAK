"""Prompt building, JSON extraction and strict vetting validation."""

from __future__ import annotations

import json
import re

from pathlib import Path
from typing import Any

from .models import Case, Judgement

OPS: tuple[str, ...] = ("eq", "ne", "contains", "regex", "gte", "lte", "in")
DIFFICULTIES: tuple[str, ...] = ("easy", "mid", "hard")
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
# "<mode> craft" mentions are checked against the worker's own crafts so a
# case can never demand behaviour from a craft the worker does not have
# (e.g. plan-craft questions for a test/validate-only worker). Generic prose
# ("the craft of ...") never matches; only known mode names do.
_CRAFT_VOCAB = ("plan", "assign", "build", "assess", "review", "audit",
                "test", "validate", "assist")
_CRAFT_MENTION_RE = re.compile(
    r"\b(" + "|".join(_CRAFT_VOCAB) + r")\s+craft\b", re.IGNORECASE)
# "N modes" claims ("three modes", "2 modes") are checked against the
# worker's real mode count: generators invent mode counts the same way they
# invent unlisted crafts. Step/step-limit wording never matches (no "modes").
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
_MODES_CLAIM_RE = re.compile(
    r"\b(one|two|three|four|five|\d+)\s+(?:autonomy\s+)?modes?\b",
    re.IGNORECASE)
_MAX_CHECKS_PER_CASE = 20


def _claimed_mode_counts(*texts: Any) -> list[int]:
    counts: list[int] = []
    for text in texts:
        for match in _MODES_CLAIM_RE.finditer(str(text or "")):
            word = match.group(1).lower()
            counts.append(_NUMBER_WORDS.get(word, int(word)
                                           if word.isdigit() else 0))
    return [c for c in counts if c > 0]


def _mentioned_crafts(*texts: Any) -> list[str]:
    seen: list[str] = []
    for text in texts:
        for match in _CRAFT_MENTION_RE.finditer(str(text or "")):
            name = match.group(1).lower()
            if name not in seen:
                seen.append(name)
    return seen
_BRITTLE_CHECK_TOKENS = {
    "plan", "personality", "laya", "nemotron", "max_steps", "do_not",
    "tool_verdict", "permission", "decision.model", "decision", "memory",
}
_CHECK_CLEAN = re.compile(r"[^a-z0-9]+")


def _check_phrase(value: Any) -> str:
    return _CHECK_CLEAN.sub(" ", str(value or "").lower()).strip()


class PromptError(ValueError):
    """A prompt/response violated the vetting contract."""


def _template(name: str) -> str:
    path = Path(__file__).resolve().parent.parent / "prompts" / f"{name}.md"
    return path.read_text(encoding="utf-8")


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def fence_untrusted(value: Any) -> str:
    """Fence arbitrary data with a backtick run longer than any inside it."""
    text = _text(value)
    runs = re.findall(r"`+", text)
    longest = max((len(r) for r in runs), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text\n{text}\n{fence}"


def _distribution(n_cases: int) -> str:
    if n_cases == 6:
        return "2 easy, 2 mid, 2 hard"
    base, extra = divmod(n_cases, 3)
    counts = [base + (1 if i < extra else 0) for i in range(3)]
    return ", ".join(f"{count} {name}" for count, name in zip(counts, DIFFICULTIES, strict=True))


def build_plan_prompt(profile: dict[str, Any], n_cases: int) -> str:
    return (
        _template("plan")
        .replace("{{profile}}", fence_untrusted(profile))
        .replace("{{cases}}", str(n_cases))
        .replace("{{distribution}}", _distribution(n_cases))
    )


def build_plan_retry_prompt(profile: dict[str, Any], n_cases: int,
                            previous: str, error: str) -> str:
    return (
        _template("plan_retry")
        .replace("{{profile}}", fence_untrusted(profile))
        .replace("{{cases}}", str(n_cases))
        .replace("{{distribution}}", _distribution(n_cases))
        .replace("{{previous}}", fence_untrusted(previous))
        .replace("{{error}}", fence_untrusted(error))
    )


def build_judge_prompt(case: Case, answer: str, error: str = "",
                       policy: dict[str, Any] | None = None,
                       checks: list[dict[str, Any]] | None = None,
                       profile_facts: dict[str, Any] | None = None) -> str:
    case_data = {
        "id": case.id,
        "question": case.question,
        "expected": case.expected,
        "rubric": case.rubric,
        "difficulty": case.difficulty,
        "weight": case.weight,
    }
    policy_text = json.dumps(policy, ensure_ascii=False, sort_keys=True) if policy else ""
    checks_text = json.dumps(checks, ensure_ascii=False, sort_keys=True) if checks else ""
    facts_text = json.dumps(profile_facts, ensure_ascii=False,
                            sort_keys=True) if profile_facts else ""
    return (
        _template("judge")
        .replace("{{case}}", fence_untrusted(case_data))
        .replace("{{profile_facts}}", fence_untrusted(facts_text))
        .replace("{{answer}}", fence_untrusted(answer))
        .replace("{{error}}", fence_untrusted(error))
        .replace("{{policy}}", fence_untrusted(policy_text))
        .replace("{{checks}}", fence_untrusted(checks_text))
        .replace("{{case_id}}", case.id)
    )


def _loads(text: str) -> Any:
    return json.loads(text)


_BARE_CHECK_RE = re.compile(
    r'("op"\s*:\s*"[^"]+"\s*,\s*)"([^"]*)"(\s*})')
_UNQUOTED_KEY_RE = re.compile(
    r'([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)"(\s*:)')
_UNQUOTED_KEY_PLAIN_RE = re.compile(
    r'([,{]\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*:)')


def _balance_json(raw: str) -> str:
    """Append missing closing braces/brackets to a truncated JSON object.

    LLM responses are sometimes cut off one character early (a missing final
    `}`). Repair only when the braces/brackets are otherwise well-formed; a
    mismatched or unclosed string is left untouched for the fallback parser.
    """
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in raw:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return raw
            opener = stack.pop()
            if (char == "}" and opener != "{") or (char == "]" and opener != "["):
                return raw
    if in_string or not stack:
        return raw
    closers = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    return raw + closers


def _repair_plan_json(raw: str) -> str:
    """Repair common model JSON glitches without discarding the plan.

    Two shapes seen in live runs:
      * {"op": "contains", "word"}          (missing `expected` key)
      * {"id":"q1",expected":"E"}           (missing opening quote on a key)
    """
    raw = _BARE_CHECK_RE.sub(r'\1"expected":"\2"\3', raw)
    raw = _UNQUOTED_KEY_RE.sub(r'\1"\2"\3', raw)
    return _UNQUOTED_KEY_PLAIN_RE.sub(r'\1"\2"\3', raw)


_CASE_KEYS = {"id", "difficulty", "question", "expected", "rubric"}


def _extract_case_objects(raw: str) -> list[dict[str, Any]]:
    """Find every case-like object, even outside a prematurely closed array."""
    decoder = json.JSONDecoder()
    cases: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _end = decoder.raw_decode(raw[index:])
        except ValueError:
            continue
        if not isinstance(value, dict) or not value.keys() >= _CASE_KEYS:
            continue
        key = (value.get("id"), value.get("question"))
        if key in seen:
            continue
        seen.add(key)
        cases.append(value)
    return cases


def _extract_field_cases(raw: str) -> list[dict[str, Any]]:
    """Recover cases whose fields were emitted at the top level.

    Seen in live runs:

        {"cases":[{...q1...}],"id":"q2",...,"checks":[...]}

    The first case is inside the array, the rest are bare key/value runs at the
    top level (sometimes with stray `]`). Split on case-id boundaries and read
    the fields between them with a tolerant decoder.
    """
    starts = []
    for match in re.finditer(r'"id"\s*:\s*"([A-Za-z][A-Za-z0-9_-]*)"', raw):
        case_id = match.group(1)
        if case_id.endswith("-check") or "-check-" in case_id:
            continue
        prefix = raw[:match.start()].rstrip()
        if prefix and prefix[-1] not in "{,[":
            continue
        starts.append(match)
    if not starts:
        return []
    decoder = json.JSONDecoder()
    cases: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(raw)
        block = raw[match.start():end]
        fields: dict[str, Any] = {}
        for key_match in re.finditer(
                r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', block):
            key = key_match.group(1)
            if key in fields:
                continue
            pos = key_match.end()
            while pos < len(block) and block[pos].isspace():
                pos += 1
            try:
                value, _ = decoder.raw_decode(block[pos:])
            except ValueError:
                continue
            fields[key] = value
        if not fields.keys() >= _CASE_KEYS:
            continue
        marker = (fields.get("id"), fields.get("question"))
        if marker in seen:
            continue
        seen.add(marker)
        cases.append(fields)
    return cases


def _coalesce_cases(data: dict[str, Any], raw: str) -> dict[str, Any]:
    """Recover cases the model left outside the top-level `cases` array.

    Models sometimes repeat the `cases` key or close the array early. Plain
    json.loads then keeps only a prefix (or the last key), silently dropping
    cases. Prefer the parsed array unless scanning finds more case objects.
    """
    extracted = _extract_case_objects(raw)
    field_cases = _extract_field_cases(raw)
    if field_cases and len(field_cases) >= len(extracted):
        extracted = field_cases
    if not extracted:
        return data
    existing = data.get("cases")
    if not isinstance(existing, list):
        existing = []
    if len(extracted) > len(existing):
        return {**data, "cases": extracted}
    return data


def parse_json_object(text: str) -> dict[str, Any]:
    """Accept bare JSON, fenced JSON, or JSON embedded in prose."""
    original = str(text or "").strip()
    if not original:
        raise PromptError("empty response (expected a JSON object)")
    repaired = _balance_json(_repair_plan_json(original))
    candidates = [original]
    if repaired != original:
        candidates.append(repaired)

    for raw in candidates:
        try:
            data = _loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict):
            return _coalesce_cases(data, raw)

    for raw in candidates:
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", raw,
                                 re.DOTALL | re.IGNORECASE):
            try:
                data = _loads(match.group(1).strip())
            except ValueError:
                continue
            if isinstance(data, dict):
                return _coalesce_cases(data, raw)

    decoder = json.JSONDecoder()
    for raw in candidates:
        for idx, char in enumerate(raw):
            if char != "{":
                continue
            try:
                data, _end = decoder.raw_decode(raw[idx:])
            except ValueError:
                continue
            if isinstance(data, dict):
                return _coalesce_cases(data, raw)
    for raw in candidates:
        salvaged = _extract_field_cases(raw)
        if salvaged:
            return {"cases": salvaged}
    raise PromptError("response does not contain a JSON object")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_case(item: Any, index: int) -> Case:
    if not isinstance(item, dict):
        raise PromptError(f"case {index}: must be an object")
    cid = str(item.get("id", "") or "").strip()
    if not cid or not _ID_RE.match(cid):
        raise PromptError(
            f"case {index}: id must start with a letter and use only letters, "
            f"digits, '_' or '-' (got {cid!r})"
        )
    difficulty = str(item.get("difficulty", "") or "").strip().lower()
    if difficulty not in DIFFICULTIES:
        raise PromptError(
            f"case {cid}: difficulty must be one of {list(DIFFICULTIES)} "
            f"(got {difficulty!r})"
        )
    question = str(item.get("question", "") or "").strip()
    expected = str(item.get("expected", "") or "").strip()
    rubric = str(item.get("rubric", "") or "").strip()
    if not question:
        raise PromptError(f"case {cid}: question must be non-empty")
    if not expected:
        raise PromptError(f"case {cid}: expected must be non-empty")
    if not rubric:
        raise PromptError(f"case {cid}: rubric must be non-empty")
    weight = item.get("weight", 1)
    if not _is_number(weight) or float(weight) <= 0:
        raise PromptError(f"case {cid}: weight must be a positive number")
    raw_checks = item.get("checks", [])
    if raw_checks is None:
        raw_checks = []
    if not isinstance(raw_checks, list):
        raise PromptError(f"case {cid}: checks must be a list")
    if len(raw_checks) > _MAX_CHECKS_PER_CASE:
        raise PromptError(
            f"case {cid}: too many checks ({len(raw_checks)} > {_MAX_CHECKS_PER_CASE})"
        )
    checks: list[dict[str, Any]] = []
    for pos, check in enumerate(raw_checks, 1):
        if not isinstance(check, dict):
            raise PromptError(f"case {cid}: check {pos} must be an object")
        op = str(check.get("op", "") or "").strip().lower()
        if op not in OPS:
            raise PromptError(f"case {cid}: check {pos} bad op {op!r}")
        if "expected" not in check:
            raise PromptError(f"case {cid}: check {pos} missing expected")
        expected_value = check.get("expected")
        if op == "regex":
            try:
                re.compile(str(expected_value))
            except re.error as exc:
                raise PromptError(f"case {cid}: check {pos} bad regex: {exc}") from exc
        if op in ("gte", "lte") and not _is_number(expected_value):
            raise PromptError(f"case {cid}: check {pos} {op} expected must be numeric")
        if op == "contains":
            raw_phrase = str(expected_value).strip().lower()
            phrase = _check_phrase(expected_value)
            brittle = (raw_phrase in _BRITTLE_CHECK_TOKENS
                       or not phrase
                       or (len(phrase.split()) == 1 and len(phrase) < 4))
            if brittle:
                # Drop brittle checks rather than failing the whole plan:
                # the judge still scores the case against expected/rubric.
                continue
        checks.append({
            "id": str(check.get("id", "") or f"{cid}-check-{pos}").strip(),
            "op": op,
            "expected": expected_value,
        })
    return Case(id=cid, question=question, expected=expected, rubric=rubric,
                difficulty=difficulty, weight=float(weight), checks=checks)


def validate_plan(payload: dict[str, Any], n_cases: int,
                  allowed_crafts: Any = None,
                  enforce_distribution: bool = True,
                  autonomy_modes: Any = None) -> list[Case]:
    if not isinstance(payload, dict):
        raise PromptError("plan must be a JSON object")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise PromptError("plan.cases must be a non-empty list")
    if len(raw_cases) != n_cases:
        raise PromptError(
            f"plan must contain exactly {n_cases} cases (got {len(raw_cases)})")
    cases = [_check_case(item, i) for i, item in enumerate(raw_cases, 1)]
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise PromptError(f"plan case ids must be unique: {ids}")
    counts = {d: sum(1 for c in cases if c.difficulty == d) for d in DIFFICULTIES}
    if enforce_distribution:
        if n_cases == 6:
            expected = {d: 2 for d in DIFFICULTIES}
            if counts != expected:
                raise PromptError(
                    f"plan must contain 2 easy, 2 mid and 2 hard cases (got {counts})"
                )
        elif n_cases >= 3:
            missing = [d for d, count in counts.items() if count == 0]
            if missing:
                raise PromptError(f"plan is missing difficulties: {missing}")
    if allowed_crafts is not None:
        allowed = {str(c or "").strip().lower()
                   for c in (allowed_crafts or []) if str(c or "").strip()}
        if allowed:
            for case in cases:
                mentioned = _mentioned_crafts(case.question, case.expected,
                                              case.rubric)
                foreign = [m for m in mentioned if m not in allowed]
                if foreign:
                    raise PromptError(
                        f"case {case.id}: mentions {foreign} craft, but the "
                        f"worker only has: {sorted(allowed)}")
    if autonomy_modes is not None:
        modes = [str(m or "").strip().lower()
                 for m in (autonomy_modes or []) if str(m or "").strip()]
        if modes:
            # Question + expected only: rubrics speak in partial-credit
            # tiers ("0.5 if only one mode is named") that are not factual
            # claims — checking them false-positives on good cases.
            for case in cases:
                claimed = _claimed_mode_counts(case.question, case.expected)
                wrong = [c for c in claimed if c != len(modes)]
                if wrong:
                    raise PromptError(
                        f"case {case.id}: claims {wrong} mode(s) but the "
                        f"worker has {len(modes)} ({sorted(modes)})")
    return cases


def validate_judgement(payload: dict[str, Any], case: Case) -> Judgement:
    if not isinstance(payload, dict):
        raise PromptError("judgement must be a JSON object")
    cid = str(payload.get("case_id", "") or "").strip()
    if cid != case.id:
        raise PromptError(f"judgement case_id {cid!r} does not match {case.id!r}")
    score = payload.get("score")
    if not _is_number(score) or not 0.0 <= float(score) <= 1.0:
        raise PromptError(f"judgement {cid}: score must be a number in [0, 1]")
    rationale = str(payload.get("rationale", "") or "").strip()
    if not rationale:
        raise PromptError(f"judgement {cid}: rationale must be non-empty")
    evidence = payload.get("evidence", "")
    if evidence is None:
        evidence = ""
    elif not isinstance(evidence, (dict, list)):
        evidence = str(evidence)
    return Judgement(case_id=cid, score=float(score),
                     rationale=rationale, evidence=evidence)
