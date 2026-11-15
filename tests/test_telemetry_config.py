"""Telemetry config is cached between events and refreshed when the file changes."""
import os
import time

import yaml

from gateway.telemetry import events


def test_cached_until_file_changes(tmp_path, monkeypatch):
    f = tmp_path / "security.yaml"
    f.write_text("telemetry: {enabled: true}\n")
    monkeypatch.setattr(events, "CONFIG_DIR", tmp_path)
    events._TELEMETRY_CACHE.clear()
    assert events._load_telemetry_config() == {"enabled": True}

    parses = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda t: parses.append(1) or real(t))
    events._load_telemetry_config(); events._load_telemetry_config()
    assert parses == []                                  # no re-parse per event

    time.sleep(0.01)
    f.write_text("telemetry: {enabled: false, path: x}\n")
    os.utime(f, None)
    assert events._load_telemetry_config()["enabled"] is False


def test_missing_or_broken_config_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(events, "CONFIG_DIR", tmp_path)
    events._TELEMETRY_CACHE.clear()
    assert events._load_telemetry_config() == {}
    (tmp_path / "security.yaml").write_text("telemetry: [unclosed\n")
    assert events._load_telemetry_config() == {}


def test_returned_dict_is_a_copy(tmp_path, monkeypatch):
    (tmp_path / "security.yaml").write_text("telemetry: {enabled: true}\n")
    monkeypatch.setattr(events, "CONFIG_DIR", tmp_path)
    events._TELEMETRY_CACHE.clear()
    events._load_telemetry_config()["enabled"] = False
    assert events._load_telemetry_config()["enabled"] is True
