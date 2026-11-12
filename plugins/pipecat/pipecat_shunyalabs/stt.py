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
import inspect
import logging
import threading
import os
import time
from typing import AsyncGenerator, Iterable, Optional

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language

try:
    from pipecat.services.settings import STTSettings as _STTSettings
except ImportError:
    _STTSettings = None

try:
    from pipecat.audio.turn.base_turn_analyzer import BaseTurnAnalyzer, EndOfTurnState
except ImportError:  # pipecat without turn analyzers; commit_on_turn_end then cannot be enabled
    BaseTurnAnalyzer = EndOfTurnState = None

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

# P99 seconds from the transport VAD's stop to the final, when that stop commits the turn
# (commit_on_vad_stop). pipecat's turn-stop strategy waits this long for a transcript that is
# not marked finalized; its own default is a 1.0 s guess.
_TTFS_P99_S = 0.6

# Continued silence after the VAD stop before committing. pipecat's VAD stops after 0.2 s, which a
# mid-sentence pause easily reaches -- and finalising there splits the sentence into segments the
# decoder sees apart. Measured: splitting at 300 ms pauses (endpoint_silence_ms=300) doubled Hindi
# CER (10.6% -> 23.2%), and a 0.4 s delay still cost English accuracy (CER 2.2% -> 2.6%, 30 clips).
# A VAD start inside the window cancels the commit, so a pause is not a cut.
_COMMIT_DELAY_S = 0.8

# Languages commit_on_turn_end runs for unless told otherwise: Smart Turn v3.2's training languages
# (pipecat-ai/smart-turn README: 23, among them English, Hindi, Bengali and Marathi) that the gateway
# serves. Tamil, Telugu, Kannada, Malayalam, Gujarati and Punjabi are not among them. Being trained on
# a language did not make the model safe to commit on: English, Hindi and Bengali split sentences and
# lost accuracy like the others (2026-10-25, 30 FLEURS clips each, plus the same clips with a 0.6 s
# pause inserted mid-sentence).
_SMART_TURN_LANGUAGES = frozenset({"en", "hi", "bn", "mr"})

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
    wall-clock delay after the speaker stops before a final can be emitted. The
    gateway default is 700 ms on asrv2prod since 2026-11-12 (1000 ms before): the
    transcript reaches the pipeline about 0.29 s sooner than at 1000 ms, where it
    took English 1.01 s, Hindi 0.99 s and Hinglish 1.25 s (median, 30 clips each);
    WER was flat on FLEURS Hindi and English. ``commit_on_vad_stop`` can end the
    turn from the transport VAD instead; measured against the 1000 ms default it
    saved under 0.1 s and cost Hinglish accuracy, so it is off unless asked for.
    ``commit_on_turn_end`` ends it when a turn model says the speaker finished:
    about 0.6 s faster, but the model calls many mid-sentence pauses finished, so it
    splits sentences and costs accuracy in every language measured -- also off.
    ``min_send_bytes`` is the other control worth knowing about -- it is buffering
    added in front of the gateway.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY``.
        language: Language code (e.g. ``"en"``, ``"hi"``), ``"hinglish"`` for
            Hindi-English mixed speech (the gateway works out which leads), or
            ``"auto"``. Speech that is only English should go as ``"en"``.
        url: WebSocket endpoint URL.
        sample_rate: Audio sample rate in Hz (default 16 000).
        min_send_bytes: Bytes to accumulate before forwarding. Default is 100 ms
            at 16 kHz; lower it proportionally for 8 kHz telephony.
        endpoint_silence_ms: Silence before the gateway emits a final. ``None``
            takes the server default -- 700 ms on asrv2prod since 2026-11-12 (the
            ``ready`` echo reports it). Server clamps to 200-5000. Below about
            300 ms a mid-sentence pause splits the sentence and costs accuracy.
        decode_every_ms: Interim-result cadence. ``None`` takes the server
            default (1280 ms on asrv2prod). Raising it cuts per-stream GPU cost at the price of
            coarser partials. Server clamps to 320-5000.
        vad: ``"silero"`` to opt into model-based endpointing, worth it on noisy
            telephony audio where energy thresholding may never register silence.
        codeswitch: With ``"hi"`` or ``"en"``, selects the Hinglish model, which
            writes Hindi in Devanagari and English in Latin script itself (no
            ``final_refined``). With other languages, a ``final_refined`` with
            corrected scripts follows each ``final``.
        model: Explicit model/tier. ``None`` lets the gateway route on language.
        emit_turn_frames: Emit ``UserStartedSpeakingFrame`` /
            ``UserStoppedSpeakingFrame`` from the gateway's own endpointing.
            Default ``False``; requires ``ExternalUserTurnStrategies`` on the
            user aggregator. See the note above.
        commit_on_vad_stop: After ``VADUserStoppedSpeakingFrame`` and
            ``commit_delay_s`` of continued silence, send the buffered audio and
            :meth:`commit`, and mark the answering final ``finalized``. Default
            ``False``: at the gateway's former 1000 ms endpoint it measured under 0.1 s
            faster on English and Hindi and less accurate on Hinglish. Needs a
            VAD in the pipeline; without one nothing changes.
        commit_delay_s: Silence after the VAD stop before committing (default
            0.8 s). A VAD start inside it cancels the commit, so a mid-sentence
            pause does not split the sentence -- splitting at short pauses costs
            accuracy (Hindi CER doubled at 300 ms; English worse at 0.4 s).
        commit_on_turn_end: At each ``VADUserStoppedSpeakingFrame``, ask a turn
            model whether the speaker has finished, and if it says so commit at
            once and mark the answering final ``finalized``; otherwise do nothing
            and the gateway's endpoint ends the segment as before. Default
            ``False``. Needs a VAD in the pipeline. Only for ``language`` in
            ``turn_commit_languages``; an alternative to ``commit_on_vad_stop``.
            Measured live with Smart Turn v3.2 and Silero (stop 0.2 s), 30 clips
            per language: the final arrives 0.4-0.7 s after the speaker stops
            instead of about 1.0 s (gateway default then 1000 ms), but the model called 58-89% of the pauses
            inside a clip finished, so sentences are split and accuracy drops
            in all ten languages tested (English CER 1.8% -> 5.5%, Hindi
            5.4% -> 6.1%, Hinglish 22.8% -> 28.0%). Not recommended.
        turn_analyzer: The turn model for ``commit_on_turn_end``. ``None`` loads
            pipecat's ``LocalSmartTurnAnalyzerV3`` (ONNX, CPU, ~40 ms a verdict).
            It needs its own instance: an analyzer holds one stream's audio.
        turn_commit_languages: Languages ``commit_on_turn_end`` may run for.
            ``None`` means Smart Turn v3.2's training languages that the gateway
            serves (en, hi, bn, mr); any other ``language`` keeps the gateway's
            endpoint and logs a warning.
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
        commit_on_vad_stop: bool = False,
        commit_delay_s: float = _COMMIT_DELAY_S,
        commit_on_turn_end: bool = False,
        turn_analyzer: Optional[BaseTurnAnalyzer] = None,
        turn_commit_languages: Optional[Iterable[str]] = None,
        **kwargs,
    ) -> None:
        if commit_on_vad_stop and commit_on_turn_end:
            raise ValueError("commit_on_vad_stop and commit_on_turn_end are alternatives; pass one.")
        # Initialize settings for pipecat >=0.0.95 (backward-compatible)
        if _STTSettings is not None:
            kwargs.setdefault("settings", _STTSettings(model=None, language=language))
        # A committed final is measured, not guessed: tell the turn-stop strategy so it does not
        # wait its 1.0 s default for one. Only pipecat versions that know the knob get it.
        if commit_on_vad_stop and "ttfs_p99_latency" in inspect.signature(STTService.__init__).parameters:
            kwargs.setdefault("ttfs_p99_latency", _TTFS_P99_S)
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
        self._commit_on_vad_stop = commit_on_vad_stop
        self._commit_delay_s = max(0.0, float(commit_delay_s))
        self._commit_task = None       # the pending delayed commit, cancelled by a VAD start
        # Set when a VAD stop committed the turn; the next raw final is that commit's answer and
        # is marked finalized. Cleared on VAD start.
        self._awaiting_commit_final = False
        # commit_on_turn_end: the turn model, fed every audio frame labelled with the VAD's state, and
        # asked at each VAD stop. None when the option is off or the language is not in the list, and
        # then nothing below touches it -- the service behaves exactly as without the option.
        self._turn_analyzer = None
        self._vad_speaking = False
        if commit_on_turn_end:
            allowed = (_SMART_TURN_LANGUAGES if turn_commit_languages is None
                       else frozenset(str(x).strip().lower() for x in turn_commit_languages))
            if EndOfTurnState is None:
                logger.warning("ShunyalabsSTTService: commit_on_turn_end needs a pipecat with turn "
                               "analyzers; it is off.")
            elif str(language).strip().lower() not in allowed:
                logger.warning(
                    "ShunyalabsSTTService: commit_on_turn_end is off for language=%r (enabled for %s). "
                    "The gateway's endpoint ends each segment, as without the option.",
                    language, sorted(allowed),
                )
            else:
                if turn_analyzer is None:
                    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
                    turn_analyzer = LocalSmartTurnAnalyzerV3()
                self._turn_analyzer = turn_analyzer
        # True between the first partial of an utterance and its utterance_end,
        # so the start/stop frames are emitted as matched pairs.
        self._speaking = False
        # The latch is read-then-written from gateway callbacks that do not run
        # on the event loop, and `final` and `utterance_end` arrive about a
        # millisecond apart in an order the gateway does not guarantee. A plain
        # `if self._speaking:` guard is therefore not atomic: both handlers can
        # observe True and both close the turn, which produced one start frame
        # and TWO stop frames on roughly 60% of short utterances. Compare and
        # set instead, so exactly one caller can open or close a turn whatever
        # the interleaving.
        self._turn_lock = threading.Lock()
        # Serialize reconnects so a burst of audio frames arriving while the
        # socket is down cannot spawn several concurrent reconnect attempts.
        self._reconnect_lock = asyncio.Lock()
        # Ordered frame dispatch, created on connect. Declared here so a
        # disconnect that runs before any connect cannot AttributeError.
        self._dispatch_q = None
        self._dispatch_task = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        if self._turn_analyzer is not None:
            self._turn_analyzer.set_sample_rate(self.sample_rate)
        await self._connect()

    async def stop(self, frame: EndFrame) -> None:
        await self._disconnect()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._disconnect()
        await super().cancel(frame)

    async def cleanup(self) -> None:
        await super().cleanup()
        if self._turn_analyzer is not None:
            await self._turn_analyzer.cleanup()

    def _open_turn(self) -> bool:
        """Claim the turn. True only for the caller that actually opened it."""
        with self._turn_lock:
            if self._speaking:
                return False
            self._speaking = True
            return True

    def _close_turn(self) -> bool:
        """Release the turn. True only for the caller that actually closed it."""
        with self._turn_lock:
            if not self._speaking:
                return False
            self._speaking = False
            return True

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

            # ORDERED dispatch. This was `call_soon_threadsafe(ensure_future,
            # coro)`, which preserves the order frames are SCHEDULED in but not
            # the order they are DELIVERED in: `ensure_future` spawns a task per
            # frame, the tasks run concurrently, and two that suspend inside
            # `push_frame` can finish in either order.
            #
            # That lost whole clauses of a caller's sentence. The gateway is
            # strict -- it awaits a segment's `final` and only then sends
            # `utterance_end` (ws.py, same loop iteration, one socket) -- and the
            # callbacks fire in that order too. But `TranscriptionFrame` and the
            # `UserStoppedSpeakingFrame` that `utterance_end` produces became two
            # racing tasks, so the stop could land first. The aggregator then
            # closed the turn and handed the LLM everything EXCEPT that segment.
            #
            # Measured on a 28-word Hindi sentence with a 600 ms breath in it:
            # batch transcribed 28/28 words, this path delivered 17 and the agent
            # answered without the rate it had just been told. One queue, one
            # consumer, FIFO: frames reach the pipeline in the order the gateway
            # sent them. No added latency -- the work is the same, only serialised
            # -- and it fixes every event type rather than this one symptom.
            self._dispatch_q = asyncio.Queue()

            async def _dispatch_worker():
                while True:
                    coro = await self._dispatch_q.get()
                    if coro is None:            # shutdown sentinel
                        break
                    try:
                        await coro
                    except Exception:           # noqa: BLE001
                        # One bad frame must not stop the queue; a dead worker
                        # silently freezes every later turn on the call.
                        logger.exception(
                            "ShunyalabsSTTService: dispatch failed for a frame")

            self._dispatch_task = loop.create_task(_dispatch_worker())

            def _schedule(coro):
                # put_nowait is thread-safe enough only via the loop, because the
                # gateway's callbacks do not run on it.
                loop.call_soon_threadsafe(self._dispatch_q.put_nowait, coro)

            @self._conn.on(StreamingMessageType.PARTIAL)
            def on_partial(msg):
                if not msg.text:
                    return
                # First partial of an utterance is the earliest speech evidence the
                # gateway gives us, so it doubles as the turn-start signal. It is
                # only as prompt as `decode_every_ms` (640 ms by default), which is
                # fine for turn-taking but too slow to drive barge-in -- keep a
                # transport VAD for that.
                if self._emit_turn_frames and self._open_turn():
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
                if self._emit_turn_frames and self._close_turn():
                    _schedule(self.push_frame(UserStoppedSpeakingFrame()))

            @self._conn.on(StreamingMessageType.FINAL)
            def on_final(msg):
                if not msg.text:
                    # An empty final is the gateway re-endpointing on continued
                    # silence. Not speech, not a turn.
                    return

                # A final carrying text is definitive evidence of speech, and it
                # can arrive with NO partial before it: a short utterance never
                # reaches a decode tick. Measured on an 8 kHz telephony render
                # of "Five." -- 1.16 s of audio, ZERO partials, and a final with
                # text. Arming the latch only on partials therefore dropped the
                # whole turn: no start frame, so utterance_end found the latch
                # closed and emitted no stop frame either, so the aggregator
                # never released the transcript and the LLM was never called.
                #
                # Short answers are exactly the ones that matter -- "five",
                # "yes", "no", "tomorrow" -- so this is not an edge case.
                # `_open_turn` returns False when a partial already opened
                # this turn, which is the same decision the old `had_partials`
                # read made -- but taken atomically.
                opened = self._emit_turn_frames and self._open_turn()
                if opened:
                    _schedule(self.push_frame(UserStartedSpeakingFrame()))

                _schedule(self._push_transcription(
                    TranscriptionFrame(
                        text=msg.text,
                        user_id="",
                        timestamp=str(time.time()),
                        language=_to_language(msg.language),
                    ),
                    refined=False,
                ))

                if opened and self._close_turn():
                    # Nothing preceded this final, so it IS the entire turn.
                    # Close it here rather than waiting for utterance_end: the
                    # two are not guaranteed to arrive in a fixed order, and a
                    # turn left open blocks every later one. The compare-and-set
                    # means that if utterance_end got there first, it closed the
                    # turn and this does not emit a second stop frame.
                    _schedule(self.push_frame(UserStoppedSpeakingFrame()))

            @self._conn.on(StreamingMessageType.FINAL_REFINED)
            def on_final_refined(msg):
                # Code-switch refinement of a segment already delivered as FINAL. Raw finals
                # are never held back waiting for this, so it arrives afterwards and is pushed
                # as its own transcription rather than being dropped.
                if msg.text:
                    _schedule(self._push_transcription(
                        TranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=_to_language(msg.language),
                        ),
                        refined=True,
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
        await self._cancel_pending_commit()
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

        # Stop the ordered dispatcher AFTER the socket is gone, so no further
        # frames can be queued, and drain what is already queued first: the
        # final of the last turn is often the newest thing on the queue, and
        # cancelling here would drop the caller's closing words.
        if self._dispatch_q is not None and self._dispatch_task is not None:
            try:
                self._dispatch_q.put_nowait(None)      # shutdown sentinel
                await asyncio.wait_for(self._dispatch_task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                # A frame wedged downstream must not hold the call open.
                self._dispatch_task.cancel()
            except Exception:  # noqa: BLE001
                logger.exception("ShunyalabsSTTService: dispatcher shutdown failed")
        self._dispatch_q = None
        self._dispatch_task = None
        self._audio_buffer.clear()
        # Clear the speaking latch. A reconnect mid-utterance would otherwise
        # leave it stuck True, and every subsequent turn-start frame would be
        # suppressed for the life of the pipeline.
        if self._close_turn():
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

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self._turn_analyzer is not None:
            await self._track_turn_end(frame)
            return
        if not self._commit_on_vad_stop:
            return
        if isinstance(frame, VADUserStartedSpeakingFrame):
            await self._cancel_pending_commit()
            self._awaiting_commit_final = False
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            await self._cancel_pending_commit()
            self._start_commit_task(self._delayed_commit())

    def _start_commit_task(self, coro) -> None:
        make = getattr(self, "create_task", None)
        self._commit_task = make(coro) if make else asyncio.ensure_future(coro)

    async def _track_turn_end(self, frame: Frame) -> None:
        """commit_on_turn_end: ask the turn model at each VAD stop; a VAD start withdraws the question.

        VAD frames arrive downstream from a VAD in front of this service, or upstream from one in the
        user aggregator; either way they land here.
        """
        if isinstance(frame, VADUserStartedSpeakingFrame):
            self._vad_speaking = True
            self._turn_analyzer.update_vad_start_secs(frame.start_secs)
            await self._cancel_pending_commit()
            self._awaiting_commit_final = False
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._vad_speaking = False
            await self._cancel_pending_commit()
            self._start_commit_task(self._commit_if_turn_complete())

    async def _commit_if_turn_complete(self) -> None:
        """Commit now if the turn model says the speaker has finished; otherwise leave it to the gateway.

        A wrong "finished" at a mid-sentence pause splits the sentence, and the gateway decodes the two
        pieces apart -- the accuracy cost ``_COMMIT_DELAY_S`` describes. A wrong "not finished" costs
        nothing but time: the gateway's endpoint ends the segment as it would have anyway.
        """
        state, _ = await self._turn_analyzer.analyze_end_of_turn()
        if state == EndOfTurnState.COMPLETE:
            await self._commit_on_vad()

    async def _cancel_pending_commit(self) -> None:
        task, self._commit_task = self._commit_task, None
        if task is None or task.done():
            return
        cancel = getattr(self, "cancel_task", None)
        if cancel:
            await cancel(task)
        else:
            task.cancel()

    async def _delayed_commit(self) -> None:
        if self._commit_delay_s:
            await asyncio.sleep(self._commit_delay_s)
        await self._commit_on_vad()

    async def _commit_on_vad(self) -> None:
        """The speaker stopped and stayed silent: finalise now, not after the silence timer.

        The same audio reaches the same decoder; it is only finalised sooner. The tail held in
        ``_audio_buffer`` goes first, or the last words could miss the segment being finalised.
        """
        conn = self._conn
        if conn is None or conn.is_closed:
            return
        if self._audio_buffer:
            tail = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            try:
                await conn.send_audio(tail)
            except Exception as exc:  # noqa: BLE001 - the gateway's timer still ends the turn
                logger.warning("ShunyalabsSTTService: tail send before commit failed: %s", exc)
                return
        if hasattr(self, "request_finalize"):
            self.request_finalize()
        self._awaiting_commit_final = True
        await self.commit()

    async def _push_transcription(self, frame: TranscriptionFrame, refined: bool) -> None:
        """Push a final from the ordered dispatch queue, marking a commit's answer finalized.

        Runs on the queue, so the mark lands on the frame that answers the commit and never on an
        earlier final still waiting to be delivered. The answer is the first raw final after the
        commit, code-switch or not: a refinement only follows on plans that include it, and the
        ``ready`` echo does not say whether this one does, so waiting for it would hold every
        turn on the plans without it. A refinement that does arrive is pushed as before.
        """
        if self._awaiting_commit_final and not refined:
            self._awaiting_commit_final = False
            if hasattr(self, "confirm_finalize"):
                self.confirm_finalize()
        await self.push_frame(frame)

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
        if self._turn_analyzer is not None:
            # The audio the gateway gets, labelled with the VAD's view of it. Its own silence
            # timeout (append_audio returning COMPLETE) is ignored: by then the gateway has ended
            # the segment.
            self._turn_analyzer.append_audio(audio, self._vad_speaking)
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
