"""Shunyalabs TTS plugin for LiveKit Agents.

Supports both chunked synthesis (single text -> audio) and real-time
streaming synthesis over WebSocket via the Shunyalabs TTS gateway,
using the Shunyalabs Python SDK for transport and protocol handling.

Install::

    pip install livekit-plugins-shunyalabs

Usage::

    from livekit.plugins import shunyalabs

    session = AgentSession(
        tts=shunyalabs.TTS(speaker="Rajesh"),
    )
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Optional

from livekit.agents import (
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
)
from livekit.agents.tts import (
    AudioEmitter,
    ChunkedStream,
    SynthesizeStream,
    TTS,
    TTSCapabilities,
)

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._http_transport import AsyncHttpTransport
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.tts._batch import AsyncBatchTTS
from shunyalabs.tts._models import TTSConfig
from shunyalabs.tts._streaming import AsyncStreamingTTS

from ._version import __version__

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://tts.shunyalabs.ai"
_DEFAULT_WS_URL = "wss://tts.shunyalabs.ai/ws"


class TTS(tts.TTS):
    """LiveKit Agents TTS plugin backed by the Shunyalabs TTS gateway.

    Uses the Shunyalabs Python SDK for WebSocket streaming transport.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        ws_url: WebSocket streaming endpoint URL.
        voice: Speaker voice name (e.g. ``"Rajesh"``, ``"Nisha"``).
        style: Emotion style tag (e.g. ``"<Happy>"``). Prepended to text.
            The gateway handles the speaker prefix and default style internally.
        language: ISO 639 language code (e.g. ``"en"``, ``"hi"``). Required.
        sample_rate: Output sample rate (default 16000).
        output_format: Audio format (``"pcm"``, ``"wav"``, ``"mp3"``).
        speed: Speaking speed multiplier (0.5-2.0).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        api_url: str = _DEFAULT_API_URL,
        ws_url: str = _DEFAULT_WS_URL,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        style: Optional[str] = None,
        language: str = "en",
        sample_rate: int = 16000,
        output_format: str = "pcm",
        speed: float = 1.0,
    ) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._api_url = api_url.rstrip("/")
        self._ws_url = ws_url
        self._model = model
        self._voice = voice
        self._style = style
        self._language = language
        self._output_format = output_format
        self._speed = speed
        self._auth = StaticKeyAuth(self._api_key)

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "shunyalabs"

    def _format_text(self, text: str) -> str:
        """Prepend the style tag if set. The gateway adds the speaker
        prefix and a default <Conversational> style if none is present.
        """
        return f"{self._style} {text}" if self._style else text

    def _make_tts_config(self) -> TTSConfig:
        """Build a TTSConfig from plugin settings."""
        return TTSConfig(
            model=self._model,
            voice=self._voice,
            language=self._language,
            response_format=self._output_format,
            speed=self._speed,
        )

    def _make_batch_tts(self) -> AsyncBatchTTS:
        """Create an AsyncBatchTTS instance using the SDK."""
        transport = AsyncHttpTransport(
            url=self._api_url,
            auth=self._auth,
        )
        return AsyncBatchTTS(auth=self._auth, transport=transport)

    def _make_streaming_tts(self) -> AsyncStreamingTTS:
        """Create an AsyncStreamingTTS instance using the SDK."""
        return AsyncStreamingTTS(
            auth=self._auth,
            ws_url=self._ws_url,
            ws_config=WsConnectionConfig(
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ),
        )

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "ChunkedTTSStream":
        return ChunkedTTSStream(
            tts=self,
            text=text,
            conn_options=conn_options,
        )

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "StreamingTTS":
        return StreamingTTS(tts=self, conn_options=conn_options)


class ChunkedTTSStream(ChunkedStream):
    """Single text -> audio synthesis via the Shunyalabs SDK."""

    def __init__(
        self,
        *,
        tts: TTS,
        text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=text, conn_options=conn_options)
        self._tts = tts
        self._text = text

    async def _run(self, output_emitter: AudioEmitter) -> None:
        request_id = str(uuid.uuid4())
        formatted = self._tts._format_text(self._text)

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
        )

        batch_tts = self._tts._make_batch_tts()
        config = self._tts._make_tts_config()

        try:
            result = await batch_tts.synthesize(formatted, config=config)
            output_emitter.push(result.audio_data)
        finally:
            await batch_tts._transport.close()


class StreamingTTS(SynthesizeStream):
    """Token-by-token streaming TTS using the Shunyalabs SDK.

    Collects pushed text tokens, then on flush/end sends the accumulated
    text to the TTS gateway via the SDK and streams back audio chunks.
    """

    def __init__(
        self,
        *,
        tts: TTS,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: AudioEmitter) -> None:
        request_id = str(uuid.uuid4())
        segment_idx = 0
        pending_text = ""

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )

        async for data in self._input_ch:
            if isinstance(data, str):
                pending_text += data
                continue

            # FlushSentinel — synthesize accumulated text
            text = pending_text.strip()
            pending_text = ""
            if not text:
                continue

            seg_id = f"{request_id}-{segment_idx}"
            segment_idx += 1
            formatted = self._tts._format_text(text)

            output_emitter.start_segment(segment_id=seg_id)

            streaming_tts = self._tts._make_streaming_tts()
            config = self._tts._make_tts_config()

            async for audio_bytes in streaming_tts.stream(formatted, config=config):
                output_emitter.push(audio_bytes)

            output_emitter.end_segment()
