import os
import re
import subprocess
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

# Configuration via environment variables with sensible defaults
HOST = os.getenv("AGENT_HOST", "0.0.0.0")
PORT = int(os.getenv("AGENT_PORT", "8081"))
SHARED_SECRET = os.getenv("AGENT_SECRET", "default-node-secret-change-me")

app = FastAPI(title="Local AI Fabric - Node Agent")

def verify_token(authorization: Optional[str] = Header(None)):
    """Simple Bearer token verification."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization.split(" ")[1]
    if token != SHARED_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

@app.get("/health")
async def health(authorization: Optional[str] = Header(None)):
    verify_token(authorization)
    return {"status": "ok", "version": "1.0.0"}

def _get_ollama_log_path() -> Optional[str]:
    # Typical path on Windows: %LOCALAPPDATA%\Ollama\server.log
    # Typical path on Mac: ~/.ollama/logs/server.log
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return os.path.join(local_app_data, "Ollama", "server.log")
    else:
        return os.path.expanduser("~/.ollama/logs/server.log")
    return None

def _get_lmstudio_log_path() -> Optional[str]:
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            return os.path.join(local_app_data, "lm-studio", "log", "lms-server.log")
    else:
        # On Mac, LM studio logs are in ~/.cache/lm-studio/log/
        # There might be multiple, but we'll try lms-server.log or server.log
        return os.path.expanduser("~/.cache/lm-studio/log/lms-server.log")
    return None

@app.get("/logs")
async def get_logs(
    lines: int = Query(100, ge=1, le=5000),
    source: str = Query("ollama", regex="^(ollama|lmstudio)$"),
    grep: Optional[str] = None,
    authorization: Optional[str] = Header(None)
):
    """Retrieve tail of server logs, optionally filtered by grep."""
    verify_token(authorization)
    
    log_path = None
    if source == "ollama":
        log_path = _get_ollama_log_path()
    elif source == "lmstudio":
        log_path = _get_lmstudio_log_path()
        
    if not log_path or not os.path.exists(log_path):
        raise HTTPException(status_code=404, detail=f"Log file not found for source {source}")
        
    try:
        # Simple tail implementation
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            # Read all lines (inefficient for massive files, but ok for daily rolling logs)
            # In a production agent, we'd seek from the end.
            content = f.readlines()
            
            if grep:
                content = [line for line in content if grep in line]
                
            tail = content[-lines:]
            return {"source": source, "lines": tail}
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/diagnostics/gpu")
async def gpu_diagnostics(authorization: Optional[str] = Header(None)):
    """Run nvidia-smi and return diagnostics if available."""
    verify_token(authorization)
    
    try:
        # Call nvidia-smi
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.total,memory.used", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=False
        )
        if result.returncode != 0:
            return {"status": "error", "message": "nvidia-smi returned non-zero or not found"}
            
        gpus = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 5:
                gpus.append({
                    "name": parts[0],
                    "temperature_c": parts[1],
                    "utilization_pct": parts[2].replace(' %', ''),
                    "memory_total_mb": parts[3].replace(' MiB', ''),
                    "memory_used_mb": parts[4].replace(' MiB', '')
                })
        return {"status": "success", "gpus": gpus}
    except FileNotFoundError:
        return {"status": "not_found", "message": "nvidia-smi not available on PATH"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    print(f"Starting Node Agent on {HOST}:{PORT}")
    print(f"Ensure the gateway uses the correct AGENT_SECRET.")
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)
