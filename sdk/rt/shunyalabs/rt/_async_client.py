from __future__ import annotations

import asyncio
from typing import Any
from typing import BinaryIO
from typing import Optional

from ._audio_sources import FileSource
from ._auth import AuthBase
from ._base_client import _BaseClient
from ._exceptions import AudioError
from ._exceptions import TimeoutError
from ._exceptions import TranscriptionError
from ._logging import get_logger
from ._models import AudioEventsConfig
from ._models import AudioFormat
from ._models import ClientMessageType
from ._models import ConnectionConfig
from ._models import ServerMessageType
from ._models import TranscriptionConfig
from ._models import TranslationConfig


class AsyncClient(_BaseClient):
    """
    Asynchronous client for Shunyalabs real-time audio transcription.

    This client provides a async interface to the Shunyalabs RT API,
    supporting real-time audio streaming, event-driven transcript handling, and
    comprehensive error management.

    Args:
        auth: Authentication instance. If not provided, uses StaticKeyAuth
                with api_key parameter or SHUNYALABS_API_KEY environment variable.
        api_key: Shunyalabs API key used if auth not provided.
        url: WebSocket endpoint URL. If not provided, uses SHUNYALABS_RT_URL
                environment variable or defaults to EU endpoint.
        conn_config: Websocket connection configuration.

    Raises:
        ConfigurationError: If required configuration is missing or invalid.

    Examples:
        Basic usage with event handlers:
            >>> async with AsyncClient(api_key="your-key") as client:
            ...     @client.on(ServerMessageType.ADD_TRANSCRIPT)
            ...     def handle_transcript(message):
            ...         result = TranscriptResult.from_message(message)
            ...         print(f"Final: {result.transcript}")
            ...
            ...     with open("audio.wav", "rb") as audio:
            ...         await client.transcribe(audio)

        With JWT authentication:
            >>> from shunyalabs.rt import JWTAuth
            >>> auth = JWTAuth("your-api-key", ttl=300)
            >>> async with AsyncClient(auth=auth) as client:
            ...     # Use client with custom settings
            ...     pass

        Manual resource management:
            >>> client = AsyncClient(api_key="your-key")
            >>> try:
            ...     await client.transcribe(audio_stream)
            ... finally:
            ...     await client.close()
    """

    def __init__(
        self,
        auth: Optional[AuthBase] = None,
        *,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        conn_config: Optional[ConnectionConfig] = None,
    ) -> None:
        self._logger = get_logger("shunyalabs.rt.async_client")
        self._logger.info("AsyncClient.__init__ called (api_key=%s, url=%s)", 
                         "***" if api_key else None, url)

        (
            self._session,
            self._recognition_started_evt,
            self._session_done_evt,
        ) = self._init_session_info()

        transport = self._create_transport_from_config(
            auth=auth,
            api_key=api_key,
            url=url,
            conn_config=conn_config,
            request_id=self._session.request_id,
        )
        super().__init__(transport)

        self.on(ServerMessageType.RECOGNITION_STARTED, self._on_recognition_started)
        self.on(ServerMessageType.END_OF_TRANSCRIPT, self._on_eot)
        self.on(ServerMessageType.ERROR, self._on_error)
        self.on(ServerMessageType.WARNING, self._on_warning)
        self.on(ServerMessageType.AUDIO_ADDED, self._on_audio_added)

        self._logger.debug("AsyncClient initialized (request_id=%s)", self._session.request_id)

    async def start_session(
        self,
        *,
        transcription_config: Optional[TranscriptionConfig] = None,
        audio_format: Optional[AudioFormat] = None,
        translation_config: Optional[TranslationConfig] = None,
        audio_events_config: Optional[AudioEventsConfig] = None,
        ws_headers: Optional[dict] = None,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "pingala-v1-universal",
        deliver_deltas_only: bool = True,
        use_api_gateway_format: bool = False,
    ) -> None:
        self._logger.info("AsyncClient.start_session called (session_id=%s, model=%s, use_api_gateway_format=%s)",
                         session_id, model, use_api_gateway_format)
        """
        This method establishes a WebSocket connection, and configures the transcription session.

        Args:
            transcription_config: Configuration for transcription behavior such as
                                language, partial transcripts, and advanced features.
                                Uses default if not provided.
            audio_format: Audio format specification including encoding, sample rate,
                          and chunk size. Uses default (PCM 16-bit LE, 16kHz) if not provided.
            translation_config: Optional translation configuration for real-time
                              translation output.
            audio_events_config: Optional configuration for audio event detection.
            ws_headers: Additional headers to include in the WebSocket handshake.
            session_id: Optional session ID for API Gateway format.
            api_key: Optional API key for API Gateway format.
            model: Model name for API Gateway format.
            deliver_deltas_only: Whether to deliver deltas only for API Gateway format.
            use_api_gateway_format: If True, use API Gateway format instead of standard format.

        Raises:
            ConnectionError: If the WebSocket connection fails.
            TranscriptionError: If the server reports an error during setup.
            TimeoutError: If the connection or setup times out.

        Examples:
            Basic streaming:
                >>> async with AsyncClient() as client:
                ...     await client.start_session()
                ...     await client.send_audio(frame)
        """
        try:
            await self._start_recognition_session(
                transcription_config=transcription_config,
                audio_format=audio_format,
                translation_config=translation_config,
                audio_events_config=audio_events_config,
                ws_headers=ws_headers,
                session_id=session_id,
                api_key=api_key,
                model=model,
                deliver_deltas_only=deliver_deltas_only,
                use_api_gateway_format=use_api_gateway_format,
            )
            self._logger.info("AsyncClient.start_session completed successfully")
        except Exception as e:
            self._logger.error("AsyncClient.start_session failed: %s", e)
            raise

    async def stop_session(self) -> None:
        self._logger.info("AsyncClient.stop_session called (seq_no=%s, session_id=%s)", 
                         self._seq_no, self._session_id)
        """
        This method closes the WebSocket connection and ends the transcription session.

        Raises:
            ConnectionError: If the WebSocket connection fails.
            TranscriptionError: If the server reports an error during teardown.
            TimeoutError: If the connection or teardown times out.

        Examples:
            Basic streaming:
                >>> async with AsyncClient() as client:
                ...     await client.start_session()
                ...     await client.send_audio(frame)
                ...     await client.stop_session()
        """
        try:
            await self._send_eos(self._seq_no, session_id=self._session_id)
            self._logger.info("AsyncClient.stop_session: EOS sent, waiting for session_done_evt")
            await self._session_done_evt.wait()  # Wait for end of transcript event to indicate we can stop listening
            self._logger.info("AsyncClient.stop_session: session_done_evt set, closing connection")
            await self.close()
            self._logger.info("AsyncClient.stop_session completed")
        except Exception as e:
            self._logger.error("AsyncClient.stop_session failed: %s", e)
            raise

    async def force_end_of_utterance(self) -> None:
        self._logger.info("AsyncClient.force_end_of_utterance called")
        """
    This method sends a ForceEndOfUtterance message to the server to signal
        the end of an utterance. Forcing end of utterance will cause the final
        transcript to be sent to the client early.

        Raises:
            ConnectionError: If the WebSocket connection fails.
            TranscriptionError: If the server reports an error during teardown.
            TimeoutError: If the connection or teardown times out.

        Examples:
            Basic streaming:
                >>> async with AsyncClient() as client:
                ...     await client.start_session()
                ...     await client.send_audio(frame)
                ...     await client.force_end_of_utterance()
        """
        await self.send_message({"message": ClientMessageType.FORCE_END_OF_UTTERANCE})
        self._logger.info("AsyncClient.force_end_of_utterance completed")

    async def transcribe(
        self,
        source: BinaryIO,
        *,
        transcription_config: Optional[TranscriptionConfig] = None,
        audio_format: Optional[AudioFormat] = None,
        translation_config: Optional[TranslationConfig] = None,
        audio_events_config: Optional[AudioEventsConfig] = None,
        ws_headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        session_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "pingala-v1-universal",
        deliver_deltas_only: bool = True,
        use_api_gateway_format: bool = False,
    ) -> None:
        self._logger.info("AsyncClient.transcribe called (timeout=%s, session_id=%s, use_api_gateway_format=%s)",
                         timeout, session_id, use_api_gateway_format)
        """
        Transcribe a single audio stream in real-time.

        This method establishes a WebSocket connection, configures the transcription
        session, streams the audio data, and processes the results through registered
        event handlers. The method returns when the transcription is complete or
        an error occurs.

        Args:
            source: Audio data source with a read() method. Can be a file
                        object, BytesIO, or any object supporting the binary
                        read interface.
            transcription_config: Configuration for transcription behavior such as
                                language, partial transcripts, and advanced features.
                                Uses default if not provided.
            audio_format: Audio format specification including encoding, sample rate,
                          and chunk size. Uses default (PCM 16-bit LE, 44.1kHz) if
                          not provided.
            ws_headers: Additional headers to include in the WebSocket handshake.
            timeout: Maximum time in seconds to wait for transcription completion.
                    Default None.

        Raises:
            AudioError: If source is invalid or cannot be read.
            TimeoutError: If transcription exceeds the specified timeout.
            TranscriptionError: If the server reports an error during transcription.
            ConnectionError: If the WebSocket connection fails.

        Examples:
            Basic transcription:
            >>> with open("speech.wav", "rb") as audio:
            ...     await client.transcribe(audio)

            With custom configuration:
            >>> config = TranscriptionConfig(
            ...     language="en",
            ...     enable_partials=True,
            ...     max_delay=1.0
            ... )
            >>> audio_fmt = AudioFormat(
            ...     encoding=AudioEncoding.PCM_S16LE,
            ...     sample_rate=16000
            ... )
            >>> with open("speech.raw", "rb") as audio:
            ...     await client.transcribe(
            ...         audio,
            ...         transcription_config=config,
            ...         audio_format=audio_fmt,
            ...     )
        """
        if not source:
            raise AudioError("Audio input source cannot be empty")

        transcription_config, audio_format = await self._start_recognition_session(
            transcription_config=transcription_config,
            audio_format=audio_format,
            translation_config=translation_config,
            audio_events_config=audio_events_config,
            ws_headers=ws_headers,
            session_id=session_id,
            api_key=api_key,
            model=model,
            deliver_deltas_only=deliver_deltas_only,
            use_api_gateway_format=use_api_gateway_format,
        )

        try:
            await asyncio.wait_for(
                self._audio_producer(source, audio_format.chunk_size),
                timeout=timeout,
            )
            self._logger.info("AsyncClient.transcribe completed successfully")
        except asyncio.TimeoutError as exc:
            self._logger.error("AsyncClient.transcribe timed out")
            raise TimeoutError("Transcription session timed out") from exc
        except Exception as e:
            self._logger.error("AsyncClient.transcribe failed: %s", e)
            raise

    async def _audio_producer(self, source: BinaryIO, chunk_size: int) -> None:
        """
        Continuously read from source and send data to the server.

        This method reads audio data in chunks and sends it as binary WebSocket
        frames. Automatically sends an EndOfStream message when the stream is exhausted.

        Args:
            source: File-like object to read audio from
            chunk_size: Chunk size for audio data
        """
        self._logger.info("AsyncClient._audio_producer called (chunk_size=%s)", chunk_size)
        src = FileSource(source, chunk_size=chunk_size)
        chunk_count = 0

        try:
            async for frame in src:
                if self._session_done_evt.is_set():
                    self._logger.info("AsyncClient._audio_producer: session_done_evt set, breaking")
                    break

                try:
                    # Pass session_id and sample_rate for API Gateway format
                    chunk_size = len(frame)
                    audio_duration_ms = (chunk_size / 4 / self._sample_rate * 1000) if chunk_size > 0 else 0
                    
                    self._logger.debug(
                        "AsyncClient._audio_producer: sending chunk %d, size=%d bytes, duration=%.2f ms, sample_rate=%d Hz",
                        chunk_count + 1, chunk_size, audio_duration_ms, self._sample_rate
                    )
                    
                    await self.send_audio(
                        frame,
                        session_id=self._session_id,
                        sample_rate=self._sample_rate
                    )
                    chunk_count += 1
                    
                    if chunk_count % 10 == 0:
                        total_audio_ms = (chunk_count * chunk_size / 4 / self._sample_rate * 1000) if chunk_size > 0 else 0
                        self._logger.info(
                            "AsyncClient._audio_producer: sent %d chunks (total audio: %.2f seconds, avg chunk: %.2f ms)",
                            chunk_count, total_audio_ms / 1000, audio_duration_ms
                        )
                except Exception as e:
                    self._logger.error(
                        "Failed to send audio frame (chunk %d, size=%d bytes): %s",
                        chunk_count + 1, len(frame) if frame else 0, e
                    )
                    self._session_done_evt.set()
                    break

            self._logger.info("AsyncClient._audio_producer: finished reading source, sent %s total chunks, calling stop_session", chunk_count)
            await self.stop_session()
            self._logger.info("AsyncClient._audio_producer completed")
        except asyncio.CancelledError:
            self._logger.info("AsyncClient._audio_producer cancelled")
            raise
        except Exception as e:
            self._logger.error("Audio producer error: %s", e)
            self._session_done_evt.set()

    async def _send_eos(self, seq_no: int, session_id: Optional[str] = None) -> None:
        """Send EndOfStream message to server."""
        self._logger.info("AsyncClient._send_eos called (seq_no=%s, session_id=%s, eos_sent=%s, session_done=%s)",
                         seq_no, session_id, self._eos_sent, self._session_done_evt.is_set())
        if not self._eos_sent and not self._session_done_evt.is_set():
            try:
                if self._use_api_gateway_format:
                    self._logger.info("AsyncClient._send_eos: sending API Gateway format EOS")
                    # API Gateway format - match test_apigw_ws_send_passthrough.py
                    # Just send END message (no END_OF_AUDIO frame needed)
                    effective_session_id = session_id or self._session_id or (self._session.request_id if hasattr(self._session, 'request_id') else 'default')
                    
                    end_msg = {
                        "type": "end",
                        "session_id": effective_session_id,
                        "connection_id": effective_session_id,  # Use session_id as connection_id
                    }
                    await self.send_message(end_msg)
                    self._logger.info("AsyncClient._send_eos: sent END message")
                else:
                    self._logger.info("AsyncClient._send_eos: sending standard format EOS")
                    # Standard format
                    await self.send_message({"message": ClientMessageType.END_OF_STREAM, "last_seq_no": seq_no})
                self._eos_sent = True
                self._logger.info("AsyncClient._send_eos completed (eos_sent=True)")
            except Exception as e:
                self._logger.error("Failed to send EndOfStream message: %s", e)
                raise
        else:
            self._logger.info("AsyncClient._send_eos: skipping (eos_sent=%s, session_done=%s)", 
                             self._eos_sent, self._session_done_evt.is_set())

    async def _wait_recognition_started(self, timeout: float = 5.0) -> None:
        """Wait for RecognitionStarted message from server."""
        self._logger.info("AsyncClient._wait_recognition_started called (timeout=%s)", timeout)
        try:
            await asyncio.wait_for(self._recognition_started_evt.wait(), timeout)
            self._logger.info("AsyncClient._wait_recognition_started: recognition started event received")
        except asyncio.TimeoutError:
            self._logger.error("AsyncClient._wait_recognition_started: timeout waiting for recognition")
            raise

    def _on_recognition_started(self, msg: dict[str, Any]) -> None:
        """Handle RecognitionStarted message from server (or SERVER_READY for API Gateway)."""
        self._logger.info("AsyncClient._on_recognition_started called (msg=%s)", msg)
        # Handle both standard format and API Gateway format (converted in _recv_loop)
        self._session.session_id = msg.get("id") or msg.get("session_id") or getattr(self._session, 'request_id', None)
        self._recognition_started_evt.set()
        self._logger.info("Recognition started (session_id=%s)", self._session.session_id)

    def _on_eot(self, msg: dict[str, Any]) -> None:
        """Handle EndOfTranscript message from server."""
        self._logger.info("AsyncClient._on_eot called (msg=%s)", msg)
        self._session_done_evt.set()
        self._logger.info("AsyncClient._on_eot: session_done_evt set")

    def _on_error(self, msg: dict[str, Any]) -> None:
        """Handle Error message from server."""
        self._logger.info("AsyncClient._on_error called (msg=%s)", msg)
        error = msg.get("reason", "unknown")
        self._logger.error("Server error: %s", error)
        self._session_done_evt.set()
        self._logger.info("AsyncClient._on_error: session_done_evt set, raising TranscriptionError")
        raise TranscriptionError(error)

    def _on_audio_added(self, msg: dict[str, Any]) -> None:
        """Handle AudioAdded message from server."""
        self._logger.debug("AsyncClient._on_audio_added called (msg=%s)", msg)
        old_seq_no = self._seq_no
        self._seq_no = msg.get("seq_no", 0)
        if old_seq_no != self._seq_no:
            self._logger.debug("AsyncClient._on_audio_added: seq_no updated from %s to %s", old_seq_no, self._seq_no)

    def _on_warning(self, msg: dict[str, Any]) -> None:
        """Handle Warning message from server."""
        self._logger.info("AsyncClient._on_warning called (msg=%s)", msg)
        self._logger.warning("Server warning: %s", msg.get("reason", "unknown"))

    async def close(self) -> None:
        """
        Close the client and clean up resources.
        WARNING: this closes the client without waiting for remaining messages to be processed.
        It is recommended to use stop_session() instead.

        Ensures the session is marked as complete and delegates to the base
        class for full cleanup including WebSocket connection termination.
        """
        import traceback
        # Get the caller information for debugging
        stack = traceback.extract_stack()
        caller = stack[-2] if len(stack) >= 2 else None
        caller_info = f"{caller.filename}:{caller.lineno}" if caller else "unknown"
        self._logger.info("🟡 ASYNC_CLIENT: AsyncClient.close() called (from: %s)", caller_info)
        # Print final transcription before closing (fallback if DISCONNECT/EndOfTranscript weren't received)
        self._logger.info("Connection closing, printing final transcription if available...")
        self._print_final_transcription()
        self._session_done_evt.set()
        self._logger.debug("Session done event set")
        # Cancel receive task and close transport
        if self._recv_task and not self._recv_task.done():
            self._logger.debug("Cancelling receive task...")
            self._recv_task.cancel()
            try:
                await self._recv_task
                self._logger.debug("Receive task cancelled successfully")
            except asyncio.CancelledError:
                self._logger.debug("Receive task cancellation confirmed")
        else:
            self._logger.debug("Receive task already done or doesn't exist")
        self._logger.debug("Closing transport...")
        await self._transport.close()
        self._closed_evt.set()
        self._logger.info("🟡 ASYNC_CLIENT: AsyncClient.close() completed (closed_evt set)")
