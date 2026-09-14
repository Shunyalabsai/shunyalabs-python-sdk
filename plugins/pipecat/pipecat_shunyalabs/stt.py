"""Shunyalabs STT service for Pipecat.

Maintains a persistent WebSocket connection to the Shunyalabs ASR gateway
for the lifetime of the pipeline.  Audio is streamed continuously; the
gateway's built-in VAD emits ``final_segment`` events at silence boundaries
which are surfaced as ``TranscriptionFrame``.

Uses the Shunyalabs Python SDK for transport and protocol handling.

Install::

    pip install pipecat-shunyalabs

Usage::

    from pipecat_shunyalabs import ShunyalabsSTTService

    stt = ShunyalabsSTTService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        language="auto",
    )

    pipeline = Pipeline([transport.input(), stt, ...])
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import AsyncGenerator, Optional

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language

try:
    from pipecat.services.settings import STTSettings as _STTSettings
except ImportError:
    _STTSettings = None

from shunyalabs._core._auth import TokenAuth, resolve_endpoint
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.asr._models import StreamingConfig, StreamingMessageType
from shunyalabs.asr._streaming import ASRStreamingConnection, AsyncStreamingASR

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://asrv2prod.shunyalabs.ai/v1/realtime"

# 100 ms of 16 kHz mono int16. Was 4096 (256 ms), on the belief that the gateway
# needed ~4 KB blocks for its VAD; the opposite is true. The server computes one
# RMS reading per frame it is fed, so a *larger* frame gives it coarser silence
# resolution -- which is why it defensively chops anything over ~1 s into 100 ms
# sub-frames before feeding them. Small frames pass through untouched, and every
# byte held here is latency added in front of an ASR that finalises in 39 ms.
_MIN_SEND_BYTES = 3200

# The ASR gateway may emit the detected language either as an ISO code
# (e.g. "en") or as a human-readable display name (e.g. "English"). Only
# ISO codes are accepted by ``pipecat.transcriptions.language.Language``,
# so we normalise common display names before constructing the enum and
# fall back to ``None`` if the value is still unrecognised — the frame
# itself is more valuable than a strict language tag.
_LANGUAGE_NAME_ALIASES = {
    "english": "en",
    "hindi": "hi",
    "spanish": "es",
    "french": "fr",
    "german": "de",
    "italian": "it",
    "portuguese": "pt",
    "russian": "ru",
    "japanese": "ja",
    "chinese": "zh",
    "korean": "ko",
    "arabic": "ar",
    "bengali": "bn",
    "tamil": "ta",
    "telugu": "te",
    "marathi": "mr",
    "gujarati": "gu",
    "kannada": "kn",
    "malayalam": "ml",
    "punjabi": "pa",
    "urdu": "ur",
}


def _to_language(value: Optional[str]) -> Optional[Language]:
    """Best-effort conversion of a gateway language string to ``Language``.

    Returns ``None`` for missing, ``"auto"``, or unrecognised values so
    callbacks never crash on a new / unexpected language tag.
    """
    if not value or value == "auto":
        return None
    candidate = _LANGUAGE_NAME_ALIASES.get(value.lower(), value)
    try:
        return Language(candidate)
    except ValueError:
        logger.debug(
            "ShunyalabsSTTService: unrecognised language %r from gateway", value
        )
        return None


class ShunyalabsSTTService(STTService):
    """Pipecat STT service backed by the Shunyalabs ASR gateway.

    Maintains one persistent WebSocket per pipeline run. Audio arriving from the
    pipeline is buffered into ``min_send_bytes`` blocks and forwarded.
    Transcription events are pushed back as ``TranscriptionFrame`` /
    ``InterimTranscriptionFrame``.

    **Turn-taking (opt-in).** With ``emit_turn_frames=True`` the gateway's
    ``utterance_end`` becomes ``UserStoppedSpeakingFrame`` and the first partial
    of an utterance becomes ``UserStartedSpeakingFrame``, so turn boundaries come
    from the ASR's own endpointing instead of a second VAD guessing at the same
    thing from the same audio.

    **It must be paired with pipecat's external turn strategies**, because only
    those consume these frames::

        from pipecat.processors.aggregators.llm_response_universal import (
            LLMContextAggregatorPair, LLMUserAggregatorParams,
        )
        from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

        stt = ShunyalabsSTTService(api_key=..., language="en", emit_turn_frames=True)

        agg = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=ExternalUserTurnStrategies(),
            ),
        )

    Neither half works alone, which is why this is off by default:

    * ``emit_turn_frames=True`` with the *default* strategies double-counts.
      Those consume ``VADUserStartedSpeakingFrame`` from the transport, not the
      public frame, so the aggregator broadcasts its own turn frame and ours
      passes through as well -- measured at two ``UserStartedSpeakingFrame`` per
      turn instead of one.
    * ``ExternalUserTurnStrategies`` with ``emit_turn_frames=False`` yields no
      turn signal at all, since nothing is left to produce one.

    Left at the default, this service emits no speaking frames and turn detection
    is entirely the transport VAD's job -- exactly as in 1.0.x.

    **Latency.** ``endpoint_silence_ms`` is the dominant control: it is pure
    wall-clock delay after the speaker stops before a final can be emitted.
    ``min_send_bytes`` is the other one worth knowing about -- it is buffering
    added in front of the gateway.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY``.
        language: Language code (e.g. ``"en"``, ``"hi"``) or ``"auto"``.
        url: WebSocket endpoint URL.
        sample_rate: Audio sample rate in Hz (default 16 000).
        min_send_bytes: Bytes to accumulate before forwarding. Default is 100 ms
            at 16 kHz; lower it proportionally for 8 kHz telephony.
        endpoint_silence_ms: Silence before the gateway emits a final. ``None``
            takes the server default (700 ms). Server clamps to 200-5000.
        decode_every_ms: Interim-result cadence. ``None`` takes the server
            default (640 ms). Raising it cuts per-stream GPU cost at the price of
            coarser partials. Server clamps to 320-5000.
        vad: ``"silero"`` to opt into model-based endpointing, worth it on noisy
            telephony audio where energy thresholding may never register silence.
        codeswitch: Opt into code-switch refinement (a ``final_refined`` follows
            the ``final`` with correct scripts).
        model: Explicit model/tier. ``None`` lets the gateway route on language.
        emit_turn_frames: Emit ``UserStartedSpeakingFrame`` /
            ``UserStoppedSpeakingFrame`` from the gateway's own endpointing.
            Default ``False``; requires ``ExternalUserTurnStrategies`` on the
            user aggregator. See the note above.
        **kwargs: Forwarded to ``STTService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        language: str = "auto",
        url: Optional[str] = None,
        sample_rate: int = 16000,
        min_send_bytes: int = _MIN_SEND_BYTES,
        endpoint_silence_ms: Optional[int] = None,
        decode_every_ms: Optional[int] = None,
        vad: Optional[str] = None,
        codeswitch: Optional[bool] = None,
        model: Optional[str] = None,
        emit_turn_frames: bool = False,
        **kwargs,
    ) -> None:
        # Initialize settings for pipecat >=0.0.95 (backward-compatible)
        if _STTSettings is not None:
            kwargs.setdefault("settings", _STTSettings(model=None, language=language))
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._language = language
        # `auto` is accepted, but a live stream must commit to a language from the opening
        # seconds of audio -- well before the detector has enough signal to be sure. A voice
        # agent almost always knows its language up front, and passing it removes a real
        # source of wrong-script transcripts on the first turns. Warn rather than refuse:
        # `auto` is legitimate when the language genuinely is unknown.
        if str(language).strip().lower() in ("", "auto"):
            logger.warning(
                "ShunyalabsSTTService: language=%r. Streaming language detection is "
                "best-effort because it must decide from the first seconds of audio. "
                "Pass language='hi' (or the relevant code) for reliable results.", language
            )
        # explicit arg -> env var -> built-in default; the token-provided endpoint
        # (if the service returns one) is folded in at connect time.
        self._url_arg = url
        self._ws_url = resolve_endpoint(arg=url, server=None,
                                        env_var="SHUNYALABS_ASR_WS_URL", default=_DEFAULT_WS_URL)
        self._sample_rate = sample_rate
        self._auth = TokenAuth(self._api_key)
        self._conn: Optional[ASRStreamingConnection] = None
        self._audio_buffer = bytearray()
        self._min_send_bytes = min_send_bytes
        self._endpoint_silence_ms = endpoint_silence_ms
        self._decode_every_ms = decode_every_ms
        self._vad = vad
        self._codeswitch = codeswitch
        self._model = model
        self._emit_turn_frames = emit_turn_frames
        # True between the first partial of an utterance and its utterance_end,
        # so the start/stop frames are emitted as matched pairs.
        self._speaking = False
        # Serialize reconnects so a burst of audio frames arriving while the
        # socket is down cannot spawn several concurrent reconnect attempts.
        self._reconnect_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await self._disconnect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._disconnect()
        await super().cancel(frame)

    async def _connect(self) -> None:
        """Open a streaming ASR connection via the SDK."""
        try:
            # arg -> token-provided endpoint -> env var -> default (re-resolved each connect).
            self._ws_url = resolve_endpoint(
                arg=self._url_arg, server=(await self._auth.aget_endpoints()).get("asr_ws"),
                env_var="SHUNYALABS_ASR_WS_URL", default=_DEFAULT_WS_URL)
            streaming = AsyncStreamingASR(
                auth=self._auth,
                ws_url=self._ws_url,
                ws_config=WsConnectionConfig(
                    open_timeout=10,
                    ping_interval=20,
                    ping_timeout=20,
                ),
            )

            config = StreamingConfig(
                language=self._language,
                sample_rate=self._sample_rate,
                dtype="int16",
                endpoint_silence_ms=self._endpoint_silence_ms,
                decode_every_ms=self._decode_every_ms,
                vad=self._vad,
                codeswitch=self._codeswitch,
                model=self._model,
            )

            self._conn = await streaming.stream(config=config)

            # The gateway clamps the tuning knobs and echoes the applied values.
            # Log when what took effect differs from what was asked for -- a
            # silently clamped latency setting is the kind of thing that gets
            # diagnosed as "the ASR is slow".
            applied = self._conn.effective_config
            for name, requested in (
                ("endpoint_silence_ms", self._endpoint_silence_ms),
                ("decode_every_ms", self._decode_every_ms),
            ):
                got = applied.get(name)
                if requested is not None and got is not None and int(got) != int(requested):
                    logger.warning(
                        "ShunyalabsSTTService: %s=%s was clamped to %s by the gateway.",
                        name, requested, got,
                    )

            # Capture the running event loop so callbacks fired from
            # background threads can safely schedule coroutines.
            loop = asyncio.get_running_loop()

            def _schedule(coro):
                loop.call_soon_threadsafe(asyncio.ensure_future, coro)

            @self._conn.on(StreamingMessageType.PARTIAL)
            def on_partial(msg):
                if not msg.text:
                    return
                # First partial of an utterance is the earliest speech evidence the
                # gateway gives us, so it doubles as the turn-start signal. It is
                # only as prompt as `decode_every_ms` (640 ms by default), which is
                # fine for turn-taking but too slow to drive barge-in -- keep a
                # transport VAD for that.
                if self._emit_turn_frames and not self._speaking:
                    self._speaking = True
                    _schedule(self.push_frame(UserStartedSpeakingFrame()))
                _schedule(self.push_frame(
                    InterimTranscriptionFrame(
                        text=msg.text,
                        user_id="",
                        timestamp=str(time.time()),
                        language=_to_language(msg.language),
                    )
                ))

            # NOTE: there is deliberately no FINAL_SEGMENT handler. /v1/realtime
            # never sends that event -- it belonged to the older gateway -- so the
            # handler that used to live here could not fire. The real per-utterance
            # event is FINAL, below.

            @self._conn.on(StreamingMessageType.UTTERANCE_END)
            def on_utterance_end(msg):
                # The speaker stopped. Only fires after a final whose
                # end_of_utterance is true, so it is absent when the gateway cut a
                # segment at its maximum length -- which is what makes it usable as
                # a turn boundary where a bare `final` is not.
                if self._emit_turn_frames and self._speaking:
                    self._speaking = False
                    _schedule(self.push_frame(UserStoppedSpeakingFrame()))

            @self._conn.on(StreamingMessageType.FINAL)
            def on_final(msg):
                if msg.text:
                    _schedule(self.push_frame(
                        TranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=_to_language(msg.language),
                        )
                    ))

            @self._conn.on(StreamingMessageType.FINAL_REFINED)
            def on_final_refined(msg):
                # Code-switch refinement of a segment already delivered as FINAL. Raw finals
                # are never held back waiting for this, so it arrives afterwards and is pushed
                # as its own transcription rather than being dropped.
                if msg.text:
                    _schedule(self.push_frame(
                        TranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=_to_language(msg.language),
                        )
                    ))

            @self._conn.on(StreamingMessageType.ERROR)
            def on_error(msg):
                logger.error("ShunyalabsSTTService gateway error: %s", msg.message)

            logger.info("ShunyalabsSTTService connected (session=%s)", self._conn.session_id)
        except Exception as exc:
            logger.error("ShunyalabsSTTService connection failed: %s", exc)
            raise

    async def _disconnect(self) -> None:
        """Flush remaining audio, send END, and close the connection."""
        if self._conn and not self._conn.is_closed:
            if self._audio_buffer:
                try:
                    await self._conn.send_audio(bytes(self._audio_buffer))
                except Exception:
                    pass
                self._audio_buffer.clear()
            try:
                await self._conn.end()
            except Exception:
                pass
            try:
                await self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._audio_buffer.clear()
        # Clear the speaking latch. A reconnect mid-utterance would otherwise
        # leave it stuck True, and every subsequent turn-start frame would be
        # suppressed for the life of the pipeline.
        if self._speaking:
            self._speaking = False
            if self._emit_turn_frames:
                try:
                    await self.push_frame(UserStoppedSpeakingFrame())
                except Exception:
                    pass
        logger.info("ShunyalabsSTTService disconnected")

    # ------------------------------------------------------------------
    # Turn control
    # ------------------------------------------------------------------

    async def commit(self) -> None:
        """Force a turn boundary now, without waiting out ``endpoint_silence_ms``.

        Call this from your own endpointing -- a transport VAD, a push-to-talk
        release, a semantic turn detector -- when you know the speaker is done.
        The gateway finalises the current utterance immediately and keeps the
        socket open, so the resulting ``final`` / ``utterance_end`` arrive on the
        normal event path and the next turn reuses the same connection.

        Combining this with a high ``endpoint_silence_ms`` makes the gateway's own
        timer a backstop rather than the primary signal, which is usually what a
        voice agent wants: turn-end becomes a decision you control, and it can use
        cues a silence timer cannot see.

        No-op when the connection is not open.
        """
        conn = self._conn
        if conn is None or conn.is_closed:
            return
        try:
            await conn.commit()
        except Exception as exc:  # noqa: BLE001 - never break the audio path
            logger.warning("ShunyalabsSTTService commit failed: %s", exc)

    # ------------------------------------------------------------------
    # Audio ingestion
    # ------------------------------------------------------------------

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Buffer and forward raw PCM bytes to the gateway.

        Small audio frames from the pipeline are accumulated until at
        least ``min_send_bytes`` are available, then sent as a single
        chunk. Every byte held here is latency in front of the gateway, so the
        block is deliberately small (100 ms at 16 kHz by default); the gateway
        reads silence per frame it is fed and handles small frames fine.

        Transcription results arrive asynchronously via the event
        handlers registered in :meth:`_connect`.
        """
        # Proactively re-open a dropped socket before buffering more audio,
        # so a mid-call disconnect (e.g. a transient network blip) recovers
        # transparently instead of silently dropping the rest of the utterance.
        if not self._conn or self._conn.is_closed:
            async with self._reconnect_lock:
                if not self._conn or self._conn.is_closed:
                    try:
                        await self._connect()
                    except Exception as exc:
                        logger.error("ShunyalabsSTTService reconnect failed: %s", exc)
                        yield
                        return

        self._audio_buffer.extend(audio)
        while len(self._audio_buffer) >= self._min_send_bytes:
            chunk = bytes(self._audio_buffer[:self._min_send_bytes])
            del self._audio_buffer[:self._min_send_bytes]
            try:
                await self._conn.send_audio(chunk)
            except Exception:
                logger.warning("ShunyalabsSTTService send failed; reconnecting")
                async with self._reconnect_lock:
                    await self._connect()
                await self._conn.send_audio(chunk)
        yield  # async generator — no frames yielded synchronously
