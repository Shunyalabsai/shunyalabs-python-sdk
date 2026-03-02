"""Flow conversational AI client — adapted from the existing Flow SDK.

Uses the shared core transport and event emitter instead of per-package versions.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from collections.abc import AsyncGenerator
from typing import Any, BinaryIO, Optional, Union

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._events import EventEmitter
from shunyalabs._core._exceptions import (
    AudioError,
    ConfigurationError,
    ConversationEndedException,
    ConversationError,
    SessionError,
    TimeoutError,
)
from shunyalabs._core._logging import get_logger
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport

from ._models import (
    AddInput,
    AudioFormat,
    ClientMessageType,
    ConversationConfig,
    DebugMode,
    ServerMessageType,
    SessionInfo,
    ToolFunctionParam,
)

Tool = Union[ToolFunctionParam, dict[str, Any]]


async def _read_audio_chunks(stream: BinaryIO, chunk_size: int) -> AsyncGenerator[bytes, None]:
    """Read audio stream in chunks with async support."""
    if not hasattr(stream, "read"):
        raise TypeError("Stream must have read() method")

    try:
        while True:
            if inspect.iscoroutinefunction(stream.read):
                chunk = await stream.read(chunk_size)
            else:
                loop = asyncio.get_running_loop()
                chunk = await loop.run_in_executor(None, stream.read, chunk_size)

            if not chunk:
                break
            yield chunk
    except Exception as e:
        raise OSError(f"Error reading from stream: {e}")


class AsyncFlowClient(EventEmitter):
    """Async client for Shunyalabs Flow conversational AI.

    Event-driven: subscribe with @client.on(ServerMessageType.ADD_TRANSCRIPT)
    to react to transcripts, LLM tool calls, etc.

    Examples:
        >>> async with AsyncFlowClient(api_key="key") as client:
        ...     @client.on(ServerMessageType.ADD_TRANSCRIPT)
        ...     def on_transcript(msg):
        ...         print(msg["metadata"]["transcript"])
        ...     with open("audio.raw", "rb") as mic:
        ...         await client.start_conversation(mic)
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        conn_config: Optional[WsConnectionConfig] = None,
        auth: Optional[StaticKeyAuth] = None,
    ) -> None:
        super().__init__()

        if auth is not None:
            self._auth = auth
        else:
            self._auth = StaticKeyAuth(api_key)

        self._url = url or os.getenv("SHUNYALABS_FLOW_URL") or "wss://flow.api.shunyalabs.com/v1/flow"
        self._conn_config = conn_config or WsConnectionConfig()
        self._session = SessionInfo(request_id=str(uuid.uuid4()))
        self._transport = WsTransport(
            self._url,
            self._auth,
            self._conn_config,
            sdk_component="flow",
        )
        self._logger = get_logger(__name__)
        self._conversation_started = asyncio.Event()

    @property
    def request_id(self) -> str:
        return self._session.request_id

    @property
    def conversation_id(self) -> Optional[str]:
        return self._session.conversation_id

    @property
    def is_running(self) -> bool:
        return self._session.is_running

    async def __aenter__(self) -> AsyncFlowClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def start_conversation(
        self,
        source: BinaryIO,
        *,
        conversation_config: Optional[ConversationConfig] = None,
        audio_format: Optional[AudioFormat] = None,
        tools: Optional[list[Tool]] = None,
        debug_mode: Optional[DebugMode] = None,
        ws_headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> None:
        """Begin a new Flow conversation and run it to completion."""
        if source is None:
            raise AudioError("Audio source must be provided")

        conversation_config = conversation_config or ConversationConfig()
        audio_format = audio_format or AudioFormat()
        self._conversation_started.clear()

        try:
            await asyncio.wait_for(
                self._conversation_pipeline(
                    source, audio_format, conversation_config,
                    tools, debug_mode, ws_headers,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Conversation timed-out after {timeout}s") from exc
        except ConversationEndedException:
            pass
        finally:
            self._session.is_running = False

    async def close(self) -> None:
        """Gracefully close the connection."""
        if self._session.is_running:
            try:
                await self._send_audio_ended()
            except Exception:
                pass

        self._session.is_running = False
        try:
            await self._transport.close()
        except Exception:
            pass
        self.remove_all_listeners()

    async def send_input(
        self,
        *,
        input_text: str,
        immediate: bool = False,
        interrupt_response: bool = False,
    ) -> None:
        """Send text input to the LLM."""
        add_input = AddInput(
            input=input_text,
            immediate=immediate,
            interrupt_response=interrupt_response,
        )
        await self._transport.send_message(add_input.to_dict())

    async def send_tool_result(self, *, tool_call_id: str, content: str, status: str) -> None:
        """Return the result of a tool-function execution back to the LLM."""
        await self._transport.send_message({
            "message": ClientMessageType.TOOL_RESULT,
            "id": tool_call_id,
            "content": content,
            "status": status,
        })

    # --- Internal pipeline ---

    async def _conversation_pipeline(
        self,
        source: BinaryIO,
        audio_format: AudioFormat,
        conversation_config: ConversationConfig,
        tools: Optional[list[Tool]],
        debug_mode: Optional[DebugMode],
        ws_headers: Optional[dict[str, str]],
    ) -> None:
        await self._transport.connect(ws_headers)
        await self._start_conversation(conversation_config, audio_format, tools, debug_mode)

        producer = asyncio.create_task(self._audio_producer(source, audio_format))
        consumer = asyncio.create_task(self._message_consumer())

        done, pending = await asyncio.wait({producer, consumer}, return_when=asyncio.FIRST_EXCEPTION)

        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, ConversationEndedException):
                raise exc

    async def _start_conversation(
        self,
        conversation_config: ConversationConfig,
        audio_format: AudioFormat,
        tools: Optional[list[Tool]],
        debug_mode: Optional[DebugMode],
    ) -> None:
        msg: dict[str, Any] = {
            "message": ClientMessageType.START_CONVERSATION,
            "audio_format": audio_format.to_dict(),
            "conversation_config": conversation_config.to_dict(),
        }
        if tools:
            msg["tools"] = [t.to_dict() if isinstance(t, ToolFunctionParam) else t for t in tools]
        if debug_mode:
            msg["debug"] = debug_mode.to_dict()
        await self._transport.send_message(msg)
        self._session.is_running = True

    async def _audio_producer(self, source: BinaryIO, audio_format: AudioFormat) -> None:
        await self._conversation_started.wait()
        try:
            async for frame in _read_audio_chunks(source, audio_format.chunk_size):
                if not self._session.is_running:
                    break
                self._session.client_seq_no += 1
                await self._transport.send_message(frame)
                await asyncio.sleep(0.001)

            if self._session.is_running:
                await self._send_audio_ended()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._session.is_running = False
            raise AudioError("Failed to send audio") from exc

    async def _message_consumer(self) -> None:
        try:
            while self._session.is_running:
                try:
                    msg = await asyncio.wait_for(
                        self._transport.receive_message(), timeout=0.1,
                    )
                except asyncio.TimeoutError:
                    await asyncio.sleep(0.001)
                    continue

                if isinstance(msg, bytes):
                    self._handle_binary_message(msg)
                elif isinstance(msg, dict):
                    self._handle_json_message(msg)
        except asyncio.CancelledError:
            raise
        except ConversationEndedException:
            self._session.is_running = False
            raise
        except Exception as exc:
            self._session.is_running = False
            raise SessionError("Message consumer error") from exc

    def _handle_json_message(self, message: dict[str, Any]) -> None:
        msg_type = message.get("message")
        if not msg_type:
            return

        try:
            enum_type = ServerMessageType(msg_type)
        except ValueError:
            return

        # Internal state machine
        if enum_type == ServerMessageType.CONVERSATION_STARTED:
            self._session.conversation_id = message.get("id")
            self._conversation_started.set()
        elif enum_type == ServerMessageType.CONVERSATION_ENDED:
            self._session.is_running = False
            self.emit(enum_type, message)
            raise ConversationEndedException("Conversation completed normally")
        elif enum_type == ServerMessageType.ERROR:
            self._session.is_running = False
            reason = message.get("reason", "unknown")
            self.emit(enum_type, message)
            raise ConversationError(reason)

        self.emit(enum_type, message)

    def _handle_binary_message(self, message: bytes) -> None:
        self.emit(ServerMessageType.ADD_AUDIO, message)

    async def _send_audio_ended(self) -> None:
        msg = {
            "message": ClientMessageType.AUDIO_ENDED,
            "last_seq_no": self._session.client_seq_no,
        }
        await self._transport.send_message(msg)


__all__ = ["AsyncFlowClient"]
