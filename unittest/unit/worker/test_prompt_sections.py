"""Conditional prompt sections: team-scoped rosters."""
import json
from pathlib import Path

from nak.worker import GenericWorker
from nak.worker.engine.prompt import _render_prompt_template, _render_sections


def _identity():
    return {"role": "tester", "personality": [], "tone": "", "formality": "",
            "humor": "", "language": "", "values": [], "do_not": []}


def test_sections_keep_and_drop():
    text = "A{{#team}} roster {{workers}}{{/team}}Z"
    assert _render_sections(text, {"team": True}) == "A roster {{workers}}Z"
    assert _render_sections(text, {"team": False}) == "AZ"


def test_render_fills_workers_when_team_present():
    out = _render_prompt_template(
        "A\n{{#team}}Names: {{workers}}{{/team}}\nZ", _identity(), "bob",
        team={"name": "core", "members": ["bob", "ann"]})
    assert "Names: bob, ann" in out
    assert "{{" not in out


def test_render_drops_block_without_team():
    out = _render_prompt_template(
        "A\n{{#team}}Names: {{workers}}{{/team}}\nZ", _identity(), "bob")
    assert "Names:" not in out
    assert "{{" not in out


def _write_worker(tmp_path: Path, entry: dict, template: str, teams=None, extra=()):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "w.md").write_text(template, encoding="utf-8")
    workers_file = tmp_path / "workers.json"
    workers_file.write_text(json.dumps([entry, *extra]), encoding="utf-8")
    if teams is not None:
        (tmp_path / "teams.json").write_text(
            json.dumps({"teams": teams}), encoding="utf-8")
    return str(workers_file)


_TEMPLATE = "Base\n{{#team}}Team: {{workers}}{{/team}}\nEnd"


def _entry(**extra):
    e = {"name": "w", "type": "tester",
         "identity": {"role": "tester"},
         "memory": {"enabled": True},
         "prompt_file": "prompts/w.md",
         "crafts": []}
    e.update(extra)
    return e


def test_worker_prompt_lists_team_members(tmp_path):
    wf = _write_worker(
        tmp_path, _entry(team={"enabled": True, "name": "core"}), _TEMPLATE,
        teams=[{"name": "core", "leader": "w", "members": ["w", "ann"]}],
        extra=[_entry(name="ann", team={"enabled": True, "name": "core"})])
    out = GenericWorker("w", workers_file=wf).prompt()
    assert "Team: w, ann" in out


def test_worker_prompt_hides_block_without_team(tmp_path):
    wf = _write_worker(tmp_path, _entry(), _TEMPLATE)
    out = GenericWorker("w", workers_file=wf).prompt()
    assert "Team:" not in out
    assert "{{" not in out


def test_worker_prompt_hides_block_with_ambiguous_teams(tmp_path):
    wf = _write_worker(
        tmp_path, _entry(teams=["a", "b"]), _TEMPLATE,
        teams=[{"name": "a", "leader": "w", "members": ["w"]},
               {"name": "b", "leader": "w", "members": ["w"]}])
    out = GenericWorker("w", workers_file=wf).prompt()
    assert "Team:" not in out


def test_prompt_file_appends_autonomy_guardrails(tmp_path):
    wf = _write_worker(tmp_path, _entry(), _TEMPLATE)
    out = GenericWorker("w", workers_file=wf).prompt()
    assert "## Autonomy" in out
    assert "Autonomy:" in out


def test_prompt_file_autonomy_placeholder_not_duplicated(tmp_path):
    wf = _write_worker(tmp_path, _entry(), "Base\n{{autonomy}}")
    out = GenericWorker("w", workers_file=wf).prompt()
    assert out.count("Autonomy:") == 1
    assert "## Autonomy" not in out


def test_all_worker_prompts_include_persona_lock():
    for name in ("vizhi", "arivu", "aayan", "ezhilan", "thiran", "viyan"):
        prompt = GenericWorker(name).prompt()
        assert "## Persona lock" in prompt
        assert "Keep the configured tone" in prompt
