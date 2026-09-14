"""Live-tuning config, the turn-end signal, and the commit control frame.

These cover the three things that made the streaming knobs unreachable before
1.1.0: fields that did not exist, a message type that was not in the enum, and a
finalise verb that could only close the socket.
"""

import asyncio

import pytest
from pydantic import ValidationError

from shunyalabs.asr._models import (
    StreamingConfig,
    StreamingFinal,
    StreamingMessageType,
    StreamingUtteranceEnd,
    parse_streaming_message,
)
from shunyalabs.asr._streaming import ASRStreamingConnection


class _FakeTransport:
    """Records what was sent; returns nothing."""

    def __init__(self):
        self.sent = []
        self._closed = False

    async def send_message(self, msg):
        self.sent.append(msg)

    async def close(self):
        self._closed = True


class TestStreamingConfigTuning:
    def test_tuning_fields_reach_the_payload(self):
        cfg = StreamingConfig(
            language="en",
            sample_rate=8000,
            endpoint_silence_ms=250,
            decode_every_ms=960,
            vad="silero",
            codeswitch=True,
            model="zero-codeswitch",
        )
        payload = cfg.to_ws_payload()
        assert payload["endpoint_silence_ms"] == 250
        assert payload["decode_every_ms"] == 960
        assert payload["vad"] == "silero"
        assert payload["codeswitch"] is True
        assert payload["model"] == "zero-codeswitch"

    def test_unset_tuning_fields_are_omitted(self):
        # The gateway must apply its own defaults rather than receive explicit
        # nulls, so None values never go on the wire.
        payload = StreamingConfig(language="en").to_ws_payload()
        for key in ("endpoint_silence_ms", "decode_every_ms", "vad", "codeswitch", "model"):
            assert key not in payload

    def test_unknown_field_raises_instead_of_vanishing(self):
        # The regression this guards: a mistyped latency knob used to be dropped
        # in silence, so the setting appeared to be applied and was not.
        with pytest.raises(ValidationError):
            StreamingConfig(endpoint_silence=250)

    def test_legacy_ignored_fields_still_accepted(self):
        # Documented as inert, but they must not break existing callers.
        cfg = StreamingConfig(chunk_size_sec=0.5, silence_threshold_sec=1.5)
        assert cfg.to_ws_payload()["chunk_size_sec"] == 0.5


class TestUtteranceEnd:
    def test_utterance_end_is_a_known_type(self):
        msg = parse_streaming_message({"type": "utterance_end", "seg": 4})
        assert isinstance(msg, StreamingUtteranceEnd)
        assert msg.segment_id == 4
        assert msg.seg == 4

    def test_final_carries_end_of_utterance(self):
        real = parse_streaming_message(
            {"type": "final", "seg": 2, "text": "hello", "end_of_utterance": True}
        )
        assert real.end_of_utterance is True

        # A forced maximum-length cut: same event type, but the turn is NOT over
        # and no utterance_end will follow.
        cut = parse_streaming_message(
            {"type": "final", "seg": 3, "text": "and then", "end_of_utterance": False}
        )
        assert cut.end_of_utterance is False

    def test_final_refined_parses_as_a_final(self):
        # It used to fall through to the unknown-type branch and come back as a
        # StreamingError, which has no `.text` -- so subscribers could not use it.
        msg = parse_streaming_message(
            {"type": "final_refined", "seg": 2, "text": "namaste"}
        )
        assert isinstance(msg, StreamingFinal)
        assert msg.text == "namaste"


class TestWireFieldNames:
    """/v1/realtime sends seg/elapsed_ms; the older gateway sent segment_id/latency_ms."""

    def test_realtime_names_populate(self):
        msg = parse_streaming_message(
            {"type": "partial", "seg": 1, "delta": "he", "text": "he", "elapsed_ms": 12}
        )
        assert msg.segment_id == 1
        assert msg.latency_ms == 12
        assert msg.delta == "he"

    def test_legacy_names_still_populate(self):
        msg = parse_streaming_message(
            {"type": "partial", "segment_id": 7, "text": "x", "latency_ms": 30}
        )
        assert msg.segment_id == 7
        assert msg.latency_ms == 30


class TestCommit:
    def test_commit_sends_control_frame_and_keeps_socket_open(self):
        transport = _FakeTransport()
        conn = ASRStreamingConnection(transport, "sess-1")

        asyncio.run(conn.commit())

        assert transport.sent == [{"type": "commit"}]
        assert not conn.is_closed
        assert not transport._closed

    def test_flush_is_an_alias(self):
        transport = _FakeTransport()
        conn = ASRStreamingConnection(transport, "sess-1")
        asyncio.run(conn.flush())
        assert transport.sent == [{"type": "commit"}]

    def test_commit_on_closed_connection_raises(self):
        from shunyalabs._core._exceptions import TransportError

        conn = ASRStreamingConnection(_FakeTransport(), "sess-1")
        conn._closed = True
        with pytest.raises(TransportError):
            asyncio.run(conn.commit())


class TestEffectiveConfig:
    def test_ready_echo_is_exposed(self):
        # The gateway clamps and echoes; callers need to see what took effect
        # rather than assume their request was honoured.
        ready = {
            "type": "ready",
            "session_id": "s",
            "endpoint_silence_ms": 200,
            "decode_every_ms": 640,
        }
        conn = ASRStreamingConnection(_FakeTransport(), "s", ready=ready)
        assert conn.effective_config["endpoint_silence_ms"] == 200

    def test_effective_config_is_a_copy(self):
        conn = ASRStreamingConnection(_FakeTransport(), "s", ready={"a": 1})
        conn.effective_config["a"] = 999
        assert conn.effective_config["a"] == 1

    def test_defaults_to_empty_when_absent(self):
        conn = ASRStreamingConnection(_FakeTransport(), "s")
        assert conn.effective_config == {}
