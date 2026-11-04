from pipeline.vetting.utils.probes import probe_crafts, probe_tools


def test_probe_crafts_for_vizhi():
    probes = probe_crafts("vizhi")
    names = {p.name for p in probes}
    assert {"plan", "assign"} <= names
    assert all(p.status == "pass" for p in probes)


def test_probe_crafts_for_nunnan_includes_plan_and_assess():
    probes = probe_crafts("nunnan")
    assert {p.name for p in probes} == {"plan", "assess"}
    assert all(p.status == "pass" for p in probes)


def test_probe_tools_respects_deny_for_researcher(tmp_path):
    probes = probe_tools("aayan", root=tmp_path)
    by_name = {p.name: p for p in probes}
    assert by_name["file"].status == "pass"
    assert by_name["directory"].status == "pass"
    assert by_name["observe"].status == "pass"
    assert by_name["terminal"].status == "deny"


def test_probe_tools_exercises_allowed_tools(tmp_path):
    probes = probe_tools("vizhi", root=tmp_path)
    by_name = {p.name: p for p in probes}
    assert by_name["file"].status == "pass"
    assert by_name["directory"].status == "pass"
    assert by_name["terminal"].status == "pass"
    assert by_name["observe"].status == "pass"
