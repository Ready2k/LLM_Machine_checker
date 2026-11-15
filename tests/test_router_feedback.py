"""Auto-router feedback loop + observability (/api/router/*, scripts/autoroute_feedback.py)."""
import asyncio
import importlib.util
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest
from fastapi.testclient import TestClient

import gateway.server as server
from gateway import api_router
from gateway.policy.autoroute import classify
from gateway.telemetry.database import ROUTER_FEEDBACK_SCHEMA
from tests.test_events_stats import SCHEMA as REQUESTS_SCHEMA

client = TestClient(server.app)
FAST, QUALITY, PRIVATE = "local-fast", "local-quality", "private-docs"
EVENTS_SCHEMA = ("CREATE TABLE llm_events (id INTEGER PRIMARY KEY AUTOINCREMENT, correlation_id TEXT, "
                 "timestamp TEXT, event_type TEXT, message TEXT, metadata TEXT)")
SECRET_PROMPT = "Explain how to deploy a FastAPI app to Kubernetes with zero downtime"

_spec = importlib.util.spec_from_file_location(
    "autoroute_feedback", Path(__file__).resolve().parent.parent / "scripts" / "autoroute_feedback.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


# cid, age, route, stage, confidence, status, duration, tokens_in, tokens_out
DECISIONS = [
    ("f1", dict(minutes=5), FAST, "complexity", 0.95, "success", 100, 10, 20),
    ("f2", dict(minutes=10), FAST, "complexity", 0.55, "success", 300, 10, 20),
    ("q1", dict(minutes=20), QUALITY, "complexity", 0.60, "success", 2000, 50, 400),
    ("p1", dict(hours=5), PRIVATE, "privacy_content", 1.0, "success", 900, 5, 5),
    ("f3", dict(hours=6), FAST, "complexity", 0.90, "error", 50, None, None),
    ("q2", dict(days=3), QUALITY, "complexity", 0.88, "success", 4000, 60, 600),
    ("old", dict(days=9), QUALITY, "complexity", 0.88, "success", 9999, 1, 1),
]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute(REQUESTS_SCHEMA)
    con.execute(EVENTS_SCHEMA)
    con.execute(ROUTER_FEEDBACK_SCHEMA)
    for cid, age, route, stage, conf, status, dur, tin, tout in DECISIONS:
        ts = _ago(**age)
        meta = {"route": route, "stage": stage, "score": 2.5, "threshold": 3.0, "confidence": conf,
                "signals": [{"name": "depth_mild", "weight": 1.0, "count": 1}], "reason": f"reason-{cid}"}
        con.execute("INSERT INTO llm_events (correlation_id, timestamp, event_type, message, metadata) "
                    "VALUES (?,?,?,?,?)", (cid, ts, "router.auto", "auto", json.dumps(meta)))
        con.execute("INSERT INTO llm_events (correlation_id, timestamp, event_type, message) VALUES (?,?,?,?)",
                    (cid, ts, "request.complete", "done"))
        con.execute("INSERT INTO llm_requests (correlation_id, route, backend, model, privacy, status, "
                    "timestamp_start, duration_ms, tokens_in, tokens_out, fallback_used, is_stream) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,0,0)", (cid, route, "pc", route, "personal", status, ts, dur, tin, tout))
    # a non-auto request must not show up anywhere
    con.execute("INSERT INTO llm_requests (correlation_id, route, status, timestamp_start, duration_ms) "
                "VALUES ('manual', ?, 'success', ?, 1)", (FAST, _ago(minutes=1)))
    con.commit(); con.close()
    conn = asyncio.run(aiosqlite.connect(path))

    async def fake_get_db():
        return conn
    monkeypatch.setattr(server, "get_db", fake_get_db)
    monkeypatch.setattr(server, "load_autoroute_config", lambda: {})
    monkeypatch.setattr(api_router, "_load_telemetry_config", lambda: {"enabled": True})
    yield path
    asyncio.run(conn.close())


def _rows(path, sql="SELECT * FROM router_feedback ORDER BY id"):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Feedback capture
# ---------------------------------------------------------------------------

def test_feedback_records_trace_hash_and_opt_in_prompt(db_path):
    r = client.post("/api/router/feedback", json={
        "correlation_id": "f2", "desired": "quality", "note": " needed depth ", "prompt": SECRET_PROMPT})
    assert r.status_code == 200
    assert r.json() == {"id": 1, "actual_route": FAST, "desired_route": QUALITY, "prompt_stored": True}
    row = _rows(db_path)[0]
    assert row["actual_route"] == FAST and row["desired_route"] == QUALITY and row["status"] == "new"
    assert row["reason"] == "reason-f2" and row["confidence"] == 0.55 and row["stage"] == "complexity"
    assert json.loads(row["signals"])[0]["name"] == "depth_mild"
    assert row["prompt_hash"] == api_router.prompt_fingerprint(SECRET_PROMPT) and len(row["prompt_hash"]) == 16
    assert row["prompt_text"] == SECRET_PROMPT and row["note"] == "needed depth"


def test_feedback_without_prompt_stores_no_content(db_path):
    r = client.post("/api/router/feedback", json={"correlation_id": "f1", "desired": QUALITY})
    assert r.status_code == 200 and r.json()["prompt_stored"] is False
    row = _rows(db_path)[0]
    assert row["prompt_text"] is None and row["prompt_hash"] is None


@pytest.mark.parametrize("cid,desired", [("f1", "private"), ("p1", "fast")])
def test_prompt_never_stored_when_private_route_involved(db_path, cid, desired):
    r = client.post("/api/router/feedback", json={"correlation_id": cid, "desired": desired, "prompt": "my salary is 85k"})
    assert r.status_code == 200 and r.json()["prompt_stored"] is False
    row = _rows(db_path)[0]
    assert row["prompt_text"] is None and row["prompt_hash"]  # fingerprint only


def test_prompt_storage_can_be_disabled_in_config(db_path, monkeypatch):
    monkeypatch.setattr(api_router, "_load_telemetry_config", lambda: {"feedback_store_prompts": False})
    r = client.post("/api/router/feedback", json={"correlation_id": "f1", "desired": "quality", "prompt": SECRET_PROMPT})
    assert r.json()["prompt_stored"] is False and _rows(db_path)[0]["prompt_text"] is None


def test_feedback_validation(db_path):
    post = lambda **b: client.post("/api/router/feedback", json=b).status_code  # noqa: E731
    assert post(correlation_id="nope", desired="quality") == 404
    assert post(correlation_id="f1", desired="fast") == 422            # already went there
    assert post(correlation_id="f1", desired="cloud-best") == 422
    assert post(correlation_id="manual", desired="quality") == 404     # not an auto request
    # No decision event (telemetry off) but the client knows what happened from the headers.
    assert post(correlation_id="x9", desired="quality", actual_route=FAST, reason="score=1<3") == 200
    assert _rows(db_path)[0]["reason"] == "score=1<3"


def test_resubmitting_updates_the_same_entry(db_path):
    client.post("/api/router/feedback", json={"correlation_id": "f1", "desired": "quality"})
    client.post("/api/router/feedback", json={"correlation_id": "f1", "desired": "private", "note": "second"})
    rows = _rows(db_path)
    assert len(rows) == 1 and rows[0]["desired_route"] == PRIVATE and rows[0]["note"] == "second"


def test_feedback_listing_never_returns_prompt_text(db_path):
    client.post("/api/router/feedback", json={"correlation_id": "f2", "desired": "quality", "prompt": SECRET_PROMPT})
    body = client.get("/api/router/feedback")
    assert SECRET_PROMPT not in body.text
    item = body.json()["feedback"][0]
    assert item["has_prompt"] is True and "prompt_text" not in item and item["desired_route"] == QUALITY
    assert client.get("/api/router/feedback?status=promoted").json()["returned"] == 0


def test_feedback_endpoints_need_the_api_key_from_the_lan():
    from gateway.policy.auth import is_protected
    assert is_protected("/api/router/feedback") and is_protected("/api/router/stats")


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

def test_stats_split_latency_and_volume_per_window(db_path):
    s = client.get("/api/router/stats").json()
    h1, d1, d7 = (s["windows"][w] for w in ("1h", "24h", "7d"))
    assert (h1["total"], d1["total"], d7["total"]) == (3, 5, 6)        # 9-day-old + non-auto rows excluded
    assert h1["routes"][FAST]["share_pct"] == 66.7 and h1["routes"][QUALITY]["share_pct"] == 33.3
    assert PRIVATE not in h1["routes"] and d1["routes"][PRIVATE]["requests"] == 1
    fast = d1["routes"][FAST]
    assert fast["requests"] == 3 and fast["errors"] == 1
    assert fast["p50_ms"] == 300 and fast["p95_ms"] == 300             # error row's latency ignored
    assert fast["tokens_in"] == 20 and fast["tokens_out"] == 40
    q7 = d7["routes"][QUALITY]
    assert q7["requests"] == 2 and q7["p50_ms"] == 4000 and q7["tokens_out"] == 1000
    assert q7["avg_confidence"] == 0.74
    assert h1["low_confidence"] == 2 and d7["stages"] == {"complexity": 5, "privacy_content": 1}
    assert s["feedback"]["new"] == 0


def test_stats_counts_feedback(db_path):
    client.post("/api/router/feedback", json={"correlation_id": "f2", "desired": "quality"})
    assert client.get("/api/router/stats").json()["feedback"]["new"] == 1


def test_decisions_list_reason_confidence_and_feedback(db_path):
    client.post("/api/router/feedback", json={"correlation_id": "f2", "desired": "quality"})
    d = client.get("/api/router/decisions?limit=3").json()
    assert [x["correlation_id"] for x in d["decisions"]] == ["old", "q2", "f3"]   # newest event id first
    all_ = {x["correlation_id"]: x for x in client.get("/api/router/decisions").json()["decisions"]}
    assert len(all_) == 7 and "manual" not in all_
    f2 = all_["f2"]
    assert f2["route"] == FAST and f2["reason"] == "reason-f2" and f2["confidence"] == 0.55
    assert f2["score"] == 2.5 and f2["threshold"] == 3.0 and f2["latency_ms"] == 300
    assert f2["low_confidence"] is True and f2["feedback"] == {"desired_route": QUALITY, "status": "new"}
    assert all_["f1"]["feedback"] is None and all_["p1"]["low_confidence"] is False


def test_misroute_candidates_are_the_low_confidence_decisions(db_path):
    d = client.get("/api/router/decisions?candidates=true").json()
    assert {x["correlation_id"] for x in d["decisions"]} == {"f2", "q1"}
    d = client.get("/api/router/decisions?candidates=true&max_confidence=0.56").json()
    assert [x["correlation_id"] for x in d["decisions"]] == ["f2"]


def test_no_database_is_empty_not_an_error(monkeypatch):
    async def none():
        return None
    monkeypatch.setattr(server, "get_db", none)
    assert client.get("/api/router/stats").json()["windows"]["24h"]["total"] == 0
    assert client.get("/api/router/decisions").json() == {"decisions": [], "returned": 0}
    assert client.post("/api/router/feedback", json={"correlation_id": "a", "desired": "fast"}).status_code == 503


# ---------------------------------------------------------------------------
# Feedback -> regression test (scripts/autoroute_feedback.py)
# ---------------------------------------------------------------------------

CORPUS_STUB = (
    'import pytest\nfrom gateway.policy.autoroute import classify\n'
    'FAST, QUALITY, PRIVATE = "local-fast", "local-quality", "private-docs"\n\n'
    'FEEDBACK_CORPUS = [\n    # feedback-corpus-begin\n    # feedback-corpus-end\n]\n\n\n'
    '@pytest.mark.parametrize("text,expected", FEEDBACK_CORPUS)\n'
    'def test_reported_misroutes(text, expected):\n'
    '    assert classify([{"role": "user", "content": text}]).route == expected\n'
)


def _corpus(path):
    ns = {}
    exec(compile(path.read_text(), str(path), "exec"), ns)
    return ns["FEEDBACK_CORPUS"]


def test_real_test_file_has_the_corpus_markers():
    src = cli.DEFAULT_TESTS.read_text()
    assert cli.END_MARKER in src and "def test_reported_misroutes" in src


def test_promote_appends_case_and_scrubs_stored_prompt(db_path, tmp_path, capsys):
    tricky = 'He said "it\'s fine"\nthen asked: why is the build slow?'
    client.post("/api/router/feedback", json={"correlation_id": "f2", "desired": "quality", "prompt": tricky, "note": "x"})
    client.post("/api/router/feedback", json={"correlation_id": "q1", "desired": "fast"})   # no prompt
    tests = tmp_path / "test_corpus.py"
    tests.write_text(CORPUS_STUB)

    assert cli.main(["--db", str(db_path), "promote", "--all", "--dry-run", "--tests", str(tests)]) == 0
    assert _corpus(tests) == [] and _rows(db_path)[0]["status"] == "new"

    assert cli.main(["--db", str(db_path), "promote", "--all", "--tests", str(tests)]) == 0
    out = capsys.readouterr().out
    assert "#2: no prompt stored" in out and f"currently routes to {classify([{'role': 'user', 'content': tricky}]).route}" in out
    assert _corpus(tests) == [(tricky, QUALITY)]                  # quoting/newlines survive
    assert "# fb#1 " in tests.read_text() and "was local-fast: x" in tests.read_text()
    rows = _rows(db_path)
    assert rows[0]["status"] == "promoted" and rows[0]["prompt_text"] is None
    assert rows[1]["status"] == "new"

    # --text supplies a synthetic prompt for entries that have none; duplicates are not re-added.
    assert cli.main(["--db", str(db_path), "promote", "2", "--text", "hi", "--tests", str(tests)]) == 0
    assert cli.main(["--db", str(db_path), "promote", "2", "--text", "hi", "--tests", str(tests)]) == 0
    assert _corpus(tests) == [(tricky, QUALITY), ("hi", FAST)]
    assert "already routes correctly" in capsys.readouterr().out


def test_list_and_dismiss(db_path, capsys):
    client.post("/api/router/feedback", json={"correlation_id": "f2", "desired": "quality", "prompt": SECRET_PROMPT})
    assert cli.main(["--db", str(db_path), "list"]) == 0
    out = capsys.readouterr().out
    assert "#1" in out and "local-fast -> should be local-quality" in out and SECRET_PROMPT not in out
    assert cli.main(["--db", str(db_path), "dismiss", "1"]) == 0
    row = _rows(db_path)[0]
    assert row["status"] == "dismissed" and row["prompt_text"] is None
    cli.main(["--db", str(db_path), "list"])
    assert "No pending feedback" in capsys.readouterr().out

