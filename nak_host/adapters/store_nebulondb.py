"""NebulonDB-backed TaskStorePort (append-only; inserts + reads only).

One `cosmos` corpus (default ``nak_tasks``) with four segments: ``tasks``,
``task_runs`` (one snapshot doc per transition), ``task_events`` (one doc per
event) and ``task_results``. Verified against NebulonDB's routes: for cosmos
segments ``add_records``/``load_segment`` (insert), ``get_data`` (read) and
``delete_records`` work, but ``update_records`` is broken for cosmos (it calls
``update_vec``, which ``ComosDBManager`` does not have) and ``get_record``
explicitly rejects cosmos. This adapter therefore NEVER calls update or
single-get: every write is an insert, every read filters ``get_data``.

Guarantees without server transactions (single-writer adapter):

* event-first ordering + idempotent replay: the event doc is inserted before
  the run snapshot; a crash between the two is healed by replay, which
  rebuilds any snapshot the events imply;
* optimistic concurrency: read-check-insert under the process lock;
* idempotency: ``idempotency_key`` checked under the same lock, deterministic
  ``event_id`` values make retries safe to re-issue.

Doc-shape rules (from ``add_records`` cosmos behavior): never use the reserved
keys ``text``/``lang``/``type``/``created_at``/``label``/``id``/``_id`` as
field names, keep values JSON-scalar, and carry nested structures as
JSON-string fields.
"""

from __future__ import annotations

import json
import threading
import time

from collections.abc import Mapping
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from nak.domain.errors import ConcurrencyError, DuplicateTask, TaskNotFound
from nak.domain.permission import BlockReason
from nak.domain.result import Result, ResultStatus
from nak.domain.state import TaskState, ensure_transition, is_pending_state
from nak.domain.task import Task, TaskRun

CORPUS_DEFAULT = "nak_tasks"
SEG_TASKS = "tasks"
SEG_RUNS = "task_runs"
SEG_EVENTS = "task_events"
SEG_RESULTS = "task_results"
_SEGMENTS = (SEG_TASKS, SEG_RUNS, SEG_EVENTS, SEG_RESULTS)

_RUN_FIELDS = frozenset({"assigned_worker", "attempt", "block_reason", "result"})
_TRANSIENT_HTTP = frozenset({429, 502, 503, 504})


class NDBError(RuntimeError):
    """NebulonDB transport/API failure (never carries credentials)."""


class NDBAuthError(NDBError):
    """NebulonDB rejected the credentials (401). Refreshable when a
    credentials resolver is configured; final otherwise."""


class _NDBClient:
    """Minimal NebulonDB HTTP client (Basic auth + retries, no other deps)."""

    def __init__(self, base_url: str, user: str, password: str,
                 timeout: float = 30.0, max_retries: int = 2,
                 retry_backoff: float = 1.0,
                 on_unauthorized: Any = None) -> None:
        self._base = str(base_url or "").rstrip("/")
        self._auth = HTTPBasicAuth(str(user or ""), str(password or ""))
        self._timeout = float(timeout) if float(timeout) > 0 else 30.0
        self._retries = max(0, int(max_retries))
        self._backoff = max(0.0, float(retry_backoff))
        self._on_unauthorized = on_unauthorized

    def set_auth(self, user: str, password: str) -> None:
        """Replace the Basic credentials (rotation without rebuilding)."""
        self._auth = HTTPBasicAuth(str(user or ""), str(password or ""))

    def set_base(self, base_url: str) -> None:
        """Replace the base URL (backend moved without rebuilding)."""
        if str(base_url or "").strip():
            self._base = str(base_url).rstrip("/")

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST and return the StandardResponse body (transport errors raise).

        A 401 triggers ``on_unauthorized`` once (credential refresh) before
        giving up with NDBAuthError.
        """
        url = self._base + path
        tries = 1 + self._retries + (1 if self._on_unauthorized is not None else 0)
        last: Any = None
        reauthed = False
        for i in range(tries):
            try:
                resp = requests.post(url, json=payload, auth=self._auth,
                                     timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last = exc
            else:
                if resp.status_code == 401:
                    if self._on_unauthorized is not None and not reauthed:
                        reauthed = True
                        self._on_unauthorized()
                        continue
                    raise NDBAuthError(
                        "nebulondb authentication failed (check user/password)")
                if resp.status_code in _TRANSIENT_HTTP:
                    last = RuntimeError(f"HTTP {resp.status_code}")
                elif resp.status_code >= 400:
                    raise NDBError(f"nebulondb HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    try:
                        body = resp.json()
                    except ValueError as exc:
                        raise NDBError("nebulondb returned non-JSON") from exc
                    return body if isinstance(body, dict) else {"success": False,
                                                                "message": "bad envelope"}
            if i + 1 < tries:
                time.sleep(self._backoff * (2 ** i))
        raise NDBError(f"nebulondb request failed after {tries} tries: {last}")


def _dump(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False)


def _jsonable(value: Any) -> Any:
    """Normalize to JSON scalars/lists/dicts (tuples->lists, sets->lists).

    Uses collections.abc.Mapping (not dict): domain values arrive
    deep-frozen, and MappingProxyType is a Mapping but NOT a dict subclass.
    """
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (set, frozenset)):
        try:
            return sorted((_jsonable(v) for v in value), key=repr)
        except TypeError:
            return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _load(raw: Any, fallback: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return fallback
    try:
        return json.loads(raw)
    except ValueError:
        return fallback


def _block_to_json(block: BlockReason | None) -> str:
    if block is None:
        return ""
    return _dump({"code": block.code, "detail": block.detail,
                  "resumable": bool(block.resumable),
                  "recovery": _jsonable(dict(block.recovery))})


def _block_from_json(raw: Any) -> BlockReason | None:
    data = _load(raw, None)
    if not isinstance(data, dict):
        return None
    try:
        return BlockReason(code=str(data.get("code", "")), detail=str(data.get("detail", "")),
                           resumable=bool(data.get("resumable", False)),
                           recovery=data.get("recovery") or {})
    except Exception:
        return None


def _result_to_json(result: Result | None) -> str:
    if result is None:
        return ""
    return _dump({"worker": result.worker, "status": result.status.value,
                  "summary": result.summary,
                  "files_changed": list(result.files_changed),
                  "commands_run": list(result.commands_run),
                  "evidence": _jsonable(dict(result.evidence)),
                  "blockers": list(result.blockers),
                  "needs_approval": bool(result.needs_approval)})


def _result_from_json(task_id: str, raw: Any) -> Result | None:
    data = _load(raw, None)
    if not isinstance(data, dict):
        return None
    try:
        return Result(task_id=task_id, worker=str(data.get("worker", "")),
                      status=ResultStatus(str(data.get("status", ""))),
                      summary=str(data.get("summary", "")),
                      files_changed=tuple(data.get("files_changed") or ()),
                      commands_run=tuple(data.get("commands_run") or ()),
                      evidence=data.get("evidence") or {},
                      blockers=tuple(data.get("blockers") or ()),
                      needs_approval=bool(data.get("needs_approval", False)))
    except Exception:
        return None


class NebulonDBTaskStore:
    """Append-only TaskStorePort on NebulonDB (single writer per process)."""

    def __init__(self, base_url: str = "http://localhost:6969/api/NebulonDB",
                 user: str = "", password: str = "",
                 corpus: str = CORPUS_DEFAULT, timeout: float = 30.0,
                 max_retries: int = 2, retry_backoff: float = 1.0,
                 client: Any = None, credentials_resolver: Any = None) -> None:
        # Explicit (non-empty) fields always win and are never overwritten by
        # the resolver — neither at fill time nor on 401-refresh.
        self._explicit = {"base_url": bool(str(base_url or "").strip()),
                          "user": bool(str(user or "").strip()),
                          "password": bool(str(password or "").strip())}
        self._auth_state = {"base_url": str(base_url or "").rstrip("/"),
                            "user": str(user or ""),
                            "password": str(password or "")}
        self._resolver = credentials_resolver
        if client is not None:
            self._client = client
        else:
            self._client = _NDBClient(
                base_url, user, password, timeout=timeout,
                max_retries=max_retries, retry_backoff=retry_backoff,
                on_unauthorized=self._reauth if credentials_resolver is not None else None)
        self._corpus = str(corpus or "").strip() or CORPUS_DEFAULT
        self._lock = threading.Lock()
        self._ready = False
        self._cache: dict[str, list[dict[str, Any]]] = {s: [] for s in _SEGMENTS}

    def _push_auth(self) -> None:
        """Push the effective credentials to the client (setters optional)."""
        set_base = getattr(self._client, "set_base", None)
        if callable(set_base) and self._auth_state["base_url"]:
            set_base(self._auth_state["base_url"])
        set_auth = getattr(self._client, "set_auth", None)
        if callable(set_auth):
            set_auth(self._auth_state["user"], self._auth_state["password"])

    def _apply_resolved(self, creds: Any) -> None:
        """Merge resolved credentials into the effective set (explicit wins)."""
        if not self._explicit["base_url"] and str(creds.base_url or "").strip():
            self._auth_state["base_url"] = str(creds.base_url).rstrip("/")
        if not self._explicit["user"]:
            self._auth_state["user"] = str(creds.username or "")
        if not self._explicit["password"]:
            self._auth_state["password"] = str(creds.password or "")
        self._push_auth()

    def _fill_missing_locked(self) -> None:
        """Fill empty credential fields from the resolver, then sync client."""
        if self._resolver is not None and not all(self._explicit.values()):
            self._apply_resolved(self._resolver.resolve())
            return
        self._push_auth()

    def _reauth(self) -> None:
        """Refresh resolved credentials after a 401 (explicit fields kept).

        Runs inside the client's retry path; takes no store lock (the caller
        already holds it) — only the resolver's own lock is used.
        """
        if self._resolver is None:
            raise NDBAuthError(
                "nebulondb authentication failed (no credentials resolver configured)")
        self._apply_resolved(self._resolver.refresh())

    # -- transport ---------------------------------------------------- #
    def _seg_payload(self, segment: str, extra: dict[str, Any]) -> dict[str, Any]:
        payload = {"corpus_name": self._corpus, "segment_name": segment,
                   "ndb_type": "cosmos"}
        payload.update(extra)
        return payload

    def _ensure_locked(self) -> None:
        if self._ready:
            return
        self._fill_missing_locked()
        body = self._client.post("/corpus/create_corpus",
                                 {"corpus_name": self._corpus, "ndb_type": "cosmos"})
        if not body.get("success") and not body.get("exists"):
            raise NDBError(f"cannot bootstrap corpus {self._corpus!r}: "
                           f"{str(body.get('message', 'unknown'))[:200]}")
        for seg in _SEGMENTS:
            self._cache[seg] = self._read_segment(seg)
        self._ready = True

    def _read_segment(self, segment: str) -> list[dict[str, Any]]:
        body = self._client.post("/segment/get_data",
                                 self._seg_payload(segment, {}))
        if not body.get("success"):
            raise NDBError(f"cannot read segment {segment!r}: "
                           f"{str(body.get('message', 'unknown'))[:200]}")
        data = body.get("data") or {}
        records = data.get("records") or []
        return [r for r in records if isinstance(r, dict)]

    def refresh(self) -> None:
        """Reload every segment from the server (picks up other writers)."""
        with self._lock:
            self._ensure_locked()
            for seg in _SEGMENTS:
                self._cache[seg] = self._read_segment(seg)

    def _insert_locked(self, segment: str, metadata: dict[str, Any],
                       text: str) -> dict[str, Any]:
        body = self._client.post(
            "/segment/add_records",
            self._seg_payload(segment, {"records": [{"text": text,
                                                     "metadata": dict(metadata)}]}))
        if not body.get("success"):
            raise NDBError(f"cannot insert into {segment!r}: "
                           f"{str(body.get('message', 'unknown'))[:200]}")
        data = body.get("data") or {}
        ids = data.get("record_ids") or []
        doc = dict(metadata)
        doc["text"] = text
        if ids:
            doc["_id"] = ids[0]
        self._cache[segment].append(doc)
        return doc

    # -- doc mapping -------------------------------------------------- #
    @staticmethod
    def _task_doc(task: Task) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "idempotency_key": task.idempotency_key,
            "goal": task.goal,
            "workspace": task.workspace,
            "max_steps": int(task.max_steps),
            "timeout": int(task.timeout),
            "allowed_tools_json": _dump(sorted(task.allowed_tools)),
            "payload_json": _dump({"inputs": _jsonable(dict(task.inputs)),
                                   "acceptance": list(task.acceptance)}),
            "kind": "task",
        }

    @staticmethod
    def _task_text(task: Task) -> str:
        return f"task {task.id}: {task.goal[:120]}"

    @staticmethod
    def _run_fields(task_id: str, state: TaskState, version: int,
                    attempt: int, assigned_worker: str | None,
                    block: BlockReason | None,
                    result: Result | None) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "state": state.value,
            "version": int(version),
            "attempt": int(attempt),
            "assigned_worker": assigned_worker or "",
            "block_json": _block_to_json(block),
            "result_json": _result_to_json(result),
            "kind": "run",
        }

    @staticmethod
    def _task_from_doc(doc: dict[str, Any]) -> Task:
        payload = _load(doc.get("payload_json"), {}) or {}
        return Task(
            id=str(doc.get("task_id", "")),
            idempotency_key=str(doc.get("idempotency_key", "")),
            goal=str(doc.get("goal", "")),
            inputs=payload.get("inputs") or {},
            acceptance=tuple(payload.get("acceptance") or ()),
            workspace=str(doc.get("workspace", "")),
            max_steps=int(doc.get("max_steps", 3)),
            timeout=int(doc.get("timeout", 60)),
            allowed_tools=set(_load(doc.get("allowed_tools_json"), []) or []),
        )

    @staticmethod
    def _run_from_doc(doc: dict[str, Any]) -> TaskRun:
        assigned = str(doc.get("assigned_worker", "") or "").strip() or None
        return TaskRun(
            task_id=str(doc.get("task_id", "")),
            state=TaskState(str(doc.get("state", ""))),
            version=int(doc.get("version", 0)),
            assigned_worker=assigned,
            attempt=int(doc.get("attempt", 1)),
            block_reason=_block_from_json(doc.get("block_json")),
            result=_result_from_json(str(doc.get("task_id", "")),
                                     doc.get("result_json")),
        )

    # -- cache queries (lock held) ------------------------------------ #
    def _find_task_doc(self, task_id: str) -> dict[str, Any] | None:
        for doc in self._cache[SEG_TASKS]:
            if str(doc.get("task_id", "")) == task_id:
                return doc
        return None

    def _find_task_doc_by_key(self, key: str) -> dict[str, Any] | None:
        for doc in self._cache[SEG_TASKS]:
            if str(doc.get("idempotency_key", "")) == key:
                return doc
        return None

    def _runs_for(self, task_id: str) -> list[dict[str, Any]]:
        docs = [d for d in self._cache[SEG_RUNS]
                if str(d.get("task_id", "")) == task_id]
        docs.sort(key=lambda d: int(d.get("version", 0)))
        return docs

    def _events_for(self, task_id: str) -> list[dict[str, Any]]:
        docs = [d for d in self._cache[SEG_EVENTS]
                if str(d.get("task_id", "")) == task_id]
        docs.sort(key=lambda d: int(d.get("seq", 0)))
        return docs

    def _results_for(self, task_id: str) -> list[dict[str, Any]]:
        return [d for d in self._cache[SEG_RESULTS]
                if str(d.get("task_id", "")) == task_id]

    def _latest_result(self, task_id: str) -> Result | None:
        docs = self._results_for(task_id)
        if not docs:
            return None
        best = max(docs, key=lambda d: (int(d.get("attempt", 1)),
                                        int(d.get("_id", 0)) if isinstance(d.get("_id"), int) else 0))
        try:
            return Result(
                task_id=task_id, worker=str(best.get("worker", "")),
                status=ResultStatus(str(best.get("status", ""))),
                summary=str(best.get("summary", "")),
                files_changed=tuple(_load(best.get("files_json"), []) or ()),
                commands_run=tuple(_load(best.get("commands_json"), []) or ()),
                evidence=_load(best.get("evidence_json"), {}) or {},
                blockers=tuple(_load(best.get("blockers_json"), []) or ()),
                needs_approval=bool(best.get("needs_approval", False)))
        except Exception:
            return None

    def _event_id(self, task_id: str, seq: int) -> str:
        return f"{task_id}:{seq:06d}"

    def _heal_locked(self, task_id: str) -> None:
        """Insert any run snapshot the events imply but storage lacks.

        Matches on the embedded ``run`` payload (written by every
        transition), not on the event type — transitions may carry custom
        types like ``task.planned``.
        """
        changed = []
        for event in self._events_for(task_id):
            payload = _load(event.get("payload_json"), {}) or {}
            run_state = payload.get("run") if isinstance(payload, dict) else None
            if isinstance(run_state, dict):
                changed.append(run_state)
        if not changed:
            return
        snaps = self._runs_for(task_id)
        have = {int(d.get("version", -1)) for d in snaps}
        for run_state in changed:
            version = int(run_state.get("version", -1))
            if version < 0 or version in have:
                continue
            snapshot = {
                "task_id": task_id,
                "state": str(run_state.get("to_state", "")),
                "version": version,
                "attempt": int(run_state.get("attempt", 1)),
                "assigned_worker": str(run_state.get("assigned_worker", "") or ""),
                "block_json": str(run_state.get("block_json", "") or ""),
                "result_json": str(run_state.get("result_json", "") or ""),
                "kind": "run",
            }
            self._insert_locked(SEG_RUNS, snapshot,
                                f"run {task_id} v{version} (healed)")
            have.add(version)

    def _heal_create_locked(self, task_doc: dict[str, Any]) -> None:
        """Repair a partially written create (task doc without run/event)."""
        task_id = str(task_doc.get("task_id", ""))
        if not self._runs_for(task_id):
            self._insert_locked(SEG_RUNS, self._run_fields(
                task_id, TaskState.CREATED, 0, 1, None, None, None),
                f"run {task_id} v0 created (healed)")
        if not self._events_for(task_id):
            self._insert_locked(SEG_EVENTS, {
                "event_id": self._event_id(task_id, 1), "task_id": task_id,
                "seq": 1, "event_type": "task.created",
                "payload_json": _dump({"goal": str(task_doc.get("goal", ""))}),
                "kind": "event",
            }, f"task {task_id} created (healed)")

    # -- TaskStorePort ------------------------------------------------ #
    def create_task(self, task: Task) -> TaskRun:
        with self._lock:
            self._ensure_locked()
            if self._find_task_doc_by_key(task.idempotency_key) is not None:
                raise DuplicateTask(
                    f"task with idempotency key {task.idempotency_key!r} already exists")
            if self._find_task_doc(task.id) is not None:
                raise DuplicateTask(f"task {task.id!r} already exists")
            self._insert_locked(SEG_TASKS, self._task_doc(task),
                                self._task_text(task))
            self._insert_locked(SEG_RUNS, self._run_fields(
                task.id, TaskState.CREATED, 0, 1, None, None, None),
                f"run {task.id} v0 created")
            self._insert_locked(SEG_EVENTS, {
                "event_id": self._event_id(task.id, 1), "task_id": task.id,
                "seq": 1, "event_type": "task.created",
                "payload_json": _dump({"goal": task.goal}), "kind": "event",
            }, f"task {task.id} created")
            return TaskRun(task_id=task.id, state=TaskState.CREATED,
                           version=0, assigned_worker=None, attempt=1)

    def get_task(self, task_id: str) -> tuple[Task, TaskRun] | None:
        with self._lock:
            self._ensure_locked()
            doc = self._find_task_doc(task_id)
            if doc is None:
                return None
            self._heal_create_locked(doc)
            self._heal_locked(task_id)
            snaps = self._runs_for(task_id)
            if not snaps:
                return None
            run = self._run_from_doc(snaps[-1])
            if run.result is None:
                latest = self._latest_result(task_id)
                if latest is not None:
                    run = TaskRun(task_id=run.task_id, state=run.state,
                                  version=run.version,
                                  assigned_worker=run.assigned_worker,
                                  attempt=run.attempt,
                                  block_reason=run.block_reason, result=latest)
            return self._task_from_doc(doc), run

    def get_task_by_idempotency(self, idempotency_key: str) -> tuple[Task, TaskRun] | None:
        with self._lock:
            self._ensure_locked()
            doc = self._find_task_doc_by_key(str(idempotency_key or ""))
            if doc is None:
                return None
            task_id = str(doc.get("task_id", ""))
        found = self.get_task(task_id)
        return found

    def list_pending(self) -> list[TaskRun]:
        with self._lock:
            self._ensure_locked()
            seen: dict[str, dict[str, Any]] = {}
            for doc in self._cache[SEG_RUNS]:
                key = str(doc.get("task_id", ""))
                prev = seen.get(key)
                if prev is None or int(doc.get("version", 0)) > int(prev.get("version", 0)):
                    seen[key] = doc
            out = []
            for doc in seen.values():
                try:
                    run = self._run_from_doc(doc)
                except Exception:
                    continue
                if is_pending_state(run.state):
                    out.append(run)
            return out

    def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._ensure_locked()
            if self._find_task_doc(task_id) is None:
                raise TaskNotFound(f"unknown task {task_id!r}")
            seq = len(self._events_for(task_id)) + 1
            payload = dict(event)
            event_type = str(payload.pop("type", "task.event"))
            self._insert_locked(SEG_EVENTS, {
                "event_id": self._event_id(task_id, seq), "task_id": task_id,
                "seq": seq, "event_type": event_type,
                "payload_json": _dump(_jsonable(payload)), "kind": "event",
            }, f"task {task_id} event {event_type}")

    def transition(self, task_id: str, expected_version: int,
                   to_state: TaskState, event: dict[str, Any],
                   **fields: Any) -> TaskRun:
        unknown = [k for k in fields if k not in _RUN_FIELDS]
        if unknown:
            raise ValueError(f"unknown run fields: {unknown}")
        if not isinstance(to_state, TaskState):
            try:
                to_state = TaskState(str(to_state))
            except ValueError as exc:
                raise ValueError(f"invalid target state {to_state!r}") from exc
        with self._lock:
            self._ensure_locked()
            if self._find_task_doc(task_id) is None:
                raise TaskNotFound(f"unknown task {task_id!r}")
            self._heal_locked(task_id)
            snaps = self._runs_for(task_id)
            if not snaps:
                raise TaskNotFound(f"task {task_id!r} has no run snapshots")
            current = self._run_from_doc(snaps[-1])
            if current.version != int(expected_version):
                raise ConcurrencyError(task_id, int(expected_version),
                                       current.version)
            ensure_transition(task_id, current.state, to_state)
            assigned = fields.get("assigned_worker", current.assigned_worker)
            attempt = int(fields.get("attempt", current.attempt))
            block = fields.get("block_reason", current.block_reason)
            result = fields.get("result", current.result)
            version = current.version + 1
            payload = {"from_state": current.state.value,
                       "to_state": to_state.value, "version": version,
                       "attempt": attempt,
                       "assigned_worker": assigned or "",
                       "block_json": _block_to_json(block),
                       "result_json": _result_to_json(result)}
            merged = dict(event)
            merged_type = str(merged.pop("type", "task.state_changed"))
            seq = len(self._events_for(task_id)) + 1
            event_id = self._event_id(task_id, seq)
            if any(str(e.get("event_id", "")) == event_id
                   for e in self._events_for(task_id)):
                return current  # idempotent replay: already applied
            self._insert_locked(SEG_EVENTS, {
                "event_id": event_id, "task_id": task_id, "seq": seq,
                "event_type": merged_type,
                "payload_json": _dump({**_jsonable(merged), "run": payload}),
                "kind": "event",
            }, f"task {task_id} {current.state.value}->{to_state.value}")
            self._insert_locked(SEG_RUNS, self._run_fields(
                task_id, to_state, version, attempt, assigned, block, result),
                f"run {task_id} v{version} {to_state.value}")
            return TaskRun(task_id=task_id, state=to_state, version=version,
                           assigned_worker=assigned, attempt=attempt,
                           block_reason=block, result=result)

    def save_result(self, task_id: str, result: Result) -> None:
        with self._lock:
            self._ensure_locked()
            if self._find_task_doc(task_id) is None:
                raise TaskNotFound(f"unknown task {task_id!r}")
            snaps = self._runs_for(task_id)
            attempt = max([int(d.get("attempt", 1)) for d in snaps] or [1])
            for doc in self._results_for(task_id):
                if int(doc.get("attempt", 1)) == attempt:
                    raise DuplicateTask(
                        f"result for task {task_id!r} attempt {attempt} already saved")
            self._insert_locked(SEG_RESULTS, {
                "task_id": task_id, "attempt": attempt,
                "worker": result.worker, "status": result.status.value,
                "summary": result.summary,
                "needs_approval": bool(result.needs_approval),
                "files_json": _dump(list(result.files_changed)),
                "commands_json": _dump(list(result.commands_run)),
                "evidence_json": _dump(_jsonable(dict(result.evidence))),
                "blockers_json": _dump(list(result.blockers)),
                "kind": "result",
            }, f"result {task_id} attempt {attempt} {result.status.value}")

    def recover_incomplete(self) -> list[TaskRun]:
        with self._lock:
            self._ensure_locked()
            for seg in _SEGMENTS:
                self._cache[seg] = self._read_segment(seg)
            stuck: list[str] = []
            for doc in self._cache[SEG_RUNS]:
                if str(doc.get("state", "")) != TaskState.RUNNING.value:
                    continue
                task_id = str(doc.get("task_id", ""))
                latest = max(
                    (d for d in self._runs_for(task_id)),
                    key=lambda d: int(d.get("version", 0)))
                if latest is doc:
                    stuck.append(task_id)
        recovered = []
        for task_id in stuck:
            found = self.get_task(task_id)
            if found is None:
                continue
            _, run = found
            if run.state != TaskState.RUNNING:
                continue
            reason = BlockReason(
                code="interrupted", detail="recovered on startup",
                resumable=False,
                recovery={"state": TaskState.RUNNING.value,
                          "version": run.version,
                          "worker": run.assigned_worker or ""})
            try:
                recovered.append(self.transition(
                    task_id, run.version, TaskState.BLOCKED,
                    {"type": "task.interrupted"}, block_reason=reason))
            except (ConcurrencyError, TaskNotFound):
                continue
        return recovered
