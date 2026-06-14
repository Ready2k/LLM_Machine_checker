from fastapi import Header, Request
from typing import Optional

def extract_privacy_level(request_metadata: Optional[dict], x_ai_privacy: Optional[str], default_privacy: str = "personal") -> str:
    """
    Extracts the privacy level from the request.
    Header privacy > metadata privacy > security default.
    """
    if x_ai_privacy:
        return x_ai_privacy.lower()
        
    if request_metadata and "privacy" in request_metadata:
        return request_metadata["privacy"].lower()
        
    return default_privacy.lower()

def is_privacy_allowed(requested_privacy: str, route_config: dict) -> bool:
    """
    Checks if the requested privacy level is allowed by the route's policy.
    """
    allowed = route_config.get("privacy_allowed", [])
    if not allowed:
        # If not explicitly configured, be conservative
        return requested_privacy in ["public"]
        
    return requested_privacy in allowed

def can_fallback_to_cloud(requested_privacy: str, route_config: dict, security_config: dict) -> bool:
    """
    Checks if cloud fallback is allowed for this privacy level and route.
    """
    if requested_privacy == "restricted":
        return False
        
    route_allows_cloud = route_config.get("cloud_fallback", False)
    if not route_allows_cloud:
        return False
        
    # Extra check against global security config if needed
    if not security_config.get("allow_cloud_by_default", False) and not route_allows_cloud:
         return False
         
    return True
