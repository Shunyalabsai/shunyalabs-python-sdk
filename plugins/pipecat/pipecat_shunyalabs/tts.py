"""Shunyalabs TTS service for Pipecat.

Connects to the Shunyalabs TTS gateway via WebSocket for each synthesis
request, streaming audio chunks back as ``TTSAudioRawFrame`` frames.

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

import json
import logging
import os
from typing import AsyncGenerator, Optional

import websockets
from pipecat.frames.frames import (
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://tts.shunyalabs.ai/ws/tts"


class ShunyalabsTTSService(TTSService):
    """Pipecat TTS service backed by the Shunyalabs TTS gateway.

    Each ``run_tts`` call opens a WebSocket, sends the text, and streams
    audio chunks back as ``TTSAudioRawFrame``.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        url: WebSocket endpoint URL.
        speaker: Speaker name prefix (e.g. ``"Rajesh"``).
        style: Emotion style tag (e.g. ``"<Happy>"``).
        language: Language code for transliteration (e.g. ``"en"``, ``"hi"``).
        output_format: Audio format (default ``"pcm"``).
        speed: Speaking speed multiplier (0.5–2.0).
        **kwargs: Forwarded to ``TTSService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: str = _DEFAULT_WS_URL,
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
        self._url = url
        self._speaker = speaker
        self._style = style
        self._language = language
        self._sample_rate = sample_rate
        self._output_format = output_format
        self._speed = speed

    def _format_text(self, text: str) -> str:
        return f"{self._speaker}: {self._style} {text}"

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        """Synthesize text via WebSocket and yield audio frames."""
        formatted = self._format_text(text)
        logger.debug("ShunyalabsTTS synthesizing: %s", formatted[:80])

        yield TTSStartedFrame(context_id=context_id)

        async with websockets.connect(
            self._url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
            additional_headers={
                "Authorization": f"Bearer {self._api_key}",
            },
        ) as ws:
            config = {
                "target_text": formatted,
                "language": self._language,
                "output_format": self._output_format,
                "request_type": "streaming",
                "speed": self._speed,
            }
            await ws.send(json.dumps(config))

            async for msg in ws:
                if isinstance(msg, bytes):
                    yield TTSAudioRawFrame(
                        audio=msg,
                        sample_rate=self._sample_rate,
                        num_channels=1,
                        context_id=context_id,
                    )
                else:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                    if msg_type in ("completion", "done", "error"):
                        if msg_type == "error":
                            logger.error(
                                "ShunyalabsTTS gateway error: %s",
                                data.get("error_message", "unknown"),
                            )
                        break

        yield TTSStoppedFrame(context_id=context_id)
