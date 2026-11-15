"""/api/events and /api/stats read the SQLite telemetry store (not the legacy JSONL file)."""
import asyncio
import sqlite3

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import gateway.server as server
from gateway.server import app
from gateway.telemetry.database import init_db  # noqa: F401  (schema lives there)

client = TestClient(app)

SCHEMA = """CREATE TABLE llm_requests (
  correlation_id TEXT PRIMARY KEY, route TEXT, backend TEXT, model TEXT, privacy TEXT, status TEXT,
  timestamp_start TEXT, timestamp_end TEXT, duration_ms INTEGER, time_to_first_token_ms INTEGER,
  tokens_in INTEGER, tokens_out INTEGER, fallback_used BOOLEAN, error_code TEXT,
  prompt_hash TEXT, response_hash TEXT, is_stream BOOLEAN)"""

ROWS = [  # id, route, backend, model, privacy, status, ts, dur, ttft, tin, tout, fb, err, stream
    ("a", "local-fast", "pc", "m1", "personal", "success", "2026-01-01T00:00:01", 100, 10, 5, 7, 0, None, 0),
    ("b", "local-fast", "pc", "m1", "personal", "success", "2026-01-01T00:00:02", 200, 20, None, None, 0, None, 1),
    ("c", "local-quality", "mac", "m2", "confidential", "error", "2026-01-01T00:00:03", 300, None, None, None, 1, "boom", 0),
    ("d", "local-quality", "mac", "m2", "personal", "success", "2026-01-01T00:00:04", None, None, None, None, 0, None, 0),
]


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute(SCHEMA)
    for r in ROWS:
        con.execute("INSERT INTO llm_requests (correlation_id, route, backend, model, privacy, status, "
                    "timestamp_start, timestamp_end, duration_ms, time_to_first_token_ms, tokens_in, "
                    "tokens_out, fallback_used, error_code, is_stream) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13]))
    con.commit(); con.close()
    conn = asyncio.run(aiosqlite.connect(path))

    async def fake_get_db():
        return conn
    monkeypatch.setattr(server, "get_db", fake_get_db)
    yield
    asyncio.run(conn.close())


def test_events_newest_first_with_legacy_shape(seeded):
    d = client.get("/api/events").json()
    assert d["total_on_disk"] == 4 and d["returned"] == 4
    assert [e["timestamp"][-2:] for e in d["events"]] == ["04", "03", "02", "01"]
    e = d["events"][2]
    assert e["latency_ms"] == 200 and e["ttft_ms"] == 20 and e["stream"] is True and e["fallback_used"] is False
    assert d["events"][1]["fallback_used"] is True and d["events"][1]["error_code"] == "boom"
    assert d["events"][3]["tokens_in"] == 5 and d["events"][3]["tokens_out"] == 7


def test_events_status_filter_and_limit(seeded):
    d = client.get("/api/events?status=error").json()
    assert d["returned"] == 1 and d["events"][0]["status"] == "error" and d["total_on_disk"] == 4
    assert client.get("/api/events?limit=2").json()["returned"] == 2


def test_stats_aggregates(seeded):
    s = client.get("/api/stats").json()
    assert s["total_requests"] == 4 and s["error_count"] == 1 and s["success_rate_pct"] == 75.0
    assert s["fallback_count"] == 1 and s["fallback_rate_pct"] == 25.0 and s["stream_count"] == 1
    assert s["requests_by_route"] == {"local-fast": 2, "local-quality": 2}
    assert s["requests_by_backend"] == {"pc": 2, "mac": 2}
    assert s["privacy_breakdown"] == {"personal": 3, "confidential": 1}
    assert s["avg_latency_ms_by_backend"] == {"pc": 150, "mac": 300}     # NULL durations ignored
    assert s["p95_latency_ms_by_backend"] == {"pc": 200, "mac": 300}
    assert s["period_start"] == "2026-01-01T00:00:01" and s["period_end"] == "2026-01-01T00:00:04"


def test_no_database_returns_empty_not_error(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(server, "get_db", none)
    assert client.get("/api/events").json() == {"events": [], "total_on_disk": 0, "returned": 0}
    s = client.get("/api/stats").json()
    assert s["total_requests"] == 0 and s["period_start"] is None
