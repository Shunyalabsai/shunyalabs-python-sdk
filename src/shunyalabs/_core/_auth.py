"""Authentication for the Shunyalabs SDK.

Two strategies:

* :class:`StaticKeyAuth` — presents the API key directly as a Bearer token.
  Kept for internal / on-prem gateways that accept a raw key.
* :class:`TokenAuth` — exchanges the API key for a short-lived RS256 access
  token at the website token endpoint and presents THAT token (never the raw
  key) to the serving endpoints. This is the auth model the real-time services
  (asrv2prod, ttsv2) expect: the key mints a token, the token is what streams.
"""

import asyncio
import os
import time
from typing import Optional

from ._exceptions import ConfigurationError


class StaticKeyAuth:
    """Authentication using a static API key.

    Args:
        api_key: The Shunyalabs API key. Falls back to SHUNYALABS_API_KEY env var.

    Examples:
        >>> auth = StaticKeyAuth("your-api-key")
        >>> auth.get_api_key()
        'your-api-key'
        >>> auth.get_auth_headers()
        {'Authorization': 'Bearer your-api-key'}
    """

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY")
        if not self._api_key:
            raise ConfigurationError(
                "API key required: provide api_key or set SHUNYALABS_API_KEY environment variable"
            )

    def __repr__(self) -> str:
        if len(self._api_key) > 8:
            masked = f"{self._api_key[:4]}...{self._api_key[-4:]}"
        else:
            masked = "***"
        return f"StaticKeyAuth(api_key='{masked}')"

    def __str__(self) -> str:
        return "StaticKeyAuth(***)"

    def get_api_key(self) -> str:
        """Get the raw API key string (for JSON body auth)."""
        return self._api_key

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for HTTP requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    async def aget_auth_headers(self) -> dict[str, str]:
        """Async version of get_auth_headers."""
        return self.get_auth_headers()


class TokenAuth:
    """Auth that mints a short-lived access token from an API key and refreshes it.

    The API key is POSTed to the website token endpoint
    (``https://app.shunyalabs.ai/api/auth/token``); the returned RS256 JWT — not
    the raw key — is presented as ``Authorization: Bearer <jwt>`` to the serving
    endpoints. The token is cached and re-minted shortly before it expires, so a
    long-lived pipeline keeps working without ever putting the raw key on the wire
    to the ASR/TTS services.

    Args:
        api_key: Shunyalabs API key. Falls back to ``SHUNYALABS_API_KEY``.
        mint_url: Token endpoint. Falls back to ``SHUNYALABS_AUTH_URL`` then the
            public default.
        ttl_seconds: Requested token lifetime (the server may clamp it).
        refresh_buffer_seconds: Re-mint this many seconds before expiry.
    """

    _DEFAULT_MINT_URL = "https://app.shunyalabs.ai/api/auth/token"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        mint_url: Optional[str] = None,
        ttl_seconds: int = 900,
        refresh_buffer_seconds: int = 120,
    ) -> None:
        self._api_key = api_key or os.environ.get("SHUNYALABS_API_KEY")
        if not self._api_key:
            raise ConfigurationError(
                "API key required: provide api_key or set SHUNYALABS_API_KEY environment variable"
            )
        self._mint_url = mint_url or os.environ.get("SHUNYALABS_AUTH_URL") or self._DEFAULT_MINT_URL
        self._ttl = int(ttl_seconds)
        self._buffer = int(refresh_buffer_seconds)
        self._token: Optional[str] = None
        self._expires_at: float = 0.0          # monotonic deadline
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        masked = f"{self._api_key[:4]}...{self._api_key[-4:]}" if len(self._api_key) > 8 else "***"
        return f"TokenAuth(api_key='{masked}', mint_url='{self._mint_url}')"

    def __str__(self) -> str:
        return "TokenAuth(***)"

    def get_api_key(self) -> str:
        return self._api_key

    async def _mint(self) -> None:
        import httpx  # local import: only needed when actually minting

        sep = "&" if "?" in self._mint_url else "?"
        url = f"{self._mint_url}{sep}expires_in={self._ttl}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Accept": "application/json",
                    },
                )
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(f"Token mint request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ConfigurationError(
                f"Token mint failed: HTTP {resp.status_code} {resp.text[:200]}"
            )
        self._store_token(resp.json())

    def _store_token(self, data: dict) -> None:
        token = data.get("token")
        if not token:
            raise ConfigurationError(f"Token mint returned no token: {str(data)[:200]}")
        self._token = token
        self._expires_at = time.monotonic() + float(data.get("expires_in") or self._ttl)

    def _fresh(self) -> bool:
        return bool(self._token) and time.monotonic() < self._expires_at - self._buffer

    def _mint_sync(self) -> None:
        import httpx

        sep = "&" if "?" in self._mint_url else "?"
        url = f"{self._mint_url}{sep}expires_in={self._ttl}"
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json"},
                timeout=30,
            )
        except Exception as exc:  # noqa: BLE001
            raise ConfigurationError(f"Token mint request failed: {exc}") from exc
        if resp.status_code != 200:
            raise ConfigurationError(f"Token mint failed: HTTP {resp.status_code} {resp.text[:200]}")
        self._store_token(resp.json())

    async def ensure_token(self) -> str:
        """Return a valid token, minting or refreshing if necessary (async)."""
        if self._fresh():
            return self._token  # type: ignore[return-value]
        async with self._lock:
            if self._fresh():  # a concurrent caller may have just minted
                return self._token  # type: ignore[return-value]
            await self._mint()
        return self._token  # type: ignore[return-value]

    def ensure_token_sync(self) -> str:
        """Return a valid token, minting or refreshing synchronously if necessary."""
        if not self._fresh():
            self._mint_sync()
        return self._token  # type: ignore[return-value]

    async def aget_auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.ensure_token()}"}

    def get_auth_headers(self) -> dict[str, str]:
        """Sync accessor. Mints/refreshes the token synchronously if needed, so the blocking
        (sync) SDK paths work; the async paths (streaming, async batch) use aget_auth_headers()."""
        return {"Authorization": f"Bearer {self.ensure_token_sync()}"}


__all__ = ["StaticKeyAuth", "TokenAuth"]
