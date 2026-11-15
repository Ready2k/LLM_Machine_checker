"""Backend runtime spellings: openai_compatible and openai-compatible both resolve."""
import pytest

from gateway.server import get_provider


@pytest.mark.parametrize("runtime", ["openai_compatible", "openai-compatible", "OpenAI_Compatible"])
def test_openai_compatible_runtime_spellings_resolve(runtime):
    cfg = {"backends": {"b": {"runtime": runtime, "endpoint": "http://h:1"}}}
    assert get_provider("b", cfg) is not None


def test_unknown_runtime_returns_none():
    assert get_provider("b", {"backends": {"b": {"runtime": "nope", "endpoint": "http://h:1"}}}) is None
