"""Craft decision block: normalization, fail-loud validation, defaults."""
import pytest

from nak.harness.craft import craft as C


def test_decisions_default_off_and_normalized():
    craft = C.load_craft("plan")
    dec = craft["decision"]
    assert dec["enabled"] is False
    assert dec["scope"] == "per_step"
    assert dec["assignment"] == "tags_win"
    assert dec["mode"] == "auto"
    assert dec["on_error"] == "block"
    assert dec["max_parallel"] >= 1
    assert dec["timeout"] > 0


def test_assign_craft_ships_parallel_per_step_decision():
    dec = C.load_craft("assign")["decision"]
    assert dec["enabled"] is True
    assert dec["scope"] == "per_step"
    assert dec["assignment"] == "tags_win"
    assert dec["mode"] == "auto"
    assert dec["parallel"] is True
    assert dec["timeout"] >= 30.0
    assert dec["on_error"] == "ask"


def test_normalize_decision_accepts_full_block():
    dec = C.normalize_decision({
        "enabled": True, "scope": "once", "assignment": "decision_only",
        "mode": "endpoint", "parallel": False, "max_parallel": 99,
        "timeout": 2.5, "on_error": "ask",
        "questions": [{"name": "risk", "type": "score"}],
    })
    assert dec["enabled"] is True
    assert dec["scope"] == "once"
    assert dec["assignment"] == "decision_only"
    assert dec["mode"] == "endpoint"
    assert dec["parallel"] is False
    assert dec["max_parallel"] == 64
    assert dec["timeout"] == 2.5
    assert dec["on_error"] == "ask"
    assert dec["questions"] == [{"name": "risk", "type": "score"}]


@pytest.mark.parametrize("key,value", [
    ("scope", "every"),
    ("assignment", "first"),
    ("mode", "magic"),
    ("on_error", "ignore"),
    ("max_parallel", "many"),
    ("timeout", "soon"),
    ("timeout", 0),
])
def test_bad_decision_values_fail_closed(key, value):
    with pytest.raises(ValueError, match=f"decision.{key}"):
        C.normalize_decision({key: value})


def test_non_object_decision_fails_closed():
    with pytest.raises(ValueError, match="decision must be an object"):
        C.normalize_decision(["not", "a", "dict"])
