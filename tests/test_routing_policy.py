import pytest
from fastapi import HTTPException
from gateway.policy.privacy import extract_privacy_level, is_privacy_allowed, can_fallback_to_cloud
from gateway.policy.routing import resolve_model_to_route, select_backend
import logging

def test_header_privacy_overrides_metadata_privacy():
    # Header > Metadata > Default
    privacy = extract_privacy_level({"privacy": "confidential"}, "personal", "public")
    assert privacy == "personal"

def test_metadata_privacy_used_when_no_header():
    privacy = extract_privacy_level({"privacy": "confidential"}, None, "public")
    assert privacy == "confidential"

def test_default_privacy_used_when_missing():
    privacy = extract_privacy_level(None, None, "personal")
    assert privacy == "personal"

def test_is_privacy_allowed():
    route_config = {"privacy_allowed": ["personal", "confidential"]}
    assert is_privacy_allowed("personal", route_config) is True
    assert is_privacy_allowed("restricted", route_config) is False

def test_restricted_privacy_blocks_cloud():
    route_config = {"cloud_fallback": True, "privacy_allowed": ["public", "restricted"]}
    security_config = {"allow_cloud_by_default": True}
    assert can_fallback_to_cloud("restricted", route_config, security_config) is False

def test_old_macbook_blocked_even_if_route_config_points_to_it():
    # Provide a mock config where a route explicitly asks for old-macbook as fallback
    route_config = {"primary": "pc-rtx2070", "fallback": "old-macbook", "enabled": True}
    backends_config = {
        "backends": {
            "pc-rtx2070": {"enabled": False}, # Force fallback
            "old-macbook": {"enabled": True, "inference_enabled": False} # The block
        }
    }
    
    with pytest.raises(HTTPException) as exc:
        select_backend(route_config, backends_config)
    
    assert exc.value.status_code == 503
    assert exc.value.detail["type"] == "no_backends_available"

def test_prompt_content_not_logged(capsys):
    from gateway.telemetry.events import log_routing_decision
    log_routing_decision("local-fast", "qwen2.5-coder", "personal", "pc-rtx2070", False)
    
    captured = capsys.readouterr()
    assert "qwen2.5-coder" in captured.out
    assert "prompt" not in captured.out.lower()
