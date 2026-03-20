import os

def get_admin_key() -> str:
    """Get ADMIN_API_KEY from environment. Fail hard if not set.

    Raises RuntimeError if ADMIN_API_KEY is not configured.
    This is required for production security.
    """
    key = os.getenv("ADMIN_API_KEY")
    if not key:
        raise RuntimeError(
            "ADMIN_API_KEY environment variable not set. "
            "This is required for production. "
            "Set: export ADMIN_API_KEY=<your-secret-key>"
        )
    return key
