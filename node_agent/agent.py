import hmac
import logging
import os
import re
import subprocess
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

# Configuration via environment variables with sensible defaults
HOST = os.getenv("AGENT_HOST", "0.0.0.0")
PORT = int(os.getenv("AGENT_PORT", "8081"))
DEFAULT_SECRET = "default-node-secret-change-me"
SHARED_SECRET = os.getenv("AGENT_SECRET", DEFAULT_SECRET)
logger = logging.getLogger("node_agent")


def check_secret_configured() -> None:
    """Warn (or refuse, with AGENT_STRICT=1) when running on the public default
    secret — the agent exposes server logs and listens on all interfaces."""
    if SHARED_SECRET != DEFAULT_SECRET:
        return
    msg = "AGENT_SECRET is not set: using the insecure built-in default secret."
    if os.getenv("AGENT_STRICT", "").lower() in ("1", "true", "yes"):
        raise SystemExit(msg + " Refusing to start (AGENT_STRICT=1).")
    logger.warning(msg)

app = FastAPI(title="Local AI Fabric - Node Agent")

def verify_token(authorization: Optional[str] = Header(None)):
    """Simple Bearer token verification."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[len("Bearer "):].strip()
    if not hmac.compare_digest(token.encode(), SHARED_SECRET.encode()):
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
    log_dirs = []
    if os.name == "nt":
        local_app_data = os.getenv("LOCALAPPDATA")
        if local_app_data:
            log_dirs.append(os.path.join(local_app_data, "lm-studio", "log"))
            log_dirs.append(os.path.join(os.path.expanduser("~"), ".lmstudio", "server-logs"))
    else:
        log_dirs.append(os.path.expanduser("~/.cache/lm-studio/log"))
        log_dirs.append(os.path.expanduser("~/.lmstudio/server-logs"))
        
    for log_dir in log_dirs:
        if os.path.exists(log_dir):
            log_files = []
            for root, _, files in os.walk(log_dir):
                for f in files:
                    if f.endswith(".log"):
                        log_files.append(os.path.join(root, f))
            if log_files:
                return max(log_files, key=os.path.getmtime)
    return None

@app.get("/logs")
async def get_logs(
    lines: int = Query(100, ge=1, le=5000),
    source: str = Query("ollama", pattern="^(ollama|lmstudio)$"),
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
            content = f.readlines()
            
            if grep:
                filtered = []
                for i, line in enumerate(content):
                    if grep in line:
                        start = max(0, i - 15)
                        end = min(len(content), i + 35)
                        filtered.extend(content[start:end])
                content = filtered
                
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
    check_secret_configured()
    print(f"Starting Node Agent on {HOST}:{PORT}")
    print(f"Ensure the gateway uses the correct AGENT_SECRET.")
    uvicorn.run(app, host=HOST, port=PORT, access_log=False)
