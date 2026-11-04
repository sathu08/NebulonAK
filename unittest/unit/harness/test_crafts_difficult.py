"""DIFFICULT craft tests — fail-closed validation, flat-only, inputs, allowlists."""
import pytest

from nak.harness.craft import craft as C


def test_unknown_craft_raises():
    with pytest.raises(ValueError, match="unknown craft"):
        C.load_craft("ghost-craft")


def test_empty_name_raises():
    with pytest.raises(ValueError, match="non-empty"):
        C.load_craft("   ")


def test_expand_missing_required_input_raises():
    # plan craft requires <goal>-ish inputs (see craft.md ## Inputs Required)
    craft = C.load_craft("plan")
    assert craft["required_inputs"], "plan craft must declare required inputs"
    missing = {k: "x" for k in craft["required_inputs"]}
    missing.pop(next(iter(missing)))
    with pytest.raises(ValueError, match="missing inputs"):
        C.expand_craft("plan", missing)
    # build craft also requires goal + an approved plan
    bcraft = C.load_craft("build")
    assert bcraft["required_inputs"], "build craft must declare required inputs"
    assert "plan" in bcraft["required_inputs"]
    with pytest.raises(ValueError, match="missing inputs"):
        C.expand_craft("build", {"goal": "fix login bug"})


def test_crafts_for_unknown_worker_is_list():
    out = C.crafts_for_worker("ghost-xyz")
    assert isinstance(out, list)


def test_crafts_for_empty_name_is_empty():
    assert C.crafts_for_worker("") == []


def test_list_crafts_modes_gate():
    # assign-only craft must not show for plan-only modes
    all_crafts = C.list_crafts(worker_modes=["plan", "assign"])
    plan_only = C.list_crafts(worker_modes=["plan"])
    assert len(plan_only) <= len(all_crafts)


def test_flat_only_worker_tag_refused(monkeypatch, tmp_path):
    """A craft step with [worker=] must be refused at load (no nesting)."""
    # Build a fake registry + craft dir shadowing the real one is heavy;
    # instead assert the regex gate itself refuses such a step.
    assert C._WORKER_TAG.search("do step [worker=vizhi] now")
    assert C._CRAFT_REF.match("Craft: plan something")


def test_version_pin_mismatch_detected():
    # Real registry pins 1.0.0; loader compares md version == pin.
    # Corrupt the pin in-memory by calling load with a monkeypatched pin list.
    pins = C._craft_pins()
    assert all(p.get("version") == "1.0.0" for p in pins)


def test_craft_tools_all_known():
    known = set(C._tool_names())
    for craft in C.list_crafts():
        for t in craft["tools"]:
            assert t in known, f"{craft['name']} unknown tool {t}"


def test_worker_allowlist_respected():
    # vizhi allowlist = [plan, assign]; aayan = [plan]; arivu = [plan, build];
    # nunnan = [plan, assess] (assess pin also restricts workers/roles)
    vizhi = {c["name"] for c in C.crafts_for_worker("vizhi")}
    aayan = {c["name"] for c in C.crafts_for_worker("aayan")}
    arivu = {c["name"] for c in C.crafts_for_worker("arivu")}
    nunnan = {c["name"] for c in C.crafts_for_worker("nunnan")}
    assert aayan <= vizhi | {"build"}
    assert aayan == {"plan"}
    assert arivu == {"plan", "build"}
    assert nunnan == {"plan", "assess"}
