"""
Guardrail filters for AgentCast using guardrails-ai library.

Single-layer validation strategy for agent responses (filter_output):
- DetectPII: Redacts personally identifiable information (email, phone, SSN, credit card, etc)
  on_fail="filter" means PII is redacted in-place, message still delivered

REMOVED validators due to excessive false positives on legitimate technical responses:
- PromptInjection: Was blocking normal agent responses about monitoring, telemetry, buffering
- SecretsPresent: Was blocking words like "logging", "connection", "endpoint"

Security model:
- Rely on model alignment (agents' own training prevents harmful outputs)
- Redact sensitive data (PII) to prevent accidental leakage
- Don't block legitimate technical conversation
- Host questions are NOT filtered (trusted Pipecat system code)
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
            from guardrails.hub import DetectPII

            _guard = Guard()

            # Single validator: Redact PII (email, phone, SSN, credit cards, names, addresses)
            # on_fail="filter" means redact in-place with [REDACTED], don't block message
            _guard.use(DetectPII, on_fail="filter")
            logger.info("Guardrails-ai DetectPII validator initialized (redaction mode)")

        except ImportError as e:
            logger.error(
                f"guardrails-ai not installed! Install with: "
                f"pip install guardrails-ai && "
                f"guardrails hub install hub://guardrails/detect_pii"
            )
            raise
    return _guard


def filter_output(text: str) -> str:
    """Filter agent responses by redacting PII.

    Detects and redacts personally identifiable information in agent responses:
    - Email addresses
    - Phone numbers
    - Social Security Numbers (SSN)
    - Credit card numbers
    - Names, addresses

    The message is NOT blocked - PII is simply redacted with [REDACTED].

    This prevents accidental leakage of sensitive data while allowing
    agents to freely discuss technical topics without false positives.

    Args:
        text: Agent's response text to be sent to the host

    Returns:
        Text with PII redacted to [REDACTED], message always delivered
    """
    if not text:
        return ""

    try:
        guard = _get_guard()
        result = guard.validate(text)
        logger.debug("Agent response processed - PII redacted if present")
        return str(result.validated_output) if hasattr(result, 'validated_output') else text
    except Exception as e:
        error_msg = str(e)[:100]
        logger.warning(f"PII redaction error (continuing): {error_msg}")
        # Even if redaction fails, still return the original text (don't block)
        return text


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
