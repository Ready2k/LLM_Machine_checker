from typing import List

from .openai_compatible import OpenAICompatibleProvider


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama provider: OpenAI-compatible chat, native /api/tags for health."""

    runtime_label = "ollama"

    def _health_url(self) -> str:
        return f"{self.endpoint}/api/tags"

    def _parse_health_models(self, data: dict) -> List[str]:
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
