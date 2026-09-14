"""End-to-end against a local server implementing the documented /v1/realtime contract.

The unit tests elsewhere use fake transports. This one runs the real
``AsyncStreamingASR`` over a real WebSocket, so the handshake, the config frame,
the ``ready`` echo and event dispatch are all exercised as a client would hit
them. No credentials needed -- ``StaticKeyAuth`` skips the token mint.

The server below mirrors ``asrpipe/server/ws.py``:

* the credential may arrive in the init JSON
* ``endpoint_silence_ms`` clamps to 200-5000, ``decode_every_ms`` to 320-5000,
  and both are echoed in ``ready``
* a bare ``"commit"`` text frame or ``{"type":"commit"}`` finalises and keeps the
  socket open; ``end`` finalises and closes
* a ``final`` with ``end_of_utterance`` true is followed by ``utterance_end``
"""

import asyncio
import json

import pytest

websockets = pytest.importorskip("websockets")

from shunyalabs._core._auth import StaticKeyAuth  # noqa: E402
from shunyalabs.asr._models import StreamingConfig, StreamingMessageType  # noqa: E402
from shunyalabs.asr._streaming import AsyncStreamingASR  # noqa: E402


def _clamp(value, lo, hi, default):
    if value is None:
        return default
    return max(lo, min(hi, int(value)))


class _FakeGateway:
    """Minimal stand-in for the real ASR gateway."""

    def __init__(self):
        self.inits = []
        self.control_frames = []
        self.audio_bytes = 0
        self._seg = 0

    async def handler(self, ws):
        raw_init = await ws.recv()
        init = json.loads(raw_init)
        self.inits.append(init)

        ep = _clamp(init.get("endpoint_silence_ms"), 200, 5000, 700)
        de = _clamp(init.get("decode_every_ms"), 320, 5000, 640)
        await ws.send(json.dumps({
            "type": "ready",
            "session_id": "sess-test",
            "endpoint_silence_ms": ep,
            "decode_every_ms": de,
            "codeswitch": bool(init.get("codeswitch")),
        }))

        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                self.audio_bytes += len(message)
                continue

            raw = message.strip()
            kind = raw.lower()
            if raw.startswith("{"):
                try:
                    kind = str(json.loads(raw).get("type", "")).strip().lower()
                except Exception:
                    pass
            self.control_frames.append(kind)

            keep = kind in ("commit", "flush")
            if keep or "end" in kind:
                self._seg += 1
                await ws.send(json.dumps({
                    "type": "final",
                    "seg": self._seg,
                    "text": f"utterance {self._seg}",
                    "elapsed_ms": 39,
                    "end_of_utterance": True,
                }))
                await ws.send(json.dumps({"type": "utterance_end", "seg": self._seg}))
                if not keep:
                    await ws.send(json.dumps({"type": "done", "total_segments": self._seg}))
                    return


async def _run_scenario(config):
    gateway = _FakeGateway()
    async with websockets.serve(gateway.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        streaming = AsyncStreamingASR(
            auth=StaticKeyAuth("test-key"),
            ws_url=f"ws://127.0.0.1:{port}/v1/realtime",
        )
        conn = await streaming.stream(config=config)

        finals, turn_ends = [], []
        conn.on(StreamingMessageType.FINAL)(lambda m: finals.append(m))
        conn.on(StreamingMessageType.UTTERANCE_END)(lambda m: turn_ends.append(m))

        # Two turns on one connection -- the thing `commit` exists to allow.
        for _ in range(2):
            await conn.send_audio(b"\x00" * 3200)
            await conn.commit()
            await asyncio.sleep(0.15)

        session_id = conn.session_id
        applied = conn.effective_config
        closed_before_end = conn.is_closed
        await conn.close()

    return gateway, finals, turn_ends, session_id, applied, closed_before_end


class TestRealtimeContract:
    def test_two_turns_on_one_connection(self):
        gateway, finals, turn_ends, session_id, _, closed = asyncio.run(
            _run_scenario(StreamingConfig(language="en", sample_rate=8000))
        )

        assert session_id == "sess-test"
        assert gateway.control_frames == ["commit", "commit"]
        assert not closed, "commit must not close the session"
        assert [f.text for f in finals] == ["utterance 1", "utterance 2"]
        assert all(f.end_of_utterance for f in finals)
        assert [t.segment_id for t in turn_ends] == [1, 2]
        assert gateway.audio_bytes == 6400

    def test_tuning_reaches_the_gateway(self):
        gateway, _, _, _, applied, _ = asyncio.run(
            _run_scenario(StreamingConfig(
                language="en",
                sample_rate=8000,
                endpoint_silence_ms=250,
                decode_every_ms=960,
                vad="silero",
            ))
        )

        init = gateway.inits[0]
        assert init["endpoint_silence_ms"] == 250
        assert init["decode_every_ms"] == 960
        assert init["vad"] == "silero"
        assert applied["endpoint_silence_ms"] == 250

    def test_clamped_value_is_visible_not_silent(self):
        # 50 is below the server's floor. The request is clamped, not rejected,
        # so the only way to know is the echo.
        _, _, _, _, applied, _ = asyncio.run(
            _run_scenario(StreamingConfig(language="en", endpoint_silence_ms=50))
        )
        assert applied["endpoint_silence_ms"] == 200

    def test_defaults_are_left_to_the_server(self):
        gateway, _, _, _, applied, _ = asyncio.run(
            _run_scenario(StreamingConfig(language="en"))
        )
        assert "endpoint_silence_ms" not in gateway.inits[0]
        assert applied["endpoint_silence_ms"] == 700
