"""Streaming TTS clients for the Shunyalabs SDK.

Provides :class:`AsyncStreamingTTS` and :class:`SyncStreamingTTS` which
communicate over the ``wss://ttsv2.shunyalabs.ai/v1/realtime`` WebSocket
endpoint.

WebSocket protocol
------------------
1. Client connects to ``wss://ttsv2.shunyalabs.ai/v1/realtime``.
2. Client sends an init JSON frame ``{"voice": ..., "language": ...,
   "model": ...}``; the server replies ``{"type": "ready", ...}``.
3. Client sends text as ``{"type": "text", "text": ...}`` frames and
   flushes a segment with ``{"type": "flush"}``.
4. To end audio the client sends the bare string ``"end"`` (lowercase).
5. The server streams ``{"type": "speaking"}`` markers interleaved with
   **binary** PCM audio frames, then a final ``{"type": "done"}`` frame.
6. On error at any point the server may send ``{"type": "error", ...}``.
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
    """Build the JSON config frame for the ``/v1/realtime`` WebSocket.

    Authentication is handled via the ``Authorization`` header on the
    WebSocket connection, not in the JSON payload.
    """
    cfg = config or TTSConfig()
    return cfg.to_request_payload(
        text=text,
        request_type="streaming",
    )


# ---------------------------------------------------------------------------
# Async streaming client
# ---------------------------------------------------------------------------

class AsyncStreamingTTS:
    """Async streaming TTS via WebSocket ``/v1/realtime``.

    Args:
        auth: Authentication instance providing the API key.
        ws_url: Full WebSocket URL for the ``/v1/realtime`` endpoint
            (e.g. ``"wss://ttsv2.shunyalabs.ai/v1/realtime"``).
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

            # 1. init frame -> "ready" handshake. The first frame is a JSON object with the voice /
            #    language; the server replies {"type":"ready","sample_rate":...}.
            cfg = config or TTSConfig()
            init: dict = {"voice": cfg.voice, "language": cfg.language}
            if getattr(cfg, "model", None):
                init["model"] = cfg.model
            logger.debug("WS /v1/realtime init: %s", init)
            await transport.send_message(init)
            ready = await transport.receive_message()
            if not isinstance(ready, dict) or ready.get("type") != "ready":
                if isinstance(ready, dict) and ready.get("type") == "error":
                    raise SynthesisError(f"Streaming error: {ready.get('error')}")
                raise SynthesisError(f"Expected 'ready' handshake, got {ready!r}")
            sr = ready.get("sample_rate")

            # 2. Speak the text, then close this one-shot session with the bare-string "end".
            await transport.send_message({"type": "text", "text": text})
            await transport.send_message("end")

            # 3. Receive `speaking` markers + binary PCM frames until `done`.
            while True:
                msg = await transport.receive_message()

                if isinstance(msg, (bytes, bytearray)):
                    audio_data = bytes(msg)
                    if detailed:
                        yield (TTSChunk(type="chunk", sample_rate=sr), audio_data)
                    else:
                        yield audio_data
                    continue

                if isinstance(msg, dict):
                    msg_type = msg.get("type")
                    if msg_type in ("speaking", "ready"):
                        continue
                    elif msg_type == "done":
                        logger.debug("Stream completed: %s", msg)
                        break
                    elif msg_type == "error":
                        raise SynthesisError(
                            f"Streaming error: {msg.get('error', 'Unknown streaming error')}"
                        )
                    else:
                        logger.warning("Unknown WS message type: %s", msg_type)
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

        # Reuse the ported `stream()` (new /v1/realtime handshake) and write the PCM to disk.
        total_bytes = 0
        chunks = 0
        with open(dest, "wb") as fh:
            async for audio in self.stream(text, config=config):
                fh.write(audio)
                total_bytes += len(audio)
                chunks += 1

        return TTSCompletion(
            status="complete",
            total_chunks=chunks,
            # 24 kHz mono int16 is the realtime PCM format; derive seconds from the byte count.
            total_duration_seconds=round(total_bytes / (24000 * 2), 3),
        )


# ---------------------------------------------------------------------------
# Sync streaming client
# ---------------------------------------------------------------------------

class SyncStreamingTTS:
    """Synchronous streaming TTS via WebSocket ``/v1/realtime``.

    Internally wraps :class:`AsyncStreamingTTS` using :func:`asyncio.run`.

    Args:
        auth: Authentication instance providing the API key.
        ws_url: Full WebSocket URL for the ``/v1/realtime`` endpoint.
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
