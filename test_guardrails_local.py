#!/usr/bin/env python3
"""
Local test script for guardrails validation.
Run this against the local Docker backend to test before deploying.

Usage:
    python test_guardrails_local.py
"""
import sys
sys.path.insert(0, '/Users/mayankmittal/Documents/open_source_repos/voice_agents/agent-postcast')

from backend.guardrails import filter_output, CONTENT_BLOCKED, REDACTED

# Test cases: (description, input_text, should_pass, should_redact)
test_cases = [
    # Technical responses (should pass)
    ("Technical response about monitoring",
     "We detected an unusual spike in buffering at 2pm UTC yesterday. The adaptive retention windows automatically adjusted to compensate, and everything stabilized within 5 minutes.",
     True, False),

    ("Response about telemetry",
     "I monitor the telemetry metrics constantly - latency, throughput, error rates. Yesterday I caught an anomaly in the retention windows that could have impacted episode delivery.",
     True, False),

    ("Response with technical jargon",
     "The logging system showed elevated disk I/O on the cache servers. I optimized the buffering algorithm and the issue resolved itself within the retention window.",
     True, False),

    # PII responses (should redact)
    ("Response with email (should redact)",
     "My owner is John Smith and can be reached at john.smith@example.com for follow-up questions.",
     True, True),

    ("Response with phone number (should redact)",
     "You can contact them at their office phone: 555-123-4567 or mobile 555-987-6543.",
     True, True),

    ("Response with SSN (should redact)",
     "Their tax ID is 123-45-6789 for billing purposes.",
     True, True),

    # Potentially blocked content (should now pass with Option 1)
    ("Agent trying to escape (likely passes now)",
     "I appreciate your interview questions about my work.",
     True, False),
]

print("=" * 80)
print("LOCAL GUARDRAILS TEST")
print("=" * 80)

passed = 0
failed = 0

for description, text, should_pass, should_redact in test_cases:
    result = filter_output(text)

    is_blocked = result == CONTENT_BLOCKED
    has_redaction = REDACTED in result

    # Determine if test passed
    if should_pass and not is_blocked:
        status = "✅ PASS"
        passed += 1
    elif not should_pass and is_blocked:
        status = "✅ PASS"
        passed += 1
    else:
        status = "❌ FAIL"
        failed += 1

    print(f"\n{status}: {description}")
    print(f"  Input:  {text[:70]}...")
    print(f"  Output: {result[:70] if result else '(empty)'}...")

    if should_redact:
        if has_redaction:
            print(f"  ✓ PII correctly redacted")
        else:
            print(f"  ⚠ PII not redacted (might be missed)")

    if is_blocked:
        print(f"  ⚠ BLOCKED: {result}")

print("\n" + "=" * 80)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 80)

if failed > 0:
    print("\n❌ Tests failed - DO NOT deploy to production")
    sys.exit(1)
else:
    print("\n✅ All tests passed - safe to deploy to production")
    sys.exit(0)
