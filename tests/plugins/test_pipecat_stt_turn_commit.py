"""commit_on_turn_end: a turn model decides at each VAD stop whether to commit the segment early.

Driven through a real pipecat pipeline (VAD frames and audio in, transcription frames out) against a
fake gateway connection and a fake turn model, so the tests pin the plugin's half of the contract: what
is sent to the gateway and when, and which final comes out marked ``finalized``. Whether the turn model
is RIGHT is a measurement, not a unit test.

Skipped unless pipecat is installed:

    pip install -e . -e plugins/pipecat && pytest tests/plugins
"""

import asyncio
import inspect
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat", reason="pipecat-shunyalabsai plugin not installed")

from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    InputAudioRawFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.task import PipelineParams  # noqa: E402
from pipecat.tests.utils import SleepFrame, run_test  # noqa: E402
from shunyalabs.asr._models import StreamingMessageType as MT  # noqa: E402

import pipecat_shunyalabs.stt as stt_mod  # noqa: E402
from pipecat_shunyalabs.stt import ShunyalabsSTTService  # noqa: E402

FRAME = b"\x01\x00" * 320          # 20 ms at 16 kHz; under min_send_bytes, so it stays in the tail buffer


class _FakeConn:
    """The gateway end of the socket: records what was sent, answers a commit with a final."""

    def __init__(self, answer="the whole sentence"):
        self.sent = []
        self.handlers = {}
        self.is_closed = False
        self.effective_config = {}
        self.session_id = "test"
        self._answer = answer

    def on(self, mt):
        def deco(fn):
            self.handlers[mt] = fn
            return fn
        return deco

    async def send_audio(self, b):
        self.sent.append(("audio", len(b)))

    async def commit(self):
        self.sent.append(("commit", None))
        if self._answer:
            self.final(self._answer)

    def final(self, text, eou=True):
        """The gateway's final, delivered as the SDK's receive loop does: a coroutine handler is
        scheduled as a task on the running loop, a plain one is called."""
        msg = SimpleNamespace(text=text, language="en", end_of_utterance=eou)
        fn = self.handlers[MT.FINAL]
        if inspect.iscoroutinefunction(fn):
            asyncio.get_running_loop().create_task(fn(msg))
        else:
            fn(msg)

    async def end(self):
        self.sent.append(("end", None))

    async def close(self):
        self.is_closed = True


class _FakeStreaming:
    conn = None

    def __init__(self, **kw):
        pass

    async def stream(self, config):
        return _FakeStreaming.conn


class _FakeAuth:
    async def aget_endpoints(self):
        return {}


class _FakeTurn:
    """Duck-typed BaseTurnAnalyzer: a fixed verdict after an optional think time; records its audio."""

    def __init__(self, verdict, think_s=0.0, then=None):
        self.verdict = verdict
        self.think_s = think_s
        self.then = then         # called 50 ms after each verdict (e.g. the gateway's own endpoint)
        self.audio = []          # is_speech label of each appended frame
        self.asked = 0
        self.sample_rate = None
        self.cleaned = False

    def set_sample_rate(self, sr):
        self.sample_rate = sr

    def update_vad_start_secs(self, s):
        pass

    def append_audio(self, buffer, is_speech):
        self.audio.append(is_speech)
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        self.asked += 1
        await asyncio.sleep(self.think_s)
        if self.then:
            asyncio.get_running_loop().call_later(0.05, self.then)
        return self.verdict, None

    def clear(self):
        pass

    async def cleanup(self):
        self.cleaned = True


def _service(monkeypatch, conn, **kw):
    _FakeStreaming.conn = conn
    monkeypatch.setattr(stt_mod, "AsyncStreamingASR", _FakeStreaming)
    svc = ShunyalabsSTTService(api_key="test-key", language=kw.pop("language", "en"), **kw)
    svc._auth = _FakeAuth()
    return svc


def _audio(n):
    return [InputAudioRawFrame(audio=FRAME, sample_rate=16000, num_channels=1) for _ in range(n)]


async def _run(svc, frames):
    down, _ = await run_test(svc, frames_to_send=frames,
                             pipeline_params=PipelineParams(audio_in_sample_rate=16000))
    return [f for f in down if isinstance(f, TranscriptionFrame)]


def _turn(after_stop=0.2, silence_frames=2):
    # 3 frames of speech stay under min_send_bytes, so all of it is still in the tail at the stop.
    return [VADUserStartedSpeakingFrame(start_secs=0.2), *_audio(3), VADUserStoppedSpeakingFrame(stop_secs=0.2),
            *_audio(silence_frames), SleepFrame(sleep=after_stop)]


class TestOffByDefault:
    def test_no_turn_model_is_built(self):
        svc = ShunyalabsSTTService(api_key="test-key", language="en")
        assert svc._turn_analyzer is None

    @pytest.mark.asyncio
    async def test_a_vad_stop_sends_no_commit(self, monkeypatch):
        conn = _FakeConn()
        svc = _service(monkeypatch, conn)
        await _run(svc, _turn())
        assert ("commit", None) not in conn.sent


class TestCommitOnTurnEnd:
    @pytest.mark.asyncio
    async def test_complete_commits_at_once_and_the_answer_is_finalized(self, monkeypatch):
        conn = _FakeConn(answer="book a ticket to Delhi")
        turn = _FakeTurn(EndOfTurnState.COMPLETE)
        svc = _service(monkeypatch, conn, commit_on_turn_end=True, turn_analyzer=turn)
        out = await _run(svc, _turn(silence_frames=0))
        assert turn.asked == 1
        commits = [i for i, s in enumerate(conn.sent) if s[0] == "commit"]
        assert len(commits) == 1
        # The tail held below min_send_bytes goes before the commit, or the last words miss the segment.
        assert sum(n for kind, n in conn.sent[:commits[0]] if kind == "audio") >= 3 * len(FRAME)
        assert [(f.text, f.finalized) for f in out] == [("book a ticket to Delhi", True)]

    @pytest.mark.asyncio
    async def test_incomplete_leaves_the_segment_to_the_gateway(self, monkeypatch):
        conn = _FakeConn()
        # The gateway's own endpoint ends the segment a little later. Since 1.1.10 that final is
        # marked too: it ends the utterance (end_of_utterance), so nothing more is coming for it.
        turn = _FakeTurn(EndOfTurnState.INCOMPLETE, then=lambda: conn.final("I want to"))
        svc = _service(monkeypatch, conn, commit_on_turn_end=True, turn_analyzer=turn)
        out = await _run(svc, _turn())
        assert turn.asked == 1
        assert ("commit", None) not in conn.sent
        assert [(f.text, f.finalized) for f in out] == [("I want to", True)]

    @pytest.mark.asyncio
    async def test_a_vad_start_withdraws_a_verdict_still_being_computed(self, monkeypatch):
        conn = _FakeConn()
        turn = _FakeTurn(EndOfTurnState.COMPLETE, think_s=0.2)
        svc = _service(monkeypatch, conn, commit_on_turn_end=True, turn_analyzer=turn)
        frames = [VADUserStartedSpeakingFrame(start_secs=0.2), *_audio(3), VADUserStoppedSpeakingFrame(stop_secs=0.2),
                  SleepFrame(sleep=0.05), VADUserStartedSpeakingFrame(start_secs=0.2), *_audio(2),
                  SleepFrame(sleep=0.3)]
        await _run(svc, frames)
        assert ("commit", None) not in conn.sent

    @pytest.mark.asyncio
    async def test_the_model_hears_the_audio_labelled_by_the_vad(self, monkeypatch):
        turn = _FakeTurn(EndOfTurnState.INCOMPLETE)
        svc = _service(monkeypatch, _FakeConn(), commit_on_turn_end=True, turn_analyzer=turn)
        await _run(svc, [*_audio(2), *_turn(after_stop=0.05)])
        assert turn.audio == [False] * 2 + [True] * 3 + [False] * 2
        assert turn.sample_rate == 16000
        assert turn.cleaned

    def test_languages_outside_the_list_keep_the_gateway_endpoint(self, caplog):
        with caplog.at_level(logging.WARNING, logger="pipecat_shunyalabs.stt"):
            svc = ShunyalabsSTTService(api_key="test-key", language="ta", commit_on_turn_end=True,
                                       turn_analyzer=_FakeTurn(EndOfTurnState.COMPLETE))
        assert svc._turn_analyzer is None
        assert "commit_on_turn_end is off" in caplog.text
        on = ShunyalabsSTTService(api_key="test-key", language="ta", commit_on_turn_end=True,
                                  turn_analyzer=_FakeTurn(EndOfTurnState.COMPLETE), turn_commit_languages=["ta"])
        assert on._turn_analyzer is not None

    def test_auto_is_not_a_language_the_model_was_checked_on(self):
        svc = ShunyalabsSTTService(api_key="test-key", language="auto", commit_on_turn_end=True,
                                   turn_analyzer=_FakeTurn(EndOfTurnState.COMPLETE))
        assert svc._turn_analyzer is None

    def test_the_two_commit_options_are_alternatives(self):
        with pytest.raises(ValueError):
            ShunyalabsSTTService(api_key="test-key", language="en", commit_on_vad_stop=True,
                                 commit_on_turn_end=True)
