"""Tests for shunyalabs.asr._models."""

import pytest

from shunyalabs.asr._models import (
    NLPAnalysis,
    SegmentResult,
    StreamingConfig,
    StreamingDone,
    StreamingError,
    StreamingFinal,
    StreamingFinalSegment,
    StreamingMessageType,
    StreamingPartial,
    TranscriptionConfig,
    TranscriptionResult,
    parse_streaming_message,
)


class TestTranscriptionConfig:
    def test_defaults(self):
        config = TranscriptionConfig()
        assert config.language_code == "auto"
        assert config.task == "transcribe"
        assert config.use_vad_chunking is True

    def test_to_form_fields_basic(self):
        config = TranscriptionConfig(language_code="en")
        fields = config.to_form_fields()
        assert fields["language_code"] == "en"
        assert fields["task"] == "transcribe"
        assert fields["use_vad_chunking"] == "true"

    def test_to_form_fields_booleans_lowercase(self):
        config = TranscriptionConfig(enable_summarization=True)
        fields = config.to_form_fields()
        assert fields["enable_summarization"] == "true"
        assert fields["enable_diarization"] == "false"

    def test_to_form_fields_omits_none(self):
        config = TranscriptionConfig()
        fields = config.to_form_fields()
        assert "intent_choices" not in fields
        assert "hash_keywords" not in fields

    def test_to_form_fields_serializes_lists(self):
        config = TranscriptionConfig(intent_choices=["greeting", "farewell"])
        fields = config.to_form_fields()
        assert fields["intent_choices"] == '["greeting", "farewell"]'


class TestTranscriptionResult:
    def test_minimal_creation(self):
        result = TranscriptionResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.success is True
        assert result.request_id == ""
        assert result.segments == []

    def test_full_creation(self):
        result = TranscriptionResult(
            success=True,
            request_id="req-123",
            text="Hello world",
            segments=[SegmentResult(start=0.0, end=1.5, text="Hello world")],
            detected_language="en",
            audio_duration=1.5,
            inference_time_ms=200.0,
        )
        assert result.request_id == "req-123"
        assert len(result.segments) == 1
        assert result.segments[0].text == "Hello world"
        assert result.detected_language == "en"

    def test_with_nlp_analysis(self):
        result = TranscriptionResult(
            text="Test",
            nlp_analysis=NLPAnalysis(summary="A test summary"),
        )
        assert result.nlp_analysis.summary == "A test summary"

    def test_model_dump(self):
        result = TranscriptionResult(text="Hello", request_id="r1")
        data = result.model_dump()
        assert data["text"] == "Hello"
        assert data["request_id"] == "r1"


class TestStreamingConfig:
    def test_defaults(self):
        config = StreamingConfig()
        assert config.language == "auto"
        assert config.sample_rate == 16000
        assert config.dtype == "int16"

    def test_to_ws_payload(self):
        config = StreamingConfig(language="en", api_key="key-123")
        payload = config.to_ws_payload()
        assert payload["language"] == "en"
        assert payload["api_key"] == "key-123"

    def test_to_ws_payload_excludes_none(self):
        config = StreamingConfig()
        payload = config.to_ws_payload()
        assert "api_key" not in payload  # None by default


class TestStreamingMessageType:
    def test_values(self):
        assert StreamingMessageType.READY.value == "ready"
        assert StreamingMessageType.PARTIAL.value == "partial"
        assert StreamingMessageType.FINAL.value == "final"
        assert StreamingMessageType.DONE.value == "done"
        assert StreamingMessageType.ERROR.value == "error"


class TestParseStreamingMessage:
    def test_parse_partial(self):
        msg = parse_streaming_message({"type": "partial", "text": "Hello"})
        assert isinstance(msg, StreamingPartial)
        assert msg.text == "Hello"

    def test_parse_final_segment(self):
        msg = parse_streaming_message({"type": "final_segment", "text": "Done."})
        assert isinstance(msg, StreamingFinalSegment)
        assert msg.text == "Done."

    def test_parse_final(self):
        msg = parse_streaming_message({"type": "final", "text": "All done"})
        assert isinstance(msg, StreamingFinal)

    def test_parse_done(self):
        msg = parse_streaming_message({"type": "done", "total_segments": 3})
        assert isinstance(msg, StreamingDone)
        assert msg.total_segments == 3

    def test_parse_error(self):
        msg = parse_streaming_message({"type": "error", "message": "fail"})
        assert isinstance(msg, StreamingError)
        assert msg.message == "fail"

    def test_parse_unknown(self):
        msg = parse_streaming_message({"type": "unknown_type"})
        assert isinstance(msg, StreamingError)
        assert "Unknown" in msg.message
