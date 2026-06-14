"""Tests for the profiler runner endpoint."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from gateway.server import app, PROFILER_PATH, PROJECT_ROOT


@pytest.fixture
def client():
    return TestClient(app)


class TestProfileRunSafety:
    def test_profile_run_uses_safe_subprocess_arguments(self, client):
        """POST /api/profile/run should use explicit args, not shell=True."""
        with patch("gateway.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps({"hostname": "test", "compatible": "yes"}),
                stderr="",
            )

            response = client.post("/api/profile/run")

            # Verify subprocess was called safely
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            args = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("args", [])

            # Should be a list, not a string (no shell=True)
            assert isinstance(args, list)

            # Should use sys.executable and the profiler path
            assert "--json" in args

            # Verify no shell=True
            kwargs = call_kwargs[1] if len(call_kwargs) > 1 else {}
            assert kwargs.get("shell") is not True

            # Verify timeout is set
            assert "timeout" in kwargs
            assert kwargs["timeout"] > 0

            # Verify capture_output
            assert kwargs.get("capture_output") is True

    def test_profile_run_timeout_returns_structured_error(self, client):
        """POST /api/profile/run should return structured error on timeout."""
        with patch("gateway.server.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=120)

            response = client.post("/api/profile/run")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "timed out" in data["error"].lower()

    def test_profile_run_nonzero_exit_returns_error(self, client):
        """POST /api/profile/run should handle non-zero exit codes."""
        with patch("gateway.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="some error output",
            )

            response = client.post("/api/profile/run")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "exited with code" in data["error"].lower()

    def test_profile_run_invalid_json_returns_error(self, client):
        """POST /api/profile/run should handle unparseable stdout."""
        with patch("gateway.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="this is not json at all",
                stderr="",
            )

            response = client.post("/api/profile/run")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "error"
            assert "parse" in data["error"].lower()

    def test_profile_run_success_caches_result(self, client):
        """POST /api/profile/run success should make result available
        via GET /api/profile/current."""
        profile_data = {
            "hostname": "test-host",
            "os": "Test OS",
            "compatible": "yes",
            "recommended_model": "TestModel 7B",
        }

        with patch("gateway.server.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(profile_data),
                stderr="",
            )

            # Run profiler
            response = client.post("/api/profile/run")
            assert response.status_code == 200
            assert response.json()["status"] == "ok"

            # Check cached profile
            response = client.get("/api/profile/current")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "available"
            assert data["profile"]["hostname"] == "test-host"

    def test_profile_run_missing_script_returns_503(self, client):
        """POST /api/profile/run should return 503 if profiler is missing."""
        with patch("gateway.server.PROFILER_PATH", Path("/nonexistent/profiler.py")):
            response = client.post("/api/profile/run")
            assert response.status_code == 503


class TestProfileCurrentEndpoint:
    def test_profile_current_unavailable_initially(self, client):
        """GET /api/profile/current should return unavailable when no
        profile has been run yet in this session."""
        import gateway.server
        gateway.server._cached_profile = None

        response = client.get("/api/profile/current")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "unavailable"
        assert data["profile"] is None
