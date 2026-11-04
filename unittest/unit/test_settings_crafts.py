from nak.utils.settings import (
    DEFAULT_CRAFTS,
    DEFAULT_CRAFTS_PROMPT,
    load_settings,
)


def _cfg(tmp_path, body):
    path = tmp_path / "nebulonak.cfg"
    path.write_text(body, encoding="utf-8")
    return path


def test_crafts_read_from_harness_section(tmp_path, monkeypatch):
    monkeypatch.delenv("NAK_CRAFTS", raising=False)
    monkeypatch.delenv("NAK_CRAFTS_FILE", raising=False)
    monkeypatch.delenv("NAK_CRAFTS_PROMPT", raising=False)
    cfg = _cfg(tmp_path, "[harness]\n"
                         "crafts = custom/crafts.json\n"
                         "crafts_prompt = custom/craft\n")
    settings = load_settings(cfg)
    assert settings.crafts == "custom/crafts.json"
    assert settings.crafts_prompt == "custom/craft"
    assert settings.crafts_path() == str(tmp_path / "custom/crafts.json")
    assert settings.crafts_prompt_path() == str(tmp_path / "custom/craft")


def test_crafts_default_when_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("NAK_CRAFTS", raising=False)
    monkeypatch.delenv("NAK_CRAFTS_FILE", raising=False)
    monkeypatch.delenv("NAK_CRAFTS_PROMPT", raising=False)
    settings = load_settings(_cfg(tmp_path, "[harness]\n"))
    assert settings.crafts == DEFAULT_CRAFTS
    assert settings.crafts_prompt == DEFAULT_CRAFTS_PROMPT


def test_env_overrides_and_legacy_fallback(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, "[harness]\ncrafts = file.json\n")
    monkeypatch.setenv("NAK_CRAFTS", "env/crafts.json")
    monkeypatch.setenv("NAK_CRAFTS_PROMPT", "env/craft")
    settings = load_settings(cfg)
    assert settings.crafts == "env/crafts.json"
    assert settings.crafts_prompt == "env/craft"

    monkeypatch.delenv("NAK_CRAFTS", raising=False)
    monkeypatch.setenv("NAK_CRAFTS_FILE", "legacy/crafts.json")
    settings = load_settings(cfg)
    assert settings.crafts == "legacy/crafts.json"
