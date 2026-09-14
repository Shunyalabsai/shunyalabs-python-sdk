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


class TestSTTDefaultsAreNonBreaking:
    def test_turn_frames_are_opt_in(self):
        # Measured: with emit_turn_frames=True and pipecat's DEFAULT turn
        # strategies, downstream sees two UserStartedSpeakingFrame per turn
        # instead of one. The default strategies consume
        # VADUserStartedSpeakingFrame from the transport, not the public frame,
        # so the aggregator broadcasts its own and ours passes through too.
        # Only ExternalUserTurnStrategies consumes ours -- so the two settings
        # go together, and neither may be the silent default.
        from pipecat_shunyalabs.stt import ShunyalabsSTTService

        svc = ShunyalabsSTTService(api_key="test-key", language="en")
        assert svc._emit_turn_frames is False

    def test_min_send_bytes_is_100ms_at_16k(self):
        from pipecat_shunyalabs.stt import ShunyalabsSTTService, _MIN_SEND_BYTES

        svc = ShunyalabsSTTService(api_key="test-key", language="en")
        assert svc._min_send_bytes == _MIN_SEND_BYTES
        assert _MIN_SEND_BYTES / (16000 * 2) == 0.1

    def test_tuning_defaults_to_server_side(self):
        from pipecat_shunyalabs.stt import ShunyalabsSTTService

        svc = ShunyalabsSTTService(api_key="test-key", language="en")
        assert svc._endpoint_silence_ms is None
        assert svc._decode_every_ms is None
        assert svc._vad is None


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


class TestShortUtterancesOpenATurn:
    """A final with text must open a turn even with no partial before it.

    Measured against production on an 8 kHz telephony render of "Five."
    (1.16 s): zero partials, and a final carrying text. Arming the turn latch
    only on partials dropped the entire turn -- no start frame, so utterance_end
    saw a closed latch and emitted no stop frame either, so the aggregator never
    released the transcript and the LLM was never called. On a real booking call
    the caller answered "5" to "what party size?" and got silence.
    """

    def test_final_with_text_arms_and_closes_the_turn(self):
        import inspect
        from pipecat_shunyalabs import stt as m

        src = inspect.getsource(m)
        # The final handler must arm the latch, not just read it.
        final_src = src.split("StreamingMessageType.FINAL)")[1].split("@self._conn.on")[0]
        assert "UserStartedSpeakingFrame()" in final_src, \
            "a final with text must be able to open a turn"
        assert "UserStoppedSpeakingFrame()" in final_src, \
            "a final with no preceding partial must also close the turn"
        assert "had_partials" in final_src, \
            "the close must be conditional on nothing having preceded it"

    def test_empty_finals_still_do_nothing(self):
        import inspect
        from pipecat_shunyalabs import stt as m

        final_src = inspect.getsource(m).split("StreamingMessageType.FINAL)")[1]
        # The early return on empty text must come first, or silence
        # re-endpointing would manufacture turns every ~800 ms.
        head = final_src.split("had_partials")[0]
        assert "if not msg.text" in head and "return" in head


class TestTheTurnLatchIsAtomic:
    """`final` and `utterance_end` must not both close the same turn.

    They arrive about a millisecond apart, in an order the gateway does not
    guarantee, and they are dispatched from callbacks that do not run on the
    event loop. With a plain read-then-write guard both could observe the latch
    open and both emit a stop frame -- measured against production on roughly
    60% of short utterances: one UserStartedSpeakingFrame and TWO
    UserStoppedSpeakingFrame.
    """

    def _svc(self):
        from pipecat_shunyalabs.stt import ShunyalabsSTTService

        return ShunyalabsSTTService(api_key="test-key", language="en",
                                    emit_turn_frames=True)

    def test_only_one_caller_can_open_a_turn(self):
        svc = self._svc()
        assert svc._open_turn() is True
        assert svc._open_turn() is False, "a second opener must not re-arm the turn"

    def test_only_one_caller_can_close_a_turn(self):
        svc = self._svc()
        svc._open_turn()
        assert svc._close_turn() is True
        assert svc._close_turn() is False, \
            "the second closer is the double stop frame; it must be refused"

    def test_closing_an_open_turn_twice_under_contention(self):
        # Drive the actual race: many threads racing to close one turn. Exactly
        # one must win, however the interleaving falls.
        import threading

        svc = self._svc()
        svc._open_turn()
        wins = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            if svc._close_turn():
                wins.append(1)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(wins) == 1, f"{sum(wins)} callers closed the same turn"

    def test_opening_under_contention(self):
        import threading

        svc = self._svc()
        wins = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            if svc._open_turn():
                wins.append(1)

        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(wins) == 1, f"{sum(wins)} callers opened the same turn"

    def test_the_latch_is_never_touched_directly(self):
        # Every read-then-write has to go through the helpers, or the race
        # comes back at whichever site was missed.
        import inspect
        import re

        from pipecat_shunyalabs import stt

        src = inspect.getsource(stt)
        writes = re.findall(r"self\._speaking\s*=", src)
        # Only the initialiser and the two helpers may assign it.
        assert len(writes) == 3, f"unexpected direct latch writes: {len(writes)}"
