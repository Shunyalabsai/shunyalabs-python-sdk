"""Base models and connection configs for the Shunyalabs SDK."""

from dataclasses import asdict, dataclass
from typing import Any, Optional


@dataclass
class HttpConnectionConfig:
    """Configuration for HTTP connection parameters."""

    connect_timeout: float = 30.0
    operation_timeout: float = 300.0


@dataclass
class WsConnectionConfig:
    """Configuration for WebSocket connection parameters."""

    open_timeout: Optional[float] = None
    ping_interval: Optional[float] = None
    ping_timeout: Optional[float] = 60
    close_timeout: Optional[float] = None
    max_size: Optional[int] = None
    max_queue: Optional[int] = None
    read_limit: Optional[int] = None
    write_limit: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, excluding None values (for websockets kwargs)."""
        return {k: v for k, v in asdict(self).items() if v is not None}


__all__ = ["HttpConnectionConfig", "WsConnectionConfig"]
