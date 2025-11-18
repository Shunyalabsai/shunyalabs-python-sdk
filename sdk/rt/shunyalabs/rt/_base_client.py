from __future__ import annotations

import asyncio
import contextlib
import json
import logging
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
from ._models import AudioEncoding
from ._models import AudioEventsConfig
from ._models import AudioFormat
from ._models import ConnectionConfig
from ._models import ServerMessageType
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
        self._completed_segments: list[str] = []  # Collect completed segment texts

        self._logger = get_logger("shunyalabs.rt.base_client")
        # Suppress DEBUG and INFO logs from base_client (only show WARNING and ERROR)
        self._logger.setLevel(logging.WARNING)

    def _print_final_transcription(self) -> None:
        """Print the combined final transcription from all completed segments."""
        if self._completed_segments:
            combined_text = " ".join(self._completed_segments)
            self._logger.info("=" * 80)
            self._logger.info("FINAL TRANSCRIPTION (Combined from %d completed segments):", len(self._completed_segments))
            self._logger.info("=" * 80)
            self._logger.info("%s", combined_text)
            self._logger.info("=" * 80)
            # Also print to stdout for visibility
            print("\n" + "=" * 80)
            print(f"FINAL TRANSCRIPTION (Combined from {len(self._completed_segments)} completed segments):")
            print("=" * 80)
            print(combined_text)
            print("=" * 80 + "\n")
        else:
            self._logger.info("No completed segments were collected to print")

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

    async def send_audio(
        self, 
        payload: bytes, 
        session_id: Optional[str] = None, 
        sample_rate: Optional[int] = None,
        input_encoding: Optional[AudioEncoding] = None,
    ) -> None:
        """
        Send an audio frame through the WebSocket.

        Args:
            payload: Audio data bytes. If input_encoding is specified and not PCM_F32LE,
                    the audio will be automatically converted to PCM_F32LE.
            session_id: Optional session ID for API Gateway format.
            sample_rate: Optional sample rate. Uses client's default if not provided.
            input_encoding: Optional input encoding format. If provided and not PCM_F32LE,
                          the audio will be converted to PCM_F32LE before sending.
                          If None and using API Gateway format, assumes PCM_F32LE.

        Examples:
            >>> audio_chunk = b""
            >>> await client.send_audio(audio_chunk)
            
            >>> # Convert from PCM_S16LE automatically
            >>> await client.send_audio(
            ...     audio_chunk,
            ...     input_encoding=AudioEncoding.PCM_S16LE
            ... )
        """
        if self._closed_evt.is_set() or self._eos_sent:
            raise TransportError("Client is closed")

        if not isinstance(payload, bytes):
            raise ValueError("Payload must be bytes")

        effective_sample_rate = int(sample_rate or self._sample_rate)
        
        # Convert audio to PCM_F32LE if needed (for API Gateway format)
        if self._use_api_gateway_format and input_encoding and input_encoding != AudioEncoding.PCM_F32LE:
            from ._utils.audio import convert_to_pcm_f32le
            self._logger.debug(
                "Converting audio from %s to PCM_F32LE (input size: %d bytes)",
                input_encoding, len(payload)
            )
            try:
                payload = convert_to_pcm_f32le(
                    payload,
                    input_encoding=input_encoding,
                    input_sample_rate=effective_sample_rate,
                    num_channels=1,  # Assume mono for now
                )
                self._logger.debug("Audio converted to PCM_F32LE (output size: %d bytes)", len(payload))
            except ImportError as e:
                self._logger.error(
                    "Audio conversion requires numpy. Install with: pip install numpy. Error: %s", e
                )
                raise ValueError(
                    "Audio conversion requires numpy. Install with: pip install numpy"
                ) from e
            except Exception as e:
                self._logger.error("Failed to convert audio: %s", e)
                raise

        # Calculate audio duration for logging (after conversion, always float32 = 4 bytes per sample)
        audio_duration_ms = (len(payload) / 4 / effective_sample_rate * 1000) if payload else 0
        
        self._logger.debug(
            "Sending audio chunk: seq_no=%d, size=%d bytes, duration=%.2f ms, sample_rate=%d Hz",
            self._seq_no + 1, len(payload), audio_duration_ms, effective_sample_rate
        )

        try:
            if self._use_api_gateway_format:
                # API Gateway format: send as base64-encoded JSON frame
                # Match the format from test_apigw_ws_send_passthrough.py
                import base64
                b64_audio = base64.b64encode(payload).decode("ascii")
                
                # Use session_id as connection_id (matching working test)
                effective_session_id = session_id or self._session_id or "default-session"
                
                frame_msg = {
                    "type": "frame",
                    "session_id": effective_session_id,
                    "connection_id": effective_session_id,  # Use session_id as connection_id
                    "frame_seq": self._seq_no + 1,
                    "audio": {
                        "inline_b64": b64_audio,  # Nested structure matching working test
                        "dtype": "float32",
                        "channels": 1,
                        "sr": effective_sample_rate,
                    },
                }
                
                self._logger.debug(
                    "Sending API Gateway frame: session_id=%s, frame_seq=%d, audio_size=%d bytes, b64_size=%d chars",
                    effective_session_id, self._seq_no + 1, len(payload), len(b64_audio)
                )
                await self._transport.send_message(json.dumps(frame_msg))
            else:
                # Standard format: send as binary
                self._logger.debug("Sending binary audio frame: size=%d bytes", len(payload))
                await self._transport.send_message(payload)
            
            self._seq_no += 1
            self._logger.debug("Audio chunk sent successfully: seq_no=%d", self._seq_no)
        except Exception as e:
            self._logger.error("Failed to send audio chunk (seq_no=%d, size=%d bytes): %s", self._seq_no + 1, len(payload), e)
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
                self._logger.info("=== Received message from server ===")
                self._logger.info("Full message: %s", msg)
                self._logger.info("Message type: %s", type(msg))
                if isinstance(msg, dict):
                    self._logger.info("Message keys: %s", list(msg.keys()))
                    # Also print to help debug transcript messages
                    if self._use_api_gateway_format:
                        # Check for any transcript-related keys
                        transcript_keys = [k for k in msg.keys() if "segment" in str(k).lower() or "text" in str(k).lower() or "transcript" in str(k).lower()]
                        if transcript_keys:
                            self._logger.info("API Gateway transcript-related keys found: %s", transcript_keys)
                            for key in transcript_keys:
                                self._logger.info("  %s = %s", key, msg.get(key))

                if isinstance(msg, dict):
                    # Convert API Gateway format to SDK format if needed
                    if self._use_api_gateway_format:
                        # Handle SERVER_READY
                        if msg.get("message") == "SERVER_READY":
                            self._logger.info("🔵 CONTROL MESSAGE RECEIVED: SERVER_READY (raw: %s)", msg)
                            # Convert to RecognitionStarted format
                            request_id = None
                            if hasattr(self, "_session") and hasattr(self._session, "request_id"):
                                request_id = self._session.request_id
                            converted_msg = {
                                "message": "RecognitionStarted",
                                "id": msg.get("session_id") or self._session_id or request_id or "default"
                            }
                            self._logger.info("🔵 CONTROL MESSAGE FORWARDING: RecognitionStarted -> %d handler(s)", 
                                            len(self.listeners(ServerMessageType.RECOGNITION_STARTED)))
                            self.emit(ServerMessageType.RECOGNITION_STARTED, converted_msg)
                            continue
                        
                        # Handle segments format (API Gateway transcript format)
                        if "segments" in msg and isinstance(msg["segments"], list):
                            self._logger.info("Received transcript message with %d segment(s)", len(msg["segments"]))
                            for idx, seg in enumerate(msg["segments"]):
                                text = (seg.get("text") or "").strip()
                                if not text:
                                    self._logger.debug("Skipping segment %d with no text: %s", idx, seg)
                                    continue
                                
                                completed = bool(seg.get("completed", False))
                                start_time = float(seg.get("start") or 0.0)
                                end_time = seg.get("end")
                                if end_time is not None:
                                    end_time = float(end_time)
                                else:
                                    end_time = start_time + 1.0  # Default duration
                                
                                segment_duration = end_time - start_time
                                self._logger.info(
                                    "Processing transcript segment %d: type=%s, text='%s', start=%.2fs, end=%.2fs, duration=%.2fs",
                                    idx, "FINAL" if completed else "PARTIAL", text, start_time, end_time, segment_duration
                                )
                                
                                # Convert to SDK format
                                if completed:
                                    # Final transcript - collect text for later combination
                                    self._completed_segments.append(text)
                                    self._logger.info(
                                        "✓ Final transcript segment %d collected: '%s' (total segments: %d, total text length: %d chars)",
                                        len(self._completed_segments), text, len(self._completed_segments), len(text)
                                    )
                                    
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
                                    self._logger.info(
                                        "Emitting AddTranscript event: text='%s', time=[%.2fs-%.2fs], duration=%.2fs",
                                        text, start_time, end_time, segment_duration
                                    )
                                    self.emit(ServerMessageType.ADD_TRANSCRIPT, converted_msg)
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
                                    self._logger.info(
                                        "Emitting AddPartialTranscript event: text='%s', time=[%.2fs-%.2fs], duration=%.2fs",
                                        text, start_time, end_time, segment_duration
                                    )
                                    self.emit(ServerMessageType.ADD_PARTIAL_TRANSCRIPT, converted_msg)
                            continue
                        
                        # Check for other possible transcript formats
                        # Some API Gateway implementations might send transcripts directly
                        if "text" in msg and "segments" not in msg:
                            self._logger.warning("Received message with 'text' but no 'segments' key: %s", msg)
                        if "transcript" in msg:
                            self._logger.warning("Received message with 'transcript' key: %s", msg)
                        
                        # Handle other API Gateway messages (language detection, etc.)
                        if "language" in msg and "language_prob" in msg:
                            # Language detection - can be logged or ignored
                            self._logger.debug("Detected language: %s (p=%.2f)", msg.get("language"), float(msg.get("language_prob") or 0))
                            continue
                        
                        # Handle error messages
                        if msg.get("type") == "error":
                            self._logger.warning("🔴 CONTROL MESSAGE RECEIVED: Error (raw: %s)", msg)
                            error_msg = {
                                "message": "Error",
                                "reason": msg.get("message", "Unknown error")
                            }
                            self._logger.warning("🔴 CONTROL MESSAGE FORWARDING: Error -> %d handler(s)", 
                                                len(self.listeners(ServerMessageType.ERROR)))
                            self.emit(ServerMessageType.ERROR, error_msg)
                            continue
                    
                    # Handle DISCONNECT message - combine and print all completed segments
                    if msg.get("message") == "DISCONNECT" or msg.get("type") == "disconnect":
                        self._logger.info("🟡 CONTROL MESSAGE RECEIVED: DISCONNECT (raw: %s)", msg)
                        self._logger.info("DISCONNECT message received! Collected segments: %d", len(self._completed_segments))
                        self._print_final_transcription()
                        # Still emit the DISCONNECT event (as string since it's not in ServerMessageType enum)
                        self._logger.info("🟡 CONTROL MESSAGE FORWARDING: DISCONNECT (emitting as string)")
                        self.emit("DISCONNECT", msg)  # DISCONNECT is not in ServerMessageType enum
                        continue
                    
                    # Handle EndOfTranscript in both standard and API Gateway formats
                    # Standard format: {"message": "EndOfTranscript"}
                    # API Gateway format: {"event": "END_OF_TRANSCRIPTION", "uid": "..."}
                    if msg.get("message") == "EndOfTranscript" or msg.get("event") == "END_OF_TRANSCRIPTION":
                        self._logger.info("🟢 CONTROL MESSAGE RECEIVED: EndOfTranscript (raw: %s)", msg)
                        self._logger.info("EndOfTranscript received! Collected segments: %d", len(self._completed_segments))
                        self._print_final_transcription()
                        # Convert API Gateway format to standard format for event emission
                        if msg.get("event") == "END_OF_TRANSCRIPTION":
                            # Convert to standard format
                            converted_eot = {
                                "message": "EndOfTranscript",
                                "uid": msg.get("uid"),
                            }
                            self._logger.info("🟢 CONTROL MESSAGE FORWARDING: EndOfTranscript (converted from END_OF_TRANSCRIPTION) -> %d handler(s)", 
                                            len(self.listeners(ServerMessageType.END_OF_TRANSCRIPT)))
                            self.emit(ServerMessageType.END_OF_TRANSCRIPT, converted_eot)
                        else:
                            self._logger.info("🟢 CONTROL MESSAGE FORWARDING: EndOfTranscript -> %d handler(s)", 
                                            len(self.listeners(ServerMessageType.END_OF_TRANSCRIPT)))
                            self.emit(ServerMessageType.END_OF_TRANSCRIPT, msg)
                        continue
                    
                    # Handle standard SDK format or other messages
                    if "message" in msg:
                        msg_type = msg["message"]
                        # Check if this is a control message
                        control_messages = [
                            "RecognitionStarted", "EndOfTranscript", "EndOfUtterance",
                            "AudioAdded", "Error", "Warning", "Info",
                            "AudioEventStarted", "AudioEventEnded",
                            "AddTranslation", "AddPartialTranslation", "SpeakersResult"
                        ]
                        is_control = msg_type in control_messages
                        
                        if is_control:
                            self._logger.info("🟣 CONTROL MESSAGE RECEIVED: %s (standard format, raw: %s)", msg_type, msg)
                        
                        # Also collect completed segments from standard SDK format
                        if msg["message"] == "AddTranscript":
                            metadata = msg.get("metadata", {})
                            transcript_text = metadata.get("transcript", "").strip()
                            start_time = metadata.get("start_time", 0.0)
                            end_time = metadata.get("end_time", start_time + 1.0)
                            if transcript_text:
                                self._completed_segments.append(transcript_text)
                                self._logger.info(
                                    "Received AddTranscript (standard format): text='%s', time=[%.2fs-%.2fs], duration=%.2fs (total segments: %d)",
                                    transcript_text, start_time, end_time, end_time - start_time, len(self._completed_segments)
                                )
                        elif msg["message"] == "AddPartialTranscript":
                            metadata = msg.get("metadata", {})
                            transcript_text = metadata.get("transcript", "").strip()
                            start_time = metadata.get("start_time", 0.0)
                            end_time = metadata.get("end_time", start_time + 1.0)
                            if transcript_text:
                                self._logger.info(
                                    "Received AddPartialTranscript (standard format): text='%s', time=[%.2fs-%.2fs], duration=%.2fs",
                                    transcript_text, start_time, end_time, end_time - start_time
                                )
                        
                        # Try to map message string to ServerMessageType enum for logging
                        try:
                            # Map common message types to enum
                            msg_type_map = {
                                "RecognitionStarted": ServerMessageType.RECOGNITION_STARTED,
                                "EndOfTranscript": ServerMessageType.END_OF_TRANSCRIPT,
                                "EndOfUtterance": ServerMessageType.END_OF_UTTERANCE,
                                "AudioAdded": ServerMessageType.AUDIO_ADDED,
                                "Error": ServerMessageType.ERROR,
                                "Warning": ServerMessageType.WARNING,
                                "Info": ServerMessageType.INFO,
                                "AddTranscript": ServerMessageType.ADD_TRANSCRIPT,
                                "AddPartialTranscript": ServerMessageType.ADD_PARTIAL_TRANSCRIPT,
                                "AddTranslation": ServerMessageType.ADD_TRANSLATION,
                                "AddPartialTranslation": ServerMessageType.ADD_PARTIAL_TRANSLATION,
                                "AudioEventStarted": ServerMessageType.AUDIO_EVENT_STARTED,
                                "AudioEventEnded": ServerMessageType.AUDIO_EVENT_ENDED,
                                "SpeakersResult": ServerMessageType.SPEAKERS_RESULT,
                            }
                            enum_type = msg_type_map.get(msg_type)
                            if enum_type and is_control:
                                handler_count = len(self.listeners(enum_type))
                                self._logger.info("🟣 CONTROL MESSAGE FORWARDING: %s -> %d handler(s)", msg_type, handler_count)
                            elif is_control:
                                self._logger.info("🟣 CONTROL MESSAGE FORWARDING: %s (no enum mapping, emitting as string)", msg_type)
                        except Exception:
                            pass
                        
                        self.emit(msg["message"], msg)
                    
                    # Log any unhandled messages for debugging
                    if self._use_api_gateway_format:
                        # Check if this message wasn't handled by any of the above conditions
                        # Exclude END_OF_TRANSCRIPTION event since we handle it above
                        if (msg.get("message") not in ["SERVER_READY", "DISCONNECT", "EndOfTranscript"] and 
                            msg.get("event") not in ["END_OF_TRANSCRIPTION"] and
                            "segments" not in msg and 
                            "language" not in msg and 
                            msg.get("type") != "error"):
                            self._logger.warning("Unhandled API Gateway message (may contain transcripts): %s", msg)
                            # Check if it might be a transcript in a different format
                            msg_keys = list(msg.keys())
                            self._logger.info("Message keys: %s", msg_keys)
                            # Try to find any text/transcript data in alternative formats
                            for key in msg_keys:
                                if "text" in key.lower() or "transcript" in key.lower() or "segment" in key.lower():
                                    self._logger.warning("Found potential transcript key '%s' with value: %s", key, msg.get(key))
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
            # Print final transcription as fallback when receive loop ends
            self._logger.info("Receive loop ending, printing final transcription if available...")
            self._print_final_transcription()
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
        
        # Log language being sent to ASR (for API Gateway format)
        if use_api_gateway_format and "config" in start_recognition_message:
            language = start_recognition_message["config"].get("language")
            self._logger.info("🌐 Language code being sent to Shunyalabs ASR: %s", language or "None (auto-detect)")

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
