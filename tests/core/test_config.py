"""Tests for shunyalabs._core._config."""

import os

import pytest

from shunyalabs._core._config import ClientConfig


class TestClientConfig:
    def test_default_urls(self):
        config = ClientConfig()
        assert "tts.shunyalabs.ai" in config.resolve_tts_url()
        assert "asr.shunyalabs.ai" in config.resolve_asr_url()

    def test_override_urls(self):
        config = ClientConfig(
            tts_url="http://localhost:8003",
            asr_url="http://localhost:8080",
        )
        assert config.resolve_tts_url() == "http://localhost:8003"
        assert config.resolve_asr_url() == "http://localhost:8080"

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_TTS_URL", "http://env-tts:8003")
        config = ClientConfig()
        assert config.resolve_tts_url() == "http://env-tts:8003"

    def test_constructor_takes_priority_over_env(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_TTS_URL", "http://env-tts:8003")
        config = ClientConfig(tts_url="http://constructor:9000")
        assert config.resolve_tts_url() == "http://constructor:9000"

    def test_default_timeout_and_retries(self):
        config = ClientConfig()
        assert config.timeout == 60.0
        assert config.max_retries == 2

    def test_custom_timeout(self):
        config = ClientConfig(timeout=120.0, max_retries=5)
        assert config.timeout == 120.0
        assert config.max_retries == 5

    def test_resolve_api_key_from_config(self, monkeypatch):
        monkeypatch.delenv("SHUNYALABS_API_KEY", raising=False)
        config = ClientConfig(api_key="direct-key")
        assert config.resolve_api_key() == "direct-key"

    def test_resolve_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "env-key")
        config = ClientConfig()
        assert config.resolve_api_key() == "env-key"
