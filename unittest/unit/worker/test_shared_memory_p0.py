"""P0 shared-memory regressions: identity prompt placement + session merge.

Covers the two defects found in the cross-worker memory audit:
  1. The rendered identity prompt was sent as `text`, so Mind persisted it
     as the user turn and the 400-char session snippet dropped the real ask.
  2. Session docs were written turn-by-turn (or not at all), and the
     newest-wins chat store silently lost earlier teammates' turns.
"""
import threading

import pytest

from nak.worker import GenericWorker
from nak.worker.engine.session import _strip_prompt_dump


class PersistentBrain:
    """Fake brain whose chat docs actually persist (save_chat -> get_chat)."""

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
        if self.answer == "echo":
            return {"answer": f"ack: {prompt}"}
        return {"answer": self.answer}


@pytest.fixture()
def brain():
    return PersistentBrain()


@pytest.fixture()
def vizhi(brain):
    return GenericWorker("vizhi", brain=brain)


@pytest.fixture()
def arivu(brain):
    return GenericWorker("arivu", brain=brain)


# ---------- fix 1: identity prompt is not the user turn ----------

def test_text_is_utterance_and_identity_is_tool_message(vizhi, brain):
    vizhi.chat("please review the login bug", session_id="s1")
    call = brain.chat_calls[-1]
    assert call["prompt"] == "please review the login bug"
    assert "User says:" not in call["prompt"]
    identity = call["messages"][0]
    assert identity["role"] == "tool"
    assert "vizhi" in identity["content"]


def test_stored_user_turn_is_not_prompt_dump(vizhi, brain):
    vizhi.chat("please review the login bug", session_id="s1")
    stored = brain.docs["s1"]
    assert stored[0] == {"role": "user", "content": "please review the login bug"}
    assert "Tone:" not in stored[0]["content"]


def test_long_prompt_does_not_crowd_out_ask(vizhi, brain):
    vizhi.chat("please review the login bug", session_id="s1")
    # The identity prompt is longer than the snippet cap, yet the ask is
    # what ends up stored and therefore what teammates read.
    assert len(vizhi.prompt()) > 400
    assert "login bug" in brain.docs["s1"][0]["content"]


# ---------- fix 2: session docs merge, never overwrite ----------

def test_cross_worker_turn_is_visible(vizhi, arivu, brain):
    vizhi.chat("codeword TANGERINE-77", session_id="team")
    out = arivu.chat("what codeword?", session_id="team")
    assert out["memory"]["shared"] is True
    block = next(m["content"] for m in brain.chat_calls[-1]["messages"]
                 if "session team so far" in m["content"])
    assert "TANGERINE-77" in block
    assert "ack" in block  # vizhi's reply too


def test_session_doc_grows_across_turns(brain):
    brain.answer = "echo"  # distinct replies so dedupe cannot collapse turns
    vizhi = GenericWorker("vizhi", brain=brain)
    arivu = GenericWorker("arivu", brain=brain)
    vizhi.chat("one", session_id="team")
    assert len(brain.docs["team"]) == 2
    arivu.chat("two", session_id="team")
    assert len(brain.docs["team"]) == 4
    vizhi.chat("three", session_id="team")
    assert len(brain.docs["team"]) == 6
    assert [m["content"] for m in brain.docs["team"] if m["role"] == "user"] == [
        "one", "two", "three"]


def test_merge_reports_turn_count(vizhi, brain):
    out = vizhi.chat("one", session_id="team")
    assert out["memory"]["merged"] is True
    assert out["memory"]["session_turns"] == 2
    assert out["memory"]["chat_id"] == "team"


def test_no_duplicate_when_turn_already_saved(vizhi, brain):
    vizhi.chat("one", session_id="team")
    # Simulate Mind having auto-appended this exact turn (user + assistant)
    # under the session key before AK's merge runs.
    brain.docs["team"] += [{"role": "user", "content": "two"},
                           {"role": "assistant", "content": "ack"}]
    out = vizhi.chat("two", session_id="team")
    assert out["memory"]["merged"] is False  # trailing turn already present
    assert [m["content"] for m in brain.docs["team"] if m["role"] == "user"] == ["one", "two"]


def test_legitimately_repeated_turn_is_kept():
    """A repeated utterance must NOT be collapsed as a 'duplicate' (Mind's
    identical-turn dedupe is a defect we deliberately do not copy)."""
    class CountingBrain(PersistentBrain):
        def chat(self, prompt, messages=None, session_id=None):
            self.chat_calls.append({"prompt": prompt, "messages": messages,
                                    "session_id": session_id})
            return {"answer": f"ans{len(self.chat_calls)}"}

    brain = CountingBrain()
    w = GenericWorker("vizhi", brain=brain)
    w.chat("standup", session_id="team")
    w.chat("other", session_id="team")
    w.chat("standup", session_id="team")
    users = [m["content"] for m in brain.docs["team"] if m["role"] == "user"]
    assert users == ["standup", "other", "standup"]


def test_concurrent_workers_lose_no_turns(brain):
    vizhi = GenericWorker("vizhi", brain=brain)
    arivu = GenericWorker("arivu", brain=brain)
    errors = []

    def run(worker, tag):
        try:
            for i in range(5):
                worker.chat(f"{tag}-{i}", session_id="race")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(vizhi, "v")),
               threading.Thread(target=run, args=(arivu, "a"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    users = [m["content"] for m in brain.docs["race"] if m["role"] == "user"]
    assert sorted(users) == sorted([f"v-{i}" for i in range(5)]
                                   + [f"a-{i}" for i in range(5)])


# ---------- fix 3: legacy prompt-dump docs stay readable ----------

def test_strip_prompt_dump_keeps_utterance():
    dump = "You are vizhi, project_manager.\n" + ("Tone: friendly\n" * 50)
    dump += "\n\nUser says: fix the login bug"
    assert _strip_prompt_dump(dump) == "fix the login bug"


def test_strip_prompt_dump_plain_text_untouched():
    assert _strip_prompt_dump("just a normal turn") == "just a normal turn"


def test_legacy_doc_ask_survives_snippet_cap(vizhi, brain):
    dump = vizhi.prompt() + "\n\nUser says: the real ask is at the tail"
    brain.docs["old"] = [
        {"role": "user", "content": dump},
        {"role": "assistant", "content": "ok"},
    ]
    out = vizhi.chat("continue", session_id="old")
    assert out["memory"]["shared"] is True
    block = next(m["content"] for m in brain.chat_calls[-1]["messages"]
                 if "session old so far" in m["content"])
    assert "the real ask is at the tail" in block
    assert "Tone:" not in block


# ---------- non-regressions ----------

def test_stateless_memory_disabled_keeps_nothing(tmp_path):
    import json

    entry = {
        "name": "quiet", "type": "coordinator",
        "description": "quiet", "identity": {"role": "assistant", "language": "en"},
        "memory": {"enabled": False},
    }
    path = tmp_path / "workers.json"
    path.write_text(json.dumps([entry]), encoding="utf-8")
    brain = PersistentBrain(answer="hi")
    w = GenericWorker("quiet", brain=brain, workers_file=path)
    out = w.chat("hello", session_id="s1")
    assert out["memory"]["enabled"] is False
    assert w.history == []
    assert brain.docs == {} and brain.saved == []


def test_session_without_utterance_is_not_written(vizhi, brain):
    vizhi.chat("hello", session_id="team")
    vizhi.chat("world", session_id="team")
    users = [m["content"] for m in brain.docs["team"] if m["role"] == "user"]
    assert users == ["hello", "world"]


# ---------- fix 4: transcript ownership (Mind-owned vs client-owned) ----------

class SessionBrain(PersistentBrain):
    """PersistentBrain that also knows which sessions Mind itself created."""

    def __init__(self, answer="ack", live=()):
        super().__init__(answer=answer)
        self.live = set(live)

    def get_session(self, session_id):
        if session_id not in self.live:
            raise ValueError(f"unknown session {session_id!r}")
        return {"session": {"session_id": session_id, "status": "active"}}


def test_unknown_session_is_client_owned_never_forwarded():
    # Mind cannot newest-wins clobber a doc it was never handed the key for.
    brain = SessionBrain(live=[])
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hello", session_id="harness_key")
    assert all(c["session_id"] is None for c in brain.chat_calls)
    assert out["memory"]["merged"] is True
    assert brain.docs["harness_key"][0]["content"] == "hello"


def test_mind_owned_session_is_forwarded_and_client_defers():
    brain = SessionBrain(live=["mind_key"])
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hello", session_id="mind_key")
    assert [c["session_id"] for c in brain.chat_calls] == ["mind_key"]
    assert out["memory"]["merged"] is False
    assert out["memory"]["session_id"] == "mind_key"
    assert brain.saved == []  # Mind already saved; client must not overwrite


def test_inactive_session_treated_as_client_owned():
    class DeadSession(SessionBrain):
        def get_session(self, session_id):
            return {"session": {"session_id": session_id, "status": "expired"}}

    brain = DeadSession()
    w = GenericWorker("vizhi", brain=brain)
    out = w.chat("hello", session_id="stale")
    assert all(c["session_id"] is None for c in brain.chat_calls)
    assert out["memory"]["merged"] is True


def test_brain_without_get_session_is_client_owned(vizhi, brain):
    assert not hasattr(brain, "get_session")
    out = vizhi.chat("hello", session_id="s1")
    assert all(c["session_id"] is None for c in brain.chat_calls)
    assert out["memory"]["merged"] is True
