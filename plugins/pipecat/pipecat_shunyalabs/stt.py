"""Shunyalabs STT service for Pipecat.

Maintains a persistent WebSocket connection to the Shunyalabs ASR gateway
for the lifetime of the pipeline.  Audio is streamed continuously; the
gateway's built-in VAD emits ``final_segment`` events at silence boundaries
which are surfaced as ``TranscriptionFrame``.

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
import json
import os
import time
from typing import AsyncGenerator, Optional

import websockets
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

import logging

logger = logging.getLogger(__name__)

_DEFAULT_WS_URL = "wss://asr.shunyalabs.ai/ws"


class ShunyalabsSTTService(STTService):
    """Pipecat STT service backed by the Shunyalabs ASR gateway.

    Maintains one persistent WebSocket per pipeline run.  Audio chunks
    received from the pipeline are forwarded to the gateway; transcription
    events are pushed back into the pipeline as ``TranscriptionFrame`` /
    ``InterimTranscriptionFrame``.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        language: Language code (e.g. ``"en"``, ``"hi"``) or ``"auto"``.
        url: WebSocket endpoint URL.
        sample_rate: Audio sample rate expected by the gateway (default 16000).
        **kwargs: Forwarded to ``STTService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        language: str = "auto",
        url: str = _DEFAULT_WS_URL,
        sample_rate: int = 16000,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._language = language
        self._url = url
        self._ws: Optional[websockets.ClientConnection] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._connected = asyncio.Event()
        self._sample_rate = sample_rate

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
        """Open WebSocket and send the initial config frame."""
        try:
            self._ws = await websockets.connect(
                self._url,
                open_timeout=10,
                ping_interval=20,
                ping_timeout=20,
                additional_headers={
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
            sr = self._sample_rate
            await self._ws.send(
                json.dumps(
                    {
                        "language": self._language,
                        "sample_rate": sr,
                        "dtype": "int16",
                    }
                )
            )
            # Wait for ready acknowledgement
            raw = await asyncio.wait_for(self._ws.recv(), timeout=15.0)
            msg = json.loads(raw)
            if msg.get("type") != "ready":
                raise RuntimeError(f"Expected 'ready', got: {msg}")

            self._connected.set()
            self._reader_task = asyncio.create_task(
                self._reader_loop(), name="shunyalabs-stt-reader"
            )
            logger.info("ShunyalabsSTTService connected (session=%s)", msg.get("session_id"))
        except Exception as exc:
            logger.error("ShunyalabsSTTService connection failed: %s", exc)
            raise

    async def _disconnect(self) -> None:
        """Send END, wait for reader to finish, then close the WebSocket."""
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send("END")
                if self._reader_task:
                    await asyncio.wait_for(self._reader_task, timeout=5.0)
            except Exception:
                pass
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._connected.clear()
        logger.info("ShunyalabsSTTService disconnected")

    # ------------------------------------------------------------------
    # Audio ingestion
    # ------------------------------------------------------------------

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Forward raw PCM bytes to the gateway.

        Transcription results arrive asynchronously via the reader task,
        which calls ``push_frame()`` directly.  Nothing is yielded here.
        """
        if self._ws and not self._ws.closed:
            try:
                await self._ws.send(audio)
            except websockets.ConnectionClosed:
                logger.warning("ShunyalabsSTTService WS closed during send; reconnecting")
                await self._connect()
                await self._ws.send(audio)
        yield  # async generator — no frames yielded synchronously

    # ------------------------------------------------------------------
    # Background reader
    # ------------------------------------------------------------------

    async def _reader_loop(self) -> None:
        """Read gateway messages and push transcription frames into the pipeline."""
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                text = msg.get("text", "")
                lang_str = msg.get("language") or self._language

                if msg_type == "partial" and text:
                    await self.push_frame(
                        InterimTranscriptionFrame(
                            text=text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=Language(lang_str) if lang_str != "auto" else None,
                        )
                    )

                elif msg_type in ("final_segment", "final") and text:
                    await self.push_frame(
                        TranscriptionFrame(
                            text=text,
                            user_id="",
                            timestamp=str(time.time()),
                            language=Language(lang_str) if lang_str != "auto" else None,
                        )
                    )

                elif msg_type in ("done", "error"):
                    if msg_type == "error":
                        logger.error("ShunyalabsSTTService gateway error: %s", msg.get("message"))
                    break

        except websockets.ConnectionClosed:
            logger.info("ShunyalabsSTTService WS connection closed")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("ShunyalabsSTTService reader error: %s", exc, exc_info=True)
