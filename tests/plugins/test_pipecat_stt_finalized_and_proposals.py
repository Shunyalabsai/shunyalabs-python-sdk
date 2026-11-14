"""pipecat 1.1.10: finals that end an utterance are marked finalized; turn frames are proposals.

Found by a live pipecat session test (14 Nov) against asrv2prod:

- Default setup (transport VAD + pipecat's turn strategies): pipecat waits a fixed 1.0 s after the
  VAD stop for a transcript that is not marked ``finalized``, and the gateway's final lands about
  1.0-1.1 s after speech ends, so long turns were closed before their last final and split.
- emit_turn_frames: the service emitted UserStarted/StoppedSpeakingFrame directly, which pipecat
  1.16 treats as "the service owns the interruption" -- the bot could not be interrupted -- and the
  stop (a system frame) could overtake the transcript queued before it.
- The SDK ran the plugin's plain-function callbacks in its thread pool, so ``utterance_end`` could be
  queued before the ``final`` sent ahead of it.

The fake gateway below sends what asrv2prod sends: empty finals are suppressed (its default), every
real endpoint is a final with end_of_utterance=true followed by ``utterance_end``, and a segment cut at
the length cap is a final with end_of_utterance=false -- followed, when the cut lands at the end of
speech, by nothing but ``utterance_end``. Messages are delivered as the SDK's receive loop delivers
them: each handler call is a task created on the running loop, back to back.

Skipped unless pipecat is installed.
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat", reason="pipecat-shunyalabsai plugin not installed")

from pipecat.frames.frames import (  # noqa: E402
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.task import PipelineParams  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402
from shunyalabs.asr._models import StreamingMessageType as MT  # noqa: E402

import pipecat_shunyalabs.stt as stt_mod  # noqa: E402
from pipecat_shunyalabs.stt import ShunyalabsSTTService  # noqa: E402

try:
    from pipecat.frames.frames import ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame
except ImportError:  # pragma: no cover - older pipecat
    ProposedUserStartedSpeakingFrame = ProposedUserStoppedSpeakingFrame = None

FRAME = b"\x01\x00" * 1600            # 100 ms at 16 kHz: one send to the gateway


def partial(text):
    return (MT.PARTIAL, SimpleNamespace(text=text, language="en"))


def final(text, eou=True):
    return (MT.FINAL, SimpleNamespace(text=text, language="en", end_of_utterance=eou,
                                      audio_duration_sec=1.0))


def refined(text):
    return (MT.FINAL_REFINED, SimpleNamespace(text=text, language="hi"))


UTT_END = (MT.UTTERANCE_END, SimpleNamespace())


class _Gateway:
    """Plays `script` on the first audio, the way the SDK's receive loop emits: a task per message."""

    def __init__(self, script):
        self.script = script
        self.handlers = {}
        self.is_closed = False
        self.effective_config = {}
        self.session_id = "test"
        self._played = False

    def on(self, mt):
        def deco(fn):
            self.handlers[mt] = fn
            return fn
        return deco

    def _emit(self, mt, msg):
        fn = self.handlers.get(mt)
        if fn is None:
            return
        if inspect.iscoroutinefunction(fn):
            asyncio.get_running_loop().create_task(fn(msg))
        else:                                     # what the SDK does with a plain function
            asyncio.get_running_loop().run_in_executor(None, fn, msg)

    async def send_audio(self, b):
        if not self._played:
            self._played = True
            asyncio.get_running_loop().create_task(self._play())

    async def _play(self):
        # Back to back, like one burst off the socket; a number in the script is a pause (seconds).
        for item in self.script:
            if isinstance(item, (int, float)):
                await asyncio.sleep(item)
            else:
                self._emit(*item)

    async def commit(self):
        pass

    async def end(self):
        pass

    async def close(self):
        self.is_closed = True


class _Streaming:
    gw = None

    def __init__(self, **kw):
        pass

    async def stream(self, config):
        return _Streaming.gw


class _Auth:
    async def aget_endpoints(self):
        return {}


async def _frames(monkeypatch, script, **kw):
    _Streaming.gw = _Gateway(script)
    monkeypatch.setattr(stt_mod, "AsyncStreamingASR", _Streaming)
    svc = ShunyalabsSTTService(api_key="test-key", language="hinglish", codeswitch=True, **kw)
    svc._auth = _Auth()
    audio = [InputAudioRawFrame(audio=FRAME, sample_rate=16000, num_channels=1) for _ in range(2)]
    down, _ = await run_test(svc, frames_to_send=[*audio, SleepFrame(sleep=0.3)],
                             pipeline_params=PipelineParams(audio_in_sample_rate=16000))
    keep = (TranscriptionFrame, InterimTranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame)
    if ProposedUserStartedSpeakingFrame is not None:
        keep += (ProposedUserStartedSpeakingFrame, ProposedUserStoppedSpeakingFrame)
    return [f for f in down if isinstance(f, keep)]


def _finals(frames):
    return [(f.text, f.finalized) for f in frames if type(f) is TranscriptionFrame]


class TestFinalized:
    @pytest.mark.asyncio
    async def test_a_final_that_ends_the_utterance_is_finalized(self, monkeypatch):
        out = await _frames(monkeypatch, [partial("I want"), final("I want to book."), UTT_END])
        assert _finals(out) == [("I want to book.", True)]

    @pytest.mark.asyncio
    async def test_a_capped_final_at_the_end_of_speech_is_finalized_by_utterance_end(self, monkeypatch):
        # Measured live: the cap landed at the end of a 4.5 s clip; only utterance_end followed.
        out = await _frames(monkeypatch, [partial("mera order"), final("mera order deliver nahi hua", eou=False),
                                          UTT_END, UTT_END])
        assert _finals(out) == [("mera order deliver nahi hua", True)]

    @pytest.mark.asyncio
    async def test_a_capped_final_mid_speech_goes_first_unmarked(self, monkeypatch):
        out = await _frames(monkeypatch, [partial("mera order"), final("mera order abhi tak", eou=False),
                                          partial("deliver"), final("deliver nahi hua", eou=True), UTT_END])
        assert _finals(out) == [("mera order abhi tak", False), ("deliver nahi hua", True)]

    @pytest.mark.asyncio
    async def test_silence_produces_nothing(self, monkeypatch):
        out = await _frames(monkeypatch, [UTT_END, UTT_END, UTT_END])
        assert out == []

    @pytest.mark.asyncio
    async def test_a_refinement_turns_the_marking_off(self, monkeypatch):
        # Plans that re-render scripts send final_refined after the raw final: the raw final is then
        # not the last text for its segment, so later raw finals are not marked.
        # The refinement comes some hundreds of ms after its final (a second decode).
        out = await _frames(monkeypatch, [final("mera order"), UTT_END, 0.1, refined("मेरा order"),
                                          final("aur ek baat"), UTT_END])
        assert _finals(out) == [("mera order", True), ("मेरा order", False), ("aur ek baat", False)]


class TestTtfsDefault:
    def test_default_path_tells_pipecat_the_real_latency(self):
        svc = ShunyalabsSTTService(api_key="test-key", language="en")
        assert getattr(svc, "_ttfs_p99_latency", None) == stt_mod._TTFS_P99_DEFAULT_S

    def test_commit_on_vad_stop_keeps_its_own(self):
        svc = ShunyalabsSTTService(api_key="test-key", language="en", commit_on_vad_stop=True)
        assert getattr(svc, "_ttfs_p99_latency", None) == stt_mod._TTFS_P99_S


@pytest.mark.skipif(ProposedUserStartedSpeakingFrame is None, reason="pipecat without turn proposals")
class TestTurnProposals:
    @pytest.mark.asyncio
    async def test_turn_frames_are_proposals_in_gateway_order(self, monkeypatch):
        out = await _frames(monkeypatch, [partial("I want"), final("I want to book."), UTT_END],
                            emit_turn_frames=True)
        kinds = [type(f).__name__ for f in out]
        assert kinds == ["ProposedUserStartedSpeakingFrame", "InterimTranscriptionFrame",
                         "TranscriptionFrame", "ProposedUserStoppedSpeakingFrame"]

    @pytest.mark.asyncio
    async def test_a_final_with_no_partial_is_a_whole_turn(self, monkeypatch):
        out = await _frames(monkeypatch, [final("Yes."), UTT_END], emit_turn_frames=True)
        kinds = [type(f).__name__ for f in out]
        assert kinds == ["ProposedUserStartedSpeakingFrame", "TranscriptionFrame",
                         "ProposedUserStoppedSpeakingFrame"]

    @pytest.mark.asyncio
    async def test_a_capped_final_closes_on_utterance_end_after_its_text(self, monkeypatch):
        out = await _frames(monkeypatch, [partial("mera"), final("mera order deliver nahi hua", eou=False),
                                          UTT_END], emit_turn_frames=True)
        kinds = [type(f).__name__ for f in out]
        assert kinds == ["ProposedUserStartedSpeakingFrame", "InterimTranscriptionFrame",
                         "TranscriptionFrame", "ProposedUserStoppedSpeakingFrame"]
        assert _finals(out) == [("mera order deliver nahi hua", True)]
