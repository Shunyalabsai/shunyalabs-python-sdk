"""Tests for shunyalabs._core._auth."""

import os

import pytest

from shunyalabs._core._auth import StaticKeyAuth
from shunyalabs._core._exceptions import ConfigurationError


class TestStaticKeyAuth:
    def test_explicit_key(self):
        auth = StaticKeyAuth("my-key")
        assert auth.get_api_key() == "my-key"

    def test_auth_headers(self):
        auth = StaticKeyAuth("my-key")
        headers = auth.get_auth_headers()
        assert headers["Authorization"] == "Bearer my-key"

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("SHUNYALABS_API_KEY", raising=False)
        with pytest.raises(ConfigurationError, match="API key"):
            StaticKeyAuth(None)

    def test_env_var_fallback(self, monkeypatch):
        monkeypatch.setenv("SHUNYALABS_API_KEY", "env-key")
        auth = StaticKeyAuth(None)
        assert auth.get_api_key() == "env-key"
