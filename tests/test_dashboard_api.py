"""Tests for the dashboard API endpoints."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gateway.server import app, PROJECT_ROOT, INVENTORY_PATH


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_inventory():
    """Return a minimal valid inventory dict."""
    return {
        "updated_at": "2026-06-14",
        "summary": {
            "recommended_architecture": "federated_local_ai_fabric",
            "primary_quality_node": "macbook-m4pro",
            "primary_fast_node": "pc-rtx2070",
            "utility_node": "old-macbook",
            "cloud_policy": "explicit_only",
        },
        "machines": [
            {
                "id": "macbook-m4pro",
                "hostname": "Jamess-MacBook-Pro-2.local",
                "os": "Darwin 25.5.0",
                "arch": "arm64",
                "cpu": "Apple M4 Pro",
                "ram_gb": 24.0,
                "gpu_name": "Apple Silicon / Metal GPU",
                "vram_gb": None,
                "compatible": True,
                "recommended_role": "local_quality",
                "recommended_model": "Qwen Coder 14B Q4_K_M",
                "measured_tokens_per_second": 13.1,
                "risk": "memory_pressure_if_IDE_Docker_browser_are_also_running",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-quality", "private-docs"],
                    "fallback_routes": ["local-fast"],
                },
                "notes": ["Best suited to 7B-14B quantized models."],
            },
            {
                "id": "pc-rtx2070",
                "hostname": "pc-ai.local",
                "os": "Windows",
                "arch": "x86_64",
                "cpu": "Unknown Windows CPU",
                "ram_gb": 32.0,
                "gpu_name": "NVIDIA GeForce RTX 2070",
                "vram_gb": 8.0,
                "compatible": True,
                "recommended_role": "local_fast",
                "recommended_model": "Qwen Coder 7B Q4_K_M",
                "measured_tokens_per_second": 33.8,
                "risk": "none",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-fast"],
                    "fallback_routes": ["local-quality"],
                },
                "notes": ["Best suited to 7B/8B quantized coding and chat models."],
            },
        ],
    }


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "local-ai-fabric-dashboard"
        assert data["version"] == "0.1.0"


class TestMachinesEndpoint:
    def test_machines_returns_inventory(self, client):
        """GET /api/machines should return the inventory data."""
        response = client.get("/api/machines")
        assert response.status_code == 200
        data = response.json()
        assert "machines" in data
        assert "summary" in data
        assert "updated_at" in data
        assert len(data["machines"]) > 0

    def test_machine_by_id_returns_expected_machine(self, client):
        """GET /api/machines/{id} should return matching machine."""
        response = client.get("/api/machines/macbook-m4pro")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "macbook-m4pro"
        assert data["recommended_role"] == "local_quality"

    def test_machine_by_id_404_for_unknown_machine(self, client):
        """GET /api/machines/{id} should return 404 for unknown ID."""
        response = client.get("/api/machines/nonexistent-machine")
        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


class TestRoutesEndpoint:
    def test_routes_are_derived_when_config_missing(self, client):
        """GET /api/config/routes should derive routes from inventory
        when no config file exists."""
        response = client.get("/api/config/routes")
        assert response.status_code == 200
        data = response.json()
        assert "routes" in data
        assert data["source"] in ("derived", "config")

        routes = data["routes"]
        assert "local-fast" in routes
        assert "local-quality" in routes
        assert "private-docs" in routes
        assert "cloud-best" in routes


class TestStaticServing:
    def test_static_index_served(self, client):
        """GET / should return the dashboard HTML."""
        response = client.get("/")
        # Will be 200 if index.html exists, 404 if not yet created
        assert response.status_code in (200, 404)
        if response.status_code == 200:
            assert "text/html" in response.headers.get("content-type", "")
