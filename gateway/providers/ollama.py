from .openai_compatible import OpenAICompatibleProvider
from .base import BackendHealth
import time
import httpx

class OllamaProvider(OpenAICompatibleProvider):
    """Ollama provider, preferring OpenAI-compatible endpoint."""
    
    def __init__(self, backend_id: str, endpoint: str, role: str):
        super().__init__(backend_id, endpoint, role)
        
    async def health(self) -> BackendHealth:
        start_time = time.time()
        try:
            # We can use Ollama's native /api/tags for health or the /v1/models
            url = f"{self.endpoint}/api/tags"
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
            latency_ms = int((time.time() - start_time) * 1000)
            data = resp.json()
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            return BackendHealth(
                id=self.backend_id,
                role=self.role,
                runtime="ollama",
                endpoint=self.endpoint,
                status="up",
                latency_ms=latency_ms,
                error=None,
                models=models
            )
        except Exception as e:
            return BackendHealth(
                id=self.backend_id,
                role=self.role,
                runtime="ollama",
                endpoint=self.endpoint,
                status="down",
                latency_ms=None,
                error=str(e)
            )
