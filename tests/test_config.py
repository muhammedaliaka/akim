import pytest

from akim.config import load_config


def test_defaults_without_file(tmp_path, monkeypatch):
    monkeypatch.delenv("AKIM_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.steam.enabled and cfg.epic.enabled
    assert list(cfg.notifications.channels) == ["console"]


def test_env_expansion_and_types(tmp_path, monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "123:abc")
    p = tmp_path / "c.yaml"
    p.write_text(
        """
general: {country: tr, track_days: "5"}
steam: {charts: "topsellers, most_played"}
notifications:
  channels:
    telegram: {enabled: "true", bot_token: "${TG_TOKEN}", chat_id: "${TG_CHAT:-42}", min_priority: high}
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.general.track_days == 5
    assert cfg.steam.charts == ["topsellers", "most_played"]
    tg = cfg.notifications.channels["telegram"]
    assert tg.enabled is True and tg.bot_token == "123:abc" and tg.chat_id == "42"
    assert "console" in cfg.notifications.channels  # konsol varsayılan olarak korunur


def test_unknown_keys_are_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("steam: {intervall_minutes: 5}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="intervall_minutes"):
        load_config(p)


def test_env_shortcuts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AKIM_CONFIG", raising=False)
    monkeypatch.setenv("AKIM_NTFY_TOPIC", "benim-konum")
    cfg = load_config()
    assert cfg.notifications.channels["ntfy"].topic == "benim-konum"


def test_example_config_is_valid(monkeypatch):
    from pathlib import Path

    cfg = load_config(Path(__file__).parent.parent / "config.example.yaml")
    assert cfg.scoring.concept_clone_threshold == 0.75 and cfg.llm.model == "claude-opus-5-5"
