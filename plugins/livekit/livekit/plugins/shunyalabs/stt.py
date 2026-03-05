"""Shunyalabs STT plugin for LiveKit Agents.

Supports both batch recognition (file/buffer) and real-time streaming
over WebSocket via the Shunyalabs ASR gateway.

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
import json
import os
import wave
from typing import Optional

import httpx
import websockets
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

from ._version import __version__

_DEFAULT_API_URL = "https://asr.shunyalabs.ai"
_DEFAULT_WS_URL = "wss://asr.shunyalabs.ai/ws"

# Gateway message type → LiveKit SpeechEventType
_MSG_TO_EVENT: dict[str, SpeechEventType] = {
    "partial": SpeechEventType.INTERIM_TRANSCRIPT,
    "final_segment": SpeechEventType.FINAL_TRANSCRIPT,
    "final": SpeechEventType.FINAL_TRANSCRIPT,
}


class STT(stt.STT):
    """LiveKit Agents STT plugin backed by the Shunyalabs ASR gateway.

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
        return STTStream(stt=self, conn_options=conn_options, language=lang)

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
                headers={"Authorization": f"Bearer {self._api_key}"},
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
    """Streaming recognition via Shunyalabs WebSocket ASR gateway.

    The base class handles audio resampling to 16 kHz before frames
    reach ``_run()``.  Audio is forwarded as raw int16 PCM bytes.
    Gateway partials map to ``INTERIM_TRANSCRIPT`` events; each
    ``final_segment`` (silence-detected boundary) maps to
    ``FINAL_TRANSCRIPT + END_OF_SPEECH``; the overall ``final``
    emits a ``RECOGNITION_USAGE`` event.
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
        ws_url = self._stt._ws_url

        async with websockets.connect(
            ws_url,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            # Handshake
            await ws.send(
                json.dumps(
                    {
                        "language": self._language,
                        "sample_rate": 16000,
                        "dtype": "int16",
                        "api_key": self._stt._api_key,
                    }
                )
            )
            raw_ready = await asyncio.wait_for(ws.recv(), timeout=15.0)
            ready = json.loads(raw_ready)
            if ready.get("type") != "ready":
                raise RuntimeError(f"Expected 'ready', got: {ready}")

            # Run sender and receiver concurrently
            await asyncio.gather(
                self._send_loop(ws),
                self._recv_loop(ws),
            )

    async def _send_loop(self, ws: websockets.ClientConnection) -> None:
        """Read from input channel and forward PCM bytes to the gateway."""
        async for data in self._input_ch:
            if isinstance(data, rtc.AudioFrame):
                await ws.send(data.data.tobytes())
            # FlushSentinel: gateway has its own VAD/silence detection; no action needed

        # Input channel exhausted (end_input() called) — signal end of stream
        await ws.send("END")

    async def _recv_loop(self, ws: websockets.ClientConnection) -> None:
        """Read gateway messages and push them as SpeechEvents."""
        async for raw in ws:
            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            text = msg.get("text", "")
            lang = msg.get("language") or self._language

            if msg_type == "partial":
                if text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.INTERIM_TRANSCRIPT,
                            alternatives=[SpeechData(language=lang, text=text)],
                        )
                    )

            elif msg_type == "final_segment":
                if text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(language=lang, text=text, confidence=1.0)],
                        )
                    )
                    self._event_ch.send_nowait(
                        SpeechEvent(type=SpeechEventType.END_OF_SPEECH)
                    )

            elif msg_type == "final":
                if text:
                    self._event_ch.send_nowait(
                        SpeechEvent(
                            type=SpeechEventType.FINAL_TRANSCRIPT,
                            alternatives=[SpeechData(language=lang, text=text, confidence=1.0)],
                        )
                    )
                audio_dur = msg.get("audio_duration_sec", 0.0) or 0.0
                self._event_ch.send_nowait(
                    SpeechEvent(
                        type=SpeechEventType.RECOGNITION_USAGE,
                        recognition_usage=stt.RecognitionUsage(audio_duration=audio_dur),
                    )
                )

            elif msg_type in ("done", "error"):
                break
