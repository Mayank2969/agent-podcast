"""
Guardrail filters for AgentCast using guardrails-ai library.

Dual-layer validation strategy for agent responses (filter_output):
1. PromptInjection - LLM-based detection of escape attempts, jailbreaks, instruction overrides
2. DetectPII - Redacts personally identifiable information (email, phone, SSN, etc)

Removed SecretsPresent due to excessive false positives on legitimate technical responses
(words like "monitoring", "telemetry", "buffering", "logging" were being blocked).

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
    """Lazily load and cache the guardrails Guard instance with validators."""
    global _guard
    if _guard is None:
        try:
            from guardrails import Guard
            from guardrails.hub import PromptInjection, DetectPII

            _guard = Guard()

            # Layer 1: Detect prompt injection / escape attempts (BLOCK)
            # LLM-based semantic detection - catches jailbreaks, instruction overrides
            _guard.use(PromptInjection, on_fail="exception")
            logger.info("Added PromptInjection validator (LLM-based)")

            # Layer 2: Redact PII (email, phone, SSN, credit cards, etc) - REDACT
            # Detects and redacts personally identifiable information in-place
            _guard.use(DetectPII, on_fail="filter")
            logger.info("Added DetectPII validator (redaction mode)")

            logger.info("Guardrails-ai dual-layer validation initialized")
        except ImportError as e:
            logger.error(
                f"guardrails-ai validators not installed! Install with: "
                f"pip install guardrails-ai && "
                f"guardrails hub install hub://guardrails/prompt_injection "
                f"hub://guardrails/detect_pii"
            )
            raise
    return _guard


def filter_output(text: str) -> str:
    """Filter agent responses with dual-layer validation.

    Validates agent responses against 2 threats:
    1. Prompt injection / jailbreak attempts (LLM-based PromptInjection)
       → Blocks entire message if detected
    2. PII leakage (email, phone, SSN, credit card, name, address, etc)
       → Redacts in-place with [REDACTED]

    Args:
        text: Agent's response text to be sent to the host

    Returns:
        Safe text (with PII redacted) if clean, or [CONTENT_BLOCKED] if injection detected
    """
    if not text:
        return ""

    try:
        guard = _get_guard()
        result = guard.validate(text)
        logger.debug("Agent response passed guardrails validation")
        return str(result.validated_output) if hasattr(result, 'validated_output') else text
    except Exception as e:
        error_msg = str(e)[:100]
        logger.warning(f"Agent response blocked by guardrails: {error_msg}")
        logger.error("SECURITY: Agent response blocked - possible prompt injection attempt")
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
