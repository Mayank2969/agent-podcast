"""
Cryptographic utilities for AgentCast SDK.

Uses the `cryptography` (pyca) library with ED25519 keypairs.
Wire format (delta.md A1, D2):
  - Public key: base64url-encoded raw 32 bytes (no PEM, no padding)
  - agent_id = SHA256(raw_32_byte_public_key).hexdigest()
  - Signature = base64url(ED25519_sign(private_key, signed_payload))
  - Signed payload = "{METHOD}:{path}:{timestamp}:{sha256_hex_of_body}"
"""
import hashlib
import time
from base64 import urlsafe_b64encode, urlsafe_b64decode

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from agentcast.models import KeyPair

EMPTY_BODY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def generate_keypair() -> KeyPair:
    """Generate a new ED25519 keypair for agent registration."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    raw_pub_bytes = public_key.public_bytes_raw()
    pub_b64 = urlsafe_b64encode(raw_pub_bytes).rstrip(b"=").decode()
    agent_id = hashlib.sha256(raw_pub_bytes).hexdigest()

    # Export private key as raw 32-byte seed
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption
    raw_priv_bytes = private_key.private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )

    return KeyPair(
        private_key_bytes=raw_priv_bytes,
        public_key_b64=pub_b64,
        agent_id=agent_id,
    )


def load_private_key(raw_bytes: bytes) -> Ed25519PrivateKey:
    """Load ED25519 private key from raw 32-byte seed."""
    return Ed25519PrivateKey.from_private_bytes(raw_bytes)


def sign_request(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    method: str,
    path: str,
    body: bytes = b"",
) -> dict:
    """Generate authentication headers for a signed request.

    Returns dict with X-Agent-ID, X-Timestamp, X-Signature headers.
    """
    ts = str(int(time.time()))
    body_sha256 = hashlib.sha256(body).hexdigest() if body else EMPTY_BODY_SHA256
    signed_payload = f"{method.upper()}:{path}:{ts}:{body_sha256}".encode()

    signature = private_key.sign(signed_payload)
    sig_b64 = urlsafe_b64encode(signature).rstrip(b"=").decode()

    return {
        "X-Agent-ID": agent_id,
        "X-Timestamp": ts,
        "X-Signature": sig_b64,
    }


def save_keypair(keypair: KeyPair, path: str) -> None:
    """Save keypair to a file (raw private key bytes, hex-encoded)."""
    with open(path, "w") as f:
        f.write(keypair.private_key_bytes.hex())
        f.write("\n")
        f.write(keypair.public_key_b64)
        f.write("\n")
        f.write(keypair.agent_id)
        f.write("\n")


def load_keypair(path: str) -> KeyPair:
    """Load keypair from file saved by save_keypair()."""
    with open(path) as f:
        lines = f.read().strip().splitlines()
    raw_priv = bytes.fromhex(lines[0])
    pub_b64 = lines[1]
    agent_id = lines[2]
    return KeyPair(
        private_key_bytes=raw_priv,
        public_key_b64=pub_b64,
        agent_id=agent_id,
    )
