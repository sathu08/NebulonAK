"""SIMPLE craft tests — plan/assign/build load, list, expand, worker mapping."""
from nak_host.harness.craft import craft as C


def test_load_plan_craft():
    c = C.load_craft("plan")
    assert c["name"] == "plan"
    assert len(c["steps"]) >= 1
    assert "file" in c["tools"]


def test_load_assign_craft():
    c = C.load_craft("assign")
    assert c["name"] == "assign"
    assert len(c["steps"]) >= 1


def test_load_build_craft():
    c = C.load_craft("build")
    assert c["name"] == "build"
    assert len(c["steps"]) >= 1
    assert "file" in c["tools"] and "terminal" in c["tools"]


def test_list_crafts_has_both():
    names = [c["name"] for c in C.list_crafts()]
    assert "plan" in names and "assign" in names and "build" in names


def test_expand_plan_with_goal():
    steps = C.expand_craft("plan", {"goal": "migrate db"})
    assert isinstance(steps, list) and len(steps) >= 1


def test_expand_build_with_goal():
    steps = C.expand_craft("build", {"goal": "fix login bug",
                                     "plan": "1. edit login.py"})
    assert isinstance(steps, list) and len(steps) >= 1


def test_load_assess_craft():
    c = C.load_craft("assess")
    assert c["name"] == "assess"
    assert c["required_inputs"] == ["subject"]
    assert set(c["tools"]) == {"assertion", "trace", "scorecard", "report"}
    assert c["approval"] == "all"


def test_expand_assess_with_subject():
    steps = C.expand_craft("assess", {"subject": "release 1.0"})
    assert len(steps) == 4


def test_crafts_for_nunnan_plan_and_assess():
    out = C.crafts_for_worker("nunnan")
    names = [c["name"] for c in out]
    assert names == ["plan", "assess"]


def test_crafts_for_vizhi():
    out = C.crafts_for_worker("vizhi")
    names = [c["name"] for c in out]
    assert "plan" in names and "assign" in names


def test_crafts_for_aayan_plan_only():
    out = C.crafts_for_worker("aayan")
    names = [c["name"] for c in out]
    assert "plan" in names
    assert "assign" not in names and "build" not in names


def test_crafts_for_arivu_plan_build():
    out = C.crafts_for_worker("arivu")
    names = [c["name"] for c in out]
    assert "plan" in names and "build" in names
    assert "assign" not in names


def test_craft_gaps_empty_when_healthy():
    assert C.craft_gaps() == {}
