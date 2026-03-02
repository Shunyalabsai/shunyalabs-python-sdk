"""Tests for shunyalabs._core._retry."""

from shunyalabs._core._retry import RETRYABLE_STATUS_CODES, should_retry


class TestRetry:
    def test_retryable_codes(self):
        for code in [429, 500, 502, 503, 504]:
            assert should_retry(code), f"{code} should be retryable"

    def test_non_retryable_codes(self):
        for code in [200, 201, 400, 401, 403, 404]:
            assert not should_retry(code), f"{code} should not be retryable"

    def test_retryable_status_codes_set(self):
        assert 429 in RETRYABLE_STATUS_CODES
        assert 500 in RETRYABLE_STATUS_CODES
        assert 200 not in RETRYABLE_STATUS_CODES
