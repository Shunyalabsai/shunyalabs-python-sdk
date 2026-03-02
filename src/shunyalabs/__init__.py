"""Shunyalabs Python SDK — ASR, TTS, and Conversational AI.

Install extras for specific features:
    pip install shunyalabs[ASR]     # Speech-to-text
    pip install shunyalabs[TTS]     # Text-to-speech
    pip install shunyalabs[all]     # Everything
    pip install shunyalabs[extras]  # Audio playback helpers

Usage:
    from shunyalabs import ShunyaClient, AsyncShunyaClient

    # Sync
    client = ShunyaClient(api_key="your-key")
    result = client.tts.synthesize("Hello world")
    result.save("hello.wav")

    # Async
    async with AsyncShunyaClient(api_key="your-key") as client:
        result = await client.asr.transcribe("audio.wav")
        print(result.text)
"""

from ._version import __version__
from ._core._auth import StaticKeyAuth
from ._core._config import ClientConfig
from ._core._exceptions import (
    APIError,
    AudioError,
    AuthenticationError,
    ConfigurationError,
    ConnectionError,
    ConversationEndedException,
    ConversationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    SessionError,
    ShunyalabsError,
    SynthesisError,
    TimeoutError,
    TranscriptionError,
    TransportError,
)
from ._client import ShunyaClient, AsyncShunyaClient

__all__ = [
    "__version__",
    # Clients
    "ShunyaClient",
    "AsyncShunyaClient",
    "ClientConfig",
    "StaticKeyAuth",
    # Exceptions
    "ShunyalabsError",
    "APIError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "ConfigurationError",
    "ConnectionError",
    "TimeoutError",
    "TransportError",
    "TranscriptionError",
    "SynthesisError",
    "SessionError",
    "AudioError",
    "ConversationError",
    "ConversationEndedException",
]
