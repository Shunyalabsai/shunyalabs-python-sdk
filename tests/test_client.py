"""Tests for shunyalabs._client (top-level clients)."""

import os

import pytest

from shunyalabs import AsyncShunyaClient, ShunyaClient


class TestShunyaClient:
    def test_creation(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = ShunyaClient()
        assert client is not None

    def test_lazy_asr_namespace(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = ShunyaClient()
        assert client._asr is None
        asr = client.asr
        assert asr is not None
        assert client._asr is asr  # cached

    def test_lazy_tts_namespace(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = ShunyaClient()
        assert client._tts is None
        tts = client.tts
        assert tts is not None
        assert client._tts is tts

    def test_context_manager(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        with ShunyaClient() as client:
            assert client is not None
        # After exit, client should still exist (close is called)

    def test_custom_urls(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = ShunyaClient(
            tts_url="http://localhost:8003",
            asr_url="http://localhost:8080",
        )
        assert client._config.resolve_tts_url() == "http://localhost:8003"
        assert client._config.resolve_asr_url() == "http://localhost:8080"


class TestAsyncShunyaClient:
    def test_creation(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = AsyncShunyaClient()
        assert client is not None

    def test_lazy_asr_namespace(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = AsyncShunyaClient()
        asr = client.asr
        assert asr is not None

    def test_lazy_tts_namespace(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = AsyncShunyaClient()
        tts = client.tts
        assert tts is not None

    def test_lazy_flow_namespace(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        client = AsyncShunyaClient()
        flow = client.flow
        assert flow is not None

    @pytest.mark.asyncio
    async def test_async_context_manager(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "test-key")
        async with AsyncShunyaClient() as client:
            assert client is not None
