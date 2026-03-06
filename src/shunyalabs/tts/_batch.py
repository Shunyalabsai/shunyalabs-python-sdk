"""Batch TTS clients for the Shunyalabs SDK.

Provides :class:`AsyncBatchTTS` and :class:`SyncBatchTTS` which map to
``POST /tts`` on the TTS gateway.  The API key is sent as a Bearer
token in the ``Authorization`` header.
"""

from __future__ import annotations

from typing import Optional

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import SynthesisError
from shunyalabs._core._http_transport import AsyncHttpTransport, SyncHttpTransport
from shunyalabs._core._logging import get_logger

from ._models import TTSConfig, TTSResult

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_payload(
    text: str,
    config: Optional[TTSConfig],
) -> dict:
    """Build the JSON body for ``POST /tts``.

    If *config* is ``None`` a default :class:`TTSConfig` is used.
    Authentication is handled via the ``Authorization`` header, not
    in the JSON body.
    """
    cfg = config or TTSConfig()
    return cfg.to_request_payload(
        target_text=text,
        request_type="batch",
    )


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class AsyncBatchTTS:
    """Async batch TTS client.

    Maps to ``POST /tts`` on the TTS gateway.

    Args:
        auth: Authentication instance providing the API key.
        transport: An :class:`AsyncHttpTransport` pointed at the gateway.
    """

    def __init__(
        self,
        auth: StaticKeyAuth,
        transport: AsyncHttpTransport,
    ) -> None:
        self._auth = auth
        self._transport = transport

    async def synthesize(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> TTSResult:
        """Synthesize *text* to speech and return the result.

        Args:
            text: The text to synthesise (1--10 000 characters).
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            A :class:`TTSResult` containing decoded audio bytes plus
            metadata.

        Raises:
            SynthesisError: If the gateway returns an error response.
        """
        payload = _build_payload(text, config)
        logger.debug("POST /tts payload keys: %s", list(payload.keys()))

        try:
            response_data = await self._transport.post_json("/tts", json_data=payload)
        except Exception as exc:
            raise SynthesisError(f"Batch synthesis request failed: {exc}") from exc

        # Check for gateway-level error body.
        if isinstance(response_data, dict) and "error" in response_data:
            raise SynthesisError(
                response_data.get("error", "Unknown synthesis error")
            )

        return TTSResult.from_api_response(response_data)


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class SyncBatchTTS:
    """Synchronous batch TTS client.

    Maps to ``POST /tts`` on the TTS gateway.

    Args:
        auth: Authentication instance providing the API key.
        transport: A :class:`SyncHttpTransport` pointed at the gateway.
    """

    def __init__(
        self,
        auth: StaticKeyAuth,
        transport: SyncHttpTransport,
    ) -> None:
        self._auth = auth
        self._transport = transport

    def synthesize(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> TTSResult:
        """Synthesize *text* to speech and return the result.

        Args:
            text: The text to synthesise (1--10 000 characters).
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            A :class:`TTSResult` containing decoded audio bytes plus
            metadata.

        Raises:
            SynthesisError: If the gateway returns an error response.
        """
        payload = _build_payload(text, config)
        logger.debug("POST /tts payload keys: %s", list(payload.keys()))

        try:
            response_data = self._transport.post_json("/tts", json_data=payload)
        except Exception as exc:
            raise SynthesisError(f"Batch synthesis request failed: {exc}") from exc

        # Check for gateway-level error body.
        if isinstance(response_data, dict) and "error" in response_data:
            raise SynthesisError(
                response_data.get("error", "Unknown synthesis error")
            )

        return TTSResult.from_api_response(response_data)


__all__ = ["AsyncBatchTTS", "SyncBatchTTS"]
