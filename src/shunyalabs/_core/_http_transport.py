"""HTTP transport layer for the Shunyalabs SDK.

Provides both sync (httpx) and async (aiohttp) HTTP transports with
retry logic and proper error handling.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Union

from ._auth import StaticKeyAuth
from ._exceptions import (
    ConnectionError,
    TimeoutError,
    TransportError,
    raise_for_status,
)
from ._logging import get_logger
from ._models import HttpConnectionConfig
from ._retry import RETRYABLE_STATUS_CODES, should_retry

logger = get_logger(__name__)


class AsyncHttpTransport:
    """Async HTTP transport using aiohttp.

    Args:
        url: Base URL for the API.
        auth: Authentication object.
        conn_config: Connection configuration.
        max_retries: Number of retries for transient failures.
    """

    def __init__(
        self,
        url: str,
        auth: StaticKeyAuth,
        conn_config: Optional[HttpConnectionConfig] = None,
        max_retries: int = 2,
    ) -> None:
        self._url = url.rstrip("/")
        self._auth = auth
        self._conn_config = conn_config or HttpConnectionConfig()
        self._max_retries = max_retries
        self._session = None

    async def _get_session(self):
        if self._session is None:
            try:
                import aiohttp
            except ImportError:
                raise ImportError(
                    "aiohttp is required for async HTTP transport. "
                    "Install with: pip install 'shunyalabs[ASR]' or 'shunyalabs[TTS]'"
                )
            timeout = aiohttp.ClientTimeout(
                connect=self._conn_config.connect_timeout,
                total=self._conn_config.operation_timeout,
            )
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def post_json(
        self,
        path: str,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict[str, Any]:
        """POST JSON data and return parsed JSON response."""
        import aiohttp

        session = await self._get_session()
        url = f"{self._url}{path}"
        req_headers = self._auth.get_auth_headers()
        if headers:
            req_headers.update(headers)

        last_exception = None
        for attempt in range(self._max_retries + 1):
            try:
                async with session.post(url, json=json_data, headers=req_headers) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 400:
                        if should_retry(resp.status) and attempt < self._max_retries:
                            last_exception = TransportError(f"HTTP {resp.status}")
                            continue
                        raise_for_status(resp.status, body)
                    return body
            except aiohttp.ClientConnectionError as e:
                last_exception = ConnectionError(f"Connection failed: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception
            except aiohttp.ServerTimeoutError as e:
                last_exception = TimeoutError(f"Request timed out: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception

        raise last_exception or TransportError("Request failed")

    async def post_form(
        self,
        path: str,
        form_data: Any,
        headers: Optional[dict] = None,
    ) -> dict[str, Any]:
        """POST multipart form data and return parsed JSON response."""
        import aiohttp

        session = await self._get_session()
        url = f"{self._url}{path}"
        req_headers = self._auth.get_auth_headers()
        if headers:
            req_headers.update(headers)

        last_exception = None
        for attempt in range(self._max_retries + 1):
            try:
                async with session.post(url, data=form_data, headers=req_headers) as resp:
                    body = await resp.json(content_type=None)
                    if resp.status >= 400:
                        if should_retry(resp.status) and attempt < self._max_retries:
                            last_exception = TransportError(f"HTTP {resp.status}")
                            continue
                        raise_for_status(resp.status, body)
                    return body
            except aiohttp.ClientConnectionError as e:
                last_exception = ConnectionError(f"Connection failed: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception
            except aiohttp.ServerTimeoutError as e:
                last_exception = TimeoutError(f"Request timed out: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception

        raise last_exception or TransportError("Request failed")

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None


class SyncHttpTransport:
    """Sync HTTP transport using httpx.

    Args:
        url: Base URL for the API.
        auth: Authentication object.
        conn_config: Connection configuration.
        max_retries: Number of retries for transient failures.
    """

    def __init__(
        self,
        url: str,
        auth: StaticKeyAuth,
        conn_config: Optional[HttpConnectionConfig] = None,
        max_retries: int = 2,
    ) -> None:
        self._url = url.rstrip("/")
        self._auth = auth
        self._conn_config = conn_config or HttpConnectionConfig()
        self._max_retries = max_retries
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx
            except ImportError:
                raise ImportError(
                    "httpx is required for sync HTTP transport. "
                    "Install with: pip install 'shunyalabs[ASR]' or 'shunyalabs[TTS]'"
                )
            self._client = httpx.Client(
                timeout=httpx.Timeout(
                    connect=self._conn_config.connect_timeout,
                    read=self._conn_config.operation_timeout,
                    write=self._conn_config.operation_timeout,
                    pool=self._conn_config.connect_timeout,
                ),
            )
        return self._client

    def post_json(
        self,
        path: str,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict[str, Any]:
        """POST JSON data and return parsed JSON response."""
        import httpx

        client = self._get_client()
        url = f"{self._url}{path}"
        req_headers = self._auth.get_auth_headers()
        if headers:
            req_headers.update(headers)

        last_exception = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = client.post(url, json=json_data, headers=req_headers)
                body = resp.json()
                if resp.status_code >= 400:
                    if should_retry(resp.status_code) and attempt < self._max_retries:
                        last_exception = TransportError(f"HTTP {resp.status_code}")
                        continue
                    raise_for_status(resp.status_code, body)
                return body
            except httpx.ConnectError as e:
                last_exception = ConnectionError(f"Connection failed: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception
            except httpx.ReadTimeout as e:
                last_exception = TimeoutError(f"Request timed out: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception

        raise last_exception or TransportError("Request failed")

    def post_form(
        self,
        path: str,
        data: Optional[dict] = None,
        files: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> dict[str, Any]:
        """POST multipart form data and return parsed JSON response."""
        import httpx

        client = self._get_client()
        url = f"{self._url}{path}"
        req_headers = self._auth.get_auth_headers()
        if headers:
            req_headers.update(headers)

        last_exception = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = client.post(url, data=data, files=files, headers=req_headers)
                body = resp.json()
                if resp.status_code >= 400:
                    if should_retry(resp.status_code) and attempt < self._max_retries:
                        last_exception = TransportError(f"HTTP {resp.status_code}")
                        continue
                    raise_for_status(resp.status_code, body)
                return body
            except httpx.ConnectError as e:
                last_exception = ConnectionError(f"Connection failed: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception
            except httpx.ReadTimeout as e:
                last_exception = TimeoutError(f"Request timed out: {e}")
                if attempt < self._max_retries:
                    continue
                raise last_exception

        raise last_exception or TransportError("Request failed")

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


__all__ = ["AsyncHttpTransport", "SyncHttpTransport"]
