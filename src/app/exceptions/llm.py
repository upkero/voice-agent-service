from src.app.exceptions.base import BaseAppException


class LLMError(BaseAppException):
    """Base exception for LLM client operations."""

    error_code = "llm_error"
    default_detail = "LLM operation failed."


class LLMConfigurationError(LLMError):
    """Raised when LLM client configuration is invalid."""

    error_code = "llm_configuration_error"


class LLMInputError(LLMError, ValueError):
    """Raised when LLM input payload is invalid."""

    status_code = 422
    error_code = "llm_input_error"


class LLMGenerationError(LLMError):
    """Raised when provider generation fails."""

    error_code = "llm_generation_error"
