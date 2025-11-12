from __future__ import annotations

import asyncio
import contextlib
import json
import os
import uuid
from typing import Any
from typing import Optional

from typing_extensions import Self

from ._auth import AuthBase
from ._auth import StaticKeyAuth
from ._events import EventEmitter
from ._exceptions import TransportError
from ._logging import get_logger
from ._models import AudioEventsConfig
from ._models import AudioFormat
from ._models import ConnectionConfig
from ._models import SessionInfo
from ._models import TranscriptionConfig
from ._models import TranslationConfig
from ._transport import Transport
from ._utils.message import build_start_recognition_message


class _BaseClient(EventEmitter):
    """
    Base client providing core WebSocket functionality for RT clients.

    This class handles the low-level plumbing that's common to all real-time
    clients, including connection management, message routing, and event handling.

    Parameters:
        transport: Pre-configured Transport instance for WebSocket communication.
    """

    def __init__(self, transport: Transport) -> None:
        super().__init__()
        self._transport = transport
        self._recv_task: Optional[asyncio.Task[None]] = None
        self._closed_evt = asyncio.Event()
        self._eos_sent = False
        self._seq_no = 0
        self._session_id: Optional[str] = None
        self._sample_rate: int = 16000
        self._use_api_gateway_format: bool = False

        self._logger = get_logger("shunyalabs.rt.base_client")

    @classmethod
    def _init_session_info(cls, request_id: Optional[str] = None) -> tuple[SessionInfo, asyncio.Event, asyncio.Event]:
        """
        Create common session state used by RT clients.

        This centralizes the creation of session state objects that are
        common across single and multi-channel clients, reducing duplication.

        Args:
            request_id: Optional request ID, generated if not provided

        Returns:
            Tuple of (session_info, recognition_started_event, session_done_event)
        """
        session = SessionInfo(request_id=request_id or str(uuid.uuid4()))
        recognition_started_evt = asyncio.Event()
        session_done_evt = asyncio.Event()

        return session, recognition_started_evt, session_done_evt

    @classmethod
    def _create_transport_from_config(
        cls,
        auth: Optional[AuthBase] = None,
        *,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        conn_config: Optional[ConnectionConfig] = None,
        request_id: Optional[str] = None,
    ) -> Transport:
        """
        Create a Transport instance from common configuration parameters.

        Args:
            auth: Authentication instance or None to create from api_key
            api_key: API key for StaticKeyAuth (ignored if auth provided)
            url: WebSocket URL or None for default
            conn_config: Connection configuration or None for default
            request_id: Request ID for debugging/tracking

        Returns:
            Configured Transport instance
        """
        auth = auth or StaticKeyAuth(api_key)
        url = url or os.getenv("SHUNYALABS_RT_URL") or "wss://eu2.rt.shunyalabs.com/v2"
        conn_config = conn_config or ConnectionConfig()
        request_id = request_id or str(uuid.uuid4())

        return Transport(url, conn_config, auth, request_id)

    async def _ws_connect(self, ws_headers: Optional[dict] = None) -> None:
        await self._transport.connect(ws_headers)
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def send_audio(self, payload: bytes, session_id: Optional[str] = None, sample_rate: Optional[int] = None) -> None:
        """
        Send an audio frame through the WebSocket.

        Examples:
            >>> audio_chunk = b""
            >>> await client.send_audio(audio_chunk)
        """
        if self._closed_evt.is_set() or self._eos_sent:
            raise TransportError("Client is closed")

        if not isinstance(payload, bytes):
            raise ValueError("Payload must be bytes")

        try:
            if self._use_api_gateway_format:
                # API Gateway format: send as base64-encoded JSON frame
                import base64
                b64_audio = base64.b64encode(payload).decode("ascii")
                
                frame_msg = {
                    "action": "send",
                    "type": "frame",
                    "session_id": session_id or self._session_id or "default-session",
                    "connection_id": None,  # Filled by Lambda
                    "frame_seq": self._seq_no + 1,
                    "audio_inline_b64": b64_audio,
                    "dtype": "float32",  # Default, could be detected from audio_format
                    "channels": 1,
                    "sr": sample_rate or self._sample_rate,
                }
                
                await self._transport.send_message(json.dumps(frame_msg))
            else:
                # Standard format: send as binary
                await self._transport.send_message(payload)
            
            self._seq_no += 1
        except Exception:
            self._closed_evt.set()
            raise

    async def send_message(self, message: dict[str, Any]) -> None:
        """
        Send a message through the WebSocket.

        Examples:
            >>> # Send JSON message
            >>> msg = json.dumps({"message": "StartRecognition", ...})
            >>> await client.send_message(msg)
        """
        if self._closed_evt.is_set() or self._eos_sent:
            raise TransportError("Client is closed")

        if not isinstance(message, dict):
            raise ValueError("Message must be a dict")

        try:
            data = json.dumps(message)
            await self._transport.send_message(data)
        except Exception:
            self._closed_evt.set()
            raise

    async def _recv_loop(self) -> None:
        """
        Background task that continuously receives and dispatches server messages.

        This coroutine runs for the lifetime of the connection, receiving messages
        from the WebSocket and emitting them as events. It handles graceful shutdown
        when cancelled and logs any unexpected errors.
        """
        try:
            while True:
                msg = await self._transport.receive_message()

                if isinstance(msg, dict):
                    # Convert API Gateway format to SDK format if needed
                    if self._use_api_gateway_format:
                        # Handle SERVER_READY
                        if msg.get("message") == "SERVER_READY":
                            # Convert to RecognitionStarted format
                            request_id = None
                            if hasattr(self, "_session") and hasattr(self._session, "request_id"):
                                request_id = self._session.request_id
                            converted_msg = {
                                "message": "RecognitionStarted",
                                "id": msg.get("session_id") or self._session_id or request_id or "default"
                            }
                            self.emit("RecognitionStarted", converted_msg)
                            continue
                        
                        # Handle segments format (API Gateway transcript format)
                        if "segments" in msg and isinstance(msg["segments"], list):
                            for seg in msg["segments"]:
                                text = (seg.get("text") or "").strip()
                                if not text:
                                    continue
                                
                                completed = bool(seg.get("completed", False))
                                start_time = float(seg.get("start") or 0.0)
                                end_time = seg.get("end")
                                if end_time is not None:
                                    end_time = float(end_time)
                                else:
                                    end_time = start_time + 1.0  # Default duration
                                
                                # Convert to SDK format
                                if completed:
                                    # Final transcript
                                    converted_msg = {
                                        "message": "AddTranscript",
                                        "format": "2.1",
                                        "metadata": {
                                            "transcript": text,
                                            "start_time": start_time,
                                            "end_time": end_time,
                                        },
                                        "results": [],
                                    }
                                    self.emit("AddTranscript", converted_msg)
                                else:
                                    # Partial transcript
                                    converted_msg = {
                                        "message": "AddPartialTranscript",
                                        "format": "2.1",
                                        "metadata": {
                                            "transcript": text,
                                            "start_time": start_time,
                                            "end_time": end_time,
                                        },
                                        "results": [],
                                    }
                                    self.emit("AddPartialTranscript", converted_msg)
                            continue
                        
                        # Handle other API Gateway messages (language detection, etc.)
                        if "language" in msg and "language_prob" in msg:
                            # Language detection - can be logged or ignored
                            self._logger.debug("Detected language: %s (p=%.2f)", msg.get("language"), float(msg.get("language_prob") or 0))
                            continue
                        
                        # Handle error messages
                        if msg.get("type") == "error":
                            error_msg = {
                                "message": "Error",
                                "reason": msg.get("message", "Unknown error")
                            }
                            self.emit("Error", error_msg)
                            continue
                    
                    # Handle standard SDK format or other messages
                    if "message" in msg:
                        self.emit(msg["message"], msg)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self._logger.error("Receive loop error: %s", exc)
            self._closed_evt.set()
            try:
                await self._transport.close()
            except Exception:
                pass  # Ignore close errors - we're already in error state
        finally:
            self._closed_evt.set()

    async def _start_recognition_session(
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
    ) -> tuple[TranscriptionConfig, AudioFormat]:
        transcription_config = transcription_config or TranscriptionConfig()
        audio_format = audio_format or AudioFormat()
        
        # Store session_id and sample_rate
        # Try to get request_id from session if it exists, otherwise use provided session_id or default
        request_id = None
        if hasattr(self, "_session") and hasattr(self._session, "request_id"):
            request_id = self._session.request_id
        self._session_id = session_id or request_id or "default-session"
        self._sample_rate = audio_format.sample_rate
        self._use_api_gateway_format = use_api_gateway_format

        start_recognition_message = build_start_recognition_message(
            transcription_config=transcription_config,
            audio_format=audio_format,
            translation_config=translation_config,
            audio_events_config=audio_events_config,
            session_id=self._session_id,
            api_key=api_key,
            model=model,
            deliver_deltas_only=deliver_deltas_only,
            use_api_gateway_format=use_api_gateway_format,
        )

        await self._ws_connect(ws_headers)
        await self.send_message(start_recognition_message)
        await self._wait_recognition_started()

        return transcription_config, audio_format

    async def _wait_recognition_started(self, timeout: float = 5.0) -> None:
        """Wait for RecognitionStarted message from server."""
        raise NotImplementedError()

    async def close(self) -> None:
        """
        Gracefully close the client connection and clean up resources.
        """
        self._closed_evt.set()

        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._recv_task, timeout=2.0)

        await self._transport.close()
