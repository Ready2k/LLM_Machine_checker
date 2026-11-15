"""Config caching and model-path expansion."""
import os
import time

import gateway.server as server


def test_config_cache_reuses_and_invalidates(tmp_path, monkeypatch):
    f = tmp_path / "models.yaml"
    f.write_text("models: {a: {route: r}}\n")
    monkeypatch.setattr(server, "CONFIG_DIR", tmp_path)
    server._CONFIG_CACHE.clear()

    first = server.load_models_config()
    assert "a" in first["models"]
    first["models"]["mutated"] = {}                       # mutating a result...
    assert "mutated" not in server.load_models_config()["models"]   # ...can't poison the cache

    parses = []
    import yaml
    real = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda t: parses.append(1) or real(t))
    server.load_models_config(); server.load_models_config()
    assert parses == []                                    # served from cache, no re-parse

    time.sleep(0.01)
    f.write_text("models: {b: {route: r}}\n")              # edit on disk -> picked up
    os.utime(f, None)
    assert "b" in server.load_models_config()["models"]


def test_resolve_target_model_expands_home():
    cfg = {"models": {"sim": {"model": "~/.cache/hf/gemma"}, "plain": {"model": "llama3.1:8b"}}}
    assert server.resolve_target_model("sim", cfg) == os.path.expanduser("~/.cache/hf/gemma")
    assert server.resolve_target_model("plain", cfg) == "llama3.1:8b"
    assert server.resolve_target_model("unknown", cfg) == "unknown"
