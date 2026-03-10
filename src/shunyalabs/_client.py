"""Top-level client classes providing fluent API access to all Shunyalabs services.

Usage:
    # Sync
    client = ShunyaClient(api_key="key")
    client.asr.transcribe("audio.wav")
    client.tts.synthesize("Hello")

    # Async
    async with AsyncShunyaClient(api_key="key") as client:
        await client.asr.transcribe("audio.wav")
        await client.tts.synthesize("Hello")
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ._core._auth import StaticKeyAuth
from ._core._config import ClientConfig
from ._core._http_transport import AsyncHttpTransport, SyncHttpTransport
from ._core._logging import get_logger
from ._core._models import HttpConnectionConfig, WsConnectionConfig

logger = get_logger(__name__)


class _ASRNamespace:
    """Sync ASR namespace providing batch and streaming methods."""

    def __init__(self, client: ShunyaClient) -> None:
        self._client = client
        self._batch = None

    def _get_batch(self):
        if self._batch is None:
            from .asr._batch import SyncBatchASR

            self._batch = SyncBatchASR(
                auth=self._client._auth,
                transport=SyncHttpTransport(
                    url=self._client._config.resolve_asr_url(),
                    auth=self._client._auth,
                    conn_config=HttpConnectionConfig(
                        operation_timeout=self._client._config.timeout,
                    ),
                    max_retries=self._client._config.max_retries,
                ),
            )
        return self._batch

    def transcribe(self, audio=None, *, url: Optional[str] = None, config=None):
        """Transcribe audio (batch). See shunyalabs.asr.SyncBatchASR.transcribe."""
        return self._get_batch().transcribe(audio, url=url, config=config)

    def transcribe_file(self, audio, *, config=None):
        """Transcribe from file upload. See shunyalabs.asr.SyncBatchASR.transcribe_file."""
        return self._get_batch().transcribe_file(audio, config=config)

    def transcribe_url(self, audio_url: str, *, config=None):
        """Transcribe from URL. See shunyalabs.asr.SyncBatchASR.transcribe_url."""
        return self._get_batch().transcribe_url(audio_url, config=config)


class _TTSNamespace:
    """Sync TTS namespace providing batch and streaming methods."""

    def __init__(self, client: ShunyaClient) -> None:
        self._client = client
        self._batch = None
        self._streaming = None

    def _get_batch(self):
        if self._batch is None:
            from .tts._batch import SyncBatchTTS

            self._batch = SyncBatchTTS(
                auth=self._client._auth,
                transport=SyncHttpTransport(
                    url=self._client._config.resolve_tts_url(),
                    auth=self._client._auth,
                    conn_config=HttpConnectionConfig(
                        operation_timeout=self._client._config.timeout,
                    ),
                    max_retries=self._client._config.max_retries,
                ),
            )
        return self._batch

    def _get_streaming(self):
        if self._streaming is None:
            from .tts._streaming import SyncStreamingTTS

            self._streaming = SyncStreamingTTS(
                auth=self._client._auth,
                ws_url=self._client._config.resolve_tts_ws_url(),
                ws_config=WsConnectionConfig(),
            )
        return self._streaming

    def synthesize(self, text: str, *, config=None):
        """Synthesize text to speech (batch). Returns TTSResult."""
        return self._get_batch().synthesize(text, config=config)

    def stream(self, text: str, *, config=None, detailed: bool = False):
        """Stream TTS synthesis. Returns Iterator[bytes] or Iterator[tuple[TTSChunk, bytes]]."""
        return self._get_streaming().stream(text, config=config, detailed=detailed)

    def stream_to_file(self, text: str, path: str, *, config=None):
        """Stream TTS and save to file."""
        return self._get_streaming().stream_to_file(text, path, config=config)


class ShunyaClient:
    """Synchronous Shunyalabs client with fluent API.

    Args:
        config: A pre-built :class:`ClientConfig`. When provided, all other
            keyword arguments are ignored.
        api_key: API key. Falls back to SHUNYALABS_API_KEY env var.
        timeout: Default request timeout in seconds.
        max_retries: Number of retries for failed requests.
        asr_url: Override ASR gateway URL.
        tts_url: Override TTS gateway URL.
        tts_ws_url: Override TTS streaming WebSocket URL.
        flow_url: Override Flow WebSocket URL.

    Examples:
        >>> client = ShunyaClient(api_key="key")
        >>> result = client.tts.synthesize("Hello world")
        >>> result.save("hello.pcm")
        >>>
        >>> result = client.asr.transcribe("audio.wav")
        >>> print(result.text)
    """

    def __init__(
        self,
        *,
        config: Optional[ClientConfig] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        asr_url: Optional[str] = None,
        tts_url: Optional[str] = None,
        tts_ws_url: Optional[str] = None,
        flow_url: Optional[str] = None,
    ) -> None:
        self._config = config or ClientConfig(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            asr_url=asr_url,
            tts_url=tts_url,
            tts_ws_url=tts_ws_url,
            flow_url=flow_url,
        )
        self._auth = StaticKeyAuth(self._config.resolve_api_key())
        self._asr: Optional[_ASRNamespace] = None
        self._tts: Optional[_TTSNamespace] = None

    @property
    def asr(self) -> _ASRNamespace:
        """Access ASR (speech-to-text) methods."""
        if self._asr is None:
            self._asr = _ASRNamespace(self)
        return self._asr

    @property
    def tts(self) -> _TTSNamespace:
        """Access TTS (text-to-speech) methods."""
        if self._tts is None:
            self._tts = _TTSNamespace(self)
        return self._tts

    def close(self) -> None:
        """Close all underlying connections."""
        if self._asr and self._asr._batch:
            self._asr._batch._transport.close()
        if self._tts and self._tts._batch:
            self._tts._batch._transport.close()

    def __enter__(self) -> ShunyaClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


# --- Async Client ---


class _AsyncASRNamespace:
    """Async ASR namespace providing batch and streaming methods."""

    def __init__(self, client: AsyncShunyaClient) -> None:
        self._client = client
        self._batch = None
        self._streaming = None

    def _get_batch(self):
        if self._batch is None:
            from .asr._batch import AsyncBatchASR

            self._batch = AsyncBatchASR(
                auth=self._client._auth,
                transport=AsyncHttpTransport(
                    url=self._client._config.resolve_asr_url(),
                    auth=self._client._auth,
                    conn_config=HttpConnectionConfig(
                        operation_timeout=self._client._config.timeout,
                    ),
                    max_retries=self._client._config.max_retries,
                ),
            )
        return self._batch

    def _get_streaming(self):
        if self._streaming is None:
            from .asr._streaming import AsyncStreamingASR

            self._streaming = AsyncStreamingASR(
                auth=self._client._auth,
                ws_url=self._client._config.resolve_asr_ws_url(),
                ws_config=WsConnectionConfig(),
            )
        return self._streaming

    async def transcribe(self, audio=None, *, url: Optional[str] = None, config=None):
        """Transcribe audio (batch). See shunyalabs.asr.AsyncBatchASR.transcribe."""
        return await self._get_batch().transcribe(audio, url=url, config=config)

    async def transcribe_file(self, audio, *, config=None):
        """Transcribe from file upload."""
        return await self._get_batch().transcribe_file(audio, config=config)

    async def transcribe_url(self, audio_url: str, *, config=None):
        """Transcribe from URL."""
        return await self._get_batch().transcribe_url(audio_url, config=config)

    async def stream(self, *, config=None):
        """Start a streaming ASR session. Returns ASRStreamingConnection."""
        return await self._get_streaming().stream(config=config)


class _AsyncTTSNamespace:
    """Async TTS namespace providing batch and streaming methods."""

    def __init__(self, client: AsyncShunyaClient) -> None:
        self._client = client
        self._batch = None
        self._streaming = None

    def _get_batch(self):
        if self._batch is None:
            from .tts._batch import AsyncBatchTTS

            self._batch = AsyncBatchTTS(
                auth=self._client._auth,
                transport=AsyncHttpTransport(
                    url=self._client._config.resolve_tts_url(),
                    auth=self._client._auth,
                    conn_config=HttpConnectionConfig(
                        operation_timeout=self._client._config.timeout,
                    ),
                    max_retries=self._client._config.max_retries,
                ),
            )
        return self._batch

    def _get_streaming(self):
        if self._streaming is None:
            from .tts._streaming import AsyncStreamingTTS

            self._streaming = AsyncStreamingTTS(
                auth=self._client._auth,
                ws_url=self._client._config.resolve_tts_ws_url(),
                ws_config=WsConnectionConfig(),
            )
        return self._streaming

    async def synthesize(self, text: str, *, config=None):
        """Synthesize text to speech (batch). Returns TTSResult."""
        return await self._get_batch().synthesize(text, config=config)

    async def stream(self, text: str, *, config=None, detailed: bool = False):
        """Stream TTS synthesis. Returns AsyncIterator[bytes] or AsyncIterator[tuple]."""
        return self._get_streaming().stream(text, config=config, detailed=detailed)

    async def synthesize_stream(self, text: str, *, config=None) -> bytes:
        """Collect all streaming chunks and return combined audio."""
        return await self._get_streaming().synthesize(text, config=config)

    async def stream_to_file(self, text: str, path: str, *, config=None):
        """Stream TTS and save to file."""
        return await self._get_streaming().stream_to_file(text, path, config=config)


class _AsyncFlowNamespace:
    """Async Flow namespace."""

    def __init__(self, client: AsyncShunyaClient) -> None:
        self._client = client
        self._flow_client = None

    def _get_client(self):
        if self._flow_client is None:
            from .flow._client import AsyncFlowClient

            self._flow_client = AsyncFlowClient(
                auth=self._client._auth,
                url=self._client._config.resolve_flow_url(),
                conn_config=WsConnectionConfig(),
            )
        return self._flow_client

    async def start_conversation(self, source, **kwargs):
        """Start a Flow conversation. See shunyalabs.flow.AsyncFlowClient."""
        flow_client = self._get_client()
        return await flow_client.start_conversation(source, **kwargs)

    def on(self, event, callback=None):
        """Register an event handler on the Flow client."""
        return self._get_client().on(event, callback)

    async def close(self):
        if self._flow_client:
            await self._flow_client.close()


class AsyncShunyaClient:
    """Asynchronous Shunyalabs client with fluent API.

    All methods are awaitable. Use as an async context manager for automatic cleanup.

    Args:
        config: A pre-built :class:`ClientConfig`. When provided, all other
            keyword arguments are ignored.
        api_key: API key. Falls back to SHUNYALABS_API_KEY env var.
        timeout: Default request timeout in seconds.
        max_retries: Number of retries for failed requests.
        asr_url: Override ASR gateway URL.
        asr_ws_url: Override ASR streaming WebSocket URL.
        tts_url: Override TTS gateway URL.
        tts_ws_url: Override TTS streaming WebSocket URL.
        flow_url: Override Flow WebSocket URL.

    Examples:
        >>> async with AsyncShunyaClient(api_key="key") as client:
        ...     result = await client.tts.synthesize("Hello world")
        ...     result.save("hello.pcm")
        ...
        ...     result = await client.asr.transcribe("audio.wav")
        ...     print(result.text)
    """

    def __init__(
        self,
        *,
        config: Optional[ClientConfig] = None,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
        max_retries: int = 2,
        asr_url: Optional[str] = None,
        asr_ws_url: Optional[str] = None,
        tts_url: Optional[str] = None,
        tts_ws_url: Optional[str] = None,
        flow_url: Optional[str] = None,
    ) -> None:
        self._config = config or ClientConfig(
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
            asr_url=asr_url,
            asr_ws_url=asr_ws_url,
            tts_url=tts_url,
            tts_ws_url=tts_ws_url,
            flow_url=flow_url,
        )
        self._auth = StaticKeyAuth(self._config.resolve_api_key())
        self._asr: Optional[_AsyncASRNamespace] = None
        self._tts: Optional[_AsyncTTSNamespace] = None
        self._flow: Optional[_AsyncFlowNamespace] = None

    @property
    def asr(self) -> _AsyncASRNamespace:
        """Access ASR (speech-to-text) methods."""
        if self._asr is None:
            self._asr = _AsyncASRNamespace(self)
        return self._asr

    @property
    def tts(self) -> _AsyncTTSNamespace:
        """Access TTS (text-to-speech) methods."""
        if self._tts is None:
            self._tts = _AsyncTTSNamespace(self)
        return self._tts

    @property
    def flow(self) -> _AsyncFlowNamespace:
        """Access Flow (conversational AI) methods."""
        if self._flow is None:
            self._flow = _AsyncFlowNamespace(self)
        return self._flow

    async def close(self) -> None:
        """Close all underlying connections."""
        if self._asr and self._asr._batch:
            await self._asr._batch._transport.close()
        if self._tts and self._tts._batch:
            await self._tts._batch._transport.close()
        if self._flow:
            await self._flow.close()

    async def __aenter__(self) -> AsyncShunyaClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


__all__ = ["ShunyaClient", "AsyncShunyaClient"]
