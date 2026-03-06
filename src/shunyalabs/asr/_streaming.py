"""Async streaming ASR client over WebSocket (``WS /ws``).

The streaming protocol works as follows:

1. Connect to the WebSocket endpoint (``/ws``).
2. Send a JSON configuration frame containing language, sample rate, etc.
   The ``api_key`` is injected automatically from :class:`StaticKeyAuth`.
3. Receive a ``{"type": "ready", "session_id": "..."}`` acknowledgement.
4. Send raw binary PCM audio chunks.
5. Send the text message ``"END"`` to signal the end of the audio stream.
6. Continue receiving ``partial``, ``final_segment``, ``final``, and
   ``done`` messages until the server closes the connection.

The SDK exposes two user-facing classes:

* :class:`ASRStreamingConnection` -- an active, event-driven connection
  that lets callers push audio and react to transcription events.
* :class:`AsyncStreamingASR` -- a factory that creates streaming
  connections.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional, Union

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._events import EventEmitter
from shunyalabs._core._exceptions import (
    ConnectionError,
    SessionError,
    TranscriptionError,
    TransportError,
)
from shunyalabs._core._logging import get_logger
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport

from ._models import (
    StreamingConfig,
    StreamingMessageType,
    parse_streaming_message,
)

logger = get_logger(__name__)


class ASRStreamingConnection(EventEmitter):
    """An active streaming connection to the ASR WebSocket gateway.

    Events are emitted using :class:`StreamingMessageType` values as keys.
    Register handlers with the :meth:`on` / :meth:`once` decorators inherited
    from :class:`EventEmitter`::

        conn = await streaming.stream()

        @conn.on(StreamingMessageType.PARTIAL)
        def on_partial(msg):
            print(msg.text)

        await conn.stream_file("recording.raw")

    The connection manages its own background *receiver* task that reads
    from the WebSocket and dispatches incoming JSON messages.

    Args:
        transport: A connected :class:`WsTransport`.
        session_id: The session ID returned by the server in the ``ready`` message.
    """

    def __init__(self, transport: WsTransport, session_id: str) -> None:
        super().__init__()
        self._transport = transport
        self._session_id = session_id
        self._closed = False
        self._receiver_task: Optional[asyncio.Task] = None
        self._done_event = asyncio.Event()
        self._logger = logger

    @property
    def session_id(self) -> str:
        """Server-assigned session identifier."""
        return self._session_id

    @property
    def is_closed(self) -> bool:
        return self._closed

    # -- Sending ------------------------------------------------------------

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send a chunk of raw PCM audio to the server.

        Args:
            audio_bytes: Raw audio bytes (PCM, matching the configured
                ``sample_rate`` and ``dtype``).

        Raises:
            TransportError: If the connection is closed or the send fails.
        """
        if self._closed:
            raise TransportError("Connection is closed")
        await self._transport.send_message(audio_bytes)

    async def end(self) -> None:
        """Signal the end of the audio stream.

        Sends the ``"END"`` text frame and waits for the server to emit the
        ``done`` message, then tears down the receiver task.
        """
        if self._closed:
            return
        self._logger.debug("Sending END signal")
        try:
            await self._transport.send_message("END")
        except Exception as exc:
            self._logger.warning("Failed to send END: %s", exc)

        # Wait for the receiver to see the ``done`` message
        try:
            await asyncio.wait_for(self._done_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            self._logger.warning("Timed out waiting for 'done' after END")

    async def stream_file(
        self,
        file_path: Union[str, Path],
        chunk_size: int = 4096,
    ) -> None:
        """Convenience: read a file in chunks and stream it, then send END.

        Args:
            file_path: Path to a raw PCM file.
            chunk_size: Number of bytes to read per iteration.
        """
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Audio file not found: {path}")

        self._logger.debug("Streaming file %s (chunk_size=%d)", path, chunk_size)
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                await self.send_audio(chunk)
                # Yield control so that the receiver task can process incoming
                # messages concurrently.
                await asyncio.sleep(0)

        await self.end()

    async def close(self) -> None:
        """Close the WebSocket and cancel the background receiver."""
        if self._closed:
            return
        self._closed = True
        self._done_event.set()

        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass

        try:
            await self._transport.close()
        except Exception:
            pass

        self.remove_all_listeners()
        self._logger.debug("Streaming connection closed")

    # -- Background receiver ------------------------------------------------

    def _start_receiver(self) -> None:
        """Launch the background task that reads server messages."""
        self._receiver_task = asyncio.get_running_loop().create_task(
            self._receive_loop(), name="asr-streaming-receiver"
        )

    async def _receive_loop(self) -> None:
        """Continuously read from the WebSocket and dispatch events."""
        try:
            while not self._closed:
                try:
                    raw = await self._transport.receive_message()
                except TransportError:
                    if self._closed:
                        break
                    raise

                if isinstance(raw, dict):
                    msg_type_str = raw.get("type", "")
                    parsed = parse_streaming_message(raw)

                    # Map to enum for the event key
                    try:
                        event_key = StreamingMessageType(msg_type_str)
                    except ValueError:
                        self._logger.warning("Unknown streaming message type: %s", msg_type_str)
                        continue

                    self.emit(event_key, parsed)

                    if event_key == StreamingMessageType.DONE:
                        self._done_event.set()
                        break
                    elif event_key == StreamingMessageType.ERROR:
                        self._done_event.set()
                        break
                else:
                    # Unexpected non-dict (binary echo, etc.) -- skip
                    self._logger.debug("Ignoring non-JSON message from server")

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._logger.error("Receiver loop error: %s", exc, exc_info=True)
            self._done_event.set()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class AsyncStreamingASR:
    """Factory for creating streaming ASR connections.

    Handles the WebSocket handshake (connect -> send config -> wait for
    ``ready``) and returns a ready-to-use :class:`ASRStreamingConnection`.

    Args:
        auth: A :class:`StaticKeyAuth` instance.
        ws_url: The full WebSocket URL (e.g. ``wss://asr.api.shunyalabs.com/ws``).
        ws_config: Optional WebSocket connection settings (timeouts, ping, etc.).
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
        self._logger = logger

    async def stream(
        self,
        *,
        config: Optional[StreamingConfig] = None,
    ) -> ASRStreamingConnection:
        """Open a new streaming connection and return it ready for audio.

        The method performs the full handshake:

        1. Establishes a WebSocket connection.
        2. Sends the :class:`StreamingConfig` JSON (with ``api_key`` injected).
        3. Waits for the ``ready`` acknowledgement from the server.
        4. Starts a background receiver task.

        Args:
            config: Streaming parameters.  Uses gateway defaults when ``None``.

        Returns:
            An :class:`ASRStreamingConnection` ready for :meth:`send_audio`.

        Raises:
            ConnectionError: If the WebSocket connection fails.
            SessionError: If the server does not acknowledge with a ``ready`` message.
        """
        config = config or StreamingConfig()
        payload = config.to_ws_payload()

        transport = WsTransport(
            url=self._ws_url,
            auth=self._auth,
            conn_config=self._ws_config,
            sdk_component="asr",
        )

        self._logger.debug("Connecting to streaming endpoint: %s", self._ws_url)
        await transport.connect()

        # Step 2: send config
        self._logger.debug("Sending streaming config: %s", {k: v for k, v in payload.items() if k != "api_key"})
        await transport.send_message(payload)

        # Step 3: wait for ``ready``
        try:
            raw_ready = await asyncio.wait_for(transport.receive_message(), timeout=15.0)
        except asyncio.TimeoutError:
            await transport.close()
            raise SessionError("Timed out waiting for 'ready' from the ASR server")

        if not isinstance(raw_ready, dict) or raw_ready.get("type") != "ready":
            await transport.close()
            # If the server sent an error instead, surface it
            if isinstance(raw_ready, dict) and raw_ready.get("type") == "error":
                msg = raw_ready.get("message", "Unknown error")
                raise SessionError(f"Server rejected streaming session: {msg}")
            raise SessionError(f"Expected 'ready' message, got: {raw_ready}")

        session_id = raw_ready.get("session_id", "")
        self._logger.debug("Streaming session ready (session_id=%s)", session_id)

        conn = ASRStreamingConnection(transport, session_id)
        conn._start_receiver()
        return conn


__all__ = ["ASRStreamingConnection", "AsyncStreamingASR"]
