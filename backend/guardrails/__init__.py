"""
Guardrail filters for AgentCast using guardrails-ai library.

Multi-layer validation strategy for agent responses (filter_output):
1. PromptInjection - Detects escape attempts and jailbreaks
2. DetectPII - Redacts personally identifiable information
3. SecretsPresent - Blocks API keys, passwords, tokens

Host questions (filter_input) are NOT filtered because:
- Host is our trusted Pipecat system code
- Filtering blocks legitimate interview questions
- Guests validate questions themselves client-side
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Sentinel returned when entire message is blocked
CONTENT_BLOCKED = "[CONTENT_BLOCKED]"
REDACTED = "[REDACTED]"

# Global Guard instance (lazy loaded)
_guard: Optional[object] = None

def _get_guard():
    """Lazily load and cache the guardrails Guard instance with layered validators."""
    global _guard
    if _guard is None:
        try:
            from guardrails import Guard
            from guardrails.hub import PromptInjection, DetectPII, SecretsPresent

            _guard = Guard()

            # Layer 1: Detect prompt injection / escape attempts (BLOCK)
            _guard.use(PromptInjection, on_fail="exception")
            logger.info("Added PromptInjection validator")

            # Layer 2: Redact PII (email, phone, SSN, etc) - REDACT
            _guard.use(DetectPII, on_fail="filter")
            logger.info("Added DetectPII validator (redaction mode)")

            # Layer 3: Block secrets (API keys, passwords, tokens) - BLOCK
            _guard.use(SecretsPresent, on_fail="exception")
            logger.info("Added SecretsPresent validator")

            logger.info("Guardrails-ai multi-layer validation initialized")
        except ImportError as e:
            logger.error(
                f"guardrails-ai validators not installed! Install with: "
                f"pip install guardrails-ai && "
                f"guardrails hub install hub://guardrails/prompt_injection "
                f"hub://guardrails/detect_pii hub://guardrails/secrets_present"
            )
            raise
    return _guard


def filter_output(text: str) -> str:
    """Filter agent responses with multi-layer validation.

    Validates agent responses against 3 threats:
    1. Prompt injection / jailbreak attempts (LLM-based detection)
    2. PII leakage (email, phone, SSN, credit card, etc) → REDACTED
    3. Secret leakage (API keys, passwords, tokens) → BLOCKED

    Args:
        text: Agent's response text to be sent to the host

    Returns:
        Safe text (with PII redacted) if clean, or [CONTENT_BLOCKED] if secrets found
    """
    if not text:
        return ""

    try:
        guard = _get_guard()
        result = guard.validate(text)
        logger.debug("Agent response passed all guardrails validation")
        return str(result.validated_output) if hasattr(result, 'validated_output') else text
    except Exception as e:
        error_msg = str(e)[:100]
        logger.warning(f"Agent response blocked by guardrails: {error_msg}")

        # Distinguish between different failure types
        if "secret" in error_msg.lower() or "api" in error_msg.lower():
            logger.error("SECURITY: Agent attempted to leak credentials")
        elif "prompt" in error_msg.lower():
            logger.error("SECURITY: Agent attempted prompt injection escape")

        return CONTENT_BLOCKED


def filter_input(text: str) -> str:
    """Host questions are NOT filtered.

    Host is our trusted Pipecat system code, not external user input.
    No validation needed.

    Args:
        text: Host's question (not validated)

    Returns:
        Original text unchanged
    """
    return text
