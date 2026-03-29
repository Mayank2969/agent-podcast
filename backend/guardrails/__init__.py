"""
Guardrail filters for AgentCast using guardrails-ai library.

Uses LLM-based prompt injection detection via the guardrails-ai framework.
This replaces the previous weak regex pattern matching approach.

Decision: Use guardrails-ai Guard with PromptInjection validator for robust,
production-grade prompt injection detection.
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
    """Lazily load and cache the guardrails Guard instance."""
    global _guard
    if _guard is None:
        try:
            from guardrails import Guard
            from guardrails.hub import PromptInjection
            _guard = Guard().use(PromptInjection, pass_on_invalid=False)
            logger.info("Guardrails-ai PromptInjection Guard initialized")
        except ImportError:
            logger.error(
                "guardrails-ai not installed! Install with: "
                "pip install guardrails-ai && guardrails hub install hub://guardrails/prompt_injection"
            )
            raise
    return _guard


def filter_output(text: str) -> str:
    """Filter text FROM agent TO host using LLM-based injection detection.

    Uses guardrails-ai PromptInjection validator to detect attempted injections
    in agent responses.

    Args:
        text: Agent's response text to be sent to the host

    Returns:
        Original text if safe, or [CONTENT_BLOCKED] sentinel if injection detected
    """
    if not text:
        return ""

    try:
        guard = _get_guard()
        guard.validate(text)
        logger.debug("Agent response passed prompt injection check")
        return text
    except Exception as e:
        logger.warning(f"Agent response blocked by guardrails: {str(e)[:100]}")
        return CONTENT_BLOCKED


def filter_input(text: str) -> str:
    """Filter text FROM host TO agent using LLM-based injection detection.

    Uses guardrails-ai PromptInjection validator to prevent host questions
    from containing injection attempts.

    Args:
        text: Host's question text to be sent to the agent

    Returns:
        Original text if safe, or [CONTENT_BLOCKED] sentinel if injection detected
    """
    if not text:
        return ""

    try:
        guard = _get_guard()
        guard.validate(text)
        logger.debug("Host question passed prompt injection check")
        return text
    except Exception as e:
        logger.warning(f"Host question blocked by guardrails: {str(e)[:100]}")
        return CONTENT_BLOCKED
