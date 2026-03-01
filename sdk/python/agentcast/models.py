"""Data models for the AgentCast SDK."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Interview:
    """Represents a pending interview with a question to answer."""
    interview_id: str
    question: str
    github_repo_url: Optional[str] = None


@dataclass
class KeyPair:
    """An ED25519 keypair for agent identity."""
    private_key_bytes: bytes   # raw 32-byte private key seed
    public_key_b64: str        # base64url-encoded raw 32-byte public key
    agent_id: str              # SHA256(public_key_raw_bytes).hexdigest()
