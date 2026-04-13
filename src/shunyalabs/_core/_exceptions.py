"""Unified exception hierarchy for the Shunyalabs SDK.

Inspired by OpenAI's HTTP-mapped exception pattern with domain-specific
extensions for ASR, TTS, and Flow services.
"""

from typing import Any, Optional


class ShunyalabsError(Exception):
    """Base exception for all Shunyalabs SDK errors."""

    pass


class APIError(ShunyalabsError):
    """Raised when the API returns an error response."""

    _SAFE_BODY_KEYS = frozenset({"error", "message", "detail", "code", "request_id"})

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        body: Any = None,
        request_id: Optional[str] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.body = self._sanitize_body(body)
        self.request_id = request_id

    @classmethod
    def _sanitize_body(cls, body: Any) -> Optional[dict]:
        """Strip server response body to known-safe fields only."""
        if not isinstance(body, dict):
            return None
        return {k: v for k, v in body.items() if k in cls._SAFE_BODY_KEYS}

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.status_code:
            parts.append(f"status_code={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return " ".join(parts)


class AuthenticationError(APIError):
    """Raised when authentication fails (HTTP 401)."""

    pass


class PermissionDeniedError(APIError):
    """Raised when permission is denied (HTTP 403)."""

    pass


class NotFoundError(APIError):
    """Raised when a resource is not found (HTTP 404)."""

    pass


class RateLimitError(APIError):
    """Raised when rate limit is exceeded (HTTP 429)."""

    pass


class ServerError(APIError):
    """Raised when the server returns a 5xx error."""

    pass


class ConfigurationError(ShunyalabsError):
    """Raised when there's an error in SDK configuration."""

    pass


class ConnectionError(ShunyalabsError):
    """Raised when connection to the service fails."""

    pass


class TimeoutError(ShunyalabsError):
    """Raised when an operation times out."""

    pass


class TransportError(ShunyalabsError):
    """Raised when there's an error in the transport layer."""

    pass


# --- Domain-specific errors ---


class TranscriptionError(ShunyalabsError):
    """Raised when ASR transcription fails."""

    pass


class SynthesisError(ShunyalabsError):
    """Raised when TTS synthesis fails."""

    pass


class SessionError(ShunyalabsError):
    """Raised when there's an error with the streaming session state."""

    pass


class AudioError(ShunyalabsError):
    """Raised when there's an issue with audio data."""

    pass


class ConversationError(ShunyalabsError):
    """Raised when a conversational flow error occurs."""

    pass


class ConversationEndedException(ShunyalabsError):
    """Raised when the conversation has ended normally."""

    pass


# Map HTTP status codes to exception classes
_STATUS_CODE_MAP: dict[int, type[APIError]] = {
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    429: RateLimitError,
}


def raise_for_status(status_code: int, body: Any = None, request_id: Optional[str] = None) -> None:
    """Raise an appropriate exception for an HTTP error status code."""
    if 200 <= status_code < 300:
        return

    message = f"API error: HTTP {status_code}"
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("error") or body.get("message")
        if detail:
            message = str(detail)

    exc_class = _STATUS_CODE_MAP.get(status_code)
    if exc_class is None:
        exc_class = ServerError if status_code >= 500 else APIError

    raise exc_class(message, status_code=status_code, body=body, request_id=request_id)


__all__ = [
    "ShunyalabsError",
    "APIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ConfigurationError",
    "ConnectionError",
    "TimeoutError",
    "TransportError",
    "TranscriptionError",
    "SynthesisError",
    "SessionError",
    "AudioError",
    "ConversationError",
    "ConversationEndedException",
    "raise_for_status",
]
