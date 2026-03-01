"""Unit tests for transcript service."""
import json
import pytest

from backend.interviews.transcript import build_and_store_transcript


def test_transcript_module_importable():
    """Smoke test: module imports without error."""
    from backend.interviews import transcript
    assert hasattr(transcript, "build_and_store_transcript")


def test_transcript_router_importable():
    """Smoke test: router imports without error."""
    from backend.interviews.transcript_router import router
    assert router is not None


def test_get_transcript_route_registered():
    """Verify GET /v1/transcript/{interview_id} is registered on the app."""
    from backend.main import app
    routes = [r.path for r in app.routes]
    assert "/v1/transcript/{interview_id}" in routes


def test_build_transcript_route_registered():
    """Verify POST /v1/transcript/build is registered."""
    from backend.main import app
    routes = [r.path for r in app.routes]
    assert "/v1/transcript/build" in routes
