from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import AsyncGenerator, Optional

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService

try:
    from pipecat.services.settings import TTSSettings as _TTSSettings
except ImportError:
    _TTSSettings = None

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import SynthesisError
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport
from shunyalabs.tts._models import TTSConfig


_DEFAULT_WS_URL = "wss://tts.shunyalabs.ai/ws"
_CHUNK_PAYLOAD_TIMEOUT_S = 5.0

BYTES_PER_SAMPLE = 2
CHANNELS = 1
FRAME_MS = 40

# 12 × 40 ms = 480 ms pre-buffer. Bounds the worst observed server-side chunk
# gap (~280 ms) with headroom for WebRTC encoder and scheduler jitter.
MIN_BUFFER_FRAMES = 12

_SUPPORTS_CONTEXT = (
    "context_id" in inspect.signature(TTSStartedFrame.__init__).parameters
)


class ShunyalabsTTSService(TTSService):
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: str = _DEFAULT_WS_URL,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        style: Optional[str] = None,
        language: str = "en",
        sample_rate: Optional[int] = None,
        output_format: str = "pcm",
        speed: float = 1.0,
        **kwargs,
    ) -> None:
        if _TTSSettings is not None:
            kwargs.setdefault(
                "settings", _TTSSettings(model=model, voice=voice, language=language)
            )
        super().__init__(sample_rate=sample_rate, **kwargs)

        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError("Missing SHUNYALABS_API_KEY")

        self._ws_url = url
        self._model = model
        self._voice = voice
        self._style = style
        self._language = language
        self._output_format = output_format
        self._speed = speed

        self._auth = StaticKeyAuth(self._api_key)
        self._transport: Optional[WsTransport] = None
        self._transport_lock = asyncio.Lock()

        # Pacing state persists across run_tts so consecutive sentences don't
        # each re-pay the pre-buffer delay; reset in start/stop/cancel.
        self._pace_buffer: bytearray = bytearray()
        self._pace_next_time: Optional[float] = None
        self._pace_started: bool = False

    def _frame_bytes(self) -> int:
        return int(self.sample_rate * (FRAME_MS / 1000) * BYTES_PER_SAMPLE * CHANNELS)

    def _format_text(self, text: str) -> str:
        return f"{self._style} {text}" if self._style else text

    def _make_tts_config(self) -> TTSConfig:
        return TTSConfig(
            model=self._model,
            voice=self._voice,
            language=self._language,
            response_format=self._output_format,
            speed=self._speed,
        )

    async def _open_transport(self) -> WsTransport:
        transport = WsTransport(
            url=self._ws_url,
            auth=self._auth,
            conn_config=WsConnectionConfig(
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ),
            sdk_component="tts",
        )
        await transport.connect()
        return transport

    async def _ensure_transport(self) -> WsTransport:
        if self._transport is not None and not self._transport._closed:
            return self._transport

        t0 = time.monotonic()
        self._transport = await self._open_transport()
        logger.info(f"WS opened in {(time.monotonic() - t0) * 1000:.0f}ms")
        return self._transport

    async def _close_transport(self) -> None:
        if self._transport:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None
        self._pace_buffer = bytearray()
        self._pace_next_time = None
        self._pace_started = False

    async def _consume_chunk_payload(
        self, transport: WsTransport
    ) -> Optional[bytes]:
        try:
            msg = await asyncio.wait_for(
                transport.receive_message(),
                timeout=_CHUNK_PAYLOAD_TIMEOUT_S,
            )
            return msg if isinstance(msg, bytes) else None
        except asyncio.TimeoutError:
            return None

    def _build_audio_frame(
        self, audio: bytes, context_id: Optional[str]
    ) -> TTSAudioRawFrame:
        kwargs = dict(audio=audio, sample_rate=self.sample_rate, num_channels=CHANNELS)
        if _SUPPORTS_CONTEXT:
            kwargs["context_id"] = context_id
        return TTSAudioRawFrame(**kwargs)

    async def start(self, frame: StartFrame):
        await super().start(frame)
        try:
            await self._ensure_transport()
        except Exception:
            pass

    async def stop(self, frame: EndFrame):
        await self._close_transport()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame):
        await self._close_transport()
        await super().cancel(frame)

    async def run_tts(
        self, text: str, context_id: Optional[str] = None
    ) -> AsyncGenerator[Frame, None]:
        yield (
            TTSStartedFrame(context_id=context_id)
            if _SUPPORTS_CONTEXT
            else TTSStartedFrame()
        )

        async with self._transport_lock:
            transport = await self._ensure_transport()

        await transport.send_message(
            self._make_tts_config().to_request_payload(
                text=self._format_text(text),
                request_type="streaming",
            )
        )

        completed = False

        while not completed:
            msg = await transport.receive_message()
            if not isinstance(msg, dict):
                continue

            kind = msg.get("type")

            if kind == "chunk":
                # Trust the server-reported rate; it's authoritative over
                # user-supplied / transport-negotiated defaults because the
                # model emits at its native rate regardless.
                reported_rate = msg.get("sample_rate")
                if reported_rate and reported_rate != self.sample_rate:
                    if self.sample_rate:
                        logger.info(
                            f"Sample rate adopted from server: "
                            f"{self.sample_rate} → {reported_rate}"
                        )
                    self._sample_rate = reported_rate

                frame_bytes = self._frame_bytes()

                audio = await self._consume_chunk_payload(transport)
                if not audio:
                    continue

                self._pace_buffer.extend(audio)

                if not self._pace_started:
                    if len(self._pace_buffer) < frame_bytes * MIN_BUFFER_FRAMES:
                        continue
                    self._pace_started = True
                    self._pace_next_time = time.monotonic()

                while len(self._pace_buffer) >= frame_bytes:
                    now = time.monotonic()
                    if self._pace_next_time < now:
                        # Server burst outran our drain; clamp to now so we
                        # emit immediately but never faster than realtime.
                        self._pace_next_time = now
                    else:
                        await asyncio.sleep(self._pace_next_time - now)

                    chunk = bytes(self._pace_buffer[:frame_bytes])
                    del self._pace_buffer[:frame_bytes]
                    yield self._build_audio_frame(chunk, context_id)
                    self._pace_next_time += FRAME_MS / 1000

            elif kind == "completion":
                completed = True

            elif kind == "error":
                raise SynthesisError(msg.get("error"))

        yield (
            TTSStoppedFrame(context_id=context_id)
            if _SUPPORTS_CONTEXT
            else TTSStoppedFrame()
        )
