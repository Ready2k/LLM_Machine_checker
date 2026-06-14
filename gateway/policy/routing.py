from fastapi import HTTPException
from typing import Optional, Tuple, Dict, Any

def resolve_model_to_route(model_alias: str, models_config: dict, routes_config: dict) -> Tuple[str, dict]:
    """
    Resolves a requested model alias to its route configuration.
    """
    models = models_config.get("models", {})
    if model_alias not in models:
        # Assume model_alias might be a direct route name (for MVP simplicity)
        if model_alias in routes_config:
            route_name = model_alias
        else:
            raise HTTPException(
                status_code=400,
                detail={"message": f"Unknown model alias or route: {model_alias}", "type": "unknown_model", "code": "invalid_request"}
            )
    else:
        route_name = models[model_alias].get("route")
        
    if route_name not in routes_config:
         raise HTTPException(
             status_code=400,
             detail={"message": f"Route {route_name} is not configured", "type": "route_not_configured", "code": "invalid_config"}
         )
         
    route_config = routes_config[route_name]
    
    # Enforce disabled route behaviour
    if route_config.get("enabled") is False or route_config.get("status") == "not_configured":
        raise HTTPException(
            status_code=400,
            detail={"message": f"Route {route_name} is not configured", "type": "route_not_configured", "code": "route_disabled"}
        )
        
    return route_name, route_config

def select_backend(route_config: dict, backends_config: dict) -> str:
    """
    Selects the best available backend for the route.
    For MVP, just picks primary if enabled, else fallback.
    Returns the backend_id.
    """
    backends = backends_config.get("backends", {})
    
    primary_id = route_config.get("primary")
    fallback_id = route_config.get("fallback")
    
    def is_backend_eligible(bid: str) -> bool:
        if not bid:
            return False
        b_config = backends.get(bid, {})
        if not b_config.get("enabled", False):
            return False
        if b_config.get("inference_enabled", True) is False:
            return False
        return True

    if is_backend_eligible(primary_id):
        return primary_id
        
    if is_backend_eligible(fallback_id):
        return fallback_id
        
    raise HTTPException(
        status_code=503,
        detail={"message": "No eligible backends available for route", "type": "no_backends_available", "code": "service_unavailable"}
    )
