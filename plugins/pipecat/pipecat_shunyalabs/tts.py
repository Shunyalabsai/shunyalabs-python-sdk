"""Shunyalabs TTS service for Pipecat (v2 real-time gateway).

Streams synthesised speech over a single persistent WebSocket to the Shunya
Labs real-time TTS service (``ttsv2``). The handshake mints a short-lived
access token from your API key (never the raw key) and sends a JSON init frame
``{voice, language, model}``; the service replies ``{"type": "ready",
"sample_rate": ...}`` and the session is reused for every turn.

Per turn, each sentence is sent as ``{"type": "text", ...}`` followed by
``{"type": "flush"}`` so it starts speaking immediately (low first-audio
latency); the service answers with ``{"type": "speaking"}``, binary PCM
(24 kHz, 16-bit mono), and ``{"type": "done"}``. Audio is re-chunked into
fixed 40 ms frames emitted at wall-clock rate to prevent WebRTC encoder
starvation. On barge-in the socket is reset so no stale audio leaks into the
next turn, and the real-time service answers WebSocket pings so ``ping_interval``
keeps the socket alive on idle with no application-level keepalive.

Install::

    pip install pipecat-shunyalabs

Usage::

    from pipecat_shunyalabs import ShunyalabsTTSService

    tts = ShunyalabsTTSService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        voice="Nisha",
        language="en",
    )
"""

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
    InterruptionFrame,
    StartFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.tts_service import TTSService

try:
    from pipecat.services.settings import TTSSettings as _TTSSettings
except ImportError:
    _TTSSettings = None

from shunyalabs._core._auth import TokenAuth
from shunyalabs._core._exceptions import SynthesisError
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport


_DEFAULT_WS_URL = "wss://ttsv2.shunyalabs.ai/v1/realtime"
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

        # ASR/TTS v2 services accept only a minted short-lived JWT, never the raw
        # key. TokenAuth mints and refreshes it transparently.
        self._auth = TokenAuth(self._api_key)
        self._transport: Optional[WsTransport] = None
        self._transport_lock = asyncio.Lock()
        self._session_ready = False   # init frame sent + "ready" received for the current transport

        # Pacing state persists across run_tts so consecutive sentences don't
        # each re-pay the pre-buffer delay; reset in start/stop/cancel.
        self._pace_buffer: bytearray = bytearray()
        self._pace_next_time: Optional[float] = None
        self._pace_started: bool = False

    def _frame_bytes(self) -> int:
        return int(self.sample_rate * (FRAME_MS / 1000) * BYTES_PER_SAMPLE * CHANNELS)

    def _format_text(self, text: str) -> str:
        return f"{self._style} {text}" if self._style else text

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
        if self._transport is not None and not self._transport._closed and self._session_ready:
            return self._transport

        t0 = time.monotonic()
        self._transport = await self._open_transport()
        # /v1/realtime handshake: the FIRST frame is a JSON init object; the server replies with
        # {"type":"ready","sample_rate":...}. The session then stays open for many text/flush turns.
        await self._transport.send_message(
            {"voice": self._voice, "language": self._language, "model": self._model}
        )
        ready = await asyncio.wait_for(self._transport.receive_message(), timeout=15.0)
        if not isinstance(ready, dict) or ready.get("type") != "ready":
            if isinstance(ready, dict) and ready.get("type") == "error":
                raise SynthesisError(ready.get("error"))
            raise SynthesisError(f"expected 'ready', got {ready}")
        rate = ready.get("sample_rate")
        if rate:
            self._sample_rate = rate
        self._session_ready = True
        logger.info(
            f"WS opened + ready in {(time.monotonic() - t0) * 1000:.0f}ms (rate={self.sample_rate})"
        )
        return self._transport

    async def _close_transport(self) -> None:
        if self._transport:
            try:
                # Orderly close: the bare-string "end" frame tells the server to finish and close.
                if self._session_ready and self._transport.is_connected:
                    await self._transport.send_message("end")
            except Exception:
                pass
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None
        self._session_ready = False
        self._pace_buffer = bytearray()
        self._pace_next_time = None
        self._pace_started = False

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

    async def _handle_interruption(self, frame: InterruptionFrame, direction: FrameDirection):
        """Barge-in: reset the socket so the interrupted synthesis' remaining
        audio and its ``done`` cannot leak into the next turn.

        A ``TTSService`` interruption cancels the in-flight ``run_tts`` generator
        mid-stream, leaving unread binary/``done`` frames buffered on the shared
        socket. Reusing that socket would desync the next turn (it would read the
        stale ``done`` and stop early). Dropping the socket is the simplest
        guaranteed-clean recovery — the next ``run_tts`` transparently rebuilds
        it, and the reconnect overlaps the natural STT+LLM gap before the bot
        speaks again. The v2 service stops generating as soon as the socket
        closes, so no extra ``cancel`` message is required.
        """
        await super()._handle_interruption(frame, direction)
        await self._close_transport()

    async def run_tts(
        self, text: str, context_id: Optional[str] = None
    ) -> AsyncGenerator[Frame, None]:
        yield (
            TTSStartedFrame(context_id=context_id)
            if _SUPPORTS_CONTEXT
            else TTSStartedFrame()
        )

        async with self._transport_lock:
            # Speak this text now: append it, then flush. `flush` speaks the buffer and returns a
            # `done`, leaving the session open for the next turn. Style tags ride inline in the text.
            # A persistent socket can be closed server-side (idle/token expiry); the client-side
            # `_closed` flag would not reflect that, so the first send is the earliest place we learn
            # the session is dead — rebuild once and retry so a dropped session is recovered
            # transparently rather than surfacing as a hard error mid-conversation.
            async def _send_turn():
                t = await self._ensure_transport()
                await t.send_message({"type": "text", "text": self._format_text(text)})
                await t.send_message({"type": "flush"})
                return t

            try:
                transport = await _send_turn()
            except Exception:  # noqa: BLE001
                await self._close_transport()
                transport = await _send_turn()

            completed = False
            while not completed:
                msg = await transport.receive_message()

                if isinstance(msg, (bytes, bytearray)):
                    # Raw PCM for a spoken piece. Pace it out at ~realtime so a server burst does not
                    # overrun the transport; the pre-buffer is paid once and persists across turns.
                    frame_bytes = self._frame_bytes()
                    self._pace_buffer.extend(msg)
                    if not self._pace_started:
                        if len(self._pace_buffer) < frame_bytes * MIN_BUFFER_FRAMES:
                            continue
                        self._pace_started = True
                        self._pace_next_time = time.monotonic()
                    while len(self._pace_buffer) >= frame_bytes:
                        now = time.monotonic()
                        if self._pace_next_time < now:
                            self._pace_next_time = now
                        else:
                            await asyncio.sleep(self._pace_next_time - now)
                        chunk = bytes(self._pace_buffer[:frame_bytes])
                        del self._pace_buffer[:frame_bytes]
                        yield self._build_audio_frame(chunk, context_id)
                        self._pace_next_time += FRAME_MS / 1000
                    continue

                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "speaking":
                    reported_rate = msg.get("sample_rate")
                    if reported_rate and reported_rate != self.sample_rate:
                        self._sample_rate = reported_rate
                elif kind == "done":
                    # Emit any sub-frame remainder so no audio is dropped at the utterance tail.
                    if self._pace_buffer:
                        yield self._build_audio_frame(bytes(self._pace_buffer), context_id)
                        self._pace_buffer = bytearray()
                    completed = True
                elif kind == "error":
                    raise SynthesisError(msg.get("error"))

        yield (
            TTSStoppedFrame(context_id=context_id)
            if _SUPPORTS_CONTEXT
            else TTSStoppedFrame()
        )
