#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Shunyalabs STT service integration."""

import asyncio
import datetime
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from loguru import logger
from pydantic import BaseModel

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    ErrorFrame,
    Frame,
    InterimTranscriptionFrame,
    StartFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService
from pipecat.transcriptions.language import Language, resolve_language
from pipecat.utils.tracing.service_decorators import traced_stt

try:
    from shunyalabs.rt import (
        AsyncClient,
        AudioEncoding,
        AudioFormat,
        ServerMessageType,
        TranscriptionConfig,
    )
except ModuleNotFoundError as e:
    logger.error(f"Exception: {e}")
    logger.error(
        "In order to use Shunyalabs, you need to install the shunyalabs-rt package."
    )
    raise Exception(f"Missing module: {e}")


class EndOfUtteranceMode(str, Enum):
    """End of turn delay options for transcription."""

    NONE = "none"
    FIXED = "fixed"
    ADAPTIVE = "adaptive"


@dataclass
class SpeechFragment:
    """Fragment of an utterance.

    Parameters:
        start_time: Start time of the fragment in seconds (from session start).
        end_time: End time of the fragment in seconds (from session start).
        language: Language of the fragment. Defaults to `Language.EN`.
        is_eos: Whether the fragment is the end of a sentence. Defaults to `False`.
        is_final: Whether the fragment is the final fragment. Defaults to `False`.
        content: Content of the fragment. Defaults to empty string.
        confidence: Confidence of the fragment (0.0 to 1.0). Defaults to `1.0`.
        result: Raw result of the fragment from the STT.
    """

    start_time: float
    end_time: float
    language: Language = Language.EN
    is_eos: bool = False
    is_final: bool = False
    content: str = ""
    confidence: float = 1.0
    result: Any | None = None


@dataclass
class SpeakerFragments:
    """SpeechFragment items grouped by speaker_id.

    Parameters:
        speaker_id: The ID of the speaker.
        is_active: Whether the speaker is active (emits frame).
        timestamp: The timestamp of the frame.
        language: The language of the frame.
        fragments: The list of SpeechFragment items.
    """

    speaker_id: str | None = None
    is_active: bool = False
    timestamp: str | None = None
    language: Language | None = None
    fragments: list[SpeechFragment] = field(default_factory=list)

    def __str__(self):
        """Return a string representation of the object."""
        return f"SpeakerFragments(speaker_id: {self.speaker_id}, timestamp: {self.timestamp}, language: {self.language}, text: {self._format_text()})"

    def _format_text(self, format: str | None = None) -> str:
        """Wrap text with speaker ID in an optional f-string format.

        Args:
            format: Format to wrap the text with.

        Returns:
            str: The wrapped text.
        """
        # Cumulative contents
        content = " ".join([frag.content for frag in self.fragments if frag.content])

        # Format the text, if format is provided
        if format is None or self.speaker_id is None:
            return content
        return format.format(**{"speaker_id": self.speaker_id, "text": content})

    def _as_frame_attributes(
        self, active_format: str | None = None, passive_format: str | None = None
    ) -> dict[str, Any]:
        """Return a dictionary of attributes for a TranscriptionFrame.

        Args:
            active_format: Format to wrap the text with.
            passive_format: Format to wrap the text with. Defaults to `active_format`.

        Returns:
            dict[str, Any]: The dictionary of attributes.
        """
        if not passive_format:
            passive_format = active_format
        return {
            "text": self._format_text(active_format if self.is_active else passive_format),
            "user_id": self.speaker_id or "",
            "timestamp": self.timestamp,
            "language": self.language,
            "result": [frag.result for frag in self.fragments],
        }


class ShunyalabsSTTService(STTService):
    """Shunyalabs STT service implementation.

    This service provides real-time speech-to-text transcription using the Shunyalabs API.
    It supports partial and final transcriptions, multiple languages, and various audio formats.
    """

    class InputParams(BaseModel):
        """Configuration parameters for Shunyalabs STT service.

        Parameters:
            language: Language code for transcription. Defaults to `Language.EN`.

            enable_partials: Enable partial transcriptions. When enabled, the STT engine will
                emit partial word frames - useful for the visualisation of real-time transcription.
                Defaults to True.

            max_delay: Maximum delay in seconds for transcription. This forces the STT engine to
                speed up the processing of transcribed words and reduces the interval between partial
                and final results. Lower values can have an impact on accuracy. Defaults to 1.0.

            end_of_utterance_silence_trigger: Maximum delay in seconds for end of utterance trigger.
                The delay is used to wait for any further transcribed words before emitting the final
                word frames. The value must be lower than max_delay.
                Defaults to 0.5.

            end_of_utterance_mode: End of utterance delay mode. When ADAPTIVE is used, the delay
                can be adjusted on the content of what the most recent speaker has said, such as
                rate of speech and whether they have any pauses or disfluencies. When FIXED is used,
                the delay is fixed to the value of `end_of_utterance_delay`. Use of NONE disables
                end of utterance detection and uses a fallback timer.
                Defaults to `EndOfUtteranceMode.FIXED`.

            enable_vad: Enable VAD to trigger end of utterance detection. This should be used
                without any other VAD enabled in the agent and will emit the speaker started
                and stopped frames. Defaults to False.

            model: Model name to use for transcription. Defaults to "pingala-v1-universal".

            use_api_gateway_format: Whether to use API Gateway format. Defaults to True.

            chunk_size: Audio chunk size for streaming. Defaults to 4096.
            audio_encoding: Audio encoding format. Defaults to AudioEncoding.PCM_F32LE.
        """

        # Service configuration
        language: Language | str = Language.EN
        enable_partials: bool = True
        max_delay: float = 1.0
        end_of_utterance_silence_trigger: float = 0.5
        end_of_utterance_mode: EndOfUtteranceMode = EndOfUtteranceMode.FIXED
        enable_vad: bool = False
        model: str = "pingala-v1-universal"
        use_api_gateway_format: bool = True

        # Audio
        chunk_size: int = 4096
        audio_encoding: AudioEncoding = AudioEncoding.PCM_F32LE

    class UpdateParams(BaseModel):
        """Update parameters for Shunyalabs STT service.

        These are the only parameters that can be changed once a session has started.
        Currently, no parameters can be updated during a session.
        """

        pass

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        sample_rate: int = 16000,
        params: InputParams | None = None,
        **kwargs,
    ):
        """Initialize the Shunyalabs STT service.

        Args:
            api_key: Shunyalabs API key for authentication. Uses environment variable
                `SHUNYALABS_API_KEY` if not provided.
            base_url: Base URL for Shunyalabs API. Uses environment variable `SHUNYALABS_RT_URL`
                or defaults to `wss://tl.shunyalabs.ai/`.
            sample_rate: Sample rate for audio. Defaults to 16000.
            params: Input parameters for the service. Defaults to None.
            **kwargs: Additional keyword arguments (deprecated, use params instead).
        """
        super().__init__(**kwargs)

        # Get API key
        self._api_key = api_key or os.getenv("SHUNYALABS_API_KEY")
        if not self._api_key:
            raise ValueError("Shunyalabs API key is required. Set SHUNYALABS_API_KEY environment variable or pass api_key parameter.")

        # Get base URL
        self._base_url = base_url or os.getenv("SHUNYALABS_RT_URL", "wss://tl.shunyalabs.ai/")

        # Store params
        self._params = params or self.InputParams(**kwargs)

        # Client
        self._client: AsyncClient | None = None

        # Speech fragments
        self._speech_fragments: list[SpeechFragment] = []

        # Start time
        self._start_time: datetime.datetime | None = None

        # Speaking state
        self._is_speaking: bool = False

        # Process config
        self._process_config()

        # EndOfUtterance fallback timer
        self._end_of_utterance_timer: asyncio.Task | None = None

    async def start(self, frame: StartFrame):
        """Called when the new session starts."""
        await super().start(frame)
        await self._connect()

    async def stop(self, frame: EndFrame):
        """Called when the session ends."""
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        """Called when the session is cancelled."""
        await super().cancel(frame)
        await self._disconnect()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        """Adds audio to the audio buffer and yields None."""
        try:
            if self._client:
                # Pipecat sends 16-bit PCM (int16) bytes, but Shunyalabs expects 32-bit float
                # Pass input_encoding to trigger automatic conversion in SDK
                await self._client.send_audio(
                    audio,
                    session_id=self._client._session_id,
                    sample_rate=self.sample_rate,
                    input_encoding=AudioEncoding.PCM_S16LE,  # Pipecat sends 16-bit PCM
                )
            yield None
        except Exception as e:
            logger.error(f"Shunyalabs error: {e}")
            yield ErrorFrame(f"Shunyalabs error: {e}", fatal=False)
            await self._disconnect()

    def update_params(
        self,
        params: UpdateParams,
    ) -> None:
        """Updates the service configuration.

        Currently, no parameters can be updated during a session.

        Args:
            params: Update parameters for the service.
        """
        # No parameters can be updated during a session
        pass

    async def _connect(self) -> None:
        """Connect to the STT service."""
        # Create new STT RT client
        self._client = AsyncClient(
            api_key=self._api_key,
            url=self._base_url,
        )

        # Log the event
        logger.debug(f"{self} Connecting to Shunyalabs STT service")

        # Recognition started event
        @self._client.on(ServerMessageType.RECOGNITION_STARTED)
        def _evt_on_recognition_started(message: dict[str, Any]):
            logger.debug(f"Recognition started (session: {message.get('id')})")
            self._start_time = datetime.datetime.now(datetime.timezone.utc)

        # Partial transcript event
        if self._params.enable_partials:

            @self._client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
            def _evt_on_partial_transcript(message: dict[str, Any]):
                self._handle_transcript(message, is_final=False)

        # Final transcript event
        @self._client.on(ServerMessageType.ADD_TRANSCRIPT)
        def _evt_on_final_transcript(message: dict[str, Any]):
            self._handle_transcript(message, is_final=True)

        # End of Utterance
        if self._params.end_of_utterance_mode == EndOfUtteranceMode.FIXED:

            @self._client.on(ServerMessageType.END_OF_TRANSCRIPT)
            def _evt_on_end_of_transcript(message: dict[str, Any]):
                logger.debug("End of transcript received from STT")
                asyncio.run_coroutine_threadsafe(
                    self._handle_end_of_utterance(), self.get_event_loop()
                )

        # Start session
        try:
            await self._client.start_session(
                transcription_config=self._transcription_config,
                audio_format=AudioFormat(
                    encoding=self._params.audio_encoding,
                    sample_rate=self.sample_rate,
                    chunk_size=self._params.chunk_size,
                ),
                session_id=None,
                api_key=self._api_key,
                model=self._params.model,
                deliver_deltas_only=True,
                use_api_gateway_format=self._params.use_api_gateway_format,
            )
            # Wait for recognition to start before considering connection complete
            await self._client._wait_recognition_started(timeout=10.0)
            logger.debug(f"{self} Connected to Shunyalabs STT service")
            await self._call_event_handler("on_connected")
        except Exception as e:
            logger.error(f"{self} Error connecting to Shunyalabs: {e}")
            self._client = None

    async def _disconnect(self) -> None:
        """Disconnect from the STT service."""
        # Disconnect the client
        logger.debug(f"{self} Disconnecting from Shunyalabs STT service")
        try:
            if self._client:
                await asyncio.wait_for(self._client.close(), timeout=5.0)
                logger.debug(f"{self} Disconnected from Shunyalabs STT service")
        except asyncio.TimeoutError:
            logger.warning(f"{self} Timeout while closing Shunyalabs client connection")
        except Exception as e:
            logger.error(f"{self} Error closing Shunyalabs client: {e}")
        finally:
            self._client = None
            await self._call_event_handler("on_disconnected")

    def _process_config(self) -> None:
        """Create a formatted STT transcription config.

        Creates a transcription config object based on the service parameters. Aligns
        with the Shunyalabs RT API transcription config.
        """
        # Convert language if it's a Language enum
        language = self._params.language
        if isinstance(language, Language):
            language = _language_to_shunyalabs_language(language)

        # Transcription config
        self._transcription_config = TranscriptionConfig(
            language=language,
            enable_partials=self._params.enable_partials,
        )

    def _handle_transcript(self, message: dict[str, Any], is_final: bool) -> None:
        """Handle the partial and final transcript events.

        Args:
            message: The new Partial or Final from the STT engine.
            is_final: Whether the data is final or partial.
        """
        # Add the speech fragments
        has_changed = self._add_speech_fragments(
            message=message,
            is_final=is_final,
        )

        # Skip if unchanged
        if not has_changed:
            return

        # Set a timer for the end of utterance
        self._end_of_utterance_timer_start()

        # Send frames
        asyncio.run_coroutine_threadsafe(self._send_frames(), self.get_event_loop())

    @traced_stt
    async def _handle_transcription(
        self, transcript: str, is_final: bool, language: Language | None = None
    ):
        """Handle a transcription result with tracing."""
        pass

    def _end_of_utterance_timer_start(self):
        """Start the timer for the end of utterance.

        This will use the STT's `end_of_utterance_silence_trigger` value and set
        a timer to send the latest transcript to the pipeline. It is used as a
        fallback from the EndOfTranscript messages from the STT.
        """
        # Reset the end of utterance timer
        if self._end_of_utterance_timer is not None:
            self._end_of_utterance_timer.cancel()

        # Send after a delay
        async def send_after_delay(delay: float):
            await asyncio.sleep(delay)
            logger.debug("Fallback EndOfUtterance triggered.")
            asyncio.run_coroutine_threadsafe(self._handle_end_of_utterance(), self.get_event_loop())

        # Start the timer
        self._end_of_utterance_timer = asyncio.create_task(
            send_after_delay(self._params.end_of_utterance_silence_trigger * 2)
        )

    async def _handle_end_of_utterance(self):
        """Handle the end of utterance event.

        This will check for any running timers for end of utterance, reset them,
        and then send a finalized frame to the pipeline.
        """
        # Send the frames
        await self._send_frames(finalized=True)

        # Reset the end of utterance timer
        if self._end_of_utterance_timer:
            self._end_of_utterance_timer.cancel()
            self._end_of_utterance_timer = None

    async def _send_frames(self, finalized: bool = False) -> None:
        """Send frames to the pipeline.

        Send speech frames to the pipeline. If VAD is enabled, then this will
        also send an interruption and user started speaking frames. When the
        final transcript is received, then this will send a user stopped speaking
        and stop interruption frames.

        Args:
            finalized: Whether the data is final or partial.
        """
        # Get speech frames
        speech_frames = self._get_frames_from_fragments()

        # Skip if no frames
        if not speech_frames:
            return

        # Frames to send
        downstream_frames: list[Frame] = []

        # If VAD is enabled, then send a speaking frame
        if self._params.enable_vad and not self._is_speaking:
            logger.debug("User started speaking")
            self._is_speaking = True
            await self.push_interruption_task_frame_and_wait()
            downstream_frames += [UserStartedSpeakingFrame()]

        # If final, then re-parse into TranscriptionFrame
        if finalized:
            # Reset the speech fragments
            self._speech_fragments.clear()

            # Transform frames
            downstream_frames += [
                TranscriptionFrame(
                    **frame._as_frame_attributes()
                )
                for frame in speech_frames
            ]

            # Log transcript(s)
            logger.debug(f"Finalized transcript: {[f.text for f in downstream_frames]}")

        # Return as interim results (unformatted)
        else:
            downstream_frames += [
                InterimTranscriptionFrame(
                    **frame._as_frame_attributes()
                )
                for frame in speech_frames
            ]

        # If VAD is enabled, then send a speaking frame
        if self._params.enable_vad and self._is_speaking and finalized:
            logger.debug("User stopped speaking")
            self._is_speaking = False
            downstream_frames += [UserStoppedSpeakingFrame()]

        # Send the DOWNSTREAM frames
        for frame in downstream_frames:
            await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    def _add_speech_fragments(self, message: dict[str, Any], is_final: bool = False) -> bool:
        """Takes a new Partial or Final from the STT engine.

        Accumulates it into the _speech_fragments list. As new final data is added, all
        partials are removed from the list.

        Args:
            message: The message from the STT engine.
            is_final: Whether the data is final or partial.

        Returns:
            bool: Whether the fragments have changed.
        """
        # Get metadata
        metadata = message.get("metadata", {})
        transcript = metadata.get("transcript", "").strip()
        start_time = metadata.get("start_time", 0.0)
        end_time = metadata.get("end_time", start_time + 1.0)

        # Skip if no transcript
        if not transcript:
            return False

        # Create fragment
        fragment = SpeechFragment(
            start_time=start_time,
            end_time=end_time,
            is_final=is_final,
            content=transcript,
            result=message,
        )

        # If final, clear partials and add final
        if is_final:
            # Remove all partials
            self._speech_fragments = [f for f in self._speech_fragments if f.is_final]
            # Add final
            self._speech_fragments.append(fragment)
            return True

        # If partial, add or update
        else:
            # Check if we have a partial for this time range
            existing_partial = None
            for f in self._speech_fragments:
                if not f.is_final and abs(f.start_time - start_time) < 0.1:
                    existing_partial = f
                    break

            if existing_partial:
                # Update existing partial
                existing_partial.content = transcript
                existing_partial.end_time = end_time
                existing_partial.result = message
                return True
            else:
                # Add new partial
                self._speech_fragments.append(fragment)
                return True

    def _get_frames_from_fragments(self) -> list[SpeakerFragments]:
        """Get frames from speech fragments.

        Returns:
            list[SpeakerFragments]: List of speaker fragments.
        """
        if not self._speech_fragments:
            return []

        # Group fragments by speaker (for now, all fragments are from the same speaker)
        speaker_fragments = SpeakerFragments(
            speaker_id=None,
            is_active=True,
            timestamp=self._start_time.isoformat() if self._start_time else None,
            language=self._params.language if isinstance(self._params.language, Language) else Language.EN,
            fragments=self._speech_fragments.copy(),
        )

        return [speaker_fragments]


def _language_to_shunyalabs_language(language: Language) -> str:
    """Convert a Language enum to a Shunyalabs language code.

    Args:
        language: The Language enum to convert.

    Returns:
        str: The Shunyalabs language code.
    """
    # List of supported input languages
    LANGUAGE_MAP = {
        Language.EN: "en",
        Language.ES: "es",
        Language.FR: "fr",
        Language.DE: "de",
        Language.IT: "it",
        Language.PT: "pt",
        Language.RU: "ru",
        Language.JA: "ja",
        Language.KO: "ko",
        Language.ZH: "zh",
        Language.HI: "hi",
        Language.AR: "ar",
        Language.NL: "nl",
        Language.PL: "pl",
        Language.TR: "tr",
        Language.SV: "sv",
        Language.DA: "da",
        Language.NO: "no",
        Language.FI: "fi",
        Language.CS: "cs",
        Language.RO: "ro",
        Language.HU: "hu",
        Language.EL: "el",
        Language.HE: "he",
        Language.TH: "th",
        Language.VI: "vi",
        Language.ID: "id",
        Language.MS: "ms",
    }

    return resolve_language(language, LANGUAGE_MAP, use_base_code=True)

