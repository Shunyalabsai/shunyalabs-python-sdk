"""LiveKit 1.1.6: STT turn events, and TTS quality / clause_first.

- STT sends exactly one START_OF_SPEECH and one END_OF_SPEECH per turn. The turn
  opens on the first partial with text, or on a final with text when no partial
  came first, and closes on the final that ends the utterance. An empty final
  closes a turn that partials opened (the service dropped a noise final) and
  does nothing otherwise.
- TTS ``quality`` and ``clause_first`` reach the real-time init when set and are
  left to the service otherwise; ``quality`` also reaches the batch request.

Runs the real plugin and the real SDK transport against local stubs. Skipped
unless livekit-agents is installed.
"""

import asyncio
import json

import pytest

pytest.importorskip("livekit.agents", reason="livekit-plugins-shunyalabsai not installed")
websockets = pytest.importorskip("websockets")

from livekit import rtc  # noqa: E402
from livekit.agents.stt import SpeechEventType as T  # noqa: E402
from livekit.plugins import shunyalabs  # noqa: E402
from shunyalabs._core._auth import StaticKeyAuth  # noqa: E402

from tests.plugins.test_livekit_tts_stream import Gateway as TtsGateway, _say, _tts  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def partial(text):
    return {"type": "partial", "text": text}


def final(text, eou=True):
    return {"type": "final", "text": text, "end_of_utterance": eou, "audio_duration_sec": 1.0}


UTT_END = {"type": "utterance_end"}


class ScriptGateway:
    """asrv2prod's /v1/realtime, playing a fixed list of messages on the first audio."""

    def __init__(self, script):
        self.script = script
        self.server = None
        self.port = None

    async def __aenter__(self):
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/v1/realtime"

    async def _handler(self, ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        answered = False
        try:
            async for raw in ws:
                if raw == "end":
                    await ws.send(json.dumps({"type": "done"}))
                    break
                if isinstance(raw, bytes) and not answered:
                    answered = True
                    for m in self.script:
                        await ws.send(json.dumps(m))
        except websockets.ConnectionClosed:
            pass


async def _events(script, idle_s=0.6):
    """Every event the plugin produces for `script`, collected until the stream goes quiet."""
    async with ScriptGateway(script) as gw:
        s = shunyalabs.STT(api_key="test-key", ws_url=gw.url, language="hinglish", codeswitch=True)
        s._auth = StaticKeyAuth("test-token")
        stream = s.stream()
        samples = 1600   # 100 ms at 16 kHz
        for _ in range(3):
            stream.push_frame(rtc.AudioFrame(b"\x00\x00" * samples, 16000, 1, samples))
        events = []
        it = stream.__aiter__()
        while True:
            try:
                ev = await asyncio.wait_for(it.__anext__(), timeout=idle_s)
            except asyncio.TimeoutError:
                break
            except StopAsyncIteration:
                break
            events.append(ev)
        stream.end_input()
        await stream.aclose()
    return events


def kinds(events):
    out = []
    for e in events:
        if e.type in (T.INTERIM_TRANSCRIPT, T.FINAL_TRANSCRIPT):
            out.append((e.type, e.alternatives[0].text))
        else:
            out.append(e.type)
    return out


class TestSttTurns:
    def test_partials_then_final(self):
        ev = run(_events([partial("I want"), partial("I want to"), final("I want to book."), UTT_END]))
        assert kinds(ev) == [T.START_OF_SPEECH,
                             (T.INTERIM_TRANSCRIPT, "I want"), (T.INTERIM_TRANSCRIPT, "I want to"),
                             (T.FINAL_TRANSCRIPT, "I want to book."), T.RECOGNITION_USAGE,
                             T.END_OF_SPEECH]

    def test_final_with_no_partial_opens_and_closes_the_turn(self):
        ev = run(_events([final("Yes."), UTT_END]))
        assert kinds(ev) == [T.START_OF_SPEECH, (T.FINAL_TRANSCRIPT, "Yes."),
                             T.RECOGNITION_USAGE, T.END_OF_SPEECH]

    def test_empty_final_closes_a_turn_opened_by_partials(self):
        # The service dropped a short unsure final (noise) and sent an empty one;
        # later empty finals (silence re-endpointing) add nothing.
        ev = run(_events([partial("Pop"), final(""), UTT_END, final(""), UTT_END, final(""), UTT_END]))
        assert kinds(ev) == [T.START_OF_SPEECH, (T.INTERIM_TRANSCRIPT, "Pop"), T.END_OF_SPEECH]

    def test_silence_produces_no_events(self):
        ev = run(_events([final(""), UTT_END, final(""), UTT_END]))
        assert ev == []

    def test_two_turns_each_get_one_start_and_one_end(self):
        ev = run(_events([partial("hello"), final("hello there"), UTT_END,
                          partial("one"), partial("one more"), final("one more thing"), UTT_END]))
        k = [e.type for e in ev]
        assert k.count(T.START_OF_SPEECH) == 2 and k.count(T.END_OF_SPEECH) == 2
        starts = [i for i, x in enumerate(k) if x == T.START_OF_SPEECH]
        ends = [i for i, x in enumerate(k) if x == T.END_OF_SPEECH]
        assert starts[0] < ends[0] < starts[1] < ends[1]

    def test_length_cut_keeps_the_turn_open(self):
        # A final cut at the segment length cap (end_of_utterance false) is mid-speech.
        ev = run(_events([partial("a long"), final("a long sentence", eou=False),
                          partial("that goes"), final("that goes on.", eou=True), UTT_END]))
        k = [e.type for e in ev]
        assert k.count(T.START_OF_SPEECH) == 1 and k.count(T.END_OF_SPEECH) == 1
        assert k[0] == T.START_OF_SPEECH and k[-1] == T.END_OF_SPEECH
        assert k.count(T.FINAL_TRANSCRIPT) == 2

    def test_refined_final_does_not_reopen_the_turn(self):
        ev = run(_events([partial("mera"), final("mera order"), UTT_END,
                          {"type": "final_refined", "text": "मेरा order"}]))
        assert kinds(ev) == [T.START_OF_SPEECH, (T.INTERIM_TRANSCRIPT, "mera"),
                             (T.FINAL_TRANSCRIPT, "mera order"), T.RECOGNITION_USAGE,
                             T.END_OF_SPEECH, (T.FINAL_TRANSCRIPT, "मेरा order")]


def _inits(gw):
    return [p for _, k, p in gw.log if k == "init"]


async def _init_for(**kw):
    async with TtsGateway() as gw:
        tts = _tts(gw, **kw)
        try:
            await _say(tts, "Hello there.")
        finally:
            await tts.aclose()
        return _inits(gw)[0]


class TestTtsOptions:
    def test_quality_and_clause_first_reach_the_realtime_init(self):
        init = run(_init_for(quality="low", clause_first=True))
        assert init["quality"] == "low"
        assert init["clause_first"] is True

    def test_clause_first_false_is_sent_when_given(self):
        assert run(_init_for(clause_first=False))["clause_first"] is False

    def test_unset_options_are_left_to_the_service(self):
        init = run(_init_for())
        assert "quality" not in init and "clause_first" not in init

    def test_quality_reaches_the_batch_request(self):
        tts = shunyalabs.TTS(api_key="test-key", quality="high")
        assert tts._make_tts_config().to_request_payload("hi")["quality"] == "high"
        tts2 = shunyalabs.TTS(api_key="test-key")
        assert "quality" not in tts2._make_tts_config().to_request_payload("hi")
