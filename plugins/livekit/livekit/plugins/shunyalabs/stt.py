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

from shunyalabs._core._auth import TokenAuth, resolve_endpoint
from shunyalabs._core._http_transport import AsyncHttpTransport
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.asr._batch import AsyncBatchASR
from shunyalabs.asr._models import StreamingConfig, StreamingMessageType, TranscriptionConfig
from shunyalabs.asr._streaming import ASRStreamingConnection, AsyncStreamingASR

from ._version import __version__

_DEFAULT_API_URL = "https://asrv2prod.shunyalabs.ai"
_DEFAULT_WS_URL = "wss://asrv2prod.shunyalabs.ai/v1/realtime"


class STT(stt.STT):
    """LiveKit Agents STT plugin backed by the Shunyalabs ASR gateway.

    Uses the Shunyalabs Python SDK for WebSocket streaming transport.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        language: BCP-47 language tag or ``"auto"`` for auto-detection.
        api_url: REST endpoint base URL.
        ws_url: WebSocket streaming endpoint URL.
        endpoint_silence_ms: Silence before the gateway emits a final, and so the
            dominant control over how long a speaker waits after finishing.
            ``None`` takes the server default (700 ms); clamped to 200-5000.
        decode_every_ms: Interim-result cadence. ``None`` takes the server
            default (640 ms); clamped to 320-5000. Raising it lowers per-stream
            GPU cost at the price of coarser interim transcripts.
        vad: ``"silero"`` to opt into model-based endpointing. Worth it on noisy
            telephony audio, where energy thresholding can fail to register
            silence at all and natural endpointing then never fires.
        codeswitch: Opt into code-switch refinement, delivered as an additional
            final transcript once the segment has been re-rendered in the correct
            scripts.
        model: Explicit model/tier. ``None`` lets the gateway route on language.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        language: str = "auto",
        api_url: Optional[str] = None,
        ws_url: Optional[str] = None,
        endpoint_silence_ms: Optional[int] = None,
        decode_every_ms: Optional[int] = None,
        vad: Optional[str] = None,
        codeswitch: Optional[bool] = None,
        model: Optional[str] = None,
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
        if str(language).strip().lower() in ("", "auto"):
            # See the pipecat plugin: streaming detection must decide from the opening
            # seconds of audio, so it is best-effort. Warn, do not refuse -- `auto` is
            # legitimate when the language genuinely is unknown.
            logger.warning(
                "Shunyalabs STT: language=%r. Streaming language detection is "
                "best-effort because it must decide from the first seconds of audio. "
                "Pass an explicit language code for reliable results.", language
            )
        # explicit arg -> env var -> built-in default (repoint without a code change)
        self._api_url_arg = api_url
        self._ws_url_arg = ws_url
        self._api_url = resolve_endpoint(arg=api_url, server=None,
                                         env_var="SHUNYALABS_ASR_URL", default=_DEFAULT_API_URL).rstrip("/")
        self._ws_url = resolve_endpoint(arg=ws_url, server=None,
                                        env_var="SHUNYALABS_ASR_WS_URL", default=_DEFAULT_WS_URL)
        self._auth = TokenAuth(self._api_key)
        self._endpoint_silence_ms = endpoint_silence_ms
        self._decode_every_ms = decode_every_ms
        self._vad = vad
        self._codeswitch = codeswitch
        self._model = model

    @property
    def model(self) -> str:
        return self._model or "vak-v3"

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

    async def _resolve_urls(self) -> None:
        """arg -> token-provided endpoint -> env var -> default (folded in at use time)."""
        eps = await self._auth.aget_endpoints()
        self._api_url = resolve_endpoint(arg=self._api_url_arg, server=eps.get("asr_http"),
                                         env_var="SHUNYALABS_ASR_URL", default=_DEFAULT_API_URL).rstrip("/")
        self._ws_url = resolve_endpoint(arg=self._ws_url_arg, server=eps.get("asr_ws"),
                                        env_var="SHUNYALABS_ASR_WS_URL", default=_DEFAULT_WS_URL)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> SpeechEvent:
        """Batch transcription via the SDK's AsyncBatchASR."""
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
        wav_buf.seek(0)
        wav_buf.name = "audio.wav"

        await self._resolve_urls()
        transport = AsyncHttpTransport(
            url=self._api_url,
            auth=self._auth,
        )
        batch = AsyncBatchASR(auth=self._auth, transport=transport)
        try:
            config = TranscriptionConfig(
                model="zero-indic",
                language_code=lang,
            )
            result = await batch.transcribe(audio=wav_buf, config=config)
        finally:
            await batch.close()

        audio_duration = result.audio_duration or 0.0
        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                SpeechData(
                    language=result.detected_language or lang,
                    text=result.text,
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
        await self._stt._resolve_urls()
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
            endpoint_silence_ms=self._stt._endpoint_silence_ms,
            decode_every_ms=self._stt._decode_every_ms,
            vad=self._stt._vad,
            codeswitch=self._stt._codeswitch,
            model=self._stt._model,
        )

        conn = await streaming.stream(config=config)

        # The gateway clamps these and echoes the applied values; a silently
        # clamped latency setting otherwise gets diagnosed as "the ASR is slow".
        applied = conn.effective_config
        for _name, _requested in (
            ("endpoint_silence_ms", self._stt._endpoint_silence_ms),
            ("decode_every_ms", self._stt._decode_every_ms),
        ):
            _got = applied.get(_name)
            if _requested is not None and _got is not None and int(_got) != int(_requested):
                logger.warning(
                    "Shunyalabs STT: %s=%s was clamped to %s by the gateway.",
                    _name, _requested, _got,
                )

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

            # NOTE: FINAL_SEGMENT is not handled. /v1/realtime never sends it --
            # it belonged to the older gateway. END_OF_SPEECH used to be emitted
            # only from inside that handler, which meant this plugin produced no
            # end-of-speech event at all on the current gateway. It now comes from
            # UTTERANCE_END below, which is the real signal.

            # END_OF_SPEECH is emitted from the FINAL handler below, not from
            # utterance_end, for two measured reasons:
            #
            #  1. Ordering between `final` and `utterance_end` is NOT guaranteed
            #     -- they are observed in both orders, 1 ms apart. Driving
            #     END_OF_SPEECH off utterance_end therefore emitted it BEFORE the
            #     transcript, which inverts LiveKit's convention and lets an agent
            #     act before it has the text.
            #  2. The gateway re-endpoints on continued silence: an EMPTY final
            #     with end_of_utterance=true plus an utterance_end, roughly every
            #     800 ms for as long as the line stays quiet (measured: 4 of them
            #     in 6 s of trailing silence). A caller who simply stops talking
            #     would generate a stream of end-of-speech events.
            #
            # Keying off a final that actually carries text is immune to both.

            @conn.on(StreamingMessageType.FINAL_REFINED)
            def on_final_refined(msg):
                # Code-switch refinement of a segment already delivered as FINAL,
                # re-rendered in the correct scripts. Delivered as its own
                # transcript rather than dropped.
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

            @conn.on(StreamingMessageType.FINAL)
            def on_final(msg):
                if not msg.text:
                    # An empty final is the gateway re-endpointing on silence.
                    # It carries no transcript and is not a turn boundary, so it
                    # produces no events at all -- including no usage, which would
                    # otherwise tick once per ~800 ms of quiet.
                    return

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
                # `end_of_utterance is not False` rather than `is True`: a gateway
                # that does not send the field at all should still close the turn,
                # since a final with text is the best endpoint signal available.
                if msg.end_of_utterance is not False:
                    self._event_ch.send_nowait(
                        SpeechEvent(type=SpeechEventType.END_OF_SPEECH)
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
