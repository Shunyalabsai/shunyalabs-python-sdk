"""Shunyalabs STT plugin for LiveKit Agents.

Supports both batch recognition (file/buffer) and real-time streaming
over WebSocket via the Shunyalabs ASR gateway, using the Shunyalabs
Python SDK for transport and protocol handling.

Install::

    pip install livekit-plugins-shunyalabs

Usage::

    from livekit.plugins import shunyalabs

    session = AgentSession(
        stt=shunyalabs.STT(language="en"),
        vad=silero.VAD.load(),
    )
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import wave
from typing import Optional

logger = logging.getLogger(__name__)

import httpx
from livekit import rtc
from livekit.agents import (
    APIConnectOptions,
    DEFAULT_API_CONNECT_OPTIONS,
    NOT_GIVEN,
    NotGivenOr,
    stt,
    utils,
)
from livekit.agents.stt import (
    RecognizeStream,
    STT,
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.asr._models import StreamingConfig, StreamingMessageType
from shunyalabs.asr._streaming import ASRStreamingConnection, AsyncStreamingASR

from ._version import __version__

_DEFAULT_API_URL = "https://asr.shunyalabs.ai"
_DEFAULT_WS_URL = "wss://asr.shunyalabs.ai/ws"


class STT(stt.STT):
    """LiveKit Agents STT plugin backed by the Shunyalabs ASR gateway.

    Uses the Shunyalabs Python SDK for WebSocket streaming transport.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        language: BCP-47 language tag or ``"auto"`` for auto-detection.
        api_url: REST endpoint base URL.
        ws_url: WebSocket streaming endpoint URL.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        language: str = "auto",
        api_url: str = _DEFAULT_API_URL,
        ws_url: str = _DEFAULT_WS_URL,
    ) -> None:
        super().__init__(
            capabilities=STTCapabilities(
                streaming=True,
                interim_results=True,
                offline_recognize=True,
            )
        )
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._language = language
        self._api_url = api_url.rstrip("/")
        self._ws_url = ws_url
        self._auth = StaticKeyAuth(self._api_key)

    @property
    def model(self) -> str:
        return "vak-v3"

    @property
    def provider(self) -> str:
        return "shunyalabs"

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "STTStream":
        lang = self._language if language is NOT_GIVEN else language
        return STTStream(
            stt=self,
            conn_options=conn_options,
            language=lang,
        )

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> SpeechEvent:
        """Batch transcription: POST audio to /v1/transcriptions."""
        frames = buffer if isinstance(buffer, list) else [buffer]
        pcm = b"".join(f.data.tobytes() for f in frames)
        sample_rate = frames[0].sample_rate if frames else 16000
        lang = self._language if language is NOT_GIVEN else language

        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)

        async with httpx.AsyncClient(timeout=conn_options.timeout) as client:
            resp = await client.post(
                f"{self._api_url}/v1/transcriptions",
                headers=self._auth.get_auth_headers(),
                files={"file": ("audio.wav", wav_buf.getvalue(), "audio/wav")},
                data={"language": lang},
            )
            resp.raise_for_status()
            data = resp.json()

        audio_duration = data.get("audio_duration", 0.0) or 0.0
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                SpeechData(
                    language=data.get("detected_language", lang),
                    text=data.get("text", ""),
                    confidence=1.0,
                )
            ],
            recognition_usage=stt.RecognitionUsage(audio_duration=audio_duration),
        )


class STTStream(RecognizeStream):
    """Streaming recognition via Shunyalabs SDK's AsyncStreamingASR.

    Uses the SDK's WsTransport for WebSocket connection management,
    authentication, and protocol handling. The SDK's event-based API
    is mapped to LiveKit's channel-based SpeechEvent model.
    """

    def __init__(
        self,
        *,
        stt: STT,
        conn_options: APIConnectOptions,
        language: str = "auto",
    ) -> None:
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=16000)
        self._stt = stt
        self._language = language

    async def _run(self) -> None:
        streaming = AsyncStreamingASR(
            auth=self._stt._auth,
            ws_url=self._stt._ws_url,
            ws_config=WsConnectionConfig(
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ),
        )

        config = StreamingConfig(
            language=self._language,
            sample_rate=16000,
            dtype="int16",
        )

        conn = await streaming.stream(config=config)

        try:
            # Register event handlers that push to LiveKit's event channel
            @conn.on(StreamingMessageType.PARTIAL)
            def on_partial(msg):
                if msg.text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.INTERIM_TRANSCRIPT,
                            alternatives=[SpeechData(
                                language=msg.language or self._language,
                                text=msg.text,
                            )],
                        )
                    )

            @conn.on(StreamingMessageType.FINAL_SEGMENT)
            def on_final_segment(msg):
                if msg.text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(
                                language=msg.language or self._language,
                                text=msg.text,
                                confidence=1.0,
                            )],
                        )
                    )
                    self._event_ch.send_nowait(
                        SpeechEvent(type=SpeechEventType.END_OF_SPEECH)
                    )

            @conn.on(StreamingMessageType.FINAL)
            def on_final(msg):
                if msg.text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(
                                language=msg.language or self._language,
                                text=msg.text,
                                confidence=1.0,
                            )],
                        )
                    )
                audio_dur = msg.audio_duration_sec or 0.0
                self._event_ch.send_nowait(
                    SpeechEvent(
                        type=SpeechEventType.RECOGNITION_USAGE,
                        recognition_usage=stt.RecognitionUsage(audio_duration=audio_dur),
                    )
                )

            # Send audio from LiveKit's input channel to the SDK connection
            async for data in self._input_ch:
                if isinstance(data, rtc.AudioFrame):
                    pcm = data.data.tobytes()
                    await conn.send_audio(pcm)

            # Input exhausted — signal end of stream
            await conn.end()

        finally:
            await conn.close()
