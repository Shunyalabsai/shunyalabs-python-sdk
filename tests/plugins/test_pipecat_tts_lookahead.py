"""run_tts releases the end of an utterance early, and re-buffers after running dry.

Runs the real run_tts in virtual time: the event loop jumps to the next timer
instead of sleeping, and the plugin's clock is the loop's. Skipped unless
pipecat is installed:

    pip install -e . -e plugins/pipecat && pytest tests/plugins
"""

import asyncio
import types

import pytest

pytest.importorskip("pipecat", reason="pipecat-shunyalabsai plugin not installed")

import pipecat_shunyalabs.tts as tts_mod  # noqa: E402
from pipecat_shunyalabs.tts import ShunyalabsTTSService  # noqa: E402

CHUNK = 9600          # 200 ms of 24 kHz int16 mono, as the service sends
FRAME_S = 0.04


class _VLoop(asyncio.SelectorEventLoop):
    """Every timer fires `overshoot` s late, as a real sleep does by about a millisecond."""

    def __init__(self, overshoot=0.0):
        super().__init__()
        self._vt = 100.0
        self.overshoot = overshoot

    def time(self):
        return self._vt

    def _run_once(self):
        if not self._ready and self._scheduled:
            self._vt = max(self._vt, self._scheduled[0]._when + self.overshoot)
        super()._run_once()


class _Server:
    """Answers each flush with `chunks` 200 ms chunks; chunk i at `at[i]` s after the flush."""

    def __init__(self, loop, plans):
        self.loop, self.plans = loop, list(plans)
        self.q = asyncio.Queue()
        self.sent, self.handles = [], []
        self._closed = False
        self.is_connected = True

    async def send_message(self, msg):
        self.sent.append(msg)
        now = self.loop.time()
        if isinstance(msg, dict) and msg.get("type") == "flush":
            at = self.plans.pop(0)
            for i, t in enumerate(at):
                self.handles.append(self.loop.call_at(now + t + i * 1e-6, self.q.put_nowait, b"\x00" * CHUNK))
            self.handles.append(self.loop.call_at(now + at[-1] + 1e-3, self.q.put_nowait, {"type": "done"}))
        elif isinstance(msg, dict) and msg.get("type") == "cancel":
            for h in self.handles:
                h.cancel()
            while not self.q.empty():
                self.q.get_nowait()
            self.loop.call_at(now + 0.05, self.q.put_nowait, {"type": "cancelled"})

    async def receive_message(self):
        return await self.q.get()

    async def close(self):
        self._closed = True


def _run(plans, body, overshoot=0.0, **svc_kw):
    loop = _VLoop(overshoot)
    saved = tts_mod.time
    tts_mod.time = types.SimpleNamespace(monotonic=loop.time)
    try:
        svc = ShunyalabsTTSService(api_key="test-key", **svc_kw)
        svc._sample_rate = 24000
        server = _Server(loop, plans)
        svc._transport, svc._session_ready = server, True
        return loop.run_until_complete(body(svc, server, loop))
    finally:
        tts_mod.time = saved
        loop.close()


async def _speak(svc, loop, text="x"):
    """Yield times of the audio frames of one run_tts, and when it returned."""
    times = []
    async for f in svc.run_tts(text, "ctx"):
        if hasattr(f, "audio"):
            times.append(loop.time())
    return times, loop.time()


def test_run_tts_returns_before_the_audio_ends():
    # 2.0 s of audio, all of it at 0.25 s: run_tts returns the lookahead before the end, so
    # pipecat can send the next sentence while this one is still playing.
    async def body(svc, server, loop):
        t0 = loop.time()
        times, back = await _speak(svc, loop)
        start = times[0]
        assert start == pytest.approx(t0 + 0.25, abs=0.01)
        assert back == pytest.approx(start + 2.0 - svc._lookahead_ms / 1000, abs=0.05)
        assert svc._pace_next_time == pytest.approx(start + 2.0, abs=0.01)

    _run([[0.25] * 10], body)


def test_the_next_sentence_continues_without_a_gap_or_a_new_pre_buffer():
    async def body(svc, server, loop):
        first, _ = await _speak(svc, loop, "one")
        end_one = svc._pace_next_time
        # The next sentence's first chunk lands 0.4 s after it is sent, its second 0.1 s later.
        second, _ = await _speak(svc, loop, "two")
        assert second[0] == pytest.approx(end_one, abs=0.005), "no silence between sentences"
        assert server.sent.count({"type": "flush"}) == 2

    _run([[0.25] * 10, [0.4] + [0.5] * 9], body)


def test_the_pre_buffer_is_paid_again_once_playback_has_run_dry():
    async def body(svc, server, loop):
        await _speak(svc, loop, "one")
        await asyncio.sleep(3.0)                       # the caller talks; playback is long over
        t0 = loop.time()
        times, _ = await _speak(svc, loop, "two")
        # Chunk 1 at 0.25 s, chunk 2 at 0.35 s: 320 ms pre-buffer -> start on chunk 2.
        assert times[0] == pytest.approx(t0 + 0.35, abs=0.01)

    _run([[0.25] * 10, [0.25] + [0.35] * 9], body)


def test_barge_in_while_the_next_sentence_is_queued_drops_it_and_keeps_the_session():
    async def body(svc, server, loop):
        await _speak(svc, loop, "one")                 # returns with ~0.8 s still to play
        task = asyncio.ensure_future(_speak(svc, loop, "two"))
        await asyncio.sleep(0.3)                       # "two" is in flight on the server
        await svc.discard_current_turn()
        times, _ = await task
        assert {"type": "cancel"} in server.sent
        assert times == [], "nothing of the interrupted sentence is spoken"
        assert svc._pace_buffer == bytearray() and svc._pace_started is False
        assert svc._transport is server and svc._session_ready, "no reconnect"
        assert not svc._transport_lock.locked()

    _run([[0.25] * 10, [0.4] * 10], body)


def test_sleep_overshoot_does_not_slow_the_pace():
    # Each sleep wakes 1 ms late. Resetting the clock on any lateness added that to every
    # frame (frames every ~41 ms); 100 frames must still cover 4.0 s, give or take a frame.
    async def body(svc, server, loop):
        times, _ = await _speak(svc, loop)
        assert len(times) == 100
        span = times[-1] + FRAME_S - times[0]
        assert span == pytest.approx(4.0, abs=FRAME_S), f"100 frames took {span:.3f} s"

    _run([[0.25] * 20], body, overshoot=0.001, lookahead_ms=0)
