"""P1/P2 shared-memory hardening regressions.

P1 — durable & bounded:
  * context budget on the injected session block (newest turns win)
  * ``session_report`` visibility (Mind's GET /agent/session hides the
    transcript; the shared /chats doc is the truth)
  * ``nak-worker --session`` read-only CLI

P2 — correctness/robustness:
  * merge no longer collapses legitimately-repeated turns
  * shared doc bounded by WORKER_SESSION_DOC_MAX_TURNS
  * cross-process (flock) merge guard on top of the thread lock
"""
import json
import threading
import time

import pytest

import nak_host.worker.engine.chat as chat_mod
import nak_host.worker.engine.memory as memory_mod
import nak_host.worker.engine.session as session_mod
from nak_host.worker import GenericWorker


class PersistentBrain:
    def __init__(self, answer="ack"):
        self.answer = answer
        self.docs = {}
        self.chat_calls = []
        self.saved = []

    def require_mind(self):
        pass

    def search(self, text, top_k=3):
        return []

    def decide(self, turns):
        return {"decisions": []}

    def remember(self, text, **kwargs):
        return {"ok": True}

    def save_chat(self, doc):
        self.saved.append(doc)
        self.docs[doc["id"]] = [dict(m) for m in doc.get("messages", [])]
        return {"ok": True, "id": doc["id"]}

    def get_chat(self, chat_id):
        if chat_id not in self.docs:
            raise ValueError(f"unknown chat {chat_id!r}")
        return {"chat": {"id": chat_id, "messages": list(self.docs[chat_id])}}

    def chat(self, prompt, messages=None, session_id=None):
        self.chat_calls.append({"prompt": prompt, "messages": messages,
                                "session_id": session_id})
        return {"answer": self.answer}


class CountingBrain(PersistentBrain):
    def chat(self, prompt, messages=None, session_id=None):
        self.chat_calls.append({"prompt": prompt, "messages": messages,
                                "session_id": session_id})
        return {"answer": f"ans{len(self.chat_calls)}"}


@pytest.fixture()
def brain():
    return PersistentBrain(answer="echo")


@pytest.fixture()
def vizhi(brain):
    return GenericWorker("vizhi", brain=brain)


# ---------- P1: context budget ----------

def test_context_budget_keeps_newest_drops_oldest(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_CONTEXT_CHARS", 120)
    brain = PersistentBrain(answer="echo")
    w = GenericWorker("vizhi", brain=brain)
    for i in range(6):
        w.chat(f"msg-{i}", session_id="t")
    rep = w.session_report("t")
    inj = rep["injected"]
    assert inj is not None
    assert "msg-5" in inj          # newest survives
    assert "msg-0" not in inj      # oldest dropped once budget spent
    assert rep["injected_chars"] <= 120 + 40  # header + one line slack


def test_context_budget_zero_keeps_turn_cap_only(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_CONTEXT_CHARS", 0)
    brain = PersistentBrain(answer="echo")
    w = GenericWorker("vizhi", brain=brain)
    for i in range(4):
        w.chat(f"m{i}", session_id="t")
    inj = w.session_report("t")["injected"]
    assert all(f"m{i}" in inj for i in range(4))  # under the 10-turn cap


# ---------- P1: session_report visibility ----------

def test_session_report_lists_turns_and_injection(vizhi, brain):
    brain.docs["team"] = [
        {"role": "user", "content": "codeword marigold"},
        {"role": "assistant", "content": "noted"},
    ]
    rep = vizhi.session_report("team")
    assert rep["session_id"] == "team"
    assert rep["message_count"] == 2
    assert [t["role"] for t in rep["turns"]] == ["user", "assistant"]
    assert rep["turns"][0]["preview"] == "codeword marigold"
    assert "marigold" in rep["injected"]
    assert rep["mind_owned"] is False


def test_session_report_empty_and_missing_are_safe(vizhi):
    assert vizhi.session_report("")["error"] == "empty session id"
    rep = vizhi.session_report("nope")
    assert rep["message_count"] == 0
    assert rep["error"]  # get_chat raised -> surfaced, not thrown


def test_session_report_no_brain():
    rep = GenericWorker("vizhi").session_report("t")
    assert rep["error"] == "no brain"


def test_cli_session_report(monkeypatch, capsys):
    import nak_host.wiring as wiring
    import nak_host.worker.__main__ as cli

    brain = PersistentBrain()
    brain.docs["cli_sess"] = [{"role": "user", "content": "hi there"}]
    monkeypatch.setattr(wiring, "make_brain", lambda *a, **k: brain)
    rc = cli.main(["--worker", "vizhi", "--session", "cli_sess", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "cli_sess"
    assert out["message_count"] == 1


# ---------- P2: doc bound ----------

def test_doc_is_trimmed_to_max_turns(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_DOC_MAX_TURNS", 6)
    brain = CountingBrain()
    w = GenericWorker("vizhi", brain=brain)
    for i in range(5):
        w.chat(f"turn-{i}", session_id="t")
    doc = brain.docs["t"]
    assert len(doc) == 6                       # bounded
    assert doc[-1]["content"] == "ans5"        # newest kept
    assert all("turn-0" != m["content"] for m in doc)  # oldest trimmed


# ---------- P2: cross-process merge guard ----------

def test_merge_guard_serializes_threads():
    state = {"inside": 0, "max": 0}

    def work():
        for _ in range(30):
            with session_mod._session_merge_guard("g"):
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
                time.sleep(0)
                state["inside"] -= 1

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1  # never two writers at once


def test_file_lock_creates_and_releases(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "WORKER_SESSION_LOCK_DIR", str(tmp_path))
    with session_mod._session_file_lock("abc"):
        pass
    assert list(tmp_path.glob("nak-sess-*.lock"))


def test_file_lock_off_is_noop(monkeypatch):
    monkeypatch.setattr(session_mod, "WORKER_SESSION_LOCK_DIR", "off")
    with session_mod._session_merge_guard("z"):
        pass  # no error, thread lock still applies


# ---------- P3: read-back verification ----------

def test_merge_verifies_saved_turn(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_RETRIES", 2)
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_BACKOFF", 0)
    brain = PersistentBrain(answer="echo")
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("one", session_id="t")
    assert out["memory"]["merged"] is True
    assert out["memory"]["verified"] is True
    assert out["memory"]["attempts"] == 1


def test_merge_retries_when_readback_is_stale(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_RETRIES", 2)
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_BACKOFF", 0)

    class StaleAfterSaveBrain(PersistentBrain):
        """One get_chat after a save returns the pre-save (stale) doc."""

        def __init__(self, answer="ack"):
            super().__init__(answer=answer)
            self._stale_once = False

        def save_chat(self, doc):
            result = super().save_chat(doc)
            self._stale_once = True
            return result

        def get_chat(self, chat_id):
            if self._stale_once:
                self._stale_once = False
                return {"chat": {"id": chat_id, "messages": []}}
            return super().get_chat(chat_id)

    brain = StaleAfterSaveBrain(answer="echo")
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("one", session_id="t")
    assert out["memory"]["merged"] is True
    assert out["memory"]["verified"] is True
    assert out["memory"]["attempts"] >= 2
    assert [m["content"] for m in brain.docs["t"]] == ["one", "echo"]


def test_merge_reports_unverified_when_readback_never_settles(monkeypatch):
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_RETRIES", 2)
    monkeypatch.setattr(memory_mod, "WORKER_SESSION_SAVE_BACKOFF", 0)

    class AlwaysStaleBrain(PersistentBrain):
        def get_chat(self, chat_id):
            return {"chat": {"id": chat_id, "messages": []}}

    brain = AlwaysStaleBrain(answer="echo")
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("one", session_id="t")
    assert out["memory"]["merged"] is True
    assert out["memory"]["verified"] is False
    assert out["memory"]["attempts"] == 3


# ---------- P1 (opt-in): durable Mind session promotion ----------

class SessionBrain(PersistentBrain):
    """PersistentBrain + Mind session APIs (create/list/get)."""

    def __init__(self, answer="ack"):
        super().__init__(answer=answer)
        self.mind_sessions = {}
        self._n = 0

    def create_session(self, metadata=None, user=None):
        self._n += 1
        mid = f"mind{self._n}"
        self.mind_sessions[mid] = {"session_id": mid, "status": "active",
                                   "metadata": dict(metadata or {})}
        return dict(self.mind_sessions[mid])

    def list_sessions(self, user=None):
        return [dict(s) for s in self.mind_sessions.values()]

    def get_session(self, session_id, user=None):
        s = self.mind_sessions.get(session_id)
        if not s:
            raise ValueError(f"unknown session {session_id!r}")
        return dict(s)


def test_promotion_off_creates_nothing(monkeypatch):
    monkeypatch.setattr(chat_mod, "WORKER_SESSION_PROMOTE", False)
    brain = SessionBrain()
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hi", session_id="harness_key")
    assert brain.mind_sessions == {}                      # nothing promoted
    assert all(c["session_id"] is None for c in brain.chat_calls)  # client-owned
    assert out["memory"]["merged"] is True


def test_promotion_creates_and_forwards_mind_session(monkeypatch):
    monkeypatch.setattr(chat_mod, "WORKER_SESSION_PROMOTE", True)
    brain = SessionBrain()
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hi", session_id="harness_key")
    assert len(brain.mind_sessions) == 1
    (mid,) = brain.mind_sessions
    assert brain.mind_sessions[mid]["metadata"]["harness_session"] == "harness_key"
    assert [c["session_id"] for c in brain.chat_calls] == [mid]  # forwarded
    assert out["memory"]["session_id"] == "harness_key"          # shared key stays
    assert out["memory"]["merged"] is True                       # mirror maintained
    assert brain.docs["harness_key"][0]["content"] == "hi"       # shared doc written


def test_promotion_reuses_existing_mapping(monkeypatch):
    monkeypatch.setattr(chat_mod, "WORKER_SESSION_PROMOTE", True)
    brain = SessionBrain()
    vizhi = GenericWorker("vizhi", brain=brain)
    arivu = GenericWorker("arivu", brain=brain)
    vizhi.chat("one", session_id="team")
    arivu.chat("two", session_id="team")
    assert len(brain.mind_sessions) == 1  # both workers map to the same session
    (mid,) = brain.mind_sessions
    assert [c["session_id"] for c in brain.chat_calls] == [mid, mid]


def test_promotion_falls_back_without_session_apis(monkeypatch):
    monkeypatch.setattr(chat_mod, "WORKER_SESSION_PROMOTE", True)
    brain = PersistentBrain()  # no create/list/get_session
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hi", session_id="harness_key")
    assert all(c["session_id"] is None for c in brain.chat_calls)  # client-owned
    assert out["memory"]["merged"] is True


def test_ensure_mind_session_accepts_live_harness_key(monkeypatch):
    brain = SessionBrain()
    brain.mind_sessions["live"] = {"session_id": "live", "status": "active",
                                   "metadata": {}}
    w = GenericWorker("vizhi", brain=brain)
    assert w.ensure_mind_session("live") == "live"      # already a Mind session
    assert w.ensure_mind_session("") is None
    assert GenericWorker("vizhi").ensure_mind_session("x") is None  # no brain


def test_concurrent_processes_style_merge_loses_no_turns(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "WORKER_SESSION_LOCK_DIR", str(tmp_path))

    class Shared:
        """Simulates a slower-IPC store; guard must keep writes ordered."""
        def __init__(self):
            self.docs = {}

        def get_chat(self, cid):
            if cid not in self.docs:
                raise ValueError("none")
            return {"chat": {"id": cid, "messages": list(self.docs[cid])}}

        def save_chat(self, doc):
            self.docs[doc["id"]] = [dict(m) for m in doc["messages"]]

    shared = Shared()
    # Each thread carries its own read/modify/write through the guard.
    def run(tag):
        for i in range(8):
            with session_mod._session_merge_guard("race"):
                try:
                    existing = shared.get_chat("race")["chat"]["messages"]
                except ValueError:
                    existing = []
                existing += [{"role": "user", "content": f"{tag}{i}"},
                             {"role": "assistant", "content": "ok"}]
                shared.save_chat({"id": "race", "messages": existing})

    threads = [threading.Thread(target=run, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    users = [m["content"] for m in shared.docs["race"] if m["role"] == "user"]
    assert sorted(users) == sorted([f"a{i}" for i in range(8)]
                                   + [f"b{i}" for i in range(8)])
