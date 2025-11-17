from __future__ import annotations

import base64
from typing import Any

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from .._models import AudioEncoding
from .._models import ClientMessageType


def b64_encode_audio(chan_id: str, chunk: bytes) -> dict[str, Any]:
    """
    Encode audio chunk as base64 within a JSON message for multi-channel audio.

    Args:
        chan_id: Channel identifier (e.g., "left", "right")
        chunk: Raw audio bytes to encode

    Returns:
        Dict with message type, channel ID, and base64-encoded audio

    Example:
        >>> frame = encode_audio_chunk("channel_1", b"\\x00\\x01")
        >>> # Returns: {"message": "AddChannelAudio", "channel": "channel_1", "data": "AAE="}
    """
    return {
        "message": ClientMessageType.ADD_CHANNEL_AUDIO,
        "channel": chan_id,
        "data": base64.b64encode(chunk).decode(),
    }


def convert_to_pcm_f32le(
    audio_data: bytes,
    input_encoding: AudioEncoding,
    input_sample_rate: int,
    output_sample_rate: int | None = None,
    num_channels: int = 1,
) -> bytes:
    """
    Convert audio data to PCM_F32LE (32-bit float little-endian) format.

    This function converts audio from various input formats (PCM_S16LE, MULAW, etc.)
    to PCM_F32LE format required by the API Gateway protocol. It also handles
    mono conversion if the input is stereo.

    Args:
        audio_data: Raw audio bytes in the input encoding format.
        input_encoding: The encoding format of the input audio data.
        input_sample_rate: Sample rate of the input audio in Hz.
        output_sample_rate: Desired output sample rate in Hz. If None, uses input_sample_rate.
                           Note: Sample rate conversion is not currently implemented,
                           so output_sample_rate must equal input_sample_rate.
        num_channels: Number of channels in the input audio (1 for mono, 2 for stereo).
                     Defaults to 1. Stereo audio will be converted to mono by averaging channels.

    Returns:
        Audio data as bytes in PCM_F32LE format (mono, 32-bit float).

    Raises:
        ImportError: If numpy is not installed (required for conversion).
        ValueError: If input_encoding is not supported or sample rates don't match.

    Examples:
        Convert PCM_S16LE to PCM_F32LE:
            >>> audio_s16 = b"\\x00\\x01\\x02\\x03"  # 16-bit PCM audio
            >>> audio_f32 = convert_to_pcm_f32le(
            ...     audio_s16,
            ...     AudioEncoding.PCM_S16LE,
            ...     input_sample_rate=16000
            ... )

        Convert stereo to mono:
            >>> audio_stereo = b"\\x00\\x01\\x02\\x03\\x04\\x05\\x06\\x07"  # Stereo 16-bit
            >>> audio_mono = convert_to_pcm_f32le(
            ...     audio_stereo,
            ...     AudioEncoding.PCM_S16LE,
            ...     input_sample_rate=16000,
            ...     num_channels=2
            ... )
    """
    if not HAS_NUMPY:
        raise ImportError(
            "numpy is required for audio conversion. Install it with: pip install numpy"
        )

    if output_sample_rate is None:
        output_sample_rate = input_sample_rate

    if output_sample_rate != input_sample_rate:
        raise ValueError(
            f"Sample rate conversion not yet implemented. "
            f"Input sample rate ({input_sample_rate} Hz) must equal output sample rate ({output_sample_rate} Hz)."
        )

    if not audio_data:
        return b""

    # Convert bytes to numpy array based on input encoding
    if input_encoding == AudioEncoding.PCM_S16LE:
        # 16-bit signed integer: 2 bytes per sample
        samples = np.frombuffer(audio_data, dtype=np.int16)
        # Convert to float32 and normalize to [-1.0, 1.0] range
        float_samples = samples.astype(np.float32) / 32768.0

    elif input_encoding == AudioEncoding.PCM_F32LE:
        # Already in float32 format
        float_samples = np.frombuffer(audio_data, dtype=np.float32)

    elif input_encoding == AudioEncoding.MULAW:
        # μ-law: 8-bit encoded, need to decode
        # Standard ITU-T G.711 μ-law decoding
        mulaw_samples = np.frombuffer(audio_data, dtype=np.uint8)
        
        # Invert all bits
        mulaw_samples = ~mulaw_samples
        
        # Extract sign bit (bit 7)
        sign = (mulaw_samples & 0x80) != 0
        
        # Extract exponent (bits 4-6)
        exponent = (mulaw_samples & 0x70) >> 4
        
        # Extract mantissa (bits 0-3)
        mantissa = mulaw_samples & 0x0F
        
        # Reconstruct linear value: (mantissa + 33) * 2^(exponent) - 33
        linear = (mantissa + 33).astype(np.int16)
        linear = (linear << exponent) - 33
        
        # Apply sign
        linear = np.where(sign, -linear, linear)
        
        # Normalize to [-1.0, 1.0] range (μ-law uses range -8159 to 8159)
        float_samples = linear.astype(np.float32) / 8159.0

    else:
        raise ValueError(
            f"Unsupported input encoding: {input_encoding}. "
            f"Supported encodings: PCM_S16LE, PCM_F32LE, MULAW"
        )

    # Handle stereo to mono conversion
    if num_channels == 2:
        # Reshape to separate channels: [L, R, L, R, ...]
        float_samples = float_samples.reshape(-1, 2)
        # Average channels to create mono
        float_samples = (float_samples[:, 0] + float_samples[:, 1]) * 0.5
    elif num_channels != 1:
        raise ValueError(
            f"Unsupported number of channels: {num_channels}. "
            f"Only mono (1) and stereo (2) are supported."
        )

    # Ensure values are in valid range [-1.0, 1.0]
    float_samples = np.clip(float_samples, -1.0, 1.0)

    # Convert back to bytes (PCM_F32LE)
    return float_samples.astype(np.float32, copy=False).tobytes()
