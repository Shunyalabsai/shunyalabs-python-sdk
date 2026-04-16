"""Shunyalabs TTS service for Pipecat.

Maintains one persistent WebSocket connection to the Shunyalabs TTS gateway
for the lifetime of the pipeline. Each ``run_tts`` call sends a config frame
and streams audio chunks back over the same connection — skipping TCP+TLS+WS
handshake, auth, and gRPC client setup on every synthesis after the first.

Requires a gateway that supports the keep-alive WebSocket protocol
(multiple request-frames per connection).

Install::

    pip install pipecat-shunyalabs

Usage::

    from pipecat_shunyalabs import ShunyalabsTTSService

    tts = ShunyalabsTTSService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        voice="Nisha",
        language="en",
        style="<Conversational>",
    )

    pipeline = Pipeline([..., tts, transport.output()])
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


def _supports_context(frame_cls):
    return "context_id" in inspect.signature(frame_cls.__init__).parameters


class ShunyalabsTTSService(TTSService):
    """Pipecat TTS service backed by the Shunyalabs TTS gateway.

    Uses one persistent WebSocket for the pipeline's lifetime: each
    ``run_tts`` call reuses the same connection, sending a new config
    frame and streaming its audio chunks until the ``completion`` frame.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY`` env var.
        url: WebSocket endpoint URL.
        voice: Speaker voice name (e.g. ``"Rajesh"``, ``"Nisha"``).
        style: Emotion style tag (e.g. ``"<Happy>"``). Prepended to text.
        language: ISO 639 language code (e.g. ``"en"``, ``"hi"``). Required.
        output_format: Audio format (default ``"pcm"``).
        speed: Speaking speed multiplier (0.5-2.0).
        **kwargs: Forwarded to ``TTSService.__init__``.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        url: str = _DEFAULT_WS_URL,
        model: str = "zero-indic",
        voice: str = "Rajesh",
        style: Optional[str] = None,
        language: str = "en",
        sample_rate: int = 16000,
        output_format: str = "pcm",
        speed: float = 1.0,
        **kwargs,
    ) -> None:
        if _TTSSettings is not None:
            kwargs.setdefault("settings", _TTSSettings(model=model, voice=voice, language=language))
        super().__init__(sample_rate=sample_rate, **kwargs)
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY", "")
        if not self._api_key:
            raise ValueError(
                "Shunyalabs API key required. Pass api_key= or set SHUNYALABS_API_KEY."
            )
        self._ws_url = url
        self._model = model
        self._voice = voice
        self._style = style
        self._language = language
        self._sample_rate = sample_rate
        self._output_format = output_format
        self._speed = speed
        self._auth = StaticKeyAuth(self._api_key)
        self._transport: Optional[WsTransport] = None
        self._transport_lock = asyncio.Lock()

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
        """Return a live transport, opening a new one if the current is dead."""
        if self._transport is not None and not self._transport._closed:
            return self._transport
        t_start = time.monotonic()
        self._transport = await self._open_transport()
        logger.info(
            f"ShunyalabsTTS: persistent WS opened in {(time.monotonic() - t_start) * 1000:.0f}ms"
        )
        return self._transport

    async def _close_transport(self) -> None:
        if self._transport is not None:
            try:
                await self._transport.close()
            except Exception:
                pass
            self._transport = None

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        try:
            await self._ensure_transport()
        except Exception as e:
            logger.warning(f"ShunyalabsTTS: initial connection failed, will retry lazily: {e}")

    async def stop(self, frame: EndFrame) -> None:
        await self._close_transport()
        await super().stop(frame)

    async def cancel(self, frame: CancelFrame) -> None:
        await self._close_transport()
        await super().cancel(frame)

    async def run_tts(
        self, text: str, context_id: Optional[str] = None
    ) -> AsyncGenerator[Frame, None]:
        formatted = self._format_text(text)
        logger.debug(f"ShunyalabsTTS synthesizing: {formatted[:80]}")

        supports_context = _supports_context(TTSStartedFrame)
        if supports_context:
            yield TTSStartedFrame(context_id=context_id)
        else:
            yield TTSStartedFrame()

        t_start = time.monotonic()

        # Serialise run_tts on the persistent WS — pipecat already runs these
        # sequentially, but this lock guards against reconnect-races inside
        # _ensure_transport.
        async with self._transport_lock:
            transport = await self._ensure_transport()
        t_connected = time.monotonic()

        try:
            # Drain any stale messages left over from a previous synthesis.
            # Some server models (e.g. omnivoice) can emit a ``completion``
            # frame before all of their audio chunks have been flushed onto
            # the wire — those stragglers would otherwise be consumed as if
            # they belonged to the next request, leaking audio across
            # syntheses and producing wrong-sounding output.
            _drained = 0
            while True:
                try:
                    stale = await asyncio.wait_for(
                        transport.receive_message(), timeout=0.001
                    )
                except asyncio.TimeoutError:
                    break
                except Exception:
                    break
                _drained += 1
            if _drained:
                logger.warning(
                    f"ShunyalabsTTS drained {_drained} stale message(s) before new synth"
                )

            payload = self._make_tts_config().to_request_payload(
                text=formatted, request_type="streaming"
            )
            await transport.send_message(payload)

            first_chunk_at: Optional[float] = None
            chunk_count = 0
            total_bytes = 0

            while True:
                msg = await transport.receive_message()

                if isinstance(msg, dict):
                    msg_type = msg.get("type")

                    if msg_type == "chunk":
                        # Gateway tells us the actual sample rate per chunk
                        # (e.g. 24 kHz for zero-universal/omnivoice, 16 kHz
                        # for spark_tts). Honor it so audio plays at the
                        # correct rate regardless of which model the voice
                        # routes to server-side.
                        chunk_sample_rate = int(msg.get("sample_rate", self._sample_rate))
                        audio_data = await transport.receive_message()
                        if not isinstance(audio_data, bytes):
                            raise SynthesisError(
                                f"Expected binary audio after chunk meta, got {type(audio_data).__name__}"
                            )
                        if first_chunk_at is None:
                            first_chunk_at = time.monotonic()
                            logger.info(
                                f"ShunyalabsTTS TTFB: {(first_chunk_at - t_start) * 1000:.0f}ms "
                                f"(connect={(t_connected - t_start) * 1000:.0f}ms, "
                                f"sr={chunk_sample_rate})"
                            )
                        chunk_count += 1
                        total_bytes += len(audio_data)

                        if supports_context:
                            yield TTSAudioRawFrame(
                                audio=audio_data,
                                sample_rate=chunk_sample_rate,
                                num_channels=1,
                                context_id=context_id,
                            )
                        else:
                            yield TTSAudioRawFrame(
                                audio=audio_data,
                                sample_rate=chunk_sample_rate,
                                num_channels=1,
                            )

                    elif msg_type == "completion":
                        logger.info(
                            f"ShunyalabsTTS done: chunks={chunk_count} "
                            f"bytes={total_bytes} total={(time.monotonic() - t_start) * 1000:.0f}ms"
                        )
                        break

                    elif msg_type == "error":
                        error_detail = msg.get("error", "Unknown streaming error")
                        raise SynthesisError(f"Streaming error: {error_detail}")

                    else:
                        logger.warning(f"Unknown WS message type: {msg_type}")

                elif isinstance(msg, bytes):
                    logger.warning(
                        f"Unexpected binary frame ({len(msg)} bytes) outside chunk flow"
                    )

        except Exception as e:
            # On any error, drop the transport so the next run_tts opens fresh.
            logger.warning(f"ShunyalabsTTS stream error; reconnecting next call: {e}")
            await self._close_transport()
            raise

        if supports_context:
            yield TTSStoppedFrame(context_id=context_id)
        else:
            yield TTSStoppedFrame()
