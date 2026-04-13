"""Pydantic models for the Shunyalabs TTS module.

Mirrors the TTS Gateway API schemas (TTSRequestSchema, TTSResponseSchema,
TTSChunkSchema, TTSCompletionSchema) with SDK-friendly naming and
convenience methods.
"""

from __future__ import annotations

import base64
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OutputFormat(str, Enum):
    """Supported audio output formats.

    Values correspond to the ``response_format`` field accepted by the TTS
    gateway's ``TTSRequestSchema``.
    """

    PCM = "pcm"
    WAV = "wav"
    MP3 = "mp3"
    OGG_OPUS = "ogg_opus"
    FLAC = "flac"
    MULAW = "mulaw"
    ALAW = "alaw"


# ---------------------------------------------------------------------------
# Request configuration
# ---------------------------------------------------------------------------

class TTSConfig(BaseModel):
    """Configuration for a TTS synthesis request (OpenAI-compatible).

    All fields are optional.  When passed to a synthesis method the values
    are merged into the gateway ``TTSRequestSchema`` JSON body alongside
    the ``input`` text supplied by the caller.  Authentication is handled
    via the ``Authorization: Bearer`` header.

    Attributes:
        model: Model name (default ``"zero-indic"``).
        voice: Speaker voice name (e.g. ``"Varun"``, ``"Nisha"``).
        response_format: Output audio format (default ``"mp3"``).
        speed: Speaking speed multiplier (0.25--4.0).
        language: ISO 639-1/639-2 language code (2--3 chars).
        trim_silence: Strip leading/trailing silence from audio.
        volume_normalization: ``"peak"`` or ``"loudness"``, or *None*.
        background_audio: Preset name or base64-encoded background audio.
        background_volume: Background volume relative to speech (0.0--1.0).
        max_tokens: Maximum tokens for LLM generation (1--8192).
        reference_wav: Base64-encoded reference audio for voice cloning.
        reference_text: Transcript of the reference audio.
    """

    model: str = Field(
        default="zero-indic",
        description="Model name (e.g. 'zero-indic').",
    )
    voice: Optional[str] = Field(
        default=None,
        description="Speaker voice name (e.g. 'Varun', 'Nisha', 'Rajesh').",
    )
    response_format: Optional[OutputFormat] = Field(
        OutputFormat.WAV,
        description="Output audio format.",
    )
    speed: Optional[float] = Field(
        1.0,
        ge=0.25,
        le=4.0,
        description="Speaking speed multiplier.",
    )
    language: Optional[str] = Field(
        None,
        min_length=2,
        max_length=3,
        description="ISO 639-1/639-2 language code.",
    )
    trim_silence: Optional[bool] = Field(
        False,
        description="Trim leading/trailing silence.",
    )
    word_timestamps: Optional[bool] = Field(
        False,
        description="Include word-level timing data in the response.",
    )
    volume_normalization: Optional[str] = Field(
        None,
        description="Volume normalization mode: 'peak' or 'loudness'.",
    )
    background_audio: Optional[str] = Field(
        None,
        description="Preset name or base64-encoded background audio.",
    )
    background_volume: Optional[float] = Field(
        0.1,
        ge=0.0,
        le=1.0,
        description="Background audio volume relative to speech.",
    )
    max_tokens: int = Field(
        2048,
        ge=1,
        le=8192,
        description="Maximum tokens for LLM generation.",
    )
    reference_wav: Optional[str] = Field(
        None,
        description="Base64-encoded reference audio for voice cloning.",
    )
    reference_text: Optional[str] = Field(
        "",
        description="Transcript of the reference audio.",
    )

    def to_request_payload(
        self,
        text: str,
        request_type: Literal["batch", "streaming"] = "batch",
    ) -> dict:
        """Build a dict matching the gateway ``TTSRequestSchema``.

        Uses OpenAI-compatible field names (``input``, ``model``,
        ``voice``, ``response_format``).  Authentication is handled
        via the ``Authorization`` header, not in the payload.

        Args:
            text: The text to synthesise.
            request_type: ``"batch"`` or ``"streaming"``.

        Returns:
            A dict ready to be serialised as the JSON body for
            ``POST /v1/audio/speech`` or sent over ``/ws/tts``.
        """
        payload: dict = {
            "input": text,
            "request_type": request_type,
        }

        # Merge every non-None config value.
        for field_name, field_info in self.model_fields.items():
            value = getattr(self, field_name)
            if value is not None:
                # Serialize enums to their string value.
                if isinstance(value, Enum):
                    value = value.value
                payload[field_name] = value

        return payload


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class TTSResult(BaseModel):
    """Result of a batch TTS synthesis (``POST /tts``).

    The ``audio_data`` field contains **decoded** audio bytes (the gateway
    returns base64-encoded data which is decoded automatically).

    Attributes:
        request_id: Unique request identifier.
        audio_data: Raw audio bytes (decoded from base64).
        sample_rate: Audio sample rate in Hz.
        duration_seconds: Total audio duration in seconds.
        format: Audio format string (e.g. ``"pcm"``).
    """

    request_id: str
    audio_data: bytes
    sample_rate: int = 16000
    duration_seconds: float
    format: str = "pcm"
    word_timestamps: Optional[list] = Field(default=None)

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        """Write the audio bytes to a file.

        Args:
            path: Filesystem path to write to.  Parent directories are
                created automatically.
        """
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.audio_data)

    @classmethod
    def from_raw_audio(
        cls,
        audio_bytes: bytes,
        *,
        format: str = "mp3",
        sample_rate: int = 16000,
    ) -> "TTSResult":
        """Construct a ``TTSResult`` from raw binary audio data.

        Used with the OpenAI-compatible ``/v1/audio/speech`` endpoint
        which returns raw audio bytes directly.

        Args:
            audio_bytes: Raw audio bytes from the gateway response.
            format: Audio format string (e.g. ``"mp3"``, ``"pcm"``).
            sample_rate: Audio sample rate in Hz.

        Returns:
            A populated ``TTSResult`` instance.
        """
        return cls(
            request_id="",
            audio_data=audio_bytes,
            sample_rate=sample_rate,
            duration_seconds=0.0,
            format=format,
        )

    @classmethod
    def from_api_response(cls, data: dict) -> "TTSResult":
        """Construct a ``TTSResult`` from the raw gateway JSON response.

        The gateway's ``audio_data`` field is base64-encoded; this factory
        decodes it into raw ``bytes``.

        Args:
            data: Parsed JSON dict from the ``POST /tts`` response.

        Returns:
            A populated ``TTSResult`` instance.
        """
        audio_b64: str = data.get("audio_data", "")
        audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""

        return cls(
            request_id=data["request_id"],
            audio_data=audio_bytes,
            sample_rate=data.get("sample_rate", 16000),
            duration_seconds=data.get("duration_seconds", 0.0),
            format=data.get("format", "pcm"),
            word_timestamps=data.get("word_timestamps"),
        )


# ---------------------------------------------------------------------------
# Streaming models
# ---------------------------------------------------------------------------

class TTSChunk(BaseModel):
    """Metadata for a single streaming audio chunk.

    Sent by the gateway as a JSON frame **before** the corresponding
    binary audio frame on the ``/ws/tts`` WebSocket.

    Attributes:
        type: Always ``"chunk"``.
        request_id: Unique request identifier.
        chunk_index: Zero-based index of this chunk.
        is_final: Whether this is the last audio chunk.
        format: Audio format string (present on gateway responses).
        sample_rate: Audio sample rate in Hz (present on gateway responses).
    """

    type: Literal["chunk"] = "chunk"
    request_id: str
    chunk_index: int
    is_final: bool = False
    format: Optional[str] = None
    sample_rate: Optional[int] = None


class TTSCompletion(BaseModel):
    """Completion message received at the end of a streaming session.

    Attributes:
        type: Always ``"completion"``.
        request_id: Unique request identifier.
        status: ``"complete"`` or ``"error"``.
        total_chunks: Total number of audio chunks delivered.
        total_duration_seconds: Total audio duration in seconds.
        error_message: Error detail when ``status`` is ``"error"``.
        format: Audio format string.
        sample_rate: Audio sample rate in Hz.
    """

    type: Literal["completion"] = "completion"
    request_id: str
    status: str
    total_chunks: int
    total_duration_seconds: float
    error_message: Optional[str] = None
    format: Optional[str] = None
    sample_rate: Optional[int] = None


__all__ = [
    "OutputFormat",
    "TTSConfig",
    "TTSResult",
    "TTSChunk",
    "TTSCompletion",
]
