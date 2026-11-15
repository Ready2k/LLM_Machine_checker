"""Stateless helper endpoints for the /chat/ UI: file text extraction, long-term
memory extraction and conversation titles.

Split out of server.py. Config/provider lookups go through ``gateway.server`` at
call time (not import time) to avoid a circular import and so tests can patch
the loaders on the server module.
"""
import io
import json
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from gateway.providers.base import ChatCompletionRequest

router = APIRouter()

@router.post("/chat/extract")
async def extract_file(file: UploadFile = File(...)):
    """Extract plain text from an uploaded file (PDF, DOCX, or text)."""
    MAX_BYTES = 5 * 1024 * 1024
    content = await file.read()
    if len(content) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 5 MB)")

    name = (file.filename or "").lower()

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            raise HTTPException(status_code=501, detail="pypdf not installed — run: pip install pypdf")
        reader = PdfReader(io.BytesIO(content))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()

    elif name.endswith(".docx"):
        try:
            from docx import Document
        except ImportError:
            raise HTTPException(status_code=501, detail="python-docx not installed — run: pip install python-docx")
        doc = Document(io.BytesIO(content))
        text = "\n".join(p.text for p in doc.paragraphs if p.text).strip()

    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(
                status_code=415,
                detail="Binary file — only text, PDF, and DOCX are supported",
            )

    return {"text": text, "filename": file.filename, "chars": len(text)}


# ---------------------------------------------------------------------------
# Chat utility endpoints (memory extraction + title).
#
# Stateless helpers for the /chat/ UI's long-term memory feature: they wrap a
# fixed prompt around the normal routing pipeline (forced to a cheap local
# route) and store nothing server-side. Both always return a usable response;
# on any failure they fall back to empty so the client is never blocked.
# ---------------------------------------------------------------------------

_MEMORY_EXTRACTION_PROMPT = (
    "Extract durable facts about the user worth remembering for future "
    "conversations — preferences, recurring context, projects, constraints. "
    "Ignore one-off task details and anything about the assistant. "
    "Return a JSON array of short factual strings (max 5). "
    "If nothing is worth remembering, return []."
)

_TITLE_PROMPT = (
    "Generate a concise 3 to 6 word title summarising the conversation. "
    "Reply with only the title text — no quotes, no punctuation, no preamble."
)


class ChatMessagesRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)


def _format_transcript(messages: list[dict], limit: int = 6000) -> str:
    """Render user/assistant messages into a plain transcript, length-capped."""
    lines = []
    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            who = "User" if role == "user" else "Assistant"
            lines.append(f"{who}: {content.strip()}")
    return "\n\n".join(lines)[:limit]


def _parse_facts(text: str) -> list[str]:
    """Defensively pull a JSON string-array out of model output."""
    if not text:
        return []
    import re
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    facts = []
    for item in parsed:
        if isinstance(item, str) and item.strip():
            facts.append(item.strip())
    return facts[:5]


async def _run_utility_completion(messages: list[dict], max_tokens: int, route_alias: str = "local-fast") -> str:
    """Run a non-streaming completion through the routing pipeline on a cheap
    local route. Returns the assistant text, or "" if nothing came back."""
    import asyncio
    from gateway import server as _server  # lazy: avoids circular import

    models_config = _server.load_models_config()
    routes_config = _server.load_routes_config().get("routes", {})
    backends_config = _server.load_backends_config()

    route_name, route_config = _server.resolve_model_to_route(route_alias, models_config, routes_config)
    backend_id = _server.select_backend(route_config, backends_config)
    provider = _server.get_provider(backend_id, backends_config)
    if not provider:
        raise HTTPException(status_code=503, detail="No backend available for utility request")

    target_model_name = _server.resolve_target_model(route_alias, models_config)

    chat_req = ChatCompletionRequest(
        model=target_model_name,
        messages=messages,
        stream=False,
        max_tokens=max_tokens,
    )
    response = await asyncio.wait_for(provider.chat_completions(chat_req, target_model_name), timeout=60.0)
    if response.choices:
        return response.choices[0].get("message", {}).get("content", "") or ""
    return ""


@router.post("/chat/memory/extract")
async def memory_extract(req: ChatMessagesRequest):
    """Extract candidate long-term memory facts from a conversation.
    Stateless — stores nothing. Returns {"facts": [...]} (possibly empty)."""
    transcript = _format_transcript(req.messages)
    if not transcript.strip():
        return {"facts": []}
    messages = [
        {"role": "system", "content": _MEMORY_EXTRACTION_PROMPT},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nReturn the JSON array."},
    ]
    try:
        text = await _run_utility_completion(messages, max_tokens=300)
    except Exception:
        return {"facts": []}
    return {"facts": _parse_facts(text)}


@router.post("/chat/title")
async def chat_title(req: ChatMessagesRequest):
    """Generate a short conversation title. Stateless. Returns {"title": "..."}
    (possibly empty — the client falls back to the first message locally)."""
    transcript = _format_transcript(req.messages, limit=2000)
    if not transcript.strip():
        return {"title": ""}
    messages = [
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"Conversation:\n\n{transcript}\n\nTitle:"},
    ]
    try:
        text = await _run_utility_completion(messages, max_tokens=24)
    except Exception:
        return {"title": ""}
    title = (text or "").strip()
    if title:
        title = title.splitlines()[0].strip().strip('"').strip()[:60]
    return {"title": title}


