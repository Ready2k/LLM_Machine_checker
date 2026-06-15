from abc import ABC, abstractmethod
from typing import List, Dict, Any

class LogCollector(ABC):
    """Base interface for remote node log collectors."""
    
    @abstractmethod
    async def get_recent_logs(self, lines: int = 100, grep: str = None) -> List[str]:
        """Fetch the tail of the log, optionally filtered by grep."""
        pass
        
    @abstractmethod
    async def get_diagnostics(self) -> Dict[str, Any]:
        """Fetch hardware diagnostics (e.g. GPU temps)."""
        pass
