"""Pydantic models for the Shunyalabs ASR module.

Covers both the batch HTTP API (POST /v1/transcriptions) and the
real-time streaming WebSocket API (WS /v1/realtime).  Every field mirrors the
ASR Gateway schema so that round-tripping is lossless.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


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
    detected_language_name: Optional[str] = None
    speakers: List[str] = Field(default_factory=list)
    audio_duration: Optional[float] = None
    inference_time_ms: Optional[float] = None
    nlp_analysis: Optional[NLPAnalysis] = None


# ---------------------------------------------------------------------------
# Streaming models  (WS /v1/realtime)
# ---------------------------------------------------------------------------


class StreamingConfig(BaseModel):
    """Configuration sent as the first JSON frame over the WebSocket.

    Authentication is handled via the ``Authorization`` header on the
    WebSocket connection, not in the JSON payload.

    ``language`` defaults to ``"auto"``, which is accepted -- but **set it explicitly
    whenever you know it.** A live stream has to commit to a language from the opening
    seconds of audio, long before the detector has enough signal to be sure, so
    detection here is best-effort in a way batch transcription is not. For a voice
    agent the language is nearly always known up front, and passing it removes an
    avoidable source of wrong-script transcripts on the first turns of a call.

    **Turn-latency tuning lives in** ``endpoint_silence_ms``. It is the single
    biggest lever on how long a caller waits after they stop speaking, because it
    is pure wall-clock delay before the server will emit a final. Leave it unset
    to take the server default (700 ms), lower it for snappier turns, raise it if
    callers who pause mid-sentence are being cut off. A deployment that ran it at
    3000 measured 3.05 s of perceived turn latency from this setting alone.

    ``dtype``, ``chunk_size_sec`` and ``silence_threshold_sec`` are carried over from
    the older gateway and are ignored by ``/v1/realtime``. They are kept so existing
    callers do not break, but setting them changes nothing -- use
    ``endpoint_silence_ms`` / ``decode_every_ms`` / ``vad`` instead.

    Unknown fields are rejected rather than silently dropped: a mistyped tuning
    knob that quietly did nothing is the failure this class is most prone to.
    """

    model_config = ConfigDict(extra="forbid")

    language: str = "auto"
    sample_rate: int = 16000
    dtype: str = "int16"
    chunk_size_sec: float = 1.0
    silence_threshold_sec: float = 0.5

    # -- Live tuning (``/v1/realtime``). None => server default. ------------
    # Each is echoed back in the ``ready`` frame after server-side clamping, and
    # surfaced on the connection as ``ASRStreamingConnection.effective_config``,
    # so a clamped value is visible instead of silently different.

    #: Silence before the server emits a final, in ms. Server clamps to 200-5000
    #: (default 700). The primary perceived-latency control.
    endpoint_silence_ms: Optional[int] = None

    #: How often interim results are produced, in ms. Server clamps to 320-5000
    #: (default 640). The stream re-decodes its whole buffer each tick, so raising
    #: this cuts GPU cost per stream at the price of coarser partials.
    decode_every_ms: Optional[int] = None

    #: Endpointing detector. ``"silero"`` opts into model-based VAD, which is
    #: worth it on noisy telephony audio where plain energy thresholding can fail
    #: to register silence at all -- and then never endpoints naturally.
    vad: Optional[str] = None

    #: Opt into code-switch refinement: a ``final`` is followed by a
    #: ``final_refined`` carrying the same segment re-rendered in correct scripts.
    #: Subscribe to :attr:`StreamingMessageType.FINAL_REFINED` or it is discarded.
    codeswitch: Optional[bool] = None

    #: Explicit model/tier selection. Leave unset to let the gateway route on
    #: language.
    model: Optional[str] = None

    def to_ws_payload(self) -> Dict[str, Any]:
        """Return the dict to serialise as the WebSocket config frame.

        ``None`` values are excluded so the gateway applies its own defaults.
        """
        return {k: v for k, v in self.model_dump().items() if v is not None}


class StreamingMessageType(str, Enum):
    """Discriminator for server-sent streaming messages."""

    READY = "ready"
    PARTIAL = "partial"
    FINAL = "final"
    # Emitted after a `final` when code-switch refinement is enabled: the same segment,
    # re-rendered in correct scripts. It arrives separately so a raw final is never delayed
    # waiting for it. Subscribe to it or the refinement is silently discarded.
    FINAL_REFINED = "final_refined"
    # The turn-end signal. Emitted immediately after a `final` whose
    # `end_of_utterance` is true -- i.e. the speaker genuinely stopped, as opposed
    # to the server hitting its maximum segment length and cutting mid-speech.
    # This is the event to drive turn-taking from in a voice agent; a `final` alone
    # does not distinguish the two cases. No `utterance_end` arrives for a forced
    # cut, so a caller who never pauses can starve it -- handle that explicitly.
    UTTERANCE_END = "utterance_end"
    ERROR = "error"
    # Older gateway only -- /v1/realtime never sends these. Kept so existing subscriptions
    # keep importing, but a handler registered on them will never fire.
    FINAL_SEGMENT = "final_segment"
    DONE = "done"


class StreamingPartial(BaseModel):
    """Interim transcription result received during streaming.

    ``/v1/realtime`` sends ``seg`` and ``elapsed_ms``; the older gateway sent
    ``segment_id`` and ``latency_ms``. Both spellings populate the same field, so
    code written against either name keeps working.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = StreamingMessageType.PARTIAL
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("seg", "segment_id")
    )
    latency_ms: Optional[float] = Field(
        default=None, validation_alias=AliasChoices("elapsed_ms", "latency_ms")
    )
    #: Text added since the previous partial. ``/v1/realtime`` only.
    delta: str = ""
    audio_duration_sec: Optional[float] = None

    @property
    def seg(self) -> Optional[int]:
        """Wire-name alias for :attr:`segment_id`."""
        return self.segment_id

    @property
    def elapsed_ms(self) -> Optional[float]:
        """Wire-name alias for :attr:`latency_ms`."""
        return self.latency_ms


class StreamingFinalSegment(BaseModel):
    """A completed segment emitted when silence is detected."""

    type: str = StreamingMessageType.FINAL_SEGMENT
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = None
    silence_duration_ms: Optional[float] = None
    audio_duration_sec: Optional[float] = None


class StreamingFinal(BaseModel):
    """A finalised segment.

    On ``/v1/realtime`` this arrives per utterance, not once per connection: the
    socket stays open and the next utterance starts a new segment.

    :attr:`end_of_utterance` is the field that matters for turn-taking. ``True``
    means the speaker actually stopped; ``False`` means the server hit its maximum
    segment length and cut mid-speech, so the turn is *not* over. A
    :attr:`StreamingMessageType.UTTERANCE_END` event follows only in the ``True``
    case.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = StreamingMessageType.FINAL
    text: str = ""
    language: Optional[str] = None
    segment_id: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("seg", "segment_id")
    )
    #: True if the speaker stopped; False if this was a forced maximum-length cut.
    end_of_utterance: Optional[bool] = None
    inference_time_ms: Optional[float] = Field(
        default=None, validation_alias=AliasChoices("elapsed_ms", "inference_time_ms")
    )
    audio_duration_sec: Optional[float] = None
    connection_duration_sec: Optional[float] = None

    @property
    def seg(self) -> Optional[int]:
        """Wire-name alias for :attr:`segment_id`."""
        return self.segment_id

    @property
    def elapsed_ms(self) -> Optional[float]:
        """Wire-name alias for :attr:`inference_time_ms`."""
        return self.inference_time_ms


class StreamingUtteranceEnd(BaseModel):
    """The speaker finished their turn.

    Emitted right after a :class:`StreamingFinal` whose ``end_of_utterance`` is
    true. Drive turn-taking from this rather than from ``final``, which also fires
    for forced mid-speech cuts.
    """

    model_config = ConfigDict(populate_by_name=True)

    type: str = StreamingMessageType.UTTERANCE_END
    segment_id: Optional[int] = Field(
        default=None, validation_alias=AliasChoices("seg", "segment_id")
    )

    @property
    def seg(self) -> Optional[int]:
        """Wire-name alias for :attr:`segment_id`."""
        return self.segment_id


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
    # final_refined carries the same shape as a final. Its absence here meant
    # parse_streaming_message fell through to the unknown-type branch and handed
    # subscribers a StreamingError, which has no `.text` -- so the refinement was
    # unusable even after 1.0.1 started subscribing to it.
    StreamingMessageType.FINAL_REFINED: StreamingFinal,
    StreamingMessageType.UTTERANCE_END: StreamingUtteranceEnd,
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
    "StreamingUtteranceEnd",
    "StreamingDone",
    "StreamingError",
    # Helpers
    "parse_streaming_message",
]
