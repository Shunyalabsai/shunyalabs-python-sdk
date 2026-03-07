"""Shunyalabs TTS service for Pipecat.

Connects to the Shunyalabs TTS gateway via WebSocket for each synthesis
request, streaming audio chunks back as ``TTSAudioRawFrame`` frames.

Uses the Shunyalabs Python SDK for transport and protocol handling.

Install::

    pip install pipecat-shunyalabs

Usage::

    from pipecat_shunyalabs import ShunyalabsTTSService

    tts = ShunyalabsTTSService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        speaker="Rajesh",
        style="<Happy>",
    )

    pipeline = Pipeline([..., tts, transport.output()])
"""

from __future__ import annotations

import logging
import os
from typing import AsyncGenerator, Optional

from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.tts._models import TTSConfig
from shunyalabs.tts._streaming import AsyncStreamingTTS

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://tts.shunyalabs.ai/ws"


class ShunyalabsTTSService(TTSService):
    """Pipecat TTS service backed by the Shunyalabs TTS gateway.

    Uses the Shunyalabs Python SDK for WebSocket streaming transport.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        url: WebSocket endpoint URL.
        model: TTS model name (e.g. ``"zero-indic"``).
        voice: Speaker voice name (e.g. ``"Rajesh"``, ``"Varun"``).
        speaker: Speaker name prefix for text formatting (e.g. ``"Rajesh"``).
        style: Emotion style tag (e.g. ``"<Happy>"``).
        language: Language code for transliteration (e.g. ``"en"``, ``"hi"``).
        output_format: Audio format (default ``"pcm"``).
        speed: Speaking speed multiplier (0.5-2.0).
        **kwargs: Forwarded to ``TTSService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: str = _DEFAULT_WS_URL,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        speaker: str = "Rajesh",
        style: str = "<Neutral>",
        language: str = "en",
        sample_rate: int = 16000,
        output_format: str = "pcm",
        speed: float = 1.0,
        **kwargs,
    ) -> None:
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._ws_url = url
        self._model = model
        self._voice = voice
        self._speaker = speaker
        self._style = style
        self._language = language
        self._sample_rate = sample_rate
        self._output_format = output_format
        self._speed = speed
        self._auth = StaticKeyAuth(self._api_key)

    def _format_text(self, text: str) -> str:
        return f"{self._speaker}: {self._style} {text}"

    def _make_tts_config(self) -> TTSConfig:
        """Build a TTSConfig from plugin settings."""
        return TTSConfig(
            model=self._model,
            voice=self._voice,
            language=self._language,
            response_format=self._output_format,
            speed=self._speed,
        )

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

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesize text via the SDK and yield audio frames."""
        formatted = self._format_text(text)
        logger.debug("ShunyalabsTTS synthesizing: %s", formatted[:80])

        yield TTSStartedFrame(context_id=context_id)

        streaming_tts = self._make_streaming_tts()
        config = self._make_tts_config()

        async for audio_bytes in streaming_tts.stream(formatted, config=config):
            yield TTSAudioRawFrame(
                audio=audio_bytes,
                sample_rate=self._sample_rate,
                num_channels=1,
                context_id=context_id,
            )

        yield TTSStoppedFrame(context_id=context_id)
