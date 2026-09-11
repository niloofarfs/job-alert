class SourceError(Exception):
    """Base error for ATS adapter failures."""


class SourceNetworkError(SourceError):
    """Timeouts, connection failures, and exhausted retries."""


class SourceResponseError(SourceError):
    """Remote payload was not valid JSON or did not match the expected shape."""


class SourceConfigError(SourceError):
    """Company source_identifier / ATS configuration is invalid."""


class SourceAuthError(SourceError):
    """Remote API rejected the request as unauthorized or forbidden."""
