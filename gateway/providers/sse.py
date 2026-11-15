"""Streaming helpers: pull token usage out of an OpenAI-style SSE stream.

``UsageTap`` re-frames the byte stream into complete SSE events, forwards them
unchanged, records the ``usage`` block when one appears, and (optionally) drops
the usage-only chunk (``choices: []``) that we asked the backend to add for our
own telemetry when the *client* didn't request it.
"""
import json
import re
from typing import Iterable, Optional

_SEP = re.compile(rb"\r?\n\r?\n")


class UsageTap:
    def __init__(self, drop_usage_only: bool = False):
        self.drop_usage_only = drop_usage_only
        self.usage: Optional[dict] = None
        self._buf = b""

    def feed(self, chunk: bytes) -> Iterable[bytes]:
        self._buf += chunk
        out = []
        while True:
            m = _SEP.search(self._buf)
            if not m:
                break
            event, sep = self._buf[:m.start()], self._buf[m.start():m.end()]
            self._buf = self._buf[m.end():]
            if not self._inspect(event):
                continue
            out.append(event + sep)
        return out

    def flush(self) -> Iterable[bytes]:
        rest, self._buf = self._buf, b""
        if not rest:
            return []
        return [rest] if self._inspect(rest) else []

    def _inspect(self, event: bytes) -> bool:
        """Record usage from this event; return False if it should be dropped."""
        for line in event.splitlines():
            if not line.startswith(b"data:"):
                continue
            data = line[5:].strip()
            if not data or data == b"[DONE]":
                continue
            try:
                obj = json.loads(data)
            except ValueError:
                continue
            usage = obj.get("usage") if isinstance(obj, dict) else None
            if usage:
                self.usage = usage
                if self.drop_usage_only and not obj.get("choices"):
                    return False
        return True

    @property
    def tokens(self) -> tuple:
        u = self.usage or {}
        return u.get("prompt_tokens"), u.get("completion_tokens")
