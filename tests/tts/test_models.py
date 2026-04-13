"""Tests for shunyalabs.tts._models."""

import base64
import tempfile
from pathlib import Path

import pytest

from shunyalabs.tts._models import (
    OutputFormat,
    TTSChunk,
    TTSCompletion,
    TTSConfig,
    TTSResult,
)


class TestOutputFormat:
    def test_values(self):
        assert OutputFormat.PCM.value == "pcm"
        assert OutputFormat.WAV.value == "wav"
        assert OutputFormat.MP3.value == "mp3"
        assert OutputFormat.OGG_OPUS.value == "ogg_opus"
        assert OutputFormat.FLAC.value == "flac"


class TestTTSConfig:
    def test_defaults(self):
        config = TTSConfig()
        assert config.model == "zero-indic"
        assert config.voice is None
        assert config.response_format == OutputFormat.WAV
        assert config.speed == 1.0
        assert config.max_tokens == 2048
        assert config.word_timestamps is False

    def test_to_request_payload_basic(self):
        config = TTSConfig()
        payload = config.to_request_payload(text="Hello")
        assert payload["input"] == "Hello"
        assert payload["request_type"] == "batch"
        assert payload["model"] == "zero-indic"
        assert payload["response_format"] == "wav"

    def test_to_request_payload_streaming(self):
        config = TTSConfig()
        payload = config.to_request_payload(
            text="Hi",
            request_type="streaming",
        )
        assert payload["request_type"] == "streaming"

    def test_custom_config(self):
        config = TTSConfig(
            language="en",
            response_format=OutputFormat.WAV,
            speed=1.5,
            voice="Nisha",
        )
        payload = config.to_request_payload(text="test")
        assert payload["language"] == "en"
        assert payload["response_format"] == "wav"
        assert payload["speed"] == 1.5
        assert payload["voice"] == "Nisha"

    def test_none_fields_omitted(self):
        config = TTSConfig()
        payload = config.to_request_payload(text="t")
        # reference_wav is None by default, should not be in payload
        assert "reference_wav" not in payload

    def test_word_timestamps_in_payload(self):
        config = TTSConfig(word_timestamps=True)
        payload = config.to_request_payload(text="hello")
        assert payload["word_timestamps"] is True


class TestTTSResult:
    def test_creation(self):
        result = TTSResult(
            request_id="req-1",
            audio_data=b"\x00\x01\x02",
            sample_rate=16000,
            duration_seconds=1.5,
        )
        assert result.request_id == "req-1"
        assert result.audio_data == b"\x00\x01\x02"
        assert result.sample_rate == 16000
        assert result.duration_seconds == 1.5

    def test_save(self, tmp_path):
        result = TTSResult(
            request_id="req-1",
            audio_data=b"audio_bytes_here",
            duration_seconds=0.5,
        )
        out_path = str(tmp_path / "test.pcm")
        result.save(out_path)
        assert Path(out_path).read_bytes() == b"audio_bytes_here"

    def test_from_api_response(self):
        audio_raw = b"raw audio data"
        audio_b64 = base64.b64encode(audio_raw).decode()

        data = {
            "request_id": "req-2",
            "audio_data": audio_b64,
            "sample_rate": 22050,
            "duration_seconds": 2.0,
            "format": "wav",
        }
        result = TTSResult.from_api_response(data)
        assert result.request_id == "req-2"
        assert result.audio_data == audio_raw
        assert result.sample_rate == 22050
        assert result.format == "wav"

    def test_from_api_response_with_timestamps(self):
        data = {
            "request_id": "req-3",
            "audio_data": base64.b64encode(b"x").decode(),
            "duration_seconds": 1.0,
            "word_timestamps": [
                {"word": "hello", "start": 0.0, "end": 0.5},
                {"word": "world", "start": 0.5, "end": 1.0},
            ],
        }
        result = TTSResult.from_api_response(data)
        assert len(result.word_timestamps) == 2
        assert result.word_timestamps[0]["word"] == "hello"
        assert result.word_timestamps[1]["start"] == 0.5


class TestTTSChunk:
    def test_creation(self):
        chunk = TTSChunk(request_id="r1", chunk_index=0)
        assert chunk.type == "chunk"
        assert chunk.chunk_index == 0
        assert chunk.is_final is False


class TestTTSCompletion:
    def test_creation(self):
        completion = TTSCompletion(
            request_id="r1",
            status="complete",
            total_chunks=5,
            total_duration_seconds=3.2,
        )
        assert completion.type == "completion"
        assert completion.status == "complete"
        assert completion.total_chunks == 5
