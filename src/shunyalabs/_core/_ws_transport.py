"""WebSocket transport layer for the Shunyalabs SDK.

Handles both modern (websockets >= 13.x) and legacy (websockets 10.x)
library versions. Supports JSON and binary message types.

Adapted from the existing Flow SDK transport.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional, Union
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from ._auth import StaticKeyAuth
from ._exceptions import ConnectionError, TimeoutError, TransportError
from ._logging import get_logger
from ._models import WsConnectionConfig

try:  # websockets >= 13.x
    from websockets.asyncio.client import ClientConnection
    from websockets.asyncio.client import connect

    _WS_HEADERS_KEY = "additional_headers"
except ImportError:
    try:  # websockets 10.x - 12.x (legacy)
        from websockets.legacy.client import WebSocketClientProtocol as ClientConnection  # type: ignore
        from websockets.legacy.client import connect  # type: ignore

        _WS_HEADERS_KEY = "extra_headers"
    except ImportError:
        ClientConnection = None  # type: ignore
        connect = None  # type: ignore
        _WS_HEADERS_KEY = "extra_headers"

logger = get_logger(__name__)


class WsTransport:
    """WebSocket transport for Shunyalabs API communication.

    Supports both JSON and binary message types.
    Handles connection lifecycle and authentication.

    Args:
        url: The WebSocket URL to connect to.
        auth: Authentication object.
        conn_config: WebSocket connection configuration.
        sdk_component: SDK component name for URL tracking (e.g., "asr", "tts").
    """

    __slots__ = (
        "_url",
        "_auth",
        "_conn_config",
        "_sdk_component",
        "_send_auth_headers",
        "_websocket",
        "_closed",
        "_logger",
    )

    def __init__(
        self,
        url: str,
        auth: StaticKeyAuth,
        conn_config: Optional[WsConnectionConfig] = None,
        sdk_component: str = "sdk",
        send_auth_headers: bool = True,
    ) -> None:
        if connect is None:
            raise ImportError(
                "websockets is required for WebSocket transport. "
                "Install with: pip install 'shunyalabs[ASR]' or 'shunyalabs[TTS]'"
            )
        self._url = url
        self._auth = auth
        self._conn_config = conn_config or WsConnectionConfig()
        self._sdk_component = sdk_component
        self._send_auth_headers = send_auth_headers
        self._websocket: Optional[ClientConnection] = None
        self._closed = False
        self._logger = logger

    async def connect(self, ws_headers: Optional[dict] = None) -> None:
        """Establish WebSocket connection."""
        if self._websocket or self._closed:
            return

        url_with_params = self._prepare_url()
        self._logger.debug("Connecting to WebSocket: %s", url_with_params)

        headers = {}
        if ws_headers:
            headers.update(ws_headers)
        if self._send_auth_headers:
            headers.update(self._auth.get_auth_headers())

        try:
            ws_kwargs: dict = {
                _WS_HEADERS_KEY: headers,
                **self._conn_config.to_dict(),
            }
            self._websocket = await connect(url_with_params, **ws_kwargs)
        except asyncio.TimeoutError as e:
            raise TimeoutError(f"WebSocket connection timeout: {e}")
        except Exception as e:
            raise ConnectionError(f"WebSocket connection error: {e}")

    async def send_message(self, message: Any) -> None:
        """Send a message through the WebSocket.

        Args:
            message: Dict/list (JSON), str (text), or bytes (binary).
        """
        if not self._websocket:
            raise TransportError("Not connected")

        try:
            if isinstance(message, (dict, list)):
                data = json.dumps(message)
            else:
                data = message
            await self._websocket.send(data)
        except Exception as e:
            raise TransportError(f"Send message failed: {e}")

    async def receive_message(self) -> Union[dict, bytes, str]:
        """Receive and parse a message from the WebSocket.

        Returns:
            Parsed dict for JSON messages, raw bytes for binary, or str for text.
        """
        if not self._websocket:
            raise TransportError("Not connected")

        try:
            raw_data = await self._websocket.recv()

            if isinstance(raw_data, bytes):
                return raw_data

            try:
                return json.loads(raw_data)
            except json.JSONDecodeError:
                return raw_data

        except json.JSONDecodeError as e:
            raise TransportError(f"Invalid JSON received: {e}")
        except Exception as e:
            raise TransportError(f"Receive message failed: {e}")

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            finally:
                self._websocket = None
                self._closed = True

    @property
    def is_connected(self) -> bool:
        """Check if the WebSocket connection is active."""
        return self._websocket is not None and not self._closed

    def _prepare_url(self) -> str:
        """Prepare the WebSocket URL with SDK version tracking."""
        from .._version import __version__

        parsed = urlparse(self._url)
        query_params = dict(parse_qsl(parsed.query))
        query_params["sm-sdk"] = f"python-{self._sdk_component}-v{__version__}"
        updated_query = urlencode(query_params)
        return urlunparse(parsed._replace(query=updated_query))


__all__ = ["WsTransport"]
