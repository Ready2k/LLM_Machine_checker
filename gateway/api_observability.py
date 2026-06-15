from fastapi import APIRouter, HTTPException, Response
from typing import List, Dict, Any, Optional
import json

from gateway.telemetry.database import get_db
from gateway.telemetry.collectors_http import HttpAgentCollector

router = APIRouter(prefix="/api/observability", tags=["observability"])

@router.get("/traces")
async def get_traces(limit: int = 100, offset: int = 0):
    db = await get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
        
    async with db.execute(
        "SELECT correlation_id, route, backend, model, status, timestamp_start, duration_ms, time_to_first_token_ms, fallback_used FROM llm_requests ORDER BY timestamp_start DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ) as cursor:
        rows = await cursor.fetchall()
        
    traces = []
    for row in rows:
        traces.append({
            "correlation_id": row[0],
            "route": row[1],
            "backend": row[2],
            "model": row[3],
            "status": row[4],
            "timestamp": row[5],
            "duration_ms": row[6],
            "ttft_ms": row[7],
            "fallback_used": bool(row[8])
        })
        
    return {"traces": traces}

@router.get("/traces/{correlation_id}")
async def get_trace_detail(correlation_id: str):
    db = await get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
        
    async with db.execute(
        "SELECT * FROM llm_requests WHERE correlation_id = ?",
        (correlation_id,)
    ) as cursor:
        row = await cursor.fetchone()
        
    if not row:
        raise HTTPException(status_code=404, detail="Trace not found")
        
    columns = [description[0] for description in cursor.description]
    request_detail = dict(zip(columns, row))
    
    # Get events
    async with db.execute(
        "SELECT timestamp, event_type, message, metadata FROM llm_events WHERE correlation_id = ? ORDER BY timestamp ASC",
        (correlation_id,)
    ) as cursor:
        event_rows = await cursor.fetchall()
        
    events = []
    for ev in event_rows:
        events.append({
            "timestamp": ev[0],
            "event_type": ev[1],
            "message": ev[2],
            "metadata": json.loads(ev[3]) if ev[3] else None
        })
        
    return {
        "request": request_detail,
        "events": events
    }

@router.get("/traces/{correlation_id}/logs")
async def get_trace_logs(correlation_id: str):
    db = await get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
        
    async with db.execute(
        "SELECT backend FROM llm_requests WHERE correlation_id = ?",
        (correlation_id,)
    ) as cursor:
        row = await cursor.fetchone()
        
    if not row:
        raise HTTPException(status_code=404, detail="Trace not found")
        
    backend_id = row[0]
    
    # Import inside function to avoid circular import
    from gateway.server import load_backends_config
    backends_config = load_backends_config()
    b_config = backends_config.get("backends", {}).get(backend_id, {})
    
    agent_url = b_config.get("agent_url")
    agent_secret = b_config.get("agent_secret")
    
    if not agent_url or not agent_secret:
        return {"logs": [f"No Node Agent configured for backend '{backend_id}'."]}
        
    try:
        collector = HttpAgentCollector(agent_url, agent_secret, source="ollama")
        # Grep the remote log for the correlation_id (passed as trace_id to Ollama)
        lines = await collector.get_recent_logs(lines=1000, grep=correlation_id)
        if not lines:
            return {"logs": ["No logs found matching correlation ID. Note: Ensure the provider is receiving 'trace_id' in the query string."]}
        return {"logs": lines}
    except Exception as e:
        return {"logs": [f"Error fetching logs from node agent: {str(e)}"]}

@router.post("/audit-export")
async def export_audit_bundle():
    db = await get_db()
    if not db:
        raise HTTPException(status_code=503, detail="Database not ready")
        
    async with db.execute("SELECT * FROM llm_requests ORDER BY timestamp_start DESC LIMIT 1000") as cursor:
        rows = await cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        data = [dict(zip(columns, row)) for row in rows]
        
    for row in data:
        row.pop("prompt_hash", None)
        row.pop("response_hash", None)
        
    return Response(
        content=json.dumps({"version": "1.0", "data": data}, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=audit-bundle.json"}
    )
