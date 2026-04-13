"""Audio playback and saving helpers.

Requires the 'extras' optional dependency: pip install shunyalabs[extras]
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Union

if TYPE_CHECKING:
    from shunyalabs.tts._models import TTSResult


def play(
    audio: Union["TTSResult", Iterator[bytes], bytes],
    *,
    sample_rate: int = 16000,
    channels: int = 1,
    dtype: str = "int16",
) -> None:
    """Play audio using sounddevice.

    Args:
        audio: TTSResult, bytes, or Iterator[bytes] to play.
        sample_rate: Audio sample rate in Hz.
        channels: Number of audio channels.
        dtype: Audio data type (numpy dtype string).

    Examples:
        >>> from shunyalabs.extras import play
        >>> result = client.tts.synthesize("Hello")
        >>> play(result)
    """
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        raise ImportError(
            "sounddevice and numpy are required for audio playback. "
            "Install with: pip install 'shunyalabs[extras]' numpy"
        )

    audio_bytes = _resolve_audio(audio)
    if not audio_bytes:
        return

    audio_array = np.frombuffer(audio_bytes, dtype=dtype)
    if channels > 1:
        audio_array = audio_array.reshape(-1, channels)
    sd.play(audio_array, samplerate=sample_rate)
    sd.wait()


def save(
    audio: Union["TTSResult", Iterator[bytes], bytes],
    path: str,
) -> None:
    """Save audio data to a file.

    Args:
        audio: TTSResult, bytes, or Iterator[bytes] to save.
        path: Output file path.

    Examples:
        >>> from shunyalabs.extras import save
        >>> save(client.tts.stream("Hello"), "output.pcm")
    """
    audio_bytes = _resolve_audio(audio)
    resolved = Path(path).resolve()
    if not resolved.parent.is_dir():
        raise ValueError(f"Parent directory does not exist: {resolved.parent}")
    with open(resolved, "wb") as f:
        f.write(audio_bytes)


def stream_to_file(
    chunks: Iterator[bytes],
    path: str,
) -> None:
    """Stream audio chunks directly to a file.

    Args:
        chunks: Iterator of audio byte chunks.
        path: Output file path.
    """
    resolved = Path(path).resolve()
    if not resolved.parent.is_dir():
        raise ValueError(f"Parent directory does not exist: {resolved.parent}")
    with open(resolved, "wb") as f:
        for chunk in chunks:
            f.write(chunk)


def _resolve_audio(audio: Union["TTSResult", Iterator[bytes], bytes]) -> bytes:
    """Resolve audio from various input types to bytes."""
    if isinstance(audio, bytes):
        return audio

    # Check if it's a TTSResult (has audio_data attribute)
    if hasattr(audio, "audio_data"):
        return audio.audio_data

    # Must be an iterator
    chunks = []
    for chunk in audio:
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = ["play", "save", "stream_to_file"]
