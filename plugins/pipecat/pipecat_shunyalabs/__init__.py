"""Shunyalabs STT and TTS services for Pipecat pipelines."""

from .stt import ShunyalabsSTTService
from .tts import ShunyalabsTTSService

__version__ = "1.2.0"
__all__ = ["ShunyalabsSTTService", "ShunyalabsTTSService"]
