"""Batch TTS clients for the Shunyalabs SDK (OpenAI-compatible).

Provides :class:`AsyncBatchTTS` and :class:`SyncBatchTTS` which map to
``POST /v1/audio/speech`` on the TTS gateway.  The API key is sent as a
Bearer token in the ``Authorization`` header.  The endpoint returns raw
binary audio data (not JSON).
"""

from __future__ import annotations

from typing import Optional

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import APIError, SynthesisError
from shunyalabs._core._http_transport import AsyncHttpTransport, SyncHttpTransport
from shunyalabs._core._logging import get_logger

from ._models import TTSConfig, TTSResult

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TTS_PATH = "/"


def _build_payload(
    text: str,
    config: Optional[TTSConfig],
) -> dict:
    """Build the JSON body for ``POST /``.

    If *config* is ``None`` a default :class:`TTSConfig` is used.
    Authentication is handled via the ``Authorization`` header, not
    in the JSON body.
    """
    cfg = config or TTSConfig()
    return cfg.to_request_payload(
        text=text,
        request_type="batch",
    )


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class AsyncBatchTTS:
    """Async batch TTS client (OpenAI-compatible).

    Maps to ``POST /v1/audio/speech`` on the TTS gateway.  The endpoint
    returns raw binary audio data.

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
            A :class:`TTSResult` containing audio bytes plus metadata.

        Raises:
            SynthesisError: If the gateway returns an error response.
        """
        cfg = config or TTSConfig()
        payload = _build_payload(text, config)
        logger.debug("POST %s payload keys: %s", _TTS_PATH, list(payload.keys()))

        try:
            audio_bytes = await self._transport.post_json_raw(_TTS_PATH, json_data=payload)
        except APIError:
            raise
        except Exception as exc:
            raise SynthesisError(f"Batch synthesis request failed: {exc}") from exc

        fmt = cfg.response_format.value if cfg.response_format else "mp3"
        return TTSResult.from_raw_audio(audio_bytes, format=fmt)


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class SyncBatchTTS:
    """Synchronous batch TTS client (OpenAI-compatible).

    Maps to ``POST /v1/audio/speech`` on the TTS gateway.  The endpoint
    returns raw binary audio data.

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
            A :class:`TTSResult` containing audio bytes plus metadata.

        Raises:
            SynthesisError: If the gateway returns an error response.
        """
        cfg = config or TTSConfig()
        payload = _build_payload(text, config)
        logger.debug("POST %s payload keys: %s", _TTS_PATH, list(payload.keys()))

        try:
            audio_bytes = self._transport.post_json_raw(_TTS_PATH, json_data=payload)
        except APIError:
            raise
        except Exception as exc:
            raise SynthesisError(f"Batch synthesis request failed: {exc}") from exc

        fmt = cfg.response_format.value if cfg.response_format else "mp3"
        return TTSResult.from_raw_audio(audio_bytes, format=fmt)


__all__ = ["AsyncBatchTTS", "SyncBatchTTS"]
