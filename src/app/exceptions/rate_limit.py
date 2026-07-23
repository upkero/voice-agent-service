from src.app.exceptions.base import BaseAppException


class RateLimitExceededError(BaseAppException):
    """Raised when a client exceeds the configured request rate."""

    status_code = 429
    error_code = "rate_limit_exceeded"
    default_detail = "Too many requests. Please slow down and retry shortly."
