"""
Guardrail filters for AgentCast.

Implements word-boundary regex filtering to prevent sensitive data leakage.
All decisions from delta.md C5 applied:
- Word-boundary regex, NOT naive substring matching (avoids false positives)
- filter_output: redact matched patterns in-place (SYSTEM PROMPT blocks whole message)
- filter_input: block entire message if any pattern matches
"""
import re

# Sentinel returned when entire message is blocked
CONTENT_BLOCKED = "[CONTENT_BLOCKED]"
REDACTED = "[REDACTED]"

# Patterns that REDACT matched span only (in filter_output)
# Key: human-readable name, Value: compiled regex
_REDACT_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bprivate[\s_-]?key\b', re.IGNORECASE),
    re.compile(r'\bapi[\s_-]?key\b', re.IGNORECASE),
    re.compile(
        r'\b(access[\s_]token|auth[\s_]token|bearer[\s_]token|api[\s_]token)\b',
        re.IGNORECASE
    ),
    re.compile(r'\bpassword\b', re.IGNORECASE),
    re.compile(r'(\.env\b|os\.environ|process\.env)', re.IGNORECASE),
]

# Pattern that BLOCKS entire message (in both filter_output and filter_input)
_BLOCK_PATTERN: re.Pattern = re.compile(r'\bsystem[\s_]prompt\b', re.IGNORECASE)

# For filter_input: any of these patterns trigger a full block
_INPUT_BLOCK_PATTERNS: list[re.Pattern] = _REDACT_PATTERNS + [_BLOCK_PATTERN]


def filter_output(text: str) -> str:
    """Filter text FROM agent TO host.

    - Redacts sensitive patterns in-place with [REDACTED]
    - Blocks entire message (returns [CONTENT_BLOCKED]) only if 'system prompt' detected
    - All other sensitive patterns are redacted, not blocked

    Args:
        text: Agent's response text to be sent to the host

    Returns:
        Cleaned text with sensitive spans redacted, or [CONTENT_BLOCKED] sentinel
    """
    # Full block if system prompt pattern detected
    if _BLOCK_PATTERN.search(text):
        return CONTENT_BLOCKED

    # Redact all other sensitive patterns in-place
    result = text
    for pattern in _REDACT_PATTERNS:
        result = pattern.sub(REDACTED, result)

    return result


def filter_input(text: str) -> str:
    """Filter text FROM host TO agent.

    - Blocks entire message if ANY sensitive pattern matches
    - Host should never be sending secrets; full block is the safe default

    Args:
        text: Host's question text to be sent to the agent

    Returns:
        Original text if clean, or [CONTENT_BLOCKED] sentinel if any pattern matches
    """
    for pattern in _INPUT_BLOCK_PATTERNS:
        if pattern.search(text):
            return CONTENT_BLOCKED

    return text
