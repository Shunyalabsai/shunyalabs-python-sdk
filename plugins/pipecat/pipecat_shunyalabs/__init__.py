"""Shunyalabs STT and TTS services for Pipecat pipelines."""

# Set before the submodules are imported: they read it to tag their init frames.
__version__ = "1.1.9"
# Sent as "client" in each init frame (and as the TTS socket's User-Agent) so the
# services can log which plugin version a customer runs.
_CLIENT_ID = f"pipecat-shunyalabsai/{__version__}"

from .stt import ShunyalabsSTTService  # noqa: E402
from .tts import ShunyalabsTTSService  # noqa: E402
from .turns import ShunyaUserTurnStrategies  # noqa: E402
__all__ = [
    "ShunyalabsSTTService",
    "ShunyalabsTTSService",
    # Drop-in for pipecat's `ExternalUserTurnStrategies()`, whose hidden 0.5 s
    # turn-stop timeout costs 250-500 ms on every turn -- see turns.py.
    "ShunyaUserTurnStrategies",
]
