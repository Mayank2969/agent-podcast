"""Custom exceptions for Pipecat host."""


class InterviewTimeoutError(Exception):
    """Raised when agent does not respond within the timeout window."""
    pass
