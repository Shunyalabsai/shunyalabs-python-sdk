"""LiveKit TTS streaming: one pooled /v1/realtime session, text forwarded as it arrives.

Runs the real plugin, the real SDK WsTransport and LiveKit's SynthesizeStream /
AudioEmitter against a local stub of the gateway's realtime protocol. Skipped
unless livekit-agents is installed (the plugin is a separate distribution):

    pip install -e . -e plugins/livekit && pytest tests/plugins
"""

import asyncio
import json

import pytest

pytest.importorskip("livekit.agents", reason="livekit-plugins-shunyalabsai not installed")
websockets = pytest.importorskip("websockets")

from livekit.agents import APIStatusError  # noqa: E402
from livekit.plugins import shunyalabs  # noqa: E402
from livekit.plugins.shunyalabs.tts import StreamingTTS  # noqa: E402
from shunyalabs._core._auth import StaticKeyAuth  # noqa: E402

PCM_PER_CHAR = 480  # bytes of 24 kHz s16 PCM the stub returns per character (10 ms)


class Gateway:
    """A local stand-in for ttsv2's /v1/realtime.

    Mirrors the parts of the protocol the plugin relies on: init -> ready; text
    deltas are buffered and every COMPLETE sentence is spoken at once; flush speaks
    the remainder and answers `done`; cancel discards and answers `cancelled`.
    """

    def __init__(self, *, audio_delay=0.0, drop_after_turns=None, error_on=None):
        self.connections = 0
        self.log = []                 # (conn_no, kind, payload) in arrival order
        self.audio_delay = audio_delay
        self.drop_after_turns = drop_after_turns
        self.error_on = error_on
        self.turns = 0
        self.server = None
        self.port = None
        self.open_sockets = set()

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

    async def _speak(self, ws, text, gen, state):
        await ws.send(json.dumps({"type": "speaking", "piece": 0, "chars": len(text)}))
        if self.audio_delay:
            await asyncio.sleep(self.audio_delay)
        if state["gen"] != gen:
            return                    # cancelled while "synthesising": audio discarded
        await ws.send(b"\x01\x00" * (PCM_PER_CHAR * len(text) // 2))

    async def _handler(self, ws):
        self.connections += 1
        conn = self.connections
        self.open_sockets.add(ws)
        state = {"gen": 0}
        buf = ""
        work = asyncio.Queue()

        async def speaker():
            while True:
                kind, gen, text = await work.get()
                if gen != state["gen"]:
                    continue
                if kind == "text":
                    await self._speak(ws, text, gen, state)
                elif kind == "flush" and gen == state["gen"]:
                    await ws.send(json.dumps({"type": "done", "audio_ms": 0, "pieces": 1}))

        spk = asyncio.ensure_future(speaker())
        try:
            init = json.loads(await ws.recv())
            self.log.append((conn, "init", init))
            await ws.send(json.dumps({"type": "ready", "sample_rate": 24000, "voice": init.get("voice")}))
            async for raw in ws:
                if raw == "end":
                    # like the gateway: finish what is buffered, answer `done`, then close
                    self.log.append((conn, "end", None))
                    if buf.strip():
                        work.put_nowait(("text", state["gen"], buf.strip()))
                    buf = ""
                    work.put_nowait(("flush", state["gen"], None))
                    while not work.empty():
                        await asyncio.sleep(0.01)
                    await asyncio.sleep(0.05)
                    break
                msg = json.loads(raw)
                kind = msg.get("type")
                self.log.append((conn, kind, msg.get("text")))
                if kind == "text":
                    if self.error_on and self.error_on in msg["text"]:
                        await ws.send(json.dumps({"type": "error", "error": "synthesis failed for a segment"}))
                        continue
                    buf += msg["text"]
                    # speak complete sentences now, keep the rest
                    cut = max(buf.rfind(". "), buf.rfind("? "), buf.rfind("! "))
                    if buf.rstrip().endswith((".", "?", "!")):
                        cut = len(buf)
                    if cut > 0:
                        work.put_nowait(("text", state["gen"], buf[:cut + 1].strip()))
                        buf = buf[cut + 1:]
                elif kind == "flush":
                    if buf.strip():
                        work.put_nowait(("text", state["gen"], buf.strip()))
                    buf = ""
                    work.put_nowait(("flush", state["gen"], None))
                    self.turns += 1
                    if self.drop_after_turns and self.turns == self.drop_after_turns:
                        await asyncio.sleep(0.3)          # let the done go out, then drop the socket
                        await ws.close()
                        break
                elif kind == "cancel":
                    state["gen"] += 1
                    buf = ""
                    await ws.send(json.dumps({"type": "cancelled", "pieces": 0}))
        except websockets.ConnectionClosed:
            pass
        finally:
            spk.cancel()
            self.open_sockets.discard(ws)

    def texts(self, conn=None):
        return [p for c, k, p in self.log if k == "text" and (conn is None or c == conn)]

    def kinds(self, conn=None):
        return [k for c, k, _ in self.log if conn is None or c == conn]


def _tts(gw, **kw):
    t = shunyalabs.TTS(api_key="test-key", ws_url=gw.url, voice="Nisha", language="en", **kw)
    t._auth = StaticKeyAuth("test-token")
    return t


async def _say(tts, *chunks):
    stream = tts.stream()
    for c in chunks:
        stream.push_text(c)
    stream.end_input()
    frames = []
    async with stream:
        async for ev in stream:
            frames.append(ev)
    return frames


def _pcm_bytes(frames):
    return sum(len(ev.frame.data) * 2 for ev in frames)   # int16 samples -> bytes


def run(coro):
    return asyncio.run(coro)


class TestOneSocket:
    def test_three_turns_share_one_session(self):
        async def main():
            async with Gateway() as gw:
                tts = _tts(gw)
                try:
                    for text in ("Hello there.", "Your order ships today.", "Anything else?"):
                        frames = await _say(tts, text)
                        assert _pcm_bytes(frames) == PCM_PER_CHAR * len(text)
                        assert frames[-1].is_final
                finally:
                    await tts.aclose()
                assert gw.connections == 1, "every turn should reuse the pooled session"
                assert gw.kinds(1).count("init") == 1
                assert gw.kinds(1).count("flush") == 3
        run(main())

    def test_three_flushes_in_one_stream_are_three_segments(self):
        # LiveKit 1.8 creates one stream per reply, but the protocol allows several
        # flushes per stream; each must become its own LiveKit segment, in order.
        async def main():
            async with Gateway() as gw:
                tts = _tts(gw)
                stream = tts.stream()
                for text in ("One.", "Two two.", "Three three three."):
                    stream._input_ch.send_nowait(text)
                    stream._input_ch.send_nowait(StreamingTTS._FlushSentinel())
                stream._input_ch.close()
                seg_bytes = {}
                async with stream:
                    async for ev in stream:
                        seg_bytes[ev.segment_id] = seg_bytes.get(ev.segment_id, 0) + len(ev.frame.data) * 2
                await tts.aclose()
                assert gw.connections == 1
                assert list(seg_bytes.values()) == [PCM_PER_CHAR * len(t) for t in ("One.", "Two two.", "Three three three.")]
        run(main())


class TestTextIsForwardedAsItArrives:
    def test_text_reaches_the_gateway_before_flush(self):
        async def main():
            async with Gateway() as gw:
                tts = _tts(gw)
                stream = tts.stream()
                stream.push_text("First sentence is complete. ")
                stream.push_text("Second one is still being")
                first_audio = None
                async with stream:
                    it = stream.__aiter__()
                    first_audio = await asyncio.wait_for(it.__anext__(), 5)
                    # audio for the finished sentence arrived while the turn is still open
                    assert gw.texts() == ["First sentence is complete. ", "Second one is still being"]
                    assert "flush" not in gw.kinds()
                    stream.push_text(" written.")
                    stream.end_input()
                    rest = [ev async for ev in it]
                await tts.aclose()
                assert first_audio is not None
                assert gw.kinds().index("flush") > gw.kinds().index("text")
                total = len(first_audio.frame.data) * 2 + _pcm_bytes(rest)
                assert total == PCM_PER_CHAR * (len("First sentence is complete.") + len("Second one is still being written."))
        run(main())

    def test_style_tag_prefixes_the_first_delta_only(self):
        async def main():
            async with Gateway() as gw:
                tts = _tts(gw, style="<Happy>")
                await _say(tts, "Great ", "news.")
                await tts.aclose()
                assert gw.texts() == ["<Happy> Great ", "news."]
        run(main())


class TestInterruption:
    def test_cancel_mid_segment_keeps_the_session_clean(self):
        async def main():
            async with Gateway(audio_delay=0.5) as gw:
                tts = _tts(gw)
                stream = tts.stream()
                stream.push_text("This reply gets interrupted by the caller.")
                stream.end_input()
                await asyncio.sleep(0.2)              # text sent, audio still "synthesising"
                await stream.aclose()                  # barge-in
                await asyncio.sleep(0.3)              # background cancel + drain
                assert "cancel" in gw.kinds(1)
                gw.audio_delay = 0.0
                frames = await _say(tts, "Next turn.")
                await tts.aclose()
                assert gw.connections == 1, "a clean cancel must not cost a reconnect"
                assert _pcm_bytes(frames) == PCM_PER_CHAR * len("Next turn."), "no stale audio may leak"
        run(main())

    def test_unacknowledged_cancel_closes_the_socket(self):
        async def main():
            async with Gateway(audio_delay=0.5) as gw:
                tts = _tts(gw)
                stream = tts.stream()
                stream.push_text("Interrupted.")
                stream.end_input()
                await asyncio.sleep(0.2)
                # the gateway goes silent: stop answering by closing every socket server-side
                for ws in list(gw.open_sockets):
                    await ws.close()
                await stream.aclose()
                await asyncio.sleep(0.2)
                gw.audio_delay = 0.0
                frames = await _say(tts, "Recovered.")
                await tts.aclose()
                assert gw.connections == 2
                assert _pcm_bytes(frames) == PCM_PER_CHAR * len("Recovered.")
        run(main())


class TestReconnect:
    def test_dropped_pooled_socket_is_replaced_transparently(self):
        async def main():
            async with Gateway(drop_after_turns=1) as gw:
                tts = _tts(gw)
                await _say(tts, "First turn.")
                await asyncio.sleep(0.5)               # server drops the idle pooled socket
                frames = await _say(tts, "Second turn after the drop.")
                await tts.aclose()
                assert gw.connections == 2
                assert _pcm_bytes(frames) == PCM_PER_CHAR * len("Second turn after the drop.")
                assert gw.texts(2) == ["Second turn after the drop."], "the turn is replayed on the new socket"
        run(main())

    def test_gateway_error_is_not_retried(self):
        async def main():
            async with Gateway(error_on="BOOM") as gw:
                tts = _tts(gw)
                with pytest.raises(APIStatusError):
                    await _say(tts, "BOOM goes the segment.")
                await tts.aclose()
        run(main())


class TestBatchPathUnchanged:
    def test_synthesize_still_uses_the_batch_api(self):
        class _Result:
            audio_data = b"\x01\x00" * 2400

        class _Transport:
            closed = False

            async def close(self):
                _Transport.closed = True

        class _Batch:
            _transport = _Transport()

            async def synthesize(self, text, config):
                assert text == "Hello"
                return _Result()

        async def main():
            tts = shunyalabs.TTS(api_key="test-key", voice="Nisha", language="en")
            tts._auth = StaticKeyAuth("test-token")
            tts._make_batch_tts = lambda: _Batch()
            frames = [ev async for ev in tts.synthesize("Hello")]
            await tts.aclose()
            assert sum(len(ev.frame.data) * 2 for ev in frames) == 4800
            assert _Transport.closed
        run(main())
