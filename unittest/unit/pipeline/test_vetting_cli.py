import importlib

from pipeline.vetting.cli.common import _names
from pipeline.vetting.cli.run import _parser

cli_main = importlib.import_module("pipeline.vetting.cli.main")


def test_names_splits_and_dedupes():
    assert _names(["vizhi,aayan", "vizhi", " arivu "]) == [
        "vizhi", "aayan", "arivu"]


def test_names_empty_is_none():
    assert _names(None) is None
    assert _names(["", " , "]) is None


def test_parser_defaults():
    args = _parser().parse_args([])
    assert args.cases == 6
    assert args.threshold == 0.6
    assert args.workers is None
    assert args.target_decision is False


def test_parser_can_enable_target_decision():
    assert _parser().parse_args(["--target-decision"]).target_decision is True


def test_parser_bank_and_repeat_defaults():
    args = _parser().parse_args([])
    assert args.cases_from is None
    assert args.repeat == 1
    assert _parser().parse_args(
        ["--cases-from", "arivu", "--repeat", "3"]).cases_from == "arivu"


def test_run_rejects_bad_repeat():
    from pipeline.vetting.cli.run import run as run_fn

    args = _parser().parse_args(["--repeat", "0"])
    assert run_fn(args) == 2


def test_cli_defaults_to_run(monkeypatch):
    seen = {}

    def fake_run(args):
        seen["workers"] = args.workers
        return 0

    monkeypatch.setattr(cli_main.run_cmd, "run", fake_run)
    assert cli_main.main(["--workers", "vizhi"]) == 0
    assert seen["workers"] == ["vizhi"]


def test_cli_dispatches_card(monkeypatch):
    monkeypatch.setattr(cli_main.card, "run", lambda args: 7)
    assert cli_main.main(["card", "--workers", "vizhi"]) == 7


def test_cli_help_lists_commands(capsys):
    try:
        cli_main.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    out = capsys.readouterr().out
    assert "run" in out
    assert "card" in out
    assert "improve" in out
