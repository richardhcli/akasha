import sys

from akasha import config


def test_defaults():
    cfg = config.load_config(config_path="/nonexistent/config.toml")
    assert cfg.port == 7433
    assert cfg.bind == "127.0.0.1"


def test_windows_path_uses_neutral_name(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", r"C:\Users\test\AppData\Roaming")
    path = config.default_config_dir()
    assert "tm-daemon" in path.parts
    assert "akasha" not in str(path).lower()


def test_loads_overrides_from_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text('port = 9000\nbind = "0.0.0.0"\n', encoding="utf-8")
    cfg = config.load_config(config_file)
    assert cfg.port == 9000
    assert cfg.bind == "0.0.0.0"
