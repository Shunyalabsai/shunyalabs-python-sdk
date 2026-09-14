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
import threading
import time
from typing import Dict, Optional, Tuple

from ._exceptions import ConfigurationError


class _SharedTokenState:
    """Token state shared by every :class:`TokenAuth` for the same credential.

    A voice pipeline builds one service object per stream -- often one STT and
    one TTS per concurrent call -- and each used to hold its own token. Fifty
    concurrent calls therefore meant a hundred mint requests, all on the critical
    path of call setup. Sharing the state means one mint per credential, refreshed
    once for everyone.
    """

    __slots__ = ("token", "expires_at", "endpoints", "_alock", "_alock_loop", "_tlock")

    def __init__(self) -> None:
        self.token: Optional[str] = None
        self.expires_at: float = 0.0        # monotonic deadline
        self.endpoints: dict = {}
        self._alock: Optional[asyncio.Lock] = None
        self._alock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._tlock = threading.Lock()

    def async_lock(self) -> asyncio.Lock:
        """An asyncio lock bound to the *running* loop.

        Rebuilt if the loop changed: an ``asyncio.Lock`` binds to the first loop
        that touches it and raises if later used from another, which a
        process-wide cache would otherwise hit whenever a host runs more than one
        loop over its lifetime (``asyncio.run`` twice, a test suite, a worker that
        restarts its loop).
        """
        loop = asyncio.get_running_loop()
        if self._alock is None or self._alock_loop is not loop:
            self._alock = asyncio.Lock()
            self._alock_loop = loop
        return self._alock

    def sync_lock(self) -> threading.Lock:
        return self._tlock


# Keyed by (api_key, mint_url, ttl) so callers asking for different lifetimes, or
# pointing at different minters, never share a token.
_TOKEN_STATES: Dict[Tuple[str, str, int], _SharedTokenState] = {}
_TOKEN_STATES_GUARD = threading.Lock()


def _shared_token_state(key: Tuple[str, str, int]) -> _SharedTokenState:
    state = _TOKEN_STATES.get(key)
    if state is None:
        with _TOKEN_STATES_GUARD:
            state = _TOKEN_STATES.get(key)
            if state is None:
                state = _SharedTokenState()
                _TOKEN_STATES[key] = state
    return state


def reset_token_cache() -> None:
    """Drop every cached token. For tests and credential rotation."""
    with _TOKEN_STATES_GUARD:
        _TOKEN_STATES.clear()


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

    # StaticKeyAuth never mints, so it carries no server-provided endpoints.
    def endpoints(self) -> dict:
        return {}

    async def aget_endpoints(self) -> dict:
        return {}

    def get_endpoints_sync(self) -> dict:
        return {}


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
        # Token, expiry and endpoints live in process-wide state keyed by the
        # credential, so N service instances mint once between them rather than
        # once each. See _SharedTokenState.
        self._state = _shared_token_state((self._api_key, self._mint_url, self._ttl))

    # The token/expiry/endpoints are shared state; these keep the previous
    # attribute names readable for anything that reached for them.
    @property
    def _token(self) -> Optional[str]:
        return self._state.token

    @property
    def _expires_at(self) -> float:
        return self._state.expires_at

    @property
    def _endpoints(self) -> dict:
        # Endpoints optionally delivered by the token service, e.g.
        # {"asr_ws": ..., "asr_http": ..., "tts_ws": ..., "tts_http": ...}.
        # Empty until a mint returns them; lets the control plane repoint the
        # data plane without an SDK release.
        return self._state.endpoints

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
        self._state.token = token
        self._state.expires_at = time.monotonic() + float(data.get("expires_in") or self._ttl)
        # Optional server-provided data-plane endpoints (absent today; used
        # transparently once the token service starts returning them).
        eps = data.get("endpoints")
        self._state.endpoints = eps if isinstance(eps, dict) else {}

    def _fresh(self) -> bool:
        return bool(self._state.token) and time.monotonic() < self._state.expires_at - self._buffer

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
        """Return a valid token, minting or refreshing if necessary (async).

        The lock is shared across every instance using this credential, so a
        burst of concurrent calls produces one mint, not one per instance.
        """
        if self._fresh():
            return self._state.token  # type: ignore[return-value]
        async with self._state.async_lock():
            if self._fresh():  # a concurrent caller may have just minted
                return self._state.token  # type: ignore[return-value]
            await self._mint()
        return self._state.token  # type: ignore[return-value]

    def ensure_token_sync(self) -> str:
        """Return a valid token, minting or refreshing synchronously if necessary."""
        if self._fresh():
            return self._state.token  # type: ignore[return-value]
        with self._state.sync_lock():
            if self._fresh():
                return self._state.token  # type: ignore[return-value]
            self._mint_sync()
        return self._state.token  # type: ignore[return-value]

    async def aget_auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self.ensure_token()}"}

    def get_auth_headers(self) -> dict[str, str]:
        """Sync accessor. Mints/refreshes the token synchronously if needed, so the blocking
        (sync) SDK paths work; the async paths (streaming, async batch) use aget_auth_headers()."""
        return {"Authorization": f"Bearer {self.ensure_token_sync()}"}

    def endpoints(self) -> dict:
        """Endpoints already delivered by the token service (may be empty until minted)."""
        return dict(self._state.endpoints)

    async def aget_endpoints(self) -> dict:
        """Ensure a token (minting if needed) and return any server-provided endpoints."""
        await self.ensure_token()
        return dict(self._state.endpoints)

    def get_endpoints_sync(self) -> dict:
        self.ensure_token_sync()
        return dict(self._state.endpoints)


def resolve_endpoint(*, arg: Optional[str], server: Optional[str],
                     env_var: str, default: str) -> str:
    """Resolve an endpoint URL by precedence:

    explicit constructor arg -> server-provided (token) -> env var -> default.
    """
    return arg or server or os.environ.get(env_var) or default


__all__ = ["StaticKeyAuth", "TokenAuth", "resolve_endpoint", "reset_token_cache"]
