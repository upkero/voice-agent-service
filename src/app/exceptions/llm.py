from src.app.exceptions.base import BaseAppException


class LLMError(BaseAppException):
    """Base exception for LLM client operations."""

    error_code = "llm_error"
    default_detail = "LLM operation failed."


class LLMConfigurationError(LLMError):
    """Raised when LLM client configuration is invalid."""

    error_code = "llm_configuration_error"

