"""Turn-taking strategies for a pipeline whose turns come from our ASR.

Why this module exists, measured rather than argued. A pipeline using
pipecat's `ExternalUserTurnStrategies()` spends an extra 250-500 ms on every
single turn, and nothing in that API hints at it.

`ExternalUserTurnStopStrategy` takes `timeout=0.5`, described as "a short delay
used internally to handle consecutive or slightly delayed transcriptions". It is
not a debounce in practice, it is a poll:

    while True:
        try:
            await asyncio.wait_for(self._event.wait(), timeout=self._timeout)
            self._event.clear()
        except asyncio.TimeoutError:
            await self._maybe_trigger_user_turn_stopped()

The turn is only triggered from the `TimeoutError` branch of a free-running
loop. So the cost depends entirely on the ORDER two frames arrive in:

* transcript, then `UserStoppedSpeakingFrame` -> the stop handler calls
  `_maybe_trigger_user_turn_stopped()` itself and the turn fires at once.
* `UserStoppedSpeakingFrame`, then transcript -> the stop handler sees no text
  and does nothing; the transcript handler only sets the text and RESETS the
  timer, without re-checking. The turn then waits for the next tick: anywhere
  from 0 to `timeout`, averaging half of it.

Our ASR lands in the second case on essentially every turn. Measured against
both `asrv2prod` and an on-box deployment, the final transcript follows the
utterance-end signal by about **1 ms** -- so the stop frame wins the race by a
hair and the turn then sits in the poll.

Measured effect on a freight-negotiation agent, 8 calls through `asrv2prod`:

    llm_first_text_ms  p50   1116 ms  ->  792 ms
    turn_ms            p50   1638 ms  -> 1351 ms
    caller-perceived wait    2316 ms  -> 1990 ms
    tts_first_audio    p50    529 ms  ->  526 ms   (control, unmoved)
    asr_final_ms       p50      1 ms  ->    1 ms   (control, unmoved)

Only the stage under test moved, which is what makes the attribution sound.

WHAT DOES NOT WORK, tested so nobody retries it. The obvious fix looks like
re-checking in the transcript handler, so a complete turn fires whichever order
the two frames arrive in. It is wrong, and badly: with the stop frame already
seen, the FIRST piece of a split final satisfies the predicate and the turn
fires on half an utterance. Measured against upstream on a final split into
"भिवंडी का " + "बानवे हज़ार लगेगा":

    upstream (timeout=0.5)   fires 1x, text "भिवंडी का बानवे हज़ार लगेगा"
    with the re-check        fires 3x, first text "भिवंडी का "

Three turns for one utterance, the first missing the rate -- which is the
"it repeats itself and misunderstands" failure class, bought for 500 ms. The
timeout is load-bearing. So the lever here is its VALUE, not the logic.
"""

from __future__ import annotations

import logging

from pipecat.turns.user_start.external_user_turn_start_strategy import (
    ExternalUserTurnStartStrategy,
)
from pipecat.turns.user_stop.external_user_turn_stop_strategy import (
    ExternalUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

logger = logging.getLogger(__name__)


def ShunyaUserTurnStrategies(*, timeout: float = 0.15) -> UserTurnStrategies:
    """External turn strategies with the turn-stop poll shortened, not removed.

    Drop-in for `ExternalUserTurnStrategies()` in a pipeline where an external
    processor (our STT service) emits the speaking frames::

        from pipecat_shunyalabs import ShunyaUserTurnStrategies

        aggregator = LLMContextAggregatorPair(
            context,
            user_params=LLMUserAggregatorParams(
                user_turn_strategies=ShunyaUserTurnStrategies(),
            ),
        )

    A function rather than a dataclass subclass on purpose: upstream's
    `ExternalUserTurnStrategies` is a dataclass whose `__post_init__`
    unconditionally overwrites `start` and `stop` with no-argument strategies,
    so subclassing it cannot pass a timeout through. That is also why reaching
    for the convenience class silently reinstates the 0.5 s default.

    Args:
        timeout: How long to keep merging consecutive or late transcriptions
            into one turn, and therefore also the worst-case wait before a turn
            reaches the model.

            0.15 s, against upstream's 0.5 s. It is a trade and worth stating
            plainly: the window for merging a split final narrows from 500 ms
            to 150 ms, and a final split by MORE than 150 ms would be answered
            half-way. What makes it defensible on our stack is that the final
            arrives about 1 ms after the utterance-end signal, measured on both
            `asrv2prod` and an on-box deployment -- it comes as one piece, not
            dribbled out. Validated across 8 freight-negotiation calls through
            `asrv2prod`: no half-answers, and the suite's own hard checks went
            7/8 to 8/8.

            Raise it toward 0.5 for an ASR you have not measured, or one known
            to emit partial finals. Answering half an utterance is a worse
            failure than waiting for the rest of it.
    """
    return UserTurnStrategies(
        start=[ExternalUserTurnStartStrategy()],
        stop=[ExternalUserTurnStopStrategy(timeout=timeout)],
    )
