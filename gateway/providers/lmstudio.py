from .openai_compatible import OpenAICompatibleProvider
from .base import BackendHealth
import time
import httpx

class LMStudioProvider(OpenAICompatibleProvider):
    """LM Studio provider (thin wrapper around OpenAICompatibleProvider)."""
    
    def __init__(self, backend_id: str, endpoint: str, role: str):
        super().__init__(backend_id, endpoint, role)
        
    async def health(self) -> BackendHealth:
        start_time = time.time()
        try:
            url = f"{self.endpoint}/models" if self.endpoint.endswith("/v1") else f"{self.endpoint}/v1/models"
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
            latency_ms = int((time.time() - start_time) * 1000)
            data = resp.json()
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            return BackendHealth(
                id=self.backend_id,
                role=self.role,
                runtime="lmstudio",
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
                runtime="lmstudio",
                endpoint=self.endpoint,
                status="down",
                latency_ms=None,
                error=str(e)
            )
