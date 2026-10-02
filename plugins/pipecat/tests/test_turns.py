"""`ShunyaUserTurnStrategies` must be faster than upstream AND still merge.

Both halves matter. The first test is why the helper exists; the second is why
it tunes the timeout rather than re-checking in the transcript handler, which
was tried, measured, and is a worse agent.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from pipecat.frames.frames import TranscriptionFrame, UserStoppedSpeakingFrame
from pipecat.turns.user_stop.external_user_turn_stop_strategy import (
    ExternalUserTurnStopStrategy,
)

from pipecat_shunyalabs import ShunyaUserTurnStrategies


def _tf(text: str) -> TranscriptionFrame:
    return TranscriptionFrame(text=text, user_id="u", timestamp="")


async def _drive(strategy, *, gap: float | None = None):
    """Feed the frame order our ASR actually produces, and report firings.

    Stop frame first, transcript about a millisecond later. That ordering is
    the whole problem: upstream's stop handler sees no text yet and does
    nothing, and its transcript handler only resets the timer.
    """
    fired: list[str] = []
    at: list[float] = []

    async def capture():
        fired.append(strategy._text)
        at.append(time.monotonic())

    strategy.trigger_user_turn_stopped = capture
    task = asyncio.create_task(strategy._task_handler())
    await asyncio.sleep(0.05)

    t0 = time.monotonic()
    await strategy.process_frame(UserStoppedSpeakingFrame())
    await asyncio.sleep(0.001)
    if gap is None:
        await strategy.process_frame(_tf("बानवे हज़ार लगेगा"))
    else:
        await strategy.process_frame(_tf("भिवंडी का "))
        await asyncio.sleep(gap)
        await strategy.process_frame(_tf("बानवे हज़ार लगेगा"))
    await asyncio.sleep(0.8)
    task.cancel()
    first_ms = (at[0] - t0) * 1000 if at else float("inf")
    return fired, first_ms


def _stop_strategy(strategies):
    assert strategies.stop and len(strategies.stop) == 1
    return strategies.stop[0]


@pytest.mark.asyncio
async def test_a_whole_final_reaches_the_model_sooner_than_upstream():
    """The reason this helper exists.

    Upstream pays 0-500 ms on every turn because the turn only fires from the
    TimeoutError branch of a free-running poll, and our ASR delivers the stop
    frame about 1 ms before the final. Measured on a freight agent, this was
    most of `llm_first_text_ms`: 1116 ms p50 against a model that answers the
    same turn in under 120 ms.
    """
    _, upstream_ms = await _drive(ExternalUserTurnStopStrategy(timeout=0.5))
    _, ours_ms = await _drive(_stop_strategy(ShunyaUserTurnStrategies()))

    assert upstream_ms > 400, "upstream should be paying the full poll"
    assert ours_ms < 250, f"no better than upstream: {ours_ms:.0f} ms"
    assert ours_ms < upstream_ms / 2


@pytest.mark.asyncio
@pytest.mark.parametrize("gap", [0.02, 0.05, 0.1])
async def test_a_final_split_within_the_window_is_still_merged(gap):
    """Why the fix is the timeout's VALUE and not the handler's logic.

    Re-checking in the transcript handler was tried. With the stop frame
    already seen, the FIRST piece of a split final satisfies the predicate and
    the turn fires on half an utterance -- measured as three firings for one
    utterance, the first missing the rate. That is the "repeats itself and
    misunderstands" failure, bought for 500 ms. So the merge must survive.
    """
    fired, _ = await _drive(_stop_strategy(ShunyaUserTurnStrategies()), gap=gap)

    assert len(fired) >= 1, "the turn never fired at all"
    assert "बानवे हज़ार" in fired[0], (
        f"answered half the utterance: {fired[0]!r} -- the merge window is too "
        f"short for a final split by {int(gap * 1000)} ms"
    )


@pytest.mark.asyncio
async def test_the_default_is_a_deliberate_trade_not_zero():
    """0 would answer every split final half-way; 0.5 is what we are fixing."""
    s = _stop_strategy(ShunyaUserTurnStrategies())
    assert 0 < s._timeout < 0.5
    # And it must stay passable, because 0.15 is OUR ASR's number: it is only
    # safe because the final arrives ~1 ms after the utterance-end signal.
    assert _stop_strategy(ShunyaUserTurnStrategies(timeout=0.4))._timeout == 0.4
