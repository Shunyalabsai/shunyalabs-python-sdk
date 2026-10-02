"""Shunyalabs STT and TTS services for Pipecat pipelines."""

from .stt import ShunyalabsSTTService
from .tts import ShunyalabsTTSService
from .turns import ShunyaUserTurnStrategies

__version__ = "1.2.0"
__all__ = [
    "ShunyalabsSTTService",
    "ShunyalabsTTSService",
    # Drop-in for pipecat's `ExternalUserTurnStrategies()`, whose hidden 0.5 s
    # turn-stop timeout costs 250-500 ms on every turn -- see turns.py.
    "ShunyaUserTurnStrategies",
]
