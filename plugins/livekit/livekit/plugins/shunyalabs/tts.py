"""Shunyalabs TTS plugin for LiveKit Agents.

Supports both chunked synthesis (single text -> audio) and real-time
streaming synthesis over WebSocket via the Shunyalabs TTS gateway,
using the Shunyalabs Python SDK for transport and protocol handling.

Streaming keeps ``/v1/realtime`` sessions open in a connection pool and forwards
text to the gateway as the LLM produces it. The gateway speaks each sentence as
soon as it is complete, so the first audio no longer waits for the whole reply,
and a turn no longer pays a fresh TCP + TLS + ``ready`` handshake (measured from
a laptop: ~0.37 s of every segment went to connect + ready before any text).

Install::

    pip install livekit-plugins-shunyalabs

Usage::

    from livekit.plugins import shunyalabs

    session = AgentSession(
        tts=shunyalabs.TTS(voice="Rajesh"),
    )
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections import deque
from typing import Optional

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    DEFAULT_API_CONNECT_OPTIONS,
    tts,
    utils,
)
from livekit.agents.tts import (
    AudioEmitter,
    ChunkedStream,
    SynthesizeStream,
    TTS,
    TTSCapabilities,
)

from shunyalabs._core._auth import TokenAuth, resolve_endpoint
from shunyalabs._core._http_transport import AsyncHttpTransport
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs._core._ws_transport import WsTransport
from shunyalabs.tts._batch import AsyncBatchTTS
from shunyalabs.tts._models import TTSConfig
from shunyalabs.tts._streaming import AsyncStreamingTTS

from ._version import __version__

logger = logging.getLogger(__name__)

_DEFAULT_API_URL = "https://ttsv2.shunyalabs.ai"
_DEFAULT_WS_URL = "wss://ttsv2.shunyalabs.ai/v1/realtime"

# How long to wait for the gateway's `cancelled` acknowledgement after an
# interrupted turn before giving up on the socket and closing it instead.
_CANCEL_ACK_TIMEOUT_S = 1.0
# Pooled sessions are recycled after this long even if healthy, so a minted
# short-lived token is never carried past its lifetime on an idle socket.
_MAX_SESSION_S = 300.0


class _Session:
    """One ready ``/v1/realtime`` session: socket open, init sent, ``ready`` received."""

    __slots__ = ("transport", "sample_rate")

    def __init__(self, transport: WsTransport, sample_rate: Optional[int]) -> None:
        self.transport = transport
        self.sample_rate = sample_rate


class _StaleSession(Exception):
    """A pooled socket failed before this turn got any audio back: safe to retry on a new one."""


class TTS(tts.TTS):
    """LiveKit Agents TTS plugin backed by the Shunyalabs TTS gateway.

    Uses the Shunyalabs Python SDK for WebSocket streaming transport.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        ws_url: WebSocket streaming endpoint URL.
        voice: Speaker voice name (e.g. ``"Rajesh"``, ``"Nisha"``).
        style: Emotion style tag (e.g. ``"<Happy>"``). Prepended to text.
            The gateway handles the speaker prefix and default style internally.
        language: ISO 639 language code (e.g. ``"en"``, ``"hi"``). Required.
        sample_rate: Output sample rate (default 24000 — the gateway emits
            24 kHz PCM on both the streaming and batch paths; overriding this
            without resampling the audio yourself will shift pitch/tempo).
        output_format: Audio format for the batch path (``"pcm"``, ``"wav"``,
            ``"mp3"``, ``"ogg_opus"``, ``"flac"``). The real-time stream is
            always PCM.
        speed: Speaking speed multiplier (0.25-4.0), applied on the batch path.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        ws_url: Optional[str] = None,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        style: Optional[str] = None,
        language: str = "en",
        sample_rate: int = 24000,
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
        # explicit arg -> env var -> built-in default (repoint without a code change)
        self._api_url_arg = api_url
        self._ws_url_arg = ws_url
        self._api_url = resolve_endpoint(arg=api_url, server=None,
                                         env_var="SHUNYALABS_TTS_URL", default=_DEFAULT_API_URL).rstrip("/")
        self._ws_url = resolve_endpoint(arg=ws_url, server=None,
                                        env_var="SHUNYALABS_TTS_WS_URL", default=_DEFAULT_WS_URL)
        self._model = model
        self._voice = voice
        self._style = style
        self._language = language
        self._output_format = output_format
        self._speed = speed
        self._auth = TokenAuth(self._api_key)
        # Persistent realtime sessions, reused across turns (connect + init + ready
        # is paid once, not per segment). A session is checked out by one stream at
        # a time; the gateway's voice/language are session-level, so they are fixed
        # for the life of this TTS instance.
        self._pool = utils.ConnectionPool[_Session](
            connect_cb=self._connect_session,
            close_cb=self._close_session,
            max_session_duration=_MAX_SESSION_S,
        )
        # Background cancel-and-drain of interrupted sessions (see StreamingTTS).
        self._cleanup_tasks: set[asyncio.Task] = set()

    async def _connect_session(self, timeout: float) -> _Session:
        await self._resolve_urls()
        transport = WsTransport(
            url=self._ws_url,
            auth=self._auth,
            conn_config=WsConnectionConfig(
                open_timeout=timeout,
                ping_interval=20,
                ping_timeout=20,
            ),
            sdk_component="tts",
        )
        try:
            await transport.connect()
            init: dict = {"voice": self._voice, "language": self._language}
            if self._model:
                init["model"] = self._model
            await transport.send_message(init)
            ready = await asyncio.wait_for(transport.receive_message(), timeout=timeout)
        except BaseException:
            await transport.close()
            raise
        if not isinstance(ready, dict) or ready.get("type") != "ready":
            await transport.close()
            if isinstance(ready, dict) and ready.get("type") == "error":
                raise APIStatusError(f"Shunyalabs TTS: {ready.get('error')}", status_code=400,
                                     retryable=False)
            raise APIConnectionError(f"Shunyalabs TTS: expected 'ready', got {ready!r}")
        return _Session(transport, ready.get("sample_rate"))

    async def _close_session(self, session: _Session) -> None:
        try:
            if session.transport.is_connected:
                # Orderly close: the bare-string "end" tells the gateway to finish and close.
                await session.transport.send_message("end")
        except Exception:
            pass
        await session.transport.close()

    def prewarm(self) -> None:
        """Open a realtime session ahead of the first turn."""
        self._pool.prewarm()

    async def aclose(self) -> None:
        for task in list(self._cleanup_tasks):
            task.cancel()
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        await self._pool.aclose()

    def _release_after_cancel(self, session: _Session) -> None:
        """An interrupted turn left text or audio in flight on `session`. Ask the
        gateway to discard it and drain up to its `cancelled` acknowledgement in the
        background, then return the clean session to the pool; on any doubt, close it."""
        async def _drain() -> None:
            try:
                await session.transport.send_message({"type": "cancel"})
                loop = asyncio.get_running_loop()
                deadline = loop.time() + _CANCEL_ACK_TIMEOUT_S
                while True:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    msg = await asyncio.wait_for(session.transport.receive_message(), timeout=remaining)
                    if isinstance(msg, dict) and msg.get("type") == "cancelled":
                        break
            except BaseException:
                self._pool.remove(session)
                raise
            else:
                self._pool.put(session)

        task = asyncio.ensure_future(_drain())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())   # never "never retrieved"

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "shunyalabs"

    def _format_text(self, text: str) -> str:
        """Prepend the style tag if set. The gateway adds the speaker
        prefix and a default <Conversational> style if none is present.
        """
        return f"{self._style} {text}" if self._style else text

    def _make_tts_config(self) -> TTSConfig:
        """Build a TTSConfig from plugin settings."""
        return TTSConfig(
            model=self._model,
            voice=self._voice,
            language=self._language,
            response_format=self._output_format,
            speed=self._speed,
        )

    async def _resolve_urls(self) -> None:
        """arg -> token-provided endpoint -> env var -> default (folded in at use time)."""
        eps = await self._auth.aget_endpoints()
        self._api_url = resolve_endpoint(arg=self._api_url_arg, server=eps.get("tts_http"),
                                         env_var="SHUNYALABS_TTS_URL", default=_DEFAULT_API_URL).rstrip("/")
        self._ws_url = resolve_endpoint(arg=self._ws_url_arg, server=eps.get("tts_ws"),
                                        env_var="SHUNYALABS_TTS_WS_URL", default=_DEFAULT_WS_URL)

    def _make_batch_tts(self) -> AsyncBatchTTS:
        """Create an AsyncBatchTTS instance using the SDK."""
        transport = AsyncHttpTransport(
            url=self._api_url,
            auth=self._auth,
        )
        return AsyncBatchTTS(auth=self._auth, transport=transport)

    def _make_streaming_tts(self) -> AsyncStreamingTTS:
        """Create an AsyncStreamingTTS instance using the SDK."""
        return AsyncStreamingTTS(
            auth=self._auth,
            ws_url=self._ws_url,
            ws_config=WsConnectionConfig(
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
            ),
        )

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
    """Single text -> audio synthesis via the Shunyalabs SDK."""

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

        await self._tts._resolve_urls()
        batch_tts = self._tts._make_batch_tts()
        config = self._tts._make_tts_config()
        # The emitter is initialized as audio/pcm and the LiveKit pipeline
        # consumes raw PCM, so force PCM here — otherwise a non-pcm
        # output_format would push (e.g.) MP3 bytes under a PCM mime type and
        # LiveKit would mis-decode them.
        from shunyalabs.tts._models import OutputFormat
        config.response_format = OutputFormat.PCM

        try:
            result = await batch_tts.synthesize(formatted, config=config)
            output_emitter.push(result.audio_data)
        finally:
            await batch_tts._transport.close()


class StreamingTTS(SynthesizeStream):
    """Token-by-token streaming TTS over one pooled ``/v1/realtime`` session.

    Text is forwarded to the gateway as it is pushed (``{"type": "text"}``), and
    each LiveKit flush becomes a gateway ``{"type": "flush"}``. The gateway buffers
    the deltas, speaks every sentence as soon as it is complete, speaks the
    remainder on flush and answers each flush with ``done`` -- which closes the
    matching LiveKit segment. The session is taken from the TTS's connection pool
    and returned to it afterwards, so consecutive turns reuse one socket.

    Interruption (``aclose()`` mid-turn) sends ``{"type": "cancel"}`` and drains to
    the gateway's ``cancelled`` in the background before the session is reused; a
    socket that does not acknowledge is closed instead. A pooled socket found dead
    before any audio came back is replaced and the turn replayed on the new one.
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
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._tts._sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )
        replay: list = []           # input consumed so far, for replay on a replacement socket
        for attempt in range(3):
            pool = self._tts._pool
            try:
                session = await pool.get(timeout=self._conn_options.timeout)
            except (APIConnectionError, APIStatusError):
                raise
            except Exception as e:  # noqa: BLE001
                raise APIConnectionError(f"Shunyalabs TTS: could not open a session: {e}") from e
            self._acquire_time = pool.last_acquire_time
            self._connection_reused = pool.last_connection_reused
            state = _TurnState()
            try:
                await self._speak(session, output_emitter, request_id, replay, state)
            except _StaleSession as e:
                pool.remove(session)
                if attempt < 2:
                    logger.info("Shunyalabs TTS: pooled session was closed; reconnecting (%s)", e)
                    continue
                raise APIConnectionError(f"Shunyalabs TTS: session lost: {e}") from e
            except asyncio.CancelledError:
                if state.dirty and session.transport.is_connected:
                    self._tts._release_after_cancel(session)
                elif session.transport.is_connected:
                    pool.put(session)
                else:
                    pool.remove(session)
                raise
            except (APIConnectionError, APIStatusError):
                pool.remove(session)
                raise
            except Exception as e:  # noqa: BLE001
                pool.remove(session)
                raise APIConnectionError(f"Shunyalabs TTS: stream failed: {e}") from e
            else:
                pool.put(session)
            return

    async def _speak(self, session: _Session, output_emitter: AudioEmitter, request_id: str,
                     replay: list, state: "_TurnState") -> None:
        t = session.transport
        segments: deque = deque()        # segment ids, in the order their text was sent
        seg_counter = [0]
        seg_has_text = [False]

        async def send(item) -> None:
            if isinstance(item, str):
                if not seg_has_text[0]:
                    seg_id = f"{request_id}-{seg_counter[0]}"
                    seg_counter[0] += 1
                    segments.append(seg_id)
                    seg_has_text[0] = True
                    item = self._tts._format_text(item) if self._tts._style else item
                    self._mark_started()
                state.dirty = True
                await t.send_message({"type": "text", "text": item})
            elif seg_has_text[0]:
                # LiveKit flush: speak whatever the gateway still holds for this segment.
                await t.send_message({"type": "flush"})
                state.flushes += 1
                seg_has_text[0] = False

        async def sender() -> None:
            try:
                for item in replay:
                    await send(item)
                async for item in self._input_ch:
                    replay.append(item)
                    await send(item)
            except Exception as e:  # noqa: BLE001
                if not state.progress:
                    raise _StaleSession(str(e)) from e
                raise
            state.input_done = True

        async def receiver() -> None:
            current = None
            while not (state.input_done and state.dones == state.flushes):
                try:
                    msg = await t.receive_message()
                except Exception as e:  # noqa: BLE001
                    if not state.progress:
                        raise _StaleSession(str(e)) from e
                    raise
                if isinstance(msg, (bytes, bytearray)):
                    state.progress = True
                    if current is None:
                        current = segments.popleft() if segments else f"{request_id}-x"
                        output_emitter.start_segment(segment_id=current)
                    output_emitter.push(bytes(msg))
                    continue
                if not isinstance(msg, dict):
                    continue
                kind = msg.get("type")
                if kind == "done":
                    state.progress = True
                    state.dones += 1
                    if current is None and segments:
                        current = segments.popleft()
                        output_emitter.start_segment(segment_id=current)
                    if current is not None:
                        output_emitter.end_segment()
                        current = None
                    if state.dones == state.flushes and not seg_has_text[0]:
                        state.dirty = False      # nothing of ours left in flight on this socket
                elif kind == "error":
                    raise APIStatusError(f"Shunyalabs TTS: {msg.get('error')}", status_code=500,
                                         retryable=False)

        send_task = asyncio.create_task(sender(), name="shunyalabs.tts.send")
        recv_task = asyncio.create_task(receiver(), name="shunyalabs.tts.recv")
        try:
            done, _ = await asyncio.wait({send_task, recv_task}, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                if task.exception() is not None:
                    raise task.exception()
            # The sender finished cleanly (input closed). The receiver runs until every
            # flush is answered; if none is outstanding there is nothing left to read,
            # and a receive would wait forever -- stop it (cancelling recv() is safe).
            if state.dones == state.flushes:
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
            else:
                await recv_task
        finally:
            for task in (send_task, recv_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)


class _TurnState:
    __slots__ = ("flushes", "dones", "input_done", "progress", "dirty")

    def __init__(self) -> None:
        self.flushes = 0          # gateway flushes sent
        self.dones = 0            # gateway `done`s received
        self.input_done = False   # LiveKit closed the input channel
        self.progress = False     # any audio or `done` received this attempt
        self.dirty = False        # text of ours the gateway still holds or is speaking
