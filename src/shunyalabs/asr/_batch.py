"""Batch ASR clients (sync and async) for ``POST /v1/transcriptions``.

Both clients accept an audio source (file path, file-like object, or URL)
together with an optional :class:`TranscriptionConfig` and return a
:class:`TranscriptionResult`.

The API key is sent as a Bearer token in the ``Authorization`` header
(handled by the transport's ``StaticKeyAuth``).  All config values are
serialised as multipart form fields; the audio file is attached as a
standard file upload.
"""

from __future__ import annotations

import os
from io import IOBase
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Union

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import (
    APIError,
    AuthenticationError,
    ConfigurationError,
    PermissionDeniedError,
    RateLimitError,
    TranscriptionError,
)
from shunyalabs._core._http_transport import AsyncHttpTransport, SyncHttpTransport
from shunyalabs._core._logging import get_logger

from ._models import TranscriptionConfig, TranscriptionResult

logger = get_logger(__name__)

_TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_content_type(filename: str) -> str:
    """Return a sensible MIME type based on file extension."""
    ext = Path(filename).suffix.lower()
    return {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".wma": "audio/x-ms-wma",
        ".opus": "audio/opus",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Async batch client
# ---------------------------------------------------------------------------


class AsyncBatchASR:
    """Async client for the ASR batch endpoint.

    Maps to ``POST /v1/transcriptions`` on the ASR gateway.

    Args:
        auth: A :class:`StaticKeyAuth` instance (provides the Bearer header).
        transport: A pre-configured :class:`AsyncHttpTransport`.
    """

    def __init__(self, auth: StaticKeyAuth, transport: AsyncHttpTransport) -> None:
        self._auth = auth
        self._transport = transport
        self._logger = logger

    # -- High-level entry point ---------------------------------------------

    async def transcribe(
        self,
        audio: Optional[Union[str, Path, BinaryIO]] = None,
        *,
        url: Optional[str] = None,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Transcribe audio from a local file, file-like object, or remote URL.

        Exactly one of *audio* or *url* must be provided.

        Args:
            audio: A file path (``str`` / ``Path``) or a readable binary
                file-like object (e.g. ``open(..., "rb")`` or ``BytesIO``).
            url: A publicly-accessible URL pointing to the audio file.
            config: Transcription options.  Uses gateway defaults when ``None``.

        Returns:
            A :class:`TranscriptionResult` with the full transcription response.

        Raises:
            ConfigurationError: When both or neither of *audio* / *url* are given.
            TranscriptionError: When the gateway returns an error response.
        """
        if audio is not None and url is not None:
            raise ConfigurationError("Provide either 'audio' or 'url', not both")
        if audio is None and url is None:
            raise ConfigurationError("Provide either 'audio' (file) or 'url'")

        if url is not None:
            return await self.transcribe_url(url, config=config)
        return await self.transcribe_file(audio, config=config)  # type: ignore[arg-type]

    # -- Low-level: file upload ---------------------------------------------

    async def transcribe_file(
        self,
        audio: Union[str, Path, BinaryIO],
        *,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Upload a local audio file for transcription (multipart form).

        Args:
            audio: File path or readable binary object.
            config: Transcription options.

        Returns:
            :class:`TranscriptionResult`
        """
        import aiohttp

        config = config or TranscriptionConfig()
        form = aiohttp.FormData()

        # Attach config fields
        for name, value in config.to_form_fields().items():
            form.add_field(name, value)

        # Attach audio file
        if isinstance(audio, (str, Path)):
            path = Path(audio)
            if not path.is_file():
                raise ConfigurationError(f"Audio file not found: {path}")
            filename = path.name
            ct = _guess_content_type(filename)
            fh = open(path, "rb")
            form.add_field("file", fh, filename=filename, content_type=ct)
            close_after = True
        else:
            filename = getattr(audio, "name", "audio.wav")
            if isinstance(filename, (Path, str)):
                filename = Path(filename).name
            ct = _guess_content_type(str(filename))
            form.add_field("file", audio, filename=str(filename), content_type=ct)
            close_after = False

        self._logger.debug(
            "transcribe_file: uploading %s (%s)", filename, ct,
        )

        try:
            raw = await self._transport.post_form(_TRANSCRIPTIONS_PATH, form_data=form)
            return self._parse_response(raw)
        except APIError as exc:
            if isinstance(exc, (AuthenticationError, PermissionDeniedError, RateLimitError)):
                raise
            raise TranscriptionError(str(exc)) from exc
        except Exception as exc:
            raise TranscriptionError(f"Batch transcription failed: {exc}") from exc
        finally:
            if close_after:
                fh.close()  # type: ignore[possibly-undefined]

    # -- Low-level: URL-based -----------------------------------------------

    async def transcribe_url(
        self,
        audio_url: str,
        *,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Transcribe audio referenced by a remote URL.

        Args:
            audio_url: Publicly-accessible URL to the audio resource.
            config: Transcription options.

        Returns:
            :class:`TranscriptionResult`
        """
        import aiohttp

        config = config or TranscriptionConfig()
        form = aiohttp.FormData()

        for name, value in config.to_form_fields().items():
            form.add_field(name, value)

        form.add_field("url", audio_url)

        self._logger.debug("transcribe_url: %s", audio_url)

        try:
            raw = await self._transport.post_form(_TRANSCRIPTIONS_PATH, form_data=form)
            return self._parse_response(raw)
        except APIError as exc:
            if isinstance(exc, (AuthenticationError, PermissionDeniedError, RateLimitError)):
                raise
            raise TranscriptionError(str(exc)) from exc
        except Exception as exc:
            raise TranscriptionError(f"Batch transcription (URL) failed: {exc}") from exc

    # -- Response parsing ---------------------------------------------------

    @staticmethod
    def _parse_response(raw: Dict[str, Any]) -> TranscriptionResult:
        """Validate and convert the raw JSON dict into a model instance."""
        if not raw.get("success", False):
            detail = raw.get("detail") or raw.get("error") or raw.get("message") or "Unknown error"
            raise TranscriptionError(f"Gateway returned failure: {detail}")
        return TranscriptionResult.model_validate(raw)

    # -- Lifecycle ----------------------------------------------------------

    async def close(self) -> None:
        """Release the underlying HTTP transport resources."""
        await self._transport.close()


# ---------------------------------------------------------------------------
# Sync batch client
# ---------------------------------------------------------------------------


class SyncBatchASR:
    """Synchronous client for the ASR batch endpoint.

    Mirrors :class:`AsyncBatchASR` but uses ``httpx`` under the hood via
    :class:`SyncHttpTransport`.

    Args:
        auth: A :class:`StaticKeyAuth` instance.
        transport: A pre-configured :class:`SyncHttpTransport`.
    """

    def __init__(self, auth: StaticKeyAuth, transport: SyncHttpTransport) -> None:
        self._auth = auth
        self._transport = transport
        self._logger = logger

    # -- High-level entry point ---------------------------------------------

    def transcribe(
        self,
        audio: Optional[Union[str, Path, BinaryIO]] = None,
        *,
        url: Optional[str] = None,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Transcribe audio from a local file, file-like object, or remote URL.

        Exactly one of *audio* or *url* must be provided.

        Args:
            audio: A file path (``str`` / ``Path``) or a readable binary
                file-like object.
            url: A publicly-accessible URL pointing to the audio file.
            config: Transcription options.

        Returns:
            :class:`TranscriptionResult`
        """
        if audio is not None and url is not None:
            raise ConfigurationError("Provide either 'audio' or 'url', not both")
        if audio is None and url is None:
            raise ConfigurationError("Provide either 'audio' (file) or 'url'")

        if url is not None:
            return self.transcribe_url(url, config=config)
        return self.transcribe_file(audio, config=config)  # type: ignore[arg-type]

    # -- Low-level: file upload ---------------------------------------------

    def transcribe_file(
        self,
        audio: Union[str, Path, BinaryIO],
        *,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Upload a local audio file for transcription.

        Args:
            audio: File path or readable binary object.
            config: Transcription options.

        Returns:
            :class:`TranscriptionResult`
        """
        config = config or TranscriptionConfig()
        form_fields = config.to_form_fields()

        if isinstance(audio, (str, Path)):
            path = Path(audio)
            if not path.is_file():
                raise ConfigurationError(f"Audio file not found: {path}")
            filename = path.name
            ct = _guess_content_type(filename)
            fh = open(path, "rb")
            files = {"file": (filename, fh, ct)}
            close_after = True
        else:
            filename = getattr(audio, "name", "audio.wav")
            if isinstance(filename, (Path, str)):
                filename = Path(filename).name
            ct = _guess_content_type(str(filename))
            files = {"file": (str(filename), audio, ct)}
            close_after = False

        self._logger.debug(
            "transcribe_file (sync): uploading %s (%s)", filename, ct,
        )

        try:
            raw = self._transport.post_form(
                _TRANSCRIPTIONS_PATH, data=form_fields, files=files,
            )
            return self._parse_response(raw)
        except APIError as exc:
            if isinstance(exc, (AuthenticationError, PermissionDeniedError, RateLimitError)):
                raise
            raise TranscriptionError(str(exc)) from exc
        except Exception as exc:
            raise TranscriptionError(f"Batch transcription failed: {exc}") from exc
        finally:
            if close_after:
                fh.close()  # type: ignore[possibly-undefined]

    # -- Low-level: URL-based -----------------------------------------------

    def transcribe_url(
        self,
        audio_url: str,
        *,
        config: Optional[TranscriptionConfig] = None,
    ) -> TranscriptionResult:
        """Transcribe audio referenced by a remote URL.

        Args:
            audio_url: Publicly-accessible URL to the audio resource.
            config: Transcription options.

        Returns:
            :class:`TranscriptionResult`
        """
        config = config or TranscriptionConfig()
        form_fields = config.to_form_fields()
        form_fields["url"] = audio_url

        self._logger.debug("transcribe_url (sync): %s", audio_url)

        try:
            raw = self._transport.post_form(
                _TRANSCRIPTIONS_PATH, data=form_fields,
            )
            return self._parse_response(raw)
        except APIError as exc:
            if isinstance(exc, (AuthenticationError, PermissionDeniedError, RateLimitError)):
                raise
            raise TranscriptionError(str(exc)) from exc
        except Exception as exc:
            raise TranscriptionError(f"Batch transcription (URL) failed: {exc}") from exc

    # -- Response parsing ---------------------------------------------------

    @staticmethod
    def _parse_response(raw: Dict[str, Any]) -> TranscriptionResult:
        """Validate and convert the raw JSON dict into a model instance."""
        if not raw.get("success", False):
            detail = raw.get("detail") or raw.get("error") or raw.get("message") or "Unknown error"
            raise TranscriptionError(f"Gateway returned failure: {detail}")
        return TranscriptionResult.model_validate(raw)

    # -- Lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying HTTP transport resources."""
        self._transport.close()


__all__ = ["AsyncBatchASR", "SyncBatchASR"]
