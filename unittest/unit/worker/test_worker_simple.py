"""SIMPLE worker tests — identity, prompt, crafts basics.

Covers: list_workers truth, show(), prompt() rendering,
can(), needs_plan_approval(), extract_plan/extract_assigned.
crafts is the ONLY source of truth (no autonomy block).
"""
from nak.worker import GenericWorker, list_workers, IDENTITY_FIELDS


def test_list_workers_has_vizhi_and_arivu():
    entries = list_workers()
    names = [e["name"] for e in entries]
    assert "vizhi" in names
    assert "aayan" in names
    assert "arivu" in names
    assert "viyan" in names


def test_identity_fields_only_allowed():
    entries = list_workers()
    for e in entries:
        for k in e["identity"]:
            assert k in IDENTITY_FIELDS, f"{e['name']} bad identity key {k}"


def test_show_shape_vizhi():
    w = GenericWorker("vizhi")
    s = w.show()
    assert s["name"] == "vizhi"
    assert s["type"] in ("coordinator", "coder", "tester", "researcher")
    assert "identity" in s and "autonomy" in s and "crafts" in s and "memory" in s
    assert set(s["identity"].keys()) == set(IDENTITY_FIELDS)


def test_gender_field_in_identity_and_prompt():
    w = GenericWorker("vizhi")
    assert "gender" in w.show()["identity"]
    assert "Gender: unspecified" in w.prompt()


def test_prompt_contains_name_role_and_crafts():
    w = GenericWorker("vizhi")
    p = w.prompt()
    assert "vizhi" in p
    assert "project_manager" in p
    assert "plan" in p.lower()


def test_arivu_prompt_contains_researcher():
    w = GenericWorker("aayan")
    p = w.prompt()
    assert "aayan" in p
    assert "researcher" in p


def test_arivu_developer_prompt_and_modes():
    w = GenericWorker("arivu")
    p = w.prompt()
    assert "arivu" in p
    assert "developer" in p
    assert w.can("plan") is True
    assert w.can("build") is True
    assert w.can("assign") is False


def test_can_modes():
    vizhi = GenericWorker("vizhi")
    assert vizhi.can("plan") is True
    assert vizhi.can("assign") is True
    assert vizhi.can("assist") is False
    aayan = GenericWorker("aayan")
    assert aayan.can("plan") is True
    assert aayan.can("assign") is False
    assert aayan.can("build") is False


def test_needs_plan_approval_simple():
    w = GenericWorker("vizhi")
    # vizhi crafts [plan, assign]: assign pin approval=all (most restrictive) -> always True
    assert w.needs_plan_approval(1) is True
    assert w.needs_plan_approval(5) is True
    assert w.needs_plan_approval("bad-input") is True
    # aayan crafts [plan]: plan pin approval=small/auto_steps=2
    a = GenericWorker("aayan")
    assert a.needs_plan_approval(2) is False
    assert a.needs_plan_approval(3) is True


def test_extract_plan_simple():
    ans = "Plan:\n1. read notes\n2. write summary\n3. report done"
    steps = GenericWorker.extract_plan(ans)
    assert steps == ["read notes", "write summary", "report done"]


def test_extract_plan_ignores_non_numbered():
    assert GenericWorker.extract_plan("hello, no plan here") == []
    assert GenericWorker.extract_plan("") == []


def test_extract_assigned_simple():
    ans = "1. read file [worker=vizhi]\n2. summarize [worker=aayan]"
    got = GenericWorker.extract_assigned(ans, known=["vizhi", "aayan"])
    assert got == ["vizhi", "aayan"]


def test_extract_assigned_drops_unknown():
    ans = "1. do thing [worker=ghost]"
    assert GenericWorker.extract_assigned(ans, known=["vizhi"]) == []
    # known=None keeps everything
    assert GenericWorker.extract_assigned(ans, known=None) == ["ghost"]


def test_unknown_worker_raises():
    import pytest
    with pytest.raises(ValueError, match="unknown worker"):
        GenericWorker("ghost-worker-xyz")
