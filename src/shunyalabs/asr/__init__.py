"""Shunyalabs ASR module -- batch and streaming speech recognition.

Quick-start (batch, async)::

    from shunyalabs._core._auth import StaticKeyAuth
    from shunyalabs._core._http_transport import AsyncHttpTransport
    from shunyalabs.asr import AsyncBatchASR, TranscriptionConfig

    auth = StaticKeyAuth("your-api-key")
    transport = AsyncHttpTransport("https://asr.api.shunyalabs.ai", auth)
    client = AsyncBatchASR(auth, transport)

    result = await client.transcribe("recording.wav")
    print(result.text)

Quick-start (streaming)::

    from shunyalabs.asr import AsyncStreamingASR, StreamingMessageType

    streaming = AsyncStreamingASR(auth, "wss://asr.api.shunyalabs.ai/ws")
    conn = await streaming.stream()

    @conn.on(StreamingMessageType.FINAL)
    def on_final(msg):
        print(msg.text)

    await conn.stream_file("recording.raw")
    await conn.close()
"""

# -- Models (batch) ---------------------------------------------------------
from ._models import (
    NLPAnalysis,
    SegmentResult,
    TranscriptionConfig,
    TranscriptionResult,
)

# -- Models (streaming) -----------------------------------------------------
from ._models import (
    StreamingConfig,
    StreamingDone,
    StreamingError,
    StreamingFinal,
    StreamingFinalSegment,
    StreamingMessageType,
    StreamingPartial,
    parse_streaming_message,
)

# -- Batch clients ----------------------------------------------------------
from ._batch import AsyncBatchASR, SyncBatchASR

# -- Streaming client -------------------------------------------------------
from ._streaming import ASRStreamingConnection, AsyncStreamingASR

__all__ = [
    # Batch models
    "TranscriptionConfig",
    "TranscriptionResult",
    "SegmentResult",
    "NLPAnalysis",
    # Streaming models
    "StreamingConfig",
    "StreamingMessageType",
    "StreamingPartial",
    "StreamingFinalSegment",
    "StreamingFinal",
    "StreamingDone",
    "StreamingError",
    "parse_streaming_message",
    # Batch clients
    "AsyncBatchASR",
    "SyncBatchASR",
    # Streaming clients
    "ASRStreamingConnection",
    "AsyncStreamingASR",
]
