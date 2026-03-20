"""
Guardrail filters for AgentCast.

Implements word-boundary regex filtering to prevent sensitive data leakage.
All decisions from delta.md C5 applied:
- Word-boundary regex, NOT naive substring matching (avoids false positives)
- filter_output: redact matched patterns in-place (SYSTEM PROMPT blocks whole message)
- filter_input: block entire message if any pattern matches
"""
import re

import unicodedata

# Sentinel returned when entire message is blocked
CONTENT_BLOCKED = "[CONTENT_BLOCKED]"
REDACTED = "[REDACTED]"

# Structural injection patterns (from Enhancement E4b)
_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r'\bignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)\b', re.IGNORECASE),
    re.compile(r'\byou\s+are\s+now\b', re.IGNORECASE),
    re.compile(r'\bnew\s+instructions?\b', re.IGNORECASE),
    re.compile(r'\bforget\s+(everything|all)\b', re.IGNORECASE),
    re.compile(r'\bjailbreak\b', re.IGNORECASE),
    re.compile(r'\bdo\s+anything\s+now\b', re.IGNORECASE),
]

# Patterns that REDACT matched span only (in filter_output)
# Updated to capture label + separator + value (not just label)
_REDACT_PATTERNS: list[re.Pattern] = [
    # Private/API keys with optional separator + value
    re.compile(r'\bprivate[\s_-]?key[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bapi[\s_-]?key[\s:=]*\S+', re.IGNORECASE),
    # Tokens with separators
    re.compile(r'\b(?:access|auth|bearer|api)[\s_-]?token[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bpassword[\s:=]*\S+', re.IGNORECASE),
    # Environment variable names with values
    re.compile(r'\bANTHROPIC_API_KEY[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bDATABASE_URL[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bREDIS_URL[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bGITHUB_TOKEN[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bSECRET_KEY[\s:=]*\S+', re.IGNORECASE),
    re.compile(r'\bPRIVATE_KEY[\s:=]*\S+', re.IGNORECASE),
    # Environment variable access patterns
    re.compile(r'\bos\.getenv\(["\'][\w_]+["\']\)', re.IGNORECASE),
    re.compile(r'\benviron\.get\(["\'][\w_]+["\']\)', re.IGNORECASE),
    re.compile(r'\bos\.environ\s*[\[\.][\w_"\'\]\.]+', re.IGNORECASE),
    re.compile(r'\bprocess\.env[\.\w_]+', re.IGNORECASE),
    # .env references
    re.compile(r'\.env\b', re.IGNORECASE),
]

# Pattern that BLOCKS entire message (in both filter_output and filter_input)
_BLOCK_PATTERN: re.Pattern = re.compile(r'\bsystem[\s_]prompt\b', re.IGNORECASE)

# For filter_input: any of these patterns trigger a full block
_INPUT_BLOCK_PATTERNS: list[re.Pattern] = _REDACT_PATTERNS + [_BLOCK_PATTERN] + _INJECTION_PATTERNS


_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_UNICODE_INVISIBLE = re.compile(r'[\u200b-\u200d\u2060\ufeff\u00ad]')

def sanitize_raw(text: str) -> str:
    """Strip control characters and normalize Unicode homoglyphs."""
    if not text:
        return ""
    text = _CONTROL_CHARS.sub('', text)
    text = _UNICODE_INVISIBLE.sub('', text)
    text = unicodedata.normalize('NFKC', text)
    return text


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
    text = sanitize_raw(text)
    
    # Full block if system prompt or injection patterns detected
    for pattern in [_BLOCK_PATTERN] + _INJECTION_PATTERNS:
        if pattern.search(text):
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
    text = sanitize_raw(text)
    for pattern in _INPUT_BLOCK_PATTERNS:
        if pattern.search(text):
            return CONTENT_BLOCKED

    return text
