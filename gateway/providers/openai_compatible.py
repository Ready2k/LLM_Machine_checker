import time
import httpx
from typing import List, Optional
from .base import Provider, BackendHealth, ModelInfo, ChatCompletionRequest, ChatCompletionResponse

class OpenAICompatibleProvider(Provider):
    """Generic OpenAI-compatible provider."""
    
    def __init__(self, backend_id: str, endpoint: str, role: str):
        super().__init__(backend_id, endpoint)
        self.role = role
        # Ensure endpoint doesn't have trailing slash
        self.endpoint = self.endpoint.rstrip("/")

    async def health(self) -> BackendHealth:
        start_time = time.time()
        try:
            # For OpenAI-compatible, /v1/models is a good health check
            url = f"{self.endpoint}/v1/models"
            if not self.endpoint.endswith("/v1") and "/v1" not in self.endpoint:
                 # If the user didn't specify /v1 in endpoint
                 url = f"{self.endpoint}/v1/models"
            elif self.endpoint.endswith("/v1"):
                 url = f"{self.endpoint}/models"
                 
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
            latency_ms = int((time.time() - start_time) * 1000)
            data = resp.json()
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            return BackendHealth(
                id=self.backend_id,
                role=self.role,
                runtime="openai-compatible",
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
                runtime="openai-compatible",
                endpoint=self.endpoint,
                status="down",
                latency_ms=None,
                error=str(e)
            )

    async def list_models(self) -> List[ModelInfo]:
        url = f"{self.endpoint}/models" if self.endpoint.endswith("/v1") else f"{self.endpoint}/v1/models"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            models = []
            for m in data.get("data", []):
                models.append(ModelInfo(id=m.get("id")))
            return models

    async def chat_completions_stream(self, request: ChatCompletionRequest, mapped_model: str):
        url = f"{self.endpoint}/chat/completions" if self.endpoint.endswith("/v1") else f"{self.endpoint}/v1/chat/completions"
        if request.metadata and request.metadata.get("correlation_id"):
            url += f"?trace_id={request.metadata['correlation_id']}"
        
        payload = {
            "model": mapped_model,
            "messages": request.messages,
            "stream": True,
        }
        if request.metadata and request.metadata.get("correlation_id"):
            payload["user"] = request.metadata["correlation_id"]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as client:
            async with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk

    async def chat_completions(self, request: ChatCompletionRequest, mapped_model: str) -> ChatCompletionResponse:
        url = f"{self.endpoint}/chat/completions" if self.endpoint.endswith("/v1") else f"{self.endpoint}/v1/chat/completions"
        if request.metadata and request.metadata.get("correlation_id"):
            url += f"?trace_id={request.metadata['correlation_id']}"
        
        payload = {
            "model": mapped_model,
            "messages": request.messages,
            "stream": request.stream
        }
        if request.metadata and request.metadata.get("correlation_id"):
            payload["user"] = request.metadata["correlation_id"]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            
            return ChatCompletionResponse(
                id=data.get("id", "chatcmpl-unknown"),
                created=data.get("created", int(time.time())),
                model=mapped_model,
                choices=data.get("choices", []),
                usage=data.get("usage")
            )
