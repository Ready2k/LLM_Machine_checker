"""
Web search grounding for the gateway.

Turns a user query into a set of *real, fetched* web sources so a local model can
answer with citations instead of hallucinating URLs. The flow is deliberately
"retrieve-then-answer" (RAG), not native tool-calling: small local models are
unreliable at deciding to call tools, but reliable at summarising provided text
with citations. Forcing grounding is what actually stops the hallucination.

Search goes through a self-hosted SearXNG instance (keeps the *query* on the LAN —
SearXNG proxies to upstream engines server-side). Page text is then fetched from
the open web and reduced to readable content with trafilatura.

Privacy note: page fetches DO leave the LAN. The caller is responsible for
gating this behind the privacy policy (see is_web_search_allowed in server.py).

No prompt content is logged here — telemetry records counts/latency only.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

import httpx

try:  # optional dependency — grounding is treated as unavailable if missing
    import trafilatura
except ImportError:  # pragma: no cover - exercised only without the dep installed
    trafilatura = None


@dataclass
class Source:
    """A single fetched web source. ``text`` is the extracted readable content."""

    n: int
    title: str
    url: str
    text: str = ""

    def to_meta(self) -> dict:
        """Lightweight metadata for the client (no body text)."""
        return {"n": self.n, "title": self.title, "url": self.url}


# The grounding rules are the actual fix: a strict instruction to answer ONLY
# from the supplied sources, admit ignorance, and never fabricate URLs.
GROUNDING_RULES = (
    "You are answering using ONLY the numbered web sources provided below.\n"
    "Rules (follow strictly):\n"
    "- Base every factual claim on these sources. Do not rely on prior knowledge "
    "to fill gaps.\n"
    "- Cite sources inline with bracketed numbers, e.g. [1] or [2][3].\n"
    "- If the sources do not contain the answer, say so plainly. Do NOT guess or "
    "invent details.\n"
    "- Never invent, complete, or alter URLs. Only the URLs listed below are real.\n"
)


def build_grounding_block(sources: list[Source]) -> str:
    """Render the system message that grounds the model in the fetched sources."""
    blocks = [f"[{s.n}] {s.title}\nURL: {s.url}\n{s.text}".strip() for s in sources]
    return f"{GROUNDING_RULES}\nWeb sources:\n\n" + "\n\n---\n\n".join(blocks)


class WebSearchClient:
    """SearXNG-backed search + page extraction. Construct from the
    ``tools.web_search`` config block."""

    def __init__(self, config: dict):
        config = config or {}
        self.enabled = bool(config.get("enabled", False))
        # Env override wins so deployments can set the node address without
        # editing committed config (mirrors the backends.local.yaml pattern).
        self.searxng_url = (
            os.environ.get("SEARXNG_URL") or config.get("searxng_url", "")
        ).rstrip("/")
        self.n_results = int(config.get("n_results", 3))
        self.max_chars_per_source = int(config.get("max_chars_per_source", 6000))
        self.search_timeout = float(config.get("search_timeout_s", 6.0))
        self.fetch_timeout = float(config.get("fetch_timeout_s", 6.0))
        self.engines: Optional[str] = config.get("engines") or None
        self.user_agent = config.get(
            "user_agent", "LocalAIFabric/1.0 (+web-grounding)"
        )

    @property
    def available(self) -> bool:
        """True only if grounding can actually run (enabled, configured, dep present)."""
        return bool(self.enabled and self.searxng_url and trafilatura is not None)

    async def search(self, query: str) -> list[Source]:
        """Query SearXNG's JSON API and return the top results (no body text yet)."""
        params = {"q": query, "format": "json", "safesearch": "0"}
        if self.engines:
            params["engines"] = self.engines
        async with httpx.AsyncClient(
            timeout=self.search_timeout, headers={"User-Agent": self.user_agent}
        ) as client:
            resp = await client.get(f"{self.searxng_url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()

        sources: list[Source] = []
        for r in data.get("results", []):
            url = r.get("url")
            if not url:
                continue
            sources.append(
                Source(n=len(sources) + 1, title=(r.get("title") or url).strip(), url=url)
            )
            if len(sources) >= self.n_results:
                break
        return sources

    async def _fetch_one(self, client: httpx.AsyncClient, src: Source) -> None:
        """Fetch + extract a single source. Failures leave ``text`` empty."""
        try:
            resp = await client.get(src.url)
            resp.raise_for_status()
            html = resp.text
        except Exception:
            src.text = ""
            return
        extracted = ""
        if trafilatura is not None:
            extracted = (
                trafilatura.extract(html, include_comments=False, include_tables=False)
                or ""
            )
        src.text = extracted.strip()[: self.max_chars_per_source]

    async def fetch_all(self, sources: list[Source]) -> None:
        """Fetch + extract every source concurrently, in place."""
        async with httpx.AsyncClient(
            timeout=self.fetch_timeout,
            headers={"User-Agent": self.user_agent},
            follow_redirects=True,
        ) as client:
            await asyncio.gather(*(self._fetch_one(client, s) for s in sources))

    async def gather(self, query: str) -> list[Source]:
        """Full pipeline: search → fetch → keep only sources with real text,
        renumbered 1..N. Returns [] when nothing usable came back."""
        sources = await self.search(query)
        if not sources:
            return []
        await self.fetch_all(sources)
        good = [s for s in sources if s.text]
        for i, s in enumerate(good, start=1):
            s.n = i
        return good
