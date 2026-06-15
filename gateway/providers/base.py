from typing import Any, Dict, List, Optional
from pydantic import BaseModel

class BackendHealth(BaseModel):
    id: str
    role: str
    runtime: str
    endpoint: str
    status: str
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    models: List[str] = []

class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "local-ai-fabric"

class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Dict[str, Any]]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: bool = False
    metadata: Optional[Dict[str, Any]] = None

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Dict[str, Any]]
    usage: Optional[Dict[str, Any]] = None

class Provider:
    """Base class for all AI gateway providers."""
    
    def __init__(self, backend_id: str, endpoint: str):
        self.backend_id = backend_id
        self.endpoint = endpoint

    async def health(self) -> BackendHealth:
        raise NotImplementedError()

    async def list_models(self) -> List[ModelInfo]:
        raise NotImplementedError()

    async def chat_completions(self, request: ChatCompletionRequest, mapped_model: str) -> ChatCompletionResponse:
        raise NotImplementedError()

    async def chat_completions_stream(self, request: ChatCompletionRequest, mapped_model: str):
        raise NotImplementedError()
