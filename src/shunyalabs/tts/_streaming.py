"""Streaming TTS clients for the Shunyalabs SDK.

Provides :class:`AsyncStreamingTTS` and :class:`SyncStreamingTTS` which
communicate over the ``/ws/tts`` WebSocket endpoint on the TTS gateway.

WebSocket protocol
------------------
1. Client connects to ``ws://<host>/ws/tts``.
2. Client sends a single JSON frame containing all ``TTSRequestSchema``
   fields (with ``request_type = "streaming"``).
3. For each audio chunk the server sends:
   a. A **JSON** frame with chunk metadata (``{"type": "chunk", ...}``).
   b. A **binary** frame containing the raw audio bytes.
4. After the last chunk the server sends a **JSON** completion frame
   (``{"type": "completion", ...}``).
5. On error at any point the server may send ``{"type": "error", ...}``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import (
    AsyncIterator,
    Iterator,
    Optional,
    Tuple,
    Union,
)

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import SynthesisError
from shunyalabs._core._logging import get_logger
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport

from ._models import TTSChunk, TTSCompletion, TTSConfig

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_ws_payload(
    text: str,
    config: Optional[TTSConfig],
) -> dict:
    """Build the JSON config frame for the ``/ws/tts`` WebSocket.

    Authentication is handled via the ``Authorization`` header on the
    WebSocket connection, not in the JSON payload.
    """
    cfg = config or TTSConfig()
    return cfg.to_request_payload(
        target_text=text,
        request_type="streaming",
    )


# ---------------------------------------------------------------------------
# Async streaming client
# ---------------------------------------------------------------------------

class AsyncStreamingTTS:
    """Async streaming TTS via WebSocket ``/ws/tts``.

    Args:
        auth: Authentication instance providing the API key.
        ws_url: Full WebSocket URL for the ``/ws/tts`` endpoint
            (e.g. ``"ws://localhost:8000/ws/tts"``).
        ws_config: Optional WebSocket connection configuration.
    """

    def __init__(
        self,
        auth: StaticKeyAuth,
        ws_url: str,
        ws_config: Optional[WsConnectionConfig] = None,
    ) -> None:
        self._auth = auth
        self._ws_url = ws_url
        self._ws_config = ws_config

    # -- core streaming generator ------------------------------------------

    async def stream(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
        detailed: bool = False,
    ) -> AsyncIterator[Union[bytes, Tuple[TTSChunk, bytes]]]:
        """Stream synthesised audio chunks from the gateway.

        Args:
            text: The text to synthesise.
            config: Optional :class:`TTSConfig` overriding defaults.
            detailed: When *False* (default) yields raw ``bytes`` for each
                audio chunk.  When *True* yields ``(TTSChunk, bytes)``
                tuples so callers can inspect chunk metadata.

        Yields:
            ``bytes`` audio data, or ``(TTSChunk, bytes)`` if
            *detailed* is *True*.

        Raises:
            SynthesisError: On protocol or server errors.
        """
        transport = WsTransport(
            url=self._ws_url,
            auth=self._auth,
            conn_config=self._ws_config,
            sdk_component="tts",
        )

        try:
            await transport.connect()

            # 1. Send the config frame.
            payload = _build_ws_payload(text, config)
            logger.debug("WS /ws/tts sending config: %s", list(payload.keys()))
            await transport.send_message(payload)

            # 2. Receive chunks until completion / error.
            while True:
                msg = await transport.receive_message()

                # --- JSON frame ---
                if isinstance(msg, dict):
                    msg_type = msg.get("type")

                    if msg_type == "chunk":
                        chunk = TTSChunk(**msg)
                        # Next frame must be binary audio.
                        audio_data = await transport.receive_message()
                        if not isinstance(audio_data, bytes):
                            raise SynthesisError(
                                f"Expected binary audio frame after chunk metadata, "
                                f"got {type(audio_data).__name__}"
                            )
                        if detailed:
                            yield (chunk, audio_data)
                        else:
                            yield audio_data

                    elif msg_type == "completion":
                        # Stream ended normally.
                        logger.debug("Stream completed: %s", msg)
                        break

                    elif msg_type == "error":
                        error_detail = msg.get("error", "Unknown streaming error")
                        raise SynthesisError(f"Streaming error: {error_detail}")

                    else:
                        logger.warning("Unknown WS message type: %s", msg_type)

                # --- unexpected binary frame outside chunk flow ---
                elif isinstance(msg, bytes):
                    logger.warning(
                        "Received unexpected binary frame (%d bytes), skipping.",
                        len(msg),
                    )

                else:
                    logger.warning("Received unexpected WS message: %r", msg)

        finally:
            await transport.close()

    # -- convenience: collect all chunks ------------------------------------

    async def synthesize(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> bytes:
        """Synthesise text and return the combined audio as a single
        ``bytes`` object.

        This is a convenience wrapper around :meth:`stream` that
        concatenates all chunks.

        Args:
            text: The text to synthesise.
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            Concatenated audio bytes.
        """
        chunks: list[bytes] = []
        async for audio in self.stream(text, config=config):
            chunks.append(audio)
        return b"".join(chunks)

    # -- convenience: stream to file ----------------------------------------

    async def stream_to_file(
        self,
        text: str,
        path: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> TTSCompletion:
        """Stream synthesised audio directly to a file.

        Args:
            text: The text to synthesise.
            path: Filesystem path for the output file.
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            The :class:`TTSCompletion` message received at the end of
            the stream.

        Raises:
            SynthesisError: On protocol or server errors.
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        transport = WsTransport(
            url=self._ws_url,
            auth=self._auth,
            conn_config=self._ws_config,
            sdk_component="tts",
        )

        completion: Optional[TTSCompletion] = None

        try:
            await transport.connect()

            payload = _build_ws_payload(text, config)
            await transport.send_message(payload)

            with open(dest, "wb") as fh:
                while True:
                    msg = await transport.receive_message()

                    if isinstance(msg, dict):
                        msg_type = msg.get("type")

                        if msg_type == "chunk":
                            # Read the following binary frame.
                            audio_data = await transport.receive_message()
                            if isinstance(audio_data, bytes):
                                fh.write(audio_data)

                        elif msg_type == "completion":
                            completion = TTSCompletion(**msg)
                            break

                        elif msg_type == "error":
                            error_detail = msg.get("error", "Unknown streaming error")
                            raise SynthesisError(f"Streaming error: {error_detail}")

                    elif isinstance(msg, bytes):
                        # Unexpected standalone binary -- write it anyway.
                        fh.write(msg)

        finally:
            await transport.close()

        if completion is None:
            raise SynthesisError("Stream ended without a completion message")

        return completion


# ---------------------------------------------------------------------------
# Sync streaming client
# ---------------------------------------------------------------------------

class SyncStreamingTTS:
    """Synchronous streaming TTS via WebSocket ``/ws/tts``.

    Internally wraps :class:`AsyncStreamingTTS` using :func:`asyncio.run`.

    Args:
        auth: Authentication instance providing the API key.
        ws_url: Full WebSocket URL for the ``/ws/tts`` endpoint.
        ws_config: Optional WebSocket connection configuration.
    """

    def __init__(
        self,
        auth: StaticKeyAuth,
        ws_url: str,
        ws_config: Optional[WsConnectionConfig] = None,
    ) -> None:
        self._auth = auth
        self._ws_url = ws_url
        self._ws_config = ws_config

    def _new_async(self) -> AsyncStreamingTTS:
        """Create a fresh async streaming client instance."""
        return AsyncStreamingTTS(
            auth=self._auth,
            ws_url=self._ws_url,
            ws_config=self._ws_config,
        )

    # -- core streaming generator ------------------------------------------

    def stream(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
        detailed: bool = False,
    ) -> Iterator[Union[bytes, Tuple[TTSChunk, bytes]]]:
        """Stream synthesised audio chunks synchronously.

        Args:
            text: The text to synthesise.
            config: Optional :class:`TTSConfig` overriding defaults.
            detailed: When *True* yields ``(TTSChunk, bytes)`` instead
                of raw ``bytes``.

        Yields:
            ``bytes`` or ``(TTSChunk, bytes)`` per chunk.
        """
        # We collect all chunks via the async implementation, then yield
        # them.  True incremental sync streaming would require a
        # background thread; this approach keeps the implementation simple
        # while still exposing the iterator interface.
        async def _collect():
            results = []
            async_client = self._new_async()
            async for item in async_client.stream(text, config=config, detailed=detailed):
                results.append(item)
            return results

        items = asyncio.run(_collect())
        yield from items

    # -- convenience: collect all chunks ------------------------------------

    def synthesize(
        self,
        text: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> bytes:
        """Synthesise text and return the combined audio.

        Args:
            text: The text to synthesise.
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            Concatenated audio bytes.
        """
        async_client = self._new_async()
        return asyncio.run(async_client.synthesize(text, config=config))

    # -- convenience: stream to file ----------------------------------------

    def stream_to_file(
        self,
        text: str,
        path: str,
        *,
        config: Optional[TTSConfig] = None,
    ) -> TTSCompletion:
        """Stream synthesised audio to a file synchronously.

        Args:
            text: The text to synthesise.
            path: Filesystem path for the output file.
            config: Optional :class:`TTSConfig` overriding defaults.

        Returns:
            The :class:`TTSCompletion` message.
        """
        async_client = self._new_async()
        return asyncio.run(
            async_client.stream_to_file(text, path, config=config)
        )


__all__ = ["AsyncStreamingTTS", "SyncStreamingTTS"]
