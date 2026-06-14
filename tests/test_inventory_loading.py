"""Tests for inventory loading and route derivation."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from gateway.server import (
    load_inventory,
    derive_routes_from_inventory,
    load_routes_config,
    INVENTORY_PATH,
)


class TestLoadInventory:
    def test_load_inventory_returns_valid_data(self):
        """Inventory loader should return dict with machines list."""
        data = load_inventory()
        assert isinstance(data, dict)
        assert "machines" in data
        assert isinstance(data["machines"], list)
        assert len(data["machines"]) > 0

    def test_load_inventory_machines_have_required_fields(self):
        """Each machine should have id, hostname, and recommended_role."""
        data = load_inventory()
        for machine in data["machines"]:
            assert "id" in machine
            assert "hostname" in machine
            assert "recommended_role" in machine

    def test_inventory_missing_returns_structured_error(self):
        """Should raise HTTPException when inventory file is missing."""
        from fastapi import HTTPException

        with patch("gateway.server.INVENTORY_PATH", Path("/nonexistent/path.json")):
            with pytest.raises(HTTPException) as exc_info:
                load_inventory()
            assert exc_info.value.status_code == 503
            assert "not found" in exc_info.value.detail.lower()

    def test_inventory_malformed_json_returns_error(self, tmp_path):
        """Should raise HTTPException for malformed JSON."""
        from fastapi import HTTPException

        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{ this is not valid json }", encoding="utf-8")

        with patch("gateway.server.INVENTORY_PATH", bad_file):
            with pytest.raises(HTTPException) as exc_info:
                load_inventory()
            assert exc_info.value.status_code == 503


class TestDeriveRoutes:
    def test_derives_local_fast_from_inventory(self):
        """Should assign local-fast primary to the machine preferring it."""
        machines = [
            {
                "id": "pc-rtx2070",
                "recommended_role": "local_fast",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-fast"],
                    "fallback_routes": ["local-quality"],
                },
            },
        ]
        routes = derive_routes_from_inventory(machines)
        assert routes["local-fast"]["primary"] == "pc-rtx2070"

    def test_derives_local_quality_from_inventory(self):
        """Should assign local-quality primary to the quality node."""
        machines = [
            {
                "id": "macbook-m4pro",
                "recommended_role": "local_quality",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-quality", "private-docs"],
                    "fallback_routes": ["local-fast"],
                },
            },
        ]
        routes = derive_routes_from_inventory(machines)
        assert routes["local-quality"]["primary"] == "macbook-m4pro"
        assert routes["private-docs"]["primary"] == "macbook-m4pro"

    def test_disabled_machine_not_routed(self):
        """Machines with routing.enabled=False should not appear in routes."""
        machines = [
            {
                "id": "old-macbook",
                "recommended_role": "utility_node",
                "routing": {
                    "enabled": False,
                    "preferred_routes": [],
                    "fallback_routes": [],
                },
            },
        ]
        routes = derive_routes_from_inventory(machines)
        for route in routes.values():
            assert route["primary"] != "old-macbook"
            assert route["fallback"] != "old-macbook"

    def test_cloud_best_always_has_cloud_fallback(self):
        """cloud-best route should always have cloud_fallback=True."""
        routes = derive_routes_from_inventory([])
        assert routes["cloud-best"]["cloud_fallback"] is True

    def test_fallback_routes_assigned_correctly(self):
        """Fallback routes from inventory should populate route fallbacks."""
        machines = [
            {
                "id": "macbook-m4pro",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-quality"],
                    "fallback_routes": ["local-fast"],
                },
            },
            {
                "id": "pc-rtx2070",
                "routing": {
                    "enabled": True,
                    "preferred_routes": ["local-fast"],
                    "fallback_routes": ["local-quality"],
                },
            },
        ]
        routes = derive_routes_from_inventory(machines)
        assert routes["local-fast"]["primary"] == "pc-rtx2070"
        assert routes["local-fast"]["fallback"] == "macbook-m4pro"
        assert routes["local-quality"]["primary"] == "macbook-m4pro"
        assert routes["local-quality"]["fallback"] == "pc-rtx2070"


class TestLoadRoutesConfig:
    def test_routes_derived_when_no_config_files(self):
        """Should derive routes when config directory has no route files."""
        with patch("gateway.server.CONFIG_DIR", Path("/nonexistent/config")):
            data = load_routes_config()
            assert data["source"] == "derived"
            assert "local-fast" in data["routes"]
