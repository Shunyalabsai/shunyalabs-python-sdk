"""Flow models — adapted from the existing Flow SDK.

These are reused nearly verbatim since the Flow gateway API hasn't changed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Optional


class AudioEncoding(str, Enum):
    """Supported audio encoding formats."""

    PCM_F32LE = "pcm_f32le"
    PCM_S16LE = "pcm_s16le"


class ClientMessageType(str, Enum):
    """Message types sent from client to server."""

    START_CONVERSATION = "StartConversation"
    ADD_AUDIO = "AddAudio"
    AUDIO_ENDED = "AudioEnded"
    AUDIO_RECEIVED = "AudioReceived"
    TOOL_RESULT = "ToolResult"
    ADD_INPUT = "AddInput"


class ServerMessageType(str, Enum):
    """Message types received from server."""

    CONVERSATION_STARTED = "ConversationStarted"
    CONVERSATION_ENDED = "ConversationEnded"
    CONVERSATION_ENDING = "ConversationEnding"
    ADD_TRANSCRIPT = "AddTranscript"
    ADD_PARTIAL_TRANSCRIPT = "AddPartialTranscript"
    RESPONSE_STARTED = "ResponseStarted"
    RESPONSE_COMPLETED = "ResponseCompleted"
    RESPONSE_INTERRUPTED = "ResponseInterrupted"
    ADD_AUDIO = "AddAudio"
    AUDIO_ADDED = "AudioAdded"
    TOOL_INVOKE = "ToolInvoke"
    PROMPT = "prompt"
    INFO = "Info"
    WARNING = "Warning"
    ERROR = "Error"
    DEBUG = "Debug"


@dataclass
class AudioFormat:
    """Audio configuration for Flow conversations."""

    encoding: AudioEncoding = AudioEncoding.PCM_S16LE
    sample_rate: int = 16000
    chunk_size: int = 160

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "raw",
            "encoding": self.encoding.value,
            "sample_rate": self.sample_rate,
        }


@dataclass
class DebugMode:
    """Configuration for debug flags."""

    llm: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"llm": self.llm}


@dataclass
class AddInput:
    """Message to be sent to the LLM."""

    input: str
    immediate: bool = False
    interrupt_response: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": "AddInput",
            "input": self.input,
            "interrupt_response": self.interrupt_response,
            "immediate": self.immediate,
        }


@dataclass
class ConversationConfig:
    """Configuration for Flow conversations."""

    template_id: str = "default"
    template_variables: Optional[dict[str, str]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"template_id": self.template_id}
        if self.template_variables:
            result["template_variables"] = self.template_variables
        return result


@dataclass
class SessionInfo:
    """Information about the current conversation session."""

    request_id: str
    conversation_id: Optional[str] = None
    client_seq_no: int = 0
    server_seq_no: int = 0
    is_running: bool = False


@dataclass
class FunctionParamProperty:
    """Tool function property definition."""

    type: str
    description: str


@dataclass
class FunctionParam:
    """Tool function parameters definition."""

    type: str
    properties: dict[str, FunctionParamProperty]
    required: Optional[list[str]] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": self.type,
            "properties": {
                k: {"type": v.type, "description": v.description}
                for k, v in self.properties.items()
            },
        }
        if self.required:
            result["required"] = self.required
        return result


@dataclass
class FunctionDefinition:
    """Tool function definition."""

    name: str
    description: Optional[str] = None
    parameters: Optional[FunctionParam] = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name}
        if self.description:
            result["description"] = self.description
        if self.parameters:
            result["parameters"] = self.parameters.to_dict()
        return result


@dataclass
class ToolFunctionParam:
    """Tool definition for LLM function calling."""

    function: FunctionDefinition
    type: str = "function"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "function": self.function.to_dict(),
        }


__all__ = [
    "AudioEncoding",
    "ClientMessageType",
    "ServerMessageType",
    "AudioFormat",
    "DebugMode",
    "AddInput",
    "ConversationConfig",
    "SessionInfo",
    "FunctionParamProperty",
    "FunctionParam",
    "FunctionDefinition",
    "ToolFunctionParam",
]
