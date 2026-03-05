"""Client configuration for the Shunyalabs SDK."""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ClientConfig:
    """Configuration for ShunyaClient / AsyncShunyaClient.

    Args:
        api_key: API key. Falls back to SHUNYALABS_API_KEY env var.
        timeout: Default request timeout in seconds.
        max_retries: Number of retries for failed requests (5xx, connection errors).
        asr_url: Base URL for ASR batch API.
        asr_ws_url: WebSocket URL for ASR streaming API.
        tts_url: Base URL for TTS batch API.
        tts_ws_url: WebSocket URL for TTS streaming API.
        flow_url: WebSocket URL for Flow API.
    """

    api_key: Optional[str] = None
    timeout: float = 60.0
    max_retries: int = 2

    # Service URLs — priority: explicit > env var > default
    asr_url: Optional[str] = None
    asr_ws_url: Optional[str] = None
    tts_url: Optional[str] = None
    tts_ws_url: Optional[str] = None
    flow_url: Optional[str] = None

    # Default URLs
    _DEFAULT_ASR_URL: str = field(default="https://asr.shunyalabs.ai", init=False, repr=False)
    _DEFAULT_ASR_WS_URL: str = field(default="wss://asr.shunyalabs.ai/ws", init=False, repr=False)
    _DEFAULT_TTS_URL: str = field(default="https://tts.shunyalabs.ai", init=False, repr=False)
    _DEFAULT_TTS_WS_URL: str = field(default="wss://tts.shunyalabs.ai/ws/tts", init=False, repr=False)
    _DEFAULT_FLOW_URL: str = field(default="wss://flow.api.shunyalabs.com/v1/flow", init=False, repr=False)

    def resolve_api_key(self) -> str:
        key = self.api_key or os.environ.get("SHUNYALABS_API_KEY")
        if not key:
            raise ValueError(
                "API key required: provide api_key or set SHUNYALABS_API_KEY environment variable"
            )
        return key

    def resolve_asr_url(self) -> str:
        return self.asr_url or os.environ.get("SHUNYALABS_ASR_URL") or self._DEFAULT_ASR_URL

    def resolve_asr_ws_url(self) -> str:
        return self.asr_ws_url or os.environ.get("SHUNYALABS_ASR_WS_URL") or self._DEFAULT_ASR_WS_URL

    def resolve_tts_url(self) -> str:
        return self.tts_url or os.environ.get("SHUNYALABS_TTS_URL") or self._DEFAULT_TTS_URL

    def resolve_tts_ws_url(self) -> str:
        return self.tts_ws_url or os.environ.get("SHUNYALABS_TTS_WS_URL") or self._DEFAULT_TTS_WS_URL

    def resolve_flow_url(self) -> str:
        return self.flow_url or os.environ.get("SHUNYALABS_FLOW_URL") or self._DEFAULT_FLOW_URL


__all__ = ["ClientConfig"]
