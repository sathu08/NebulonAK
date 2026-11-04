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


def test_probe_tools_search_passes(tmp_path):
    probes = probe_tools("arivu", root=tmp_path)
    by_name = {p.name: p for p in probes}
    assert by_name["search"].status == "pass"


def test_missing_probe_suggests_probe_not_tool_fix():
    from pipeline.vetting.utils.models import ProbeResult, WorkerResult
    from pipeline.vetting.utils.runner import _build_improvements

    result = WorkerResult(name="arivu", tool_probes=[
        ProbeResult("search", "tool", "fail",
                    "no safe probe for tool 'search'"),
        ProbeResult("file", "tool", "fail", "file read returned no content"),
    ])
    suggestions = {i.target: i.suggestion
                   for i in _build_improvements(result, None)}
    assert "_probe_call" in suggestions["search"]
    assert "Fix tool file" in suggestions["file"]
