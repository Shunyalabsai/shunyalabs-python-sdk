"""Tests for shunyalabs._core._exceptions."""

import pytest

from shunyalabs._core._exceptions import (
    APIError,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    ShunyalabsError,
    raise_for_status,
)


class TestExceptionHierarchy:
    def test_api_error_inherits_base(self):
        assert issubclass(APIError, ShunyalabsError)

    def test_auth_error_inherits_api_error(self):
        assert issubclass(AuthenticationError, APIError)

    def test_rate_limit_inherits_api_error(self):
        assert issubclass(RateLimitError, APIError)

    def test_server_error_inherits_api_error(self):
        assert issubclass(ServerError, APIError)


class TestRaiseForStatus:
    def test_401_raises_authentication_error(self):
        with pytest.raises(AuthenticationError):
            raise_for_status(401, {"error": "unauthorized"})

    def test_403_raises_permission_denied(self):
        with pytest.raises(PermissionDeniedError):
            raise_for_status(403, {"error": "forbidden"})

    def test_404_raises_not_found(self):
        with pytest.raises(NotFoundError):
            raise_for_status(404, {"error": "not found"})

    def test_429_raises_rate_limit(self):
        with pytest.raises(RateLimitError):
            raise_for_status(429, {"error": "too many requests"})

    def test_500_raises_server_error(self):
        with pytest.raises(ServerError):
            raise_for_status(500, {"error": "internal"})

    def test_502_raises_server_error(self):
        with pytest.raises(ServerError):
            raise_for_status(502, {"error": "bad gateway"})

    def test_unknown_4xx_raises_api_error(self):
        with pytest.raises(APIError):
            raise_for_status(418, {"error": "teapot"})

    def test_200_does_not_raise(self):
        # Should not raise for success status codes
        raise_for_status(200, {})
