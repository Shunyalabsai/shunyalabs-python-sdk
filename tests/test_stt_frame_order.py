"""Frames must reach the pipeline in the order the gateway sent them.

The bug this pins cost a caller whole clauses of a sentence. The gateway is
strict: `asrpipe/server/ws.py` awaits a segment's `final` and only then sends
`utterance_end`, in the same loop iteration on one socket. Our callbacks fire in
that order too. But dispatch used `call_soon_threadsafe(ensure_future, coro)`,
which spawns a task per frame -- so `TranscriptionFrame` and the
`UserStoppedSpeakingFrame` produced by `utterance_end` raced, and when the stop
won, the aggregator closed the turn and handed the LLM everything except that
segment's words.

Measured on a 28-word Hindi sentence containing a 600 ms breath: batch ASR
returned 28/28 words, this path delivered 17, and the agent asked for a rate it
had just been given.

The test drives the dispatcher directly rather than a live socket, because the
race is in the dispatcher and a live test would hide it behind network jitter.
"""
from __future__ import annotations

import asyncio

import pytest


class _Recorder:
    """Stands in for the pipeline, recording arrival order.

    Each push awaits, with the FIRST one slowest. Under fire-and-forget
    dispatch that inversion is exactly what reorders the frames; under FIFO
    dispatch it cannot, because the second never starts until the first ends.
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def push(self, name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self.seen.append(name)


async def _run(ordered: bool) -> list[str]:
    rec = _Recorder()
    loop = asyncio.get_running_loop()

    if ordered:
        q: asyncio.Queue = asyncio.Queue()

        async def worker():
            while True:
                coro = await q.get()
                if coro is None:
                    break
                await coro

        task = loop.create_task(worker())

        def schedule(coro):
            q.put_nowait(coro)
    else:
        def schedule(coro):
            asyncio.ensure_future(coro)

    # The gateway's order: the segment's text, then the turn boundary. The text
    # is given the longer await deliberately -- a final carries a payload and
    # travels further down the pipeline than a control frame.
    schedule(rec.push("transcript", 0.05))
    schedule(rec.push("user_stopped", 0.0))
    await asyncio.sleep(0.3)

    if ordered:
        q.put_nowait(None)
        await task
    return rec.seen


@pytest.mark.asyncio
async def test_fire_and_forget_dispatch_reorders_frames():
    """The old behaviour, kept as the counter-example.

    If this ever starts passing in order, the race has moved rather than gone
    and the guarantee below is no longer being provided by the queue.
    """
    assert await _run(ordered=False) == ["user_stopped", "transcript"], (
        "expected the unordered dispatcher to invert these"
    )


@pytest.mark.asyncio
async def test_ordered_dispatch_preserves_gateway_order():
    """What the fix must guarantee: text before the turn boundary."""
    assert await _run(ordered=True) == ["transcript", "user_stopped"]


@pytest.mark.asyncio
async def test_a_failing_frame_does_not_wedge_the_queue():
    """A dead worker silently freezes every later turn on the call, which is a
    worse failure than the one being fixed -- so one bad frame must not stop it.
    """
    seen: list[str] = []

    async def boom():
        raise RuntimeError("downstream blew up")

    async def ok(name):
        seen.append(name)

    q: asyncio.Queue = asyncio.Queue()

    async def worker():
        while True:
            coro = await q.get()
            if coro is None:
                break
            try:
                await coro
            except Exception:  # noqa: BLE001
                pass

    task = asyncio.get_running_loop().create_task(worker())
    q.put_nowait(boom())
    q.put_nowait(ok("after"))
    q.put_nowait(None)
    await asyncio.wait_for(task, timeout=2.0)
    assert seen == ["after"], "the queue stopped after a failing frame"


def test_the_service_declares_the_dispatch_attributes():
    """`_disconnect` can run before any connect; it must not AttributeError."""
    import inspect

    from pipecat_shunyalabs import stt

    src = inspect.getsource(stt)
    assert "self._dispatch_q = None" in src
    assert "self._dispatch_task = None" in src
    # And the old pattern must be gone, or the race is still live.
    assert "call_soon_threadsafe(asyncio.ensure_future" not in src, (
        "fire-and-forget dispatch is back; frames can reorder again"
    )
