"""Shunyalabs TTS module.

Provides batch and streaming text-to-speech clients that communicate
with the Shunyalabs TTS gateway.

Public API
----------
Models:
    OutputFormat, TTSConfig, TTSResult, TTSChunk, TTSCompletion

Batch clients:
    AsyncBatchTTS, SyncBatchTTS

Streaming clients:
    AsyncStreamingTTS, SyncStreamingTTS
"""

from ._models import (
    OutputFormat,
    TTSChunk,
    TTSCompletion,
    TTSConfig,
    TTSResult,
)
from ._batch import AsyncBatchTTS, SyncBatchTTS
from ._streaming import AsyncStreamingTTS, SyncStreamingTTS

__all__ = [
    # Models
    "OutputFormat",
    "TTSConfig",
    "TTSResult",
    "TTSChunk",
    "TTSCompletion",
    # Batch clients
    "AsyncBatchTTS",
    "SyncBatchTTS",
    # Streaming clients
    "AsyncStreamingTTS",
    "SyncStreamingTTS",
]
