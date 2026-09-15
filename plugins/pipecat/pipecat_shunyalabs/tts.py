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

from shunyalabs._core._auth import TokenAuth, resolve_endpoint
from shunyalabs._core._exceptions import SynthesisError
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport


_DEFAULT_WS_URL = "wss://ttsv2.shunyalabs.ai/v1/realtime"
_CHUNK_PAYLOAD_TIMEOUT_S = 5.0

BYTES_PER_SAMPLE = 2
CHANNELS = 1
FRAME_MS = 40

# 8 × 40 ms = 320 ms pre-buffer. Still bounds the worst observed server-side chunk
# gap (~280 ms) so a WebRTC encoder (Daily, LiveKit) does not starve audibly, but
# trims 160 ms off the first-audio budget versus the old 480 ms default. Note the
# pre-buffer is NOT the first-sound bottleneck on a persistent connection -- warm
# turns are ~500 ms regardless (the generator runs ~83x realtime, so the buffer
# fills near-instantly); only the first turn pays the WebSocket connect.
#
# Transports without an encoder -- a raw telephony WebSocket, where the carrier
# holds its own playback buffer -- can safely run lower still: pass
# `min_buffer_frames=3` (120 ms). Both this and `frame_ms` are constructor args.
MIN_BUFFER_FRAMES = 8

# How long to wait for the server's `cancelled` acknowledgement on barge-in
# before giving up and dropping the socket instead.
_CANCEL_ACK_TIMEOUT_S = 1.0

_SUPPORTS_CONTEXT = (
    "context_id" in inspect.signature(TTSStartedFrame.__init__).parameters
)


class ShunyalabsTTSService(TTSService):
    """Pipecat TTS service backed by the Shunyalabs ``/v1/realtime`` gateway.

    Holds one persistent WebSocket across turns, paces audio out at ~realtime,
    and recovers from barge-in without reconnecting.

    **Latency knobs, in the order they are worth reaching for:**

    ``min_buffer_frames``
        Frames to accumulate before releasing the first audio of a session.
        Defaults to 12 (480 ms at the default ``frame_ms``), sized for WebRTC
        transports where an encoder starves audibly. A telephony WebSocket has no
        encoder and the carrier buffers for you, so 3 (120 ms) is usually safe
        there -- but measure, because an underrun is audible too.

    ``clause_first``
        Release the first piece on a clause boundary instead of waiting for the
        opening sentence to terminate. Cuts first-audio noticeably on long
        replies and does nothing for short ones. Off by default: splitting a
        sentence means synthesising it in two pieces, which can be audible at the
        join, so it is your call rather than ours.

    ``quality``
        ``"low"`` | ``"medium"`` | ``"high"``. ``"low"`` roughly halves the
        diffusion work and is the single biggest lever on first-audio -- but it
        drops a word in a measurable fraction of short clips, so it does not
        belong anywhere a number, date or reference code is being read out.
        Sensible for throwaway acknowledgements, not for content.

        Note this is a **session** setting, not per utterance: the gateway reads
        it from the init frame. Mixing qualities in one call means two service
        instances, one per quality.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY``.
        url: WebSocket endpoint URL.
        model: TTS model (default ``"zero-indic"``).
        voice: Voice name (default ``"Rajesh"``).
        style: Optional style tag, prepended to each utterance's text.
        language: Language code (default ``"en"``).
        sample_rate: Overridden by whatever the server reports in ``ready``.
        output_format: Accepted for API compatibility; inert on the realtime path.
        speed: Accepted for API compatibility; inert on the realtime path.
        quality: See above. ``None`` takes the server default (medium).
        clause_first: See above. ``None`` takes the server default (off).
        min_buffer_frames: See above.
        frame_ms: Size of each emitted audio frame, in ms (default 40).
        **kwargs: Forwarded to ``TTSService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: Optional[str] = None,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        style: Optional[str] = None,
        language: str = "en",
        sample_rate: Optional[int] = None,
        output_format: str = "pcm",
        speed: float = 1.0,
        quality: Optional[str] = None,
        clause_first: Optional[bool] = None,
        min_buffer_frames: int = MIN_BUFFER_FRAMES,
        frame_ms: int = FRAME_MS,
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

        # explicit arg -> env var -> built-in default; the token-provided endpoint
        # (if the service returns one) is folded in at connect time.
        self._url_arg = url
        self._ws_url = resolve_endpoint(arg=url, server=None,
                                        env_var="SHUNYALABS_TTS_WS_URL", default=_DEFAULT_WS_URL)
        self._model = model
        self._voice = voice
        self._style = style
        self._language = language
        self._output_format = output_format
        self._speed = speed
        self._quality = quality
        self._clause_first = clause_first
        self._min_buffer_frames = int(min_buffer_frames)
        self._frame_ms = int(frame_ms)

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
        return int(self.sample_rate * (self._frame_ms / 1000) * BYTES_PER_SAMPLE * CHANNELS)

    def _format_text(self, text: str) -> str:
        return f"{self._style} {text}" if self._style else text

    async def _open_transport(self) -> WsTransport:
        # arg -> token-provided endpoint -> env var -> default (re-resolved each
        # connect so a control-plane endpoint change is picked up with no release).
        self._ws_url = resolve_endpoint(
            arg=self._url_arg, server=(await self._auth.aget_endpoints()).get("tts_ws"),
            env_var="SHUNYALABS_TTS_WS_URL", default=_DEFAULT_WS_URL)
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
        init = {"voice": self._voice, "language": self._language, "model": self._model}
        # Both are session-level on the service, so they go in the init frame and
        # apply for the life of the socket. Omitted when unset so the server's own
        # defaults apply.
        if self._quality is not None:
            init["quality"] = self._quality
        if self._clause_first is not None:
            init["clause_first"] = self._clause_first
        await self._transport.send_message(init)
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
        """Barge-in: discard the interrupted synthesis but keep the session.

        An interruption cancels the in-flight ``run_tts`` generator mid-stream,
        leaving unread binary and ``done`` frames buffered on the shared socket.
        Reusing it blindly would desync the next turn -- it would read the stale
        ``done`` and stop early.

        Previously this dropped the socket, which is always correct but costs a
        full reconnect: TCP+TLS, a fresh ``ready`` handshake, and re-paying the
        pre-buffer on the next turn. On a barge-in-heavy call that is the most
        expensive thing the service does, and it lands exactly when the user is
        waiting to be answered.

        The protocol already has the right primitive: ``{"type": "cancel"}``
        discards buffered text and in-flight audio, and the server acknowledges
        with ``cancelled`` after bumping its generation counter -- so anything
        from the interrupted turn is suppressed at the source rather than left
        for us to untangle. Draining up to that ack leaves the socket clean and
        reusable.

        Falls back to the old drop-the-socket behaviour whenever the fast path
        cannot be taken safely -- notably if ``run_tts`` still holds the
        transport lock, since two concurrent readers on one socket would be worse
        than a reconnect.
        """
        await super()._handle_interruption(frame, direction)
        await self.discard_current_turn()

    async def discard_current_turn(self) -> None:
        """Throw away the in-flight utterance, keeping the session if possible.

        Called on barge-in. Public because the same operation is useful directly:
        a caller that detects an interruption before pipecat does (its own VAD on
        the inbound leg, say) can stop the bot talking without waiting for the
        frame to propagate.

        Never raises -- any uncertainty about socket state ends in a clean
        reconnect instead.
        """
        transport = self._transport
        if transport is None or not self._session_ready:
            await self._close_transport()
            return

        # Do not read from the socket while run_tts may still be reading it.
        try:
            await asyncio.wait_for(
                self._transport_lock.acquire(), timeout=_CANCEL_ACK_TIMEOUT_S
            )
        except Exception:  # noqa: BLE001 - includes the acquire timeout
            await self._close_transport()
            return

        try:
            await transport.send_message({"type": "cancel"})
            deadline = time.monotonic() + _CANCEL_ACK_TIMEOUT_S
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                msg = await asyncio.wait_for(
                    transport.receive_message(), timeout=remaining
                )
                # Everything still arriving belongs to the interrupted turn:
                # trailing PCM, and its `speaking`/`done`. Drop it all.
                if isinstance(msg, dict) and msg.get("type") == "cancelled":
                    break
        except Exception:  # noqa: BLE001 - any doubt about socket state -> rebuild
            self._transport_lock.release()
            await self._close_transport()
            return

        # Session is clean and stays open. Pacing still resets: the buffered PCM
        # belongs to the interrupted utterance and must not be spoken, and
        # `_pace_next_time` has to be cleared alongside `_pace_started` or the
        # pacing loop compares None to a timestamp.
        #
        # So the next turn re-pays the pre-buffer but NOT the reconnect. That is
        # the expensive half: a TCP+TLS dial plus a fresh `ready` handshake to a
        # remote endpoint, all of it while the caller waits. Pair this with a
        # lower `min_buffer_frames` on transports that do not need 480 ms and
        # barge-in recovery costs almost nothing.
        self._pace_buffer = bytearray()
        self._pace_next_time = None
        self._pace_started = False
        self._transport_lock.release()

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
                        if len(self._pace_buffer) < frame_bytes * self._min_buffer_frames:
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
                        self._pace_next_time += self._frame_ms / 1000
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
