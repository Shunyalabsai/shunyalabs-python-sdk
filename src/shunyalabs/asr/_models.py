"""Pydantic models for the Shunyalabs ASR module.

Covers both the batch HTTP API (POST /v1/transcriptions) and the
real-time streaming WebSocket API (WS /ws).  Every field mirrors the
ASR Gateway schema so that round-tripping is lossless.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Batch transcription models  (POST /v1/transcriptions)
# ---------------------------------------------------------------------------


class TranscriptionConfig(BaseModel):
    """Client-side configuration for a batch transcription request.

    Each attribute maps 1-to-1 to a multipart form field accepted by
    ``POST /v1/transcriptions``.  Only non-``None`` values are sent.
    """

    model: str
    language_code: str = "auto"
    output_script: str = "auto"
    word_timestamps: bool = False

    # Diarization & speaker ID
    enable_diarization: bool = False
    enable_speaker_identification: bool = False
    enable_emotion_diarization: bool = False
    project: Optional[str] = None

    # NLP feature flags
    enable_intent_detection: bool = False
    intent_choices: Optional[List[str]] = None
    enable_summarization: bool = False
    summary_max_length: int = 150
    enable_sentiment_analysis: bool = False
    enable_keyterm_normalization: bool = False
    keyterm_keywords: Optional[List[str]] = None

    # Post-processing
    enable_profanity_hashing: bool = False
    hash_keywords: Optional[List[str]] = None
    output_language: Optional[str] = None

    def to_form_fields(self) -> Dict[str, str]:
        """Serialise to a flat ``{name: string_value}`` dict for multipart form data.

        JSON-serialisable list fields (``intent_choices``, ``hash_keywords``,
        ``keyterm_keywords``) are encoded as JSON strings, matching what the
        gateway expects.
        Boolean values are lowercased (``"true"`` / ``"false"``).
        ``None`` values are omitted.
        """
        fields: Dict[str, str] = {}
        for name, value in self:
            if value is None:
                continue
            if isinstance(value, bool):
                fields[name] = str(value).lower()
            elif isinstance(value, list):
                fields[name] = json.dumps(value)
            else:
                fields[name] = str(value)
        return fields


# --- Batch response models ---


class WordResult(BaseModel):
    """A single word with alignment timestamps and confidence score."""

    word: str
    start: float
    end: float
    score: Optional[float] = None


class SegmentResult(BaseModel):
    """A single time-aligned segment inside a transcription result."""

    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    emotion: Optional[str] = None
    words: Optional[List[WordResult]] = None


class NLPAnalysis(BaseModel):
    """Optional NLP analysis results attached to a transcription response.

    All fields are ``Optional`` because the gateway only populates those
    that were requested via the corresponding ``enable_*`` flags.
    """

    intent: Optional[Dict[str, Any]] = None
    summary: Optional[str] = None
    sentiment: Optional[Dict[str, Any]] = None
    emotion: Optional[Dict[str, Any]] = None
    translation: Optional[Union[str, Dict[str, Any]]] = None
    normalized_text: Optional[str] = None


class TranscriptionResult(BaseModel):
    """Top-level response for ``POST /v1/transcriptions``.

    Mirrors the JSON body returned by the ASR batch gateway.
    """

    success: bool = True
    request_id: str = ""
    text: str = ""
    segments: List[SegmentResult] = Field(default_factory=list)
    detected_language: Optional[str] = None
    speakers: List[str] = Field(default_factory=list)
    audio_duration: Optional[float] = None
    inference_time_ms: Optional[float] = None
    nlp_analysis: Optional[NLPAnalysis] = None


# ---------------------------------------------------------------------------
# Streaming models  (WS /ws)
# ---------------------------------------------------------------------------


class StreamingConfig(BaseModel):
    """Configuration sent as the first JSON frame over the WebSocket.

    Authentication is handled via the ``Authorization`` header on the
    WebSocket connection, not in the JSON payload.
    """

    language: str = "auto"
    sample_rate: int = 16000
    dtype: str = "int16"
    chunk_size_sec: float = 1.0
    silence_threshold_sec: float = 0.5

    def to_ws_payload(self) -> Dict[str, Any]:
        """Return the dict to serialise as the WebSocket config frame.

        ``None`` values are excluded so the gateway applies its own defaults.
        """
        return {k: v for k, v in self.model_dump().items() if v is not None}


class StreamingMessageType(str, Enum):
    """Discriminator for server-sent streaming messages."""

    READY = "ready"
    PARTIAL = "partial"
    FINAL_SEGMENT = "final_segment"
    FINAL = "final"
    DONE = "done"
    ERROR = "error"


class StreamingPartial(BaseModel):
    """Interim transcription result received during streaming."""

    type: str = StreamingMessageType.PARTIAL
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = None
    audio_duration_sec: Optional[float] = None
    latency_ms: Optional[float] = None


class StreamingFinalSegment(BaseModel):
    """A completed segment emitted when silence is detected."""

    type: str = StreamingMessageType.FINAL_SEGMENT
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = None
    silence_duration_ms: Optional[float] = None
    audio_duration_sec: Optional[float] = None


class StreamingFinal(BaseModel):
    """Final transcription result for the entire stream."""

    type: str = StreamingMessageType.FINAL
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = None
    audio_duration_sec: Optional[float] = None
    inference_time_ms: Optional[float] = None
    connection_duration_sec: Optional[float] = None


class StreamingDone(BaseModel):
    """Signals that the server has finished processing all audio."""

    type: str = StreamingMessageType.DONE
    total_segments: Optional[int] = None
    total_audio_duration_sec: Optional[float] = None
    connection_duration_sec: Optional[float] = None


class StreamingError(BaseModel):
    """Error message received from the streaming server."""

    type: str = StreamingMessageType.ERROR
    message: str = ""
    code: Optional[str] = None


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

_STREAMING_MESSAGE_MAP: Dict[str, type[BaseModel]] = {
    StreamingMessageType.PARTIAL: StreamingPartial,
    StreamingMessageType.FINAL_SEGMENT: StreamingFinalSegment,
    StreamingMessageType.FINAL: StreamingFinal,
    StreamingMessageType.DONE: StreamingDone,
    StreamingMessageType.ERROR: StreamingError,
}


def parse_streaming_message(data: Dict[str, Any]) -> BaseModel:
    """Deserialise a server JSON frame into the appropriate model.

    Falls back to returning a plain ``StreamingError`` with the raw
    ``type`` value when the message type is unrecognised.
    """
    msg_type = data.get("type", "")
    model_cls = _STREAMING_MESSAGE_MAP.get(msg_type)
    if model_cls is not None:
        return model_cls.model_validate(data)
    return StreamingError(message=f"Unknown message type: {msg_type}")


__all__ = [
    # Batch
    "TranscriptionConfig",
    "TranscriptionResult",
    "SegmentResult",
    "WordResult",
    "NLPAnalysis",
    # Streaming
    "StreamingConfig",
    "StreamingMessageType",
    "StreamingPartial",
    "StreamingFinalSegment",
    "StreamingFinal",
    "StreamingDone",
    "StreamingError",
    # Helpers
    "parse_streaming_message",
]
