"""Barge-in recovers the TTS session without reconnecting.

Skipped unless pipecat is installed, since the plugin is a separate
distribution. Run with the plugin's own extras:

    pip install -e . -e plugins/pipecat && pytest tests/plugins
"""

import asyncio

import pytest

pytest.importorskip("pipecat", reason="pipecat-shunyalabsai plugin not installed")

from pipecat_shunyalabs.tts import ShunyalabsTTSService  # noqa: E402


class _FakeTransport:
    """Stands in for a connected WsTransport.

    ``inbox`` is what the server would send next; ``sent`` records our frames.
    """

    def __init__(self, inbox=None):
        self.sent = []
        self.inbox = list(inbox or [])
        self._closed = False
        self.close_calls = 0

    @property
    def is_connected(self):
        return not self._closed

    async def send_message(self, msg):
        if self._closed:
            raise RuntimeError("socket closed")
        self.sent.append(msg)

    async def receive_message(self):
        if not self.inbox:
            # Nothing further: emulate a socket that simply goes quiet.
            await asyncio.sleep(3600)
        return self.inbox.pop(0)

    async def close(self):
        self._closed = True
        self.close_calls += 1


def _service(**kw):
    svc = ShunyalabsTTSService(api_key="test-key", **kw)
    return svc


def _attach(svc, transport):
    svc._transport = transport
    svc._session_ready = True
    return transport


class TestInterruptionKeepsTheSession:
    def test_cancel_is_sent_and_socket_survives(self):
        svc = _service()
        # Trailing audio from the interrupted turn, then the server's ack.
        t = _attach(svc, _FakeTransport(inbox=[b"\x00" * 64, {"type": "cancelled", "pieces": 1}]))
        svc._pace_buffer = bytearray(b"\x01" * 128)
        svc._pace_started = True
        svc._pace_next_time = 123.0

        asyncio.run(svc.discard_current_turn())

        assert {"type": "cancel"} in t.sent, "should ask the server to discard the turn"
        assert t.close_calls == 0, "the expensive part is the reconnect; do not reconnect"
        assert svc._transport is t
        assert svc._session_ready is True

    def test_stale_audio_is_discarded_and_pacing_reset(self):
        svc = _service()
        _attach(svc, _FakeTransport(inbox=[{"type": "cancelled", "pieces": 0}]))
        svc._pace_buffer = bytearray(b"\x01" * 128)
        svc._pace_started = True
        svc._pace_next_time = 123.0

        asyncio.run(svc.discard_current_turn())

        # Buffered PCM belongs to the interrupted utterance and must not be
        # spoken; _pace_next_time must be cleared alongside _pace_started or the
        # pacing loop compares None to a timestamp.
        assert svc._pace_buffer == bytearray()
        assert svc._pace_started is False
        assert svc._pace_next_time is None

    def test_lock_is_released(self):
        svc = _service()
        _attach(svc, _FakeTransport(inbox=[{"type": "cancelled"}]))
        asyncio.run(svc.discard_current_turn())
        assert not svc._transport_lock.locked()


class TestInterruptionFallsBackSafely:
    def test_no_session_falls_back_to_close(self):
        svc = _service()
        t = _FakeTransport()
        svc._transport = t
        svc._session_ready = False

        asyncio.run(svc.discard_current_turn())

        assert svc._transport is None
        assert svc._session_ready is False

    def test_missing_ack_falls_back_to_close(self):
        svc = _service()
        # Server never acknowledges: the drain must time out rather than hang,
        # and leave a definitely-clean socket behind.
        t = _attach(svc, _FakeTransport(inbox=[]))

        asyncio.run(svc.discard_current_turn())

        assert t.close_calls >= 1
        assert svc._transport is None
        assert not svc._transport_lock.locked()

    def test_send_failure_falls_back_to_close(self):
        svc = _service()
        t = _attach(svc, _FakeTransport())
        t._closed = True  # send_message will raise

        asyncio.run(svc.discard_current_turn())

        assert svc._transport is None
        assert not svc._transport_lock.locked()


class TestTuningKnobs:
    def test_defaults_are_unchanged(self):
        # WebRTC transports rely on the 480 ms pre-buffer; this release must not
        # move it for them.
        svc = _service()
        assert svc._min_buffer_frames == 12
        assert svc._frame_ms == 40

    def test_frame_bytes_follows_frame_ms(self):
        svc = _service(frame_ms=20)
        svc._sample_rate = 24000
        assert svc._frame_bytes() == int(24000 * 0.020 * 2)

    def test_quality_and_clause_first_omitted_when_unset(self):
        svc = _service()
        assert svc._quality is None
        assert svc._clause_first is None

    def test_quality_and_clause_first_are_carried(self):
        svc = _service(quality="low", clause_first=True, min_buffer_frames=3)
        assert svc._quality == "low"
        assert svc._clause_first is True
        assert svc._min_buffer_frames == 3
