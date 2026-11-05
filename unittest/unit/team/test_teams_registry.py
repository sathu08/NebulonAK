"""Team registry: teams.json is authoritative, worker.team must agree."""
import json

import pytest

from nak_host.worker import list_teams, list_workers

_UNSET = object()


def _entry(name, team=_UNSET, teams=_UNSET):
    e = {
        "name": name,
        "type": "coordinator",
        "identity": {"role": "tester"},
        "memory": {"enabled": True},
    }
    if team is not _UNSET:
        e["team"] = team
    if teams is not _UNSET:
        e["teams"] = teams
    return e


def _team(name="core", members=None, **extra):
    t = {
        "name": name,
        "leader": (members or ["vizhi"])[0],
        "members": members or ["vizhi"],
        "shared_memory": True,
        "max_workers": 3,
    }
    t.update(extra)
    return t


def _write(tmp_path, workers, teams=None):
    workers_file = tmp_path / "workers.json"
    workers_file.write_text(json.dumps(workers), encoding="utf-8")
    if teams is not None:
        (tmp_path / "teams.json").write_text(
            json.dumps({"teams": teams}), encoding="utf-8")
    return str(workers_file)


def test_default_team_registry_loads():
    teams = list_teams()
    core = next(t for t in teams if t["name"] == "core")
    assert core["leader"] == "vizhi"
    assert core["members"] == [
        "vizhi", "aayan", "arivu", "ezhilan", "thiran", "viyan"]
    assert core["shared_memory"] is True
    assert core["parallel"] is True
    assert core["max_workers"] == 6
    for worker in list_workers():
        if worker["name"] in core["members"]:
            assert worker["team"] == {"enabled": True, "name": "core"}
        else:
            assert worker["team"] == {"enabled": False, "name": ""}


def test_legacy_workers_without_team_block_still_load(tmp_path):
    wf = _write(tmp_path, [_entry("vizhi")])
    workers = list_workers(wf)
    assert workers[0]["team"] == {"enabled": False, "name": ""}


def test_member_must_declare_a_team(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"}), _entry("aayan")],
        [_team(members=["vizhi", "aayan"])],
    )
    with pytest.raises(ValueError, match="no team declared"):
        list_workers(wf)


def test_worker_declared_team_must_match_roster(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"}),
         _entry("aayan", {"enabled": True, "name": "research"})],
        [_team(members=["vizhi", "aayan"])],
    )
    with pytest.raises(ValueError, match="declares teams .*research"):
        list_workers(wf)


def test_enabled_worker_must_be_listed_in_members(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"}),
         _entry("aayan", {"enabled": True, "name": "core"})],
        [_team(members=["aayan"])],
    )
    with pytest.raises(ValueError, match="not in its members"):
        list_workers(wf)


def test_member_must_be_a_known_worker(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi", "ghost"])],
    )
    with pytest.raises(ValueError, match="unknown member 'ghost'"):
        list_workers(wf)


def test_team_name_cannot_collide_with_worker(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "vizhi"})],
        [_team(name="vizhi", members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="collides with a worker"):
        list_workers(wf)


def test_disabled_worker_cannot_be_a_member(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": False, "name": "core"})],
        [_team(members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="is disabled"):
        list_workers(wf)


def test_team_affiliation_requires_teams_file(tmp_path):
    wf = _write(tmp_path, [_entry("vizhi", {"enabled": True, "name": "core"})])
    with pytest.raises(ValueError, match="teams.json is missing"):
        list_workers(wf)


def test_leader_must_be_a_member(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi"], leader="aayan")],
    )
    with pytest.raises(ValueError, match="leader 'aayan' is not in members"):
        list_workers(wf)


def test_duplicate_members_rejected(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi", "vizhi"])],
    )
    with pytest.raises(ValueError, match="duplicate member 'vizhi'"):
        list_workers(wf)


def test_shared_memory_and_max_workers_validated(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi"], shared_memory="yes")],
    )
    with pytest.raises(ValueError, match="shared_memory"):
        list_workers(wf)

    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi"], max_workers=0)],
    )
    with pytest.raises(ValueError, match="max_workers"):
        list_workers(wf)

    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"})],
        [_team(members=["vizhi"], parallel="yes")],
    )
    with pytest.raises(ValueError, match="parallel"):
        list_workers(wf)


def test_team_block_validated(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": "yes", "name": "core"})],
        [_team(members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="team.enabled"):
        list_workers(wf)

    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": ""})],
        [_team(members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="team.name"):
        list_workers(wf)


def test_list_teams_accepts_bare_list(tmp_path):
    (tmp_path / "workers.json").write_text(
        json.dumps([_entry("vizhi", {"enabled": True, "name": "core"})]),
        encoding="utf-8")
    teams_file = tmp_path / "teams.json"
    teams_file.write_text(json.dumps([_team(members=["vizhi"])]), encoding="utf-8")
    teams = list_teams(str(teams_file), str(tmp_path / "workers.json"))
    assert teams[0]["name"] == "core"


def test_multi_team_worker_supported(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", teams=["core", "research"])],
        [_team(name="core", members=["vizhi"]),
         _team(name="research", members=["vizhi"])],
    )
    workers = list_workers(wf)
    assert workers[0]["teams"] == ["core", "research"]
    assert workers[0]["team"] == {"enabled": True, "name": "core"}


def test_team_and_teams_are_mutually_exclusive(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", {"enabled": True, "name": "core"},
                teams=["core"])],
        [_team(members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="use either 'team' or 'teams'"):
        list_workers(wf)


def test_duplicate_teams_rejected(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", teams=["core", "core"])],
        [_team(members=["vizhi"])],
    )
    with pytest.raises(ValueError, match="duplicate team 'core'"):
        list_workers(wf)


def test_multi_team_worker_must_be_in_each_roster(tmp_path):
    wf = _write(
        tmp_path,
        [_entry("vizhi", teams=["core", "research"]),
         _entry("aayan", {"enabled": True, "name": "core"})],
        [_team(name="core", members=["vizhi", "aayan"]),
         _team(name="research", members=["aayan"])],
    )
    with pytest.raises(ValueError, match="listed in 'research'"):
        list_workers(wf)
