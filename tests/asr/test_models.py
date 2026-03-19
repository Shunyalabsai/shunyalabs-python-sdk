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
    WordResult,
    parse_streaming_message,
)


class TestTranscriptionConfig:
    def test_defaults(self):
        config = TranscriptionConfig(model="zero-indic")
        assert config.language_code == "auto"
        assert config.output_script == "auto"
        assert config.word_timestamps is False
        assert config.enable_diarization is False
        assert config.enable_speaker_identification is False

    def test_to_form_fields_basic(self):
        config = TranscriptionConfig(model="zero-indic", language_code="en")
        fields = config.to_form_fields()
        assert fields["language_code"] == "en"
        assert fields["model"] == "zero-indic"

    def test_to_form_fields_booleans_lowercase(self):
        config = TranscriptionConfig(model="zero-indic", enable_summarization=True)
        fields = config.to_form_fields()
        assert fields["enable_summarization"] == "true"
        assert fields["enable_diarization"] == "false"

    def test_to_form_fields_omits_none(self):
        config = TranscriptionConfig(model="zero-indic")
        fields = config.to_form_fields()
        assert "intent_choices" not in fields
        assert "hash_keywords" not in fields
        assert "keyterm_keywords" not in fields
        assert "output_language" not in fields
        assert "project" not in fields

    def test_to_form_fields_serializes_lists(self):
        config = TranscriptionConfig(
            model="zero-indic",
            intent_choices=["greeting", "farewell"],
        )
        fields = config.to_form_fields()
        assert fields["intent_choices"] == '["greeting", "farewell"]'

    def test_to_form_fields_keyterm_keywords(self):
        config = TranscriptionConfig(
            model="zero-indic",
            enable_keyterm_normalization=True,
            keyterm_keywords=["EMI", "NACH mandate"],
        )
        fields = config.to_form_fields()
        assert fields["keyterm_keywords"] == '["EMI", "NACH mandate"]'
        assert fields["enable_keyterm_normalization"] == "true"

    def test_to_form_fields_output_language(self):
        config = TranscriptionConfig(model="zero-indic", output_language="en")
        fields = config.to_form_fields()
        assert fields["output_language"] == "en"

    def test_to_form_fields_speaker_identification(self):
        config = TranscriptionConfig(
            model="zero-indic",
            enable_diarization=True,
            enable_speaker_identification=True,
            project="my_project",
        )
        fields = config.to_form_fields()
        assert fields["enable_diarization"] == "true"
        assert fields["enable_speaker_identification"] == "true"
        assert fields["project"] == "my_project"

    def test_removed_fields_not_present(self):
        config = TranscriptionConfig(model="zero-indic")
        assert not hasattr(config, "task")
        assert not hasattr(config, "use_vad_chunking")
        assert not hasattr(config, "chunk_size")
        assert not hasattr(config, "enable_denoising")
        assert not hasattr(config, "enable_medical_correction")
        assert not hasattr(config, "enable_translation")
        assert not hasattr(config, "target_language")
        assert not hasattr(config, "enable_transliteration")
        assert not hasattr(config, "enable_code_switch_correction")
        assert not hasattr(config, "enable_language_identification")


class TestWordResult:
    def test_creation(self):
        w = WordResult(word="नमस्ते", start=0.53, end=0.93, score=-4.2)
        assert w.word == "नमस्ते"
        assert w.start == 0.53
        assert w.end == 0.93
        assert w.score == -4.2

    def test_score_optional(self):
        w = WordResult(word="hello", start=0.0, end=0.5)
        assert w.score is None


class TestSegmentResult:
    def test_basic(self):
        seg = SegmentResult(start=0.0, end=1.5, text="Hello")
        assert seg.speaker is None
        assert seg.emotion is None
        assert seg.words is None

    def test_with_speaker_and_emotion(self):
        seg = SegmentResult(
            start=0.5, end=3.2, text="नमस्ते",
            speaker="SPEAKER_00", emotion="angry",
        )
        assert seg.speaker == "SPEAKER_00"
        assert seg.emotion == "angry"

    def test_with_words(self):
        seg = SegmentResult(
            start=0.5, end=5.7, text="नमस्ते जी",
            words=[
                WordResult(word="नमस्ते", start=0.53, end=0.93, score=-4.2),
                WordResult(word="जी", start=1.49, end=1.65, score=-2.4),
            ],
        )
        assert len(seg.words) == 2
        assert seg.words[0].word == "नमस्ते"


class TestTranscriptionResult:
    def test_minimal_creation(self):
        result = TranscriptionResult(text="Hello world")
        assert result.text == "Hello world"
        assert result.success is True
        assert result.request_id == ""
        assert result.segments == []
        assert result.speakers == []

    def test_full_creation(self):
        result = TranscriptionResult(
            success=True,
            request_id="req-123",
            text="Hello world",
            segments=[SegmentResult(start=0.0, end=1.5, text="Hello world")],
            detected_language="en",
            speakers=[],
            audio_duration=1.5,
            inference_time_ms=200.0,
        )
        assert result.request_id == "req-123"
        assert len(result.segments) == 1
        assert result.segments[0].text == "Hello world"
        assert result.detected_language == "en"

    def test_with_speakers(self):
        result = TranscriptionResult(
            text="[Priya] hi [Rahul] hello",
            speakers=["Priya", "Rahul"],
            segments=[
                SegmentResult(start=0.5, end=3.2, text="hi", speaker="Priya"),
                SegmentResult(start=4.1, end=6.8, text="hello", speaker="Rahul"),
            ],
        )
        assert result.speakers == ["Priya", "Rahul"]
        assert result.segments[0].speaker == "Priya"

    def test_with_nlp_analysis(self):
        result = TranscriptionResult(
            text="Test",
            nlp_analysis=NLPAnalysis(summary="A test summary"),
        )
        assert result.nlp_analysis.summary == "A test summary"

    def test_with_normalized_text(self):
        result = TranscriptionResult(
            text="आपकी emi की तारीख",
            nlp_analysis=NLPAnalysis(normalized_text="आपकी EMI की तारीख"),
        )
        assert result.nlp_analysis.normalized_text == "आपकी EMI की तारीख"

    def test_model_dump(self):
        result = TranscriptionResult(text="Hello", request_id="r1")
        data = result.model_dump()
        assert data["text"] == "Hello"
        assert data["request_id"] == "r1"
        assert data["speakers"] == []


class TestStreamingConfig:
    def test_defaults(self):
        config = StreamingConfig()
        assert config.language == "auto"
        assert config.sample_rate == 16000
        assert config.dtype == "int16"

    def test_to_ws_payload(self):
        config = StreamingConfig(language="en")
        payload = config.to_ws_payload()
        assert payload["language"] == "en"
        assert payload["sample_rate"] == 16000

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
