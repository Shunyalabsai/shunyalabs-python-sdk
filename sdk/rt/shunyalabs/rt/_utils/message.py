from typing import Any
from typing import Optional

from .._models import AudioEventsConfig
from .._models import AudioFormat
from .._models import ClientMessageType
from .._models import TranscriptionConfig
from .._models import TranslationConfig


def build_start_recognition_message(
    transcription_config: TranscriptionConfig,
    audio_format: AudioFormat,
    translation_config: Optional[TranslationConfig] = None,
    audio_events_config: Optional[AudioEventsConfig] = None,
    session_id: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = "pingala-v1-universal",
    deliver_deltas_only: bool = True,
    use_api_gateway_format: bool = False,
) -> dict[str, Any]:
    """Build the start recognition message for the server.

    Args:
        transcription_config: The transcription configuration.
        audio_format: The audio format.
        translation_config: The translation configuration.
        audio_events_config: The audio events configuration.
        session_id: Optional session ID for API Gateway format.
        api_key: Optional API key for API Gateway format.
        model: Model name for API Gateway format.
        deliver_deltas_only: Whether to deliver deltas only for API Gateway format.
        use_api_gateway_format: If True, use API Gateway format instead of standard format.

    Returns:
        The start recognition message.
    """
    
    # API Gateway format
    if use_api_gateway_format:
        # Map language - handle "auto" or None
        language = transcription_config.language
        if language == "auto" or not language:
            language = None
        
        # Build config dict matching API Gateway expected format (from test_apigw_ws_send_passthrough.py)
        effective_session_id = session_id or "default-session"
        config = {
            "uid": effective_session_id,
            "language": language,
            "task": "transcribe",
            "model": model,
            "client_sample_rate": int(audio_format.sample_rate),
            "deliver_deltas_only": deliver_deltas_only,
            "inactivity_timeout": 3600.0,  # 1 hour (3600 seconds) for direct connections
            "use_vad": True,  # Voice Activity Detection
        }
        
        if api_key:
            config["api_key"] = api_key
        
        # API Gateway format - match test_apigw_ws_send_passthrough.py format
        init_msg = {
            "type": "init",
            "session_id": effective_session_id,
            "connection_id": effective_session_id,  # Use session_id as connection_id
            "config": config,
        }
        
        # Also include api_key at top level for compatibility (matching working test)
        if api_key:
            init_msg["api_key"] = api_key
        
        return init_msg
    
    # Standard SDK format
    start_recognition_message = {
        "message": ClientMessageType.START_RECOGNITION,
        "audio_format": audio_format.to_dict(),
        "transcription_config": transcription_config.to_dict(),
    }

    if translation_config:
        start_recognition_message["translation_config"] = translation_config.to_dict()

    if audio_events_config:
        start_recognition_message["audio_events_config"] = audio_events_config.to_dict()

    return start_recognition_message
