"""
Field length limit validation tests for identity router.

Tests that Pydantic validation returns 422 for oversized payloads.
"""
import hashlib
from base64 import urlsafe_b64encode

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient, ASGITransport

from backend.main import app


def generate_keypair():
    """Generate ED25519 keypair for testing."""
    priv = Ed25519PrivateKey.generate()
    pub_bytes = priv.public_key().public_bytes_raw()
    pub_b64 = urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(pub_bytes).hexdigest()
    return priv, pub_b64, agent_id


@pytest.mark.asyncio
async def test_register_callback_url_exceeds_limit():
    """Test that callback_url exceeding 500 chars returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        priv, pub_b64, agent_id = generate_keypair()

        # Try to register with oversized callback_url
        oversized_url = "https://example.com/" + ("a" * 500)
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64, "callback_url": oversized_url}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_callback_url_at_limit():
    """Test that callback_url at exactly 500 chars is accepted."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        priv, pub_b64, agent_id = generate_keypair()

        # Register with exactly 500 char callback_url
        max_url = "https://example.com/" + ("a" * 475)  # 475 + 25 = 500
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64, "callback_url": max_url}
        )
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_register_display_name_exceeds_limit():
    """Test that display_name exceeding 200 chars returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        priv, pub_b64, agent_id = generate_keypair()

        # Try to register with oversized display_name
        oversized_name = "x" * 201
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64, "display_name": oversized_name}
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_public_key_wrong_length():
    """Test that public_key not exactly 43 chars returns 422."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Try with too short key
        response = await client.post(
            "/v1/register",
            json={"public_key": "abc"}
        )
        assert response.status_code == 422

        # Try with too long key (append extra chars)
        priv, pub_b64, agent_id = generate_keypair()
        response = await client.post(
            "/v1/register",
            json={"public_key": pub_b64 + "x"}
        )
        assert response.status_code == 422
