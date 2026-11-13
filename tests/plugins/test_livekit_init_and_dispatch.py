"""LiveKit 1.1.5: what the init messages carry, and the order STT events arrive in.

- Both init messages name the plugin version (``client``), and the TTS socket
  sends the same string as its User-Agent.
- TTS ``speed`` reaches the real-time stream (it used to reach only the batch path).
- STT events are produced on the event loop, in the order the gateway sent them.
  The core runs a plain-function handler in a thread pool, one job per message,
  so a burst of partials and a final could reach LiveKit out of order.

Runs the real plugin and the real SDK transport against local stubs. Skipped
unless livekit-agents is installed.
"""

import asyncio
import json
import threading

import pytest

pytest.importorskip("livekit.agents", reason="livekit-plugins-shunyalabsai not installed")
websockets = pytest.importorskip("websockets")

from livekit import rtc  # noqa: E402
from livekit.agents.stt import SpeechEventType  # noqa: E402
from livekit.plugins import shunyalabs  # noqa: E402
from shunyalabs._core._auth import StaticKeyAuth  # noqa: E402

from tests.plugins.test_livekit_tts_stream import Gateway as TtsGateway, _say, _tts  # noqa: E402

CLIENT = f"livekit-plugins-shunyalabsai/{shunyalabs.__version__}"


def run(coro):
    return asyncio.run(coro)


class _HeaderGateway(TtsGateway):
    """The TTS stub, also recording each connection's User-Agent header(s)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.user_agents = []

    async def _handler(self, ws):
        self.user_agents.append(ws.request.headers.get_all("User-Agent"))
        await super()._handler(ws)


def _inits(gw):
    return [p for _, k, p in gw.log if k == "init"]


class TestTtsInit:
    def test_init_and_user_agent_name_the_plugin_version(self):
        async def main():
            async with _HeaderGateway() as gw:
                tts = _tts(gw)
                try:
                    await _say(tts, "Hello there.")
                finally:
                    await tts.aclose()
                assert _inits(gw)[0]["client"] == CLIENT
                assert gw.user_agents == [[CLIENT]]
        run(main())

    def test_speed_is_sent_on_the_realtime_stream(self):
        async def main():
            async with _HeaderGateway() as gw:
                tts = _tts(gw, speed=1.1)
                try:
                    await _say(tts, "Hello there.")
                finally:
                    await tts.aclose()
                assert _inits(gw)[0]["speed"] == 1.1
        run(main())

    def test_natural_speed_is_left_to_the_service(self):
        async def main():
            async with _HeaderGateway() as gw:
                tts = _tts(gw)
                try:
                    await _say(tts, "Hello there.")
                finally:
                    await tts.aclose()
                assert "speed" not in _inits(gw)[0]
        run(main())

    def test_client_string_comes_from_the_package_version(self):
        from livekit.plugins.shunyalabs._version import _CLIENT_ID

        assert _CLIENT_ID == CLIENT


class AsrGateway:
    """A local stand-in for asrv2prod's /v1/realtime.

    init -> ready. On the first audio it answers with a burst: ``partials`` partial
    messages, one final with text and end_of_utterance, and an utterance_end, back
    to back, as the gateway does at the end of a segment. "end" -> done, close.
    """

    def __init__(self, partials=40):
        self.partials = partials
        self.inits = []
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
        init = json.loads(await ws.recv())
        self.inits.append(init)
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        answered = False
        try:
            async for raw in ws:
                if raw == "end":
                    await ws.send(json.dumps({"type": "done"}))
                    break
                if isinstance(raw, bytes) and not answered:
                    answered = True
                    for i in range(self.partials):
                        await ws.send(json.dumps({"type": "partial", "text": f"p{i}"}))
                    await ws.send(json.dumps({"type": "final", "text": "the whole sentence",
                                              "end_of_utterance": True, "audio_duration_sec": 1.0}))
                    await ws.send(json.dumps({"type": "utterance_end"}))
        except websockets.ConnectionClosed:
            pass


def _stt(gw, **kw):
    s = shunyalabs.STT(api_key="test-key", ws_url=gw.url, language="hinglish", **kw)
    s._auth = StaticKeyAuth("test-token")
    return s


async def _transcribe(stt_obj, seconds=0.3):
    stream = stt_obj.stream()
    loop_thread = threading.get_ident()
    send_threads = set()
    orig = stream._event_ch.send_nowait

    def spy(ev):
        send_threads.add(threading.get_ident())
        orig(ev)

    stream._event_ch.send_nowait = spy
    samples = 1600   # 100 ms at 16 kHz
    for _ in range(int(seconds * 10)):
        stream.push_frame(rtc.AudioFrame(b"\x00\x00" * samples, 16000, 1, samples))
    events = []

    async def consume():
        async for ev in stream:
            events.append(ev)
            if ev.type == SpeechEventType.END_OF_SPEECH:
                break

    await asyncio.wait_for(consume(), timeout=10)
    stream.end_input()
    await stream.aclose()
    return events, send_threads, loop_thread


class TestStt:
    def test_init_names_the_plugin_version(self):
        async def main():
            async with AsrGateway(partials=1) as gw:
                await _transcribe(_stt(gw, codeswitch=True))
                assert gw.inits[0]["client"] == CLIENT
                assert gw.inits[0]["codeswitch"] is True
                assert gw.inits[0]["language"] == "hinglish"
        run(main())

    def test_events_arrive_on_the_loop_in_gateway_order(self):
        async def main():
            async with AsrGateway(partials=40) as gw:
                events, threads, loop_thread = await _transcribe(_stt(gw))
            kinds = [e.type for e in events]
            interims = [e.alternatives[0].text for e in events
                        if e.type == SpeechEventType.INTERIM_TRANSCRIPT]
            assert interims == [f"p{i}" for i in range(40)]
            assert kinds[-3:] == [SpeechEventType.FINAL_TRANSCRIPT,
                                  SpeechEventType.RECOGNITION_USAGE,
                                  SpeechEventType.END_OF_SPEECH]
            assert threads == {loop_thread}, "events must be sent from the event loop thread"
        run(main())
