from .openai_compatible import OpenAICompatibleProvider


class LMStudioProvider(OpenAICompatibleProvider):
    """LM Studio provider (OpenAI-compatible; only the runtime label differs)."""

    runtime_label = "lmstudio"
