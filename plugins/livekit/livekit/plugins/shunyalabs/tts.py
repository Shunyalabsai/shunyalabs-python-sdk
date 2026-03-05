"""Shunyalabs TTS plugin for LiveKit Agents.

Supports both chunked synthesis (single text → audio) and real-time
streaming synthesis over WebSocket via the Shunyalabs TTS gateway.

Install::

    pip install livekit-plugins-shunyalabs

Usage::

    from livekit.plugins import shunyalabs

    session = AgentSession(
        tts=shunyalabs.TTS(speaker="Rajesh"),
    )
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Optional

import websockets
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

from ._version import __version__

_DEFAULT_WS_URL = "wss://tts.shunyalabs.ai/ws/tts"


class TTS(tts.TTS):
    """LiveKit Agents TTS plugin backed by the Shunyalabs TTS gateway.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        ws_url: WebSocket streaming endpoint URL.
        speaker: Speaker name prefix (e.g. ``"Rajesh"``). Prepended to text as ``"Speaker: text"``.
        style: Emotion style tag (e.g. ``"<Happy>"``). Inserted between speaker and text.
        language: Language code for transliteration (e.g. ``"en"``, ``"hi"``).
        sample_rate: Output sample rate (default 16000).
        output_format: Audio format (``"pcm"``, ``"wav"``, ``"mp3"``).
        speed: Speaking speed multiplier (0.5–2.0).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        ws_url: str = _DEFAULT_WS_URL,
        speaker: str = "Rajesh",
        style: str = "<Neutral>",
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
        self._ws_url = ws_url
        self._speaker = speaker
        self._style = style
        self._language = language
        self._output_format = output_format
        self._speed = speed

    @property
    def model(self) -> str:
        return "nirukti"

    @property
    def provider(self) -> str:
        return "shunyalabs"

    def _format_text(self, text: str) -> str:
        """Format text with speaker prefix and style tag."""
        return f"{self._speaker}: {self._style} {text}"

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
    """Single text → audio synthesis via WebSocket."""

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

        async with websockets.connect(
            self._tts._ws_url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            config = {
                "api_key": self._tts._api_key,
                "target_text": formatted,
                "language": self._tts._language,
                "output_format": self._tts._output_format,
                "request_type": "streaming",
                "speed": self._tts._speed,
            }
            await ws.send(json.dumps(config))

            async for msg in ws:
                if isinstance(msg, bytes):
                    output_emitter.push(msg)
                else:
                    data = json.loads(msg)
                    msg_type = data.get("type", "")
                    if msg_type in ("completion", "done", "error"):
                        break


class StreamingTTS(SynthesizeStream):
    """Token-by-token streaming TTS.

    Collects pushed text tokens, then on flush/end sends the accumulated
    text to the TTS gateway and streams back audio chunks.
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

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )

        async for data in self._input_ch:
            if isinstance(data, str):
                text = data.strip()
                if not text:
                    continue

                seg_id = f"{request_id}-{segment_idx}"
                segment_idx += 1
                formatted = self._tts._format_text(text)

                output_emitter.start_segment(segment_id=seg_id)

                async with websockets.connect(
                    self._tts._ws_url,
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    config = {
                        "api_key": self._tts._api_key,
                        "target_text": formatted,
                        "language": self._tts._language,
                        "output_format": self._tts._output_format,
                        "request_type": "streaming",
                        "speed": self._tts._speed,
                    }
                    await ws.send(json.dumps(config))

                    async for msg in ws:
                        if isinstance(msg, bytes):
                            output_emitter.push(msg)
                        else:
                            resp = json.loads(msg)
                            if resp.get("type") in ("completion", "done", "error"):
                                break

                output_emitter.end_segment()

        output_emitter.end_input()
