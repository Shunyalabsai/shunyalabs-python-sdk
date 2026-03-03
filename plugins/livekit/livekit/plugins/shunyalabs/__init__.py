"""Shunyalabs LiveKit plugin — STT backed by the Shunyalabs ASR gateway."""

from .stt import STT, STTStream
from ._version import __version__

__all__ = ["STT", "STTStream", "__version__"]
