from collections.abc import Mapping
from typing import Any


class BaseAppException(Exception):
    """Base exception for application-specific failures."""

    status_code = 500
    error_code = "app_error"
    default_detail = "Application error."

    def __init__(
        self,
        detail: str | None = None,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        self.detail = detail or self.default_detail
        self.status_code = status_code or type(self).status_code
        self.error_code = error_code or type(self).error_code
        self.extra = dict(extra or {})
        super().__init__(self.detail)
