import httpx
from typing import List, Dict, Any
from gateway.telemetry.collectors import LogCollector

class HttpAgentCollector(LogCollector):
    """
    Connects to the standalone node_agent running on a remote node 
    (e.g. Windows RTX PC) to retrieve logs and diagnostics via HTTP.
    """
    def __init__(self, agent_url: str, secret: str, source: str = "ollama"):
        self.base_url = agent_url.rstrip("/")
        self.secret = secret
        self.source = source
        self.headers = {"Authorization": f"Bearer {self.secret}"}
        
    async def get_recent_logs(self, lines: int = 100, grep: str = None) -> List[str]:
        params = {"lines": lines, "source": self.source}
        if grep:
            params["grep"] = grep
            
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/logs",
                    headers=self.headers,
                    params=params,
                    timeout=5.0
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("lines", [])
        except Exception as e:
            # Silently fail for observability, don't crash gateway
            return [f"[Collector Error] Failed to fetch logs from {self.base_url}: {str(e)}"]

    async def get_diagnostics(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.base_url}/diagnostics/gpu",
                    headers=self.headers,
                    timeout=3.0
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            return {"status": "error", "message": str(e)}
