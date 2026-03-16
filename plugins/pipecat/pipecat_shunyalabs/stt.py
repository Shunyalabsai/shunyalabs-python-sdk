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
)
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language

try:
    from pipecat.services.settings import STTSettings as _STTSettings
except ImportError:
    _STTSettings = None

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._models import WsConnectionConfig
from shunyalabs.asr._models import StreamingConfig, StreamingMessageType
from shunyalabs.asr._streaming import ASRStreamingConnection, AsyncStreamingASR

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://asr.shunyalabs.ai/ws"
_MIN_SEND_BYTES = 4096


class ShunyalabsSTTService(STTService):
    """Pipecat STT service backed by the Shunyalabs ASR gateway.

    Maintains one persistent WebSocket per pipeline run.  Audio chunks
    received from the pipeline are buffered and forwarded to the gateway
    in larger blocks (the gateway requires chunks of at least ~4 KB for
    its VAD to function reliably).  Transcription events are pushed back
    into the pipeline as ``TranscriptionFrame`` /
    ``InterimTranscriptionFrame``.

    Args:
        api_key: Shunyalabs API key.  Falls back to ``SHUNYALABS_API_KEY``
            env var.
        language: Language code (e.g. ``"en"``, ``"hi"``) or ``"auto"``.
        url: WebSocket endpoint URL.
        sample_rate: Audio sample rate in Hz (default 16 000).
        min_send_bytes: Minimum buffer size before sending to the gateway.
        **kwargs: Forwarded to ``STTService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        language: str = "auto",
        url: str = _DEFAULT_WS_URL,
        sample_rate: int = 16000,
        min_send_bytes: int = _MIN_SEND_BYTES,
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
        self._ws_url = url
        self._sample_rate = sample_rate
        self._auth = StaticKeyAuth(self._api_key)
        self._conn: Optional[ASRStreamingConnection] = None
        self._audio_buffer = bytearray()
        self._min_send_bytes = min_send_bytes

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
            )

            self._conn = await streaming.stream(config=config)

            # Capture the running event loop so callbacks fired from
            # background threads can safely schedule coroutines.
            loop = asyncio.get_running_loop()

            def _schedule(coro):
                loop.call_soon_threadsafe(asyncio.ensure_future, coro)

            @self._conn.on(StreamingMessageType.PARTIAL)
            def on_partial(msg):
                if msg.text:
                    _schedule(self.push_frame(
                        InterimTranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=Language(msg.language) if msg.language and msg.language != "auto" else None,
                        )
                    ))

            @self._conn.on(StreamingMessageType.FINAL_SEGMENT)
            def on_final_segment(msg):
                if msg.text:
                    _schedule(self.push_frame(
                        TranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=Language(msg.language) if msg.language and msg.language != "auto" else None,
                        )
                    ))

            @self._conn.on(StreamingMessageType.FINAL)
            def on_final(msg):
                if msg.text:
                    _schedule(self.push_frame(
                        TranscriptionFrame(
                            text=msg.text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=Language(msg.language) if msg.language and msg.language != "auto" else None,
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
        logger.info("ShunyalabsSTTService disconnected")

    # ------------------------------------------------------------------
    # Audio ingestion
    # ------------------------------------------------------------------

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Buffer and forward raw PCM bytes to the gateway.

        Small audio frames from the pipeline are accumulated until at
        least ``min_send_bytes`` are available, then sent as a single
        chunk.  The Shunyalabs gateway requires adequately sized chunks
        for its built-in VAD to detect speech reliably.

        Transcription results arrive asynchronously via the event
        handlers registered in :meth:`_connect`.
        """
        if self._conn and not self._conn.is_closed:
            self._audio_buffer.extend(audio)
            while len(self._audio_buffer) >= self._min_send_bytes:
                chunk = bytes(self._audio_buffer[:self._min_send_bytes])
                del self._audio_buffer[:self._min_send_bytes]
                try:
                    await self._conn.send_audio(chunk)
                except Exception:
                    logger.warning("ShunyalabsSTTService send failed; reconnecting")
                    await self._connect()
                    await self._conn.send_audio(chunk)
        yield  # async generator — no frames yielded synchronously
