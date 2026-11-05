"""Shared fixtures for NebulonAK unittest suite (offline, no Mind/DB needed)."""
import sys
import json
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytest


class FakeBrain:
    """Minimal duck-typed brain: chat/search/decide/remember/save_chat/require_mind.

    Triage-aware: decider prompts contain 'Workers:' + 'Reply JSON only' —
    those get a valid worker/tool JSON reply so policy stays 'chat'.
    All other prompts get self.answer (plan/chat text).
    """

    DECIDE_REPLY = '{"worker": "vizhi", "tool": "file", "arguments": {"action": "read", "path": "notes.txt"}, "reason": "fake decide"}'

    def __init__(self, answer="1. step one\n2. step two", search_hits=None, decisions=None):
        self.answer = answer
        self.search_hits = search_hits if search_hits is not None else []
        self.decisions = decisions if decisions is not None else []
        self.session_docs = {}
        self.chat_calls = []
        self.saved = []
        self.remembered = []
        self.mind_ok = True

    def require_mind(self):
        if not self.mind_ok:
            raise RuntimeError("Mind down (fake)")

    def chat(self, prompt, messages=None, session_id=None):
        self.chat_calls.append({"prompt": prompt, "messages": messages,
                                "session_id": session_id})
        p = str(prompt or "").lower()
        if "task router" in p or "reply format" in p or "reply with json" in p:
            return {"answer": self.DECIDE_REPLY, "raw": "fake-decide"}
        return {"answer": self.answer, "raw": "fake"}

    def search(self, text, top_k=3):
        return list(self.search_hits)[:top_k]

    def decide(self, turns):
        return {"decisions": list(self.decisions)}

    def remember(self, text, **kwargs):
        self.remembered.append({"text": text, **kwargs})
        return {"ok": True}

    def save_chat(self, doc):
        self.saved.append(doc)
        return {"ok": True, "id": doc.get("id")}

    def get_chat(self, chat_id):
        if chat_id not in self.session_docs:
            raise ValueError(f"unknown chat {chat_id!r}")
        return {"chat": {"id": chat_id, "messages": list(self.session_docs[chat_id])}}


@pytest.fixture()
def fake_brain():
    return FakeBrain()


@pytest.fixture()
def tmp_workspace(tmp_path):
    # Workspace MUST live under <repo>/workspace so default permissions
    # resources (workspace/**) grant session files. Unique per-test.
    import uuid
    ws = REPO_ROOT / "workspace" / f"unittest-{uuid.uuid4().hex[:8]}"
    ws.mkdir(parents=True, exist_ok=True)
    yield str(ws)
    # best-effort cleanup (sessions inside); never fail the test
    import shutil
    try:
        shutil.rmtree(str(ws), ignore_errors=True)
    except Exception:
        pass


@pytest.fixture()
def harness_vizhi(tmp_workspace):
    from nak_host.harness.engine import ExecutionHarness
    return ExecutionHarness(worker="vizhi", user="tester", workspace_root=tmp_workspace)


@pytest.fixture()
def harness_vizhi_approved(tmp_workspace):
    """vizhi with approver=True: 'ask' verdicts pass so tool sandbox is tested."""
    from nak_host.harness.engine import ExecutionHarness
    return ExecutionHarness(worker="vizhi", user="tester", workspace_root=tmp_workspace,
                            approver=lambda *a, **k: True)


@pytest.fixture()
def harness_arivu(tmp_workspace):
    from nak_host.harness.engine import ExecutionHarness
    return ExecutionHarness(worker="arivu", user="tester", workspace_root=tmp_workspace)


@pytest.fixture()
def harness_aayan(tmp_workspace):
    from nak_host.harness.engine import ExecutionHarness
    return ExecutionHarness(worker="aayan", user="tester", workspace_root=tmp_workspace)


@pytest.fixture()
def worker_vizhi(fake_brain):
    from nak_host.wiring import make_worker
    return make_worker("vizhi", brain=fake_brain)


@pytest.fixture()
def worker_arivu(fake_brain):
    from nak_host.wiring import make_worker
    return make_worker("arivu", brain=fake_brain)


@pytest.fixture()
def worker_aayan(fake_brain):
    from nak_host.wiring import make_worker
    return make_worker("aayan", brain=fake_brain)


@pytest.fixture()
def bare_worker_vizhi():
    """No brain — for testing the no-brain error path."""
    from nak_host.worker import GenericWorker
    return GenericWorker("vizhi")
