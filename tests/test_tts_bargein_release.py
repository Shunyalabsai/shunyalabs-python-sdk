"""A barge-in must free the TTS socket at once, not when the interrupted
utterance would have finished playing.

Measured on the live agent across 386 turns: 15 took over two seconds to
first audio, and 14 of those 15 landed within twenty seconds of a barge-in.
The worst was 12.1s, and the recovery turn then emitted 11ms of audio. A
caller who interrupts and waits ten seconds in silence has been dropped, as
far as they can tell.

The mechanism is an ordering deadlock, not slow speech:

  * `run_tts` holds `_transport_lock` for the whole generator, and the
    generator PACES its audio out in real time -- a 7.75s utterance holds the
    lock for 7.75s.
  * `discard_current_turn` waits one second for that lock, cannot get it,
    closes the transport and RETURNS EARLY -- never reaching the pacing reset
    at the end of the function.
  * The pacing loop's own bail-out tests `_pace_next_time is None`, which the
    early return never set, so it keeps pacing and keeps the lock.
  * The next turn blocks on the lock until the interrupted utterance would
    have finished speaking.

So the reset has to happen BEFORE the acquire, not after it. These three
fields are written by the reset and only read by the pacing loop, which uses
them solely to decide whether to stop, so an unsynchronised write is both
safe and the point: it is the signal that reaches a loop holding the lock.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _service():
    """A TTS service with no network, enough to exercise the lock dance."""
    from pipecat_shunyalabs.tts import ShunyalabsTTSService

    svc = ShunyalabsTTSService.__new__(ShunyalabsTTSService)
    svc._transport_lock = asyncio.Lock()
    svc._transport = object()          # non-None, so the early bail is not taken
    svc._session_ready = True
    svc._pace_buffer = bytearray(b"\x00\x00" * 4000)
    svc._pace_next_time = 1234.5
    svc._pace_started = True

    async def _close():
        svc._transport = None
        svc._session_ready = False

    svc._close_transport = _close
    return svc


@pytest.mark.asyncio
async def test_pacing_stops_even_when_the_lock_cannot_be_taken():
    """The case that cost ten seconds on a live call.

    `run_tts` is still pacing, so it holds the lock and `discard_current_turn`
    cannot have it. The pacing state must be cleared anyway -- that flag is
    the only way to reach a loop that is inside the lock.
    """
    from pipecat_shunyalabs import tts as mod

    svc = _service()
    await svc._transport_lock.acquire()          # stand in for a pacing run_tts
    try:
        t0 = asyncio.get_running_loop().time()
        await svc.discard_current_turn()
        waited = asyncio.get_running_loop().time() - t0
    finally:
        svc._transport_lock.release()

    assert svc._pace_next_time is None, (
        "pacing was not stopped, so the interrupted utterance keeps playing "
        "and keeps the lock; the next turn waits for it to finish"
    )
    assert svc._pace_started is False
    assert not svc._pace_buffer, "the interrupted audio is still queued to speak"
    # It still pays the one-second acquire timeout before giving up on a clean
    # cancel, which is bounded and acceptable. What must not happen is paying
    # the length of the utterance.
    assert waited < mod._CANCEL_ACK_TIMEOUT_S + 0.5


@pytest.mark.asyncio
async def test_the_reset_happens_before_the_acquire():
    """Pinned structurally as well as behaviourally.

    The behavioural test above passes if the reset moves anywhere ahead of the
    early return; this one says it must be ahead of the ACQUIRE, because an
    acquire that succeeds slowly delays the signal by exactly as long.
    """
    from pipecat_shunyalabs.tts import ShunyalabsTTSService

    src = inspect.getsource(ShunyalabsTTSService.discard_current_turn)
    reset = src.index("_pace_next_time = None")
    acquire = src.index("_transport_lock.acquire()")
    assert reset < acquire, (
        "the pacing reset is still behind the lock acquire; a barge-in during "
        "playback will not be able to signal the pacing loop"
    )


@pytest.mark.asyncio
async def test_a_clean_barge_in_still_resets_pacing():
    """The fast path must not regress: when the lock IS free, the socket is
    kept and pacing is still cleared."""
    svc = _service()
    sent: list = []

    class _T:
        async def send_message(self, m):
            sent.append(m)

        async def receive_message(self):
            return {"type": "cancelled", "pieces": 2}

    svc._transport = _T()
    await svc.discard_current_turn()

    assert {"type": "cancel"} in sent, "the server was never told to cancel"
    assert svc._pace_next_time is None and svc._pace_started is False
    assert svc._transport is not None, "the session was dropped on the fast path"
    assert not svc._transport_lock.locked(), "the lock was not released"
