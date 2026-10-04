"""The pacing loop must survive an interruption that lands mid-sleep.

`_pace_next_time` is cleared by `_handle_interruption`, which runs on another
task. The pacing loop awaits `asyncio.sleep()` between emitting frames, so a
barge-in arriving during that sleep leaves the loop resuming with
`_pace_next_time is None` -- and the line after the sleep did
`self._pace_next_time += self._frame_ms / 1000`.

That raised inside the frame processor. It is reported non-fatal, so the call
continues with TTS dead, and every later turn simply goes unanswered. Seen on
the meera-voice eval suite 2026-09-21: the agent fell silent mid-booking and
four unrelated assertions failed because of it.

The guard is a re-read after the await. This test drives the exact ordering
rather than the public API, because reproducing it through a real socket
depends on winning a race.
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins" / "pipecat"))


class _PacingLoop:
    """The loop body from ShunyalabsTTSService, with the same ordering.

    Kept as a faithful transcription rather than importing the service, which
    needs a transport, an API key and a pipecat task to construct. The
    invariant under test is the read-after-await, and that is reproduced here
    exactly.
    """

    def __init__(self, guard: bool):
        self._guard = guard
        self._pace_buffer = bytearray(b"\x00" * 1600)
        self._pace_next_time = None
        self._pace_started = False
        self._frame_ms = 20.0
        self.frames = 0

    def interrupt(self):
        """What _handle_interruption does to the pacing state."""
        self._pace_buffer = bytearray()
        self._pace_next_time = None
        self._pace_started = False

    async def run(self, frame_bytes: int, sleep_hook):
        loop = asyncio.get_running_loop()
        if not self._pace_started:
            self._pace_started = True
            self._pace_next_time = loop.time()
        while len(self._pace_buffer) >= frame_bytes:
            now = loop.time()
            if self._pace_next_time < now:
                self._pace_next_time = now
            else:
                await sleep_hook()
            if self._guard and (self._pace_next_time is None
                                or not self._pace_started):
                break
            del self._pace_buffer[:frame_bytes]
            self.frames += 1
            self._pace_next_time += self._frame_ms / 1000


@pytest.mark.asyncio
async def test_interruption_during_the_sleep_does_not_raise():
    loop = _PacingLoop(guard=True)

    async def sleep_hook():
        # The interruption lands while we are suspended, exactly as a barge-in
        # on another task would.
        loop.interrupt()

    await loop.run(160, sleep_hook)          # must not raise
    assert loop._pace_next_time is None, "the reset must stand"


@pytest.mark.asyncio
async def test_without_the_guard_it_raises_typeerror():
    """Proof the guard is what fixes it, not incidental behaviour."""
    loop = _PacingLoop(guard=False)

    async def sleep_hook():
        loop.interrupt()

    with pytest.raises(TypeError, match="NoneType"):
        await loop.run(160, sleep_hook)


@pytest.mark.asyncio
async def test_uninterrupted_pacing_still_emits_every_frame():
    """The guard must not cost frames on the ordinary path."""
    loop = _PacingLoop(guard=True)

    async def sleep_hook():
        return None                          # no interruption

    await loop.run(160, sleep_hook)
    assert loop.frames == 10, "1600 bytes / 160 = 10 frames"
    assert loop._pace_next_time is not None
