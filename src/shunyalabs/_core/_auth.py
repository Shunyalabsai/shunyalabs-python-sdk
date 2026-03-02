"""Authentication for the Shunyalabs SDK.

Simple API key auth — the key is passed in request bodies or headers
as required by each gateway.
"""

import os
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

    def get_api_key(self) -> str:
        """Get the raw API key string (for JSON body auth)."""
        return self._api_key

    def get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers for HTTP requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    async def aget_auth_headers(self) -> dict[str, str]:
        """Async version of get_auth_headers."""
        return self.get_auth_headers()


__all__ = ["StaticKeyAuth"]
