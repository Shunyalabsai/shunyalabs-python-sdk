# pipecat-shunyalabs

[![PyPI](https://img.shields.io/pypi/v/pipecat-shunyalabs.svg)](https://pypi.org/project/pipecat-shunyalabs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](../../LICENSE)

[Shunyalabs](https://shunyalabs.ai) STT and TTS services for [Pipecat](https://github.com/pipecat-ai/pipecat).

Provides `ShunyalabsSTTService` and `ShunyalabsTTSService` that integrate with Pipecat's pipeline framework, backed by the [Shunyalabs Python SDK](https://github.com/Shunyalabsai/shunyalabs-python-sdk).

## Installation

```bash
pip install pipecat-shunyalabs
```

## Authentication

Set your API key as an environment variable:

```bash
export SHUNYALABS_API_KEY="your-api-key"
```

Or pass it directly:

```python
stt = ShunyalabsSTTService(api_key="your-api-key")
tts = ShunyalabsTTSService(api_key="your-api-key")
```

---

## Quick Start

```python
import os
from pipecat.pipeline.pipeline import Pipeline
from pipecat_shunyalabs import ShunyalabsSTTService, ShunyalabsTTSService

stt = ShunyalabsSTTService(language="en")
tts = ShunyalabsTTSService(speaker="Rajesh", style="<Happy>")

pipeline = Pipeline([
    transport.input(),
    stt,
    llm,
    tts,
    transport.output(),
])
```

---

## STT — `ShunyalabsSTTService`

Real-time streaming speech-to-text over WebSocket. Maintains a persistent connection for the lifetime of the pipeline.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `language` | `str` | `"auto"` | Language code (e.g. `"en"`, `"hi"`) or `"auto"` for auto-detection. |
| `url` | `str` | `wss://asr.shunyalabs.ai/ws` | WebSocket endpoint URL. |
| `sample_rate` | `int` | `16000` | Expected audio sample rate in Hz. |

### How It Works

1. On pipeline `start`, opens a WebSocket connection to the Shunyalabs ASR gateway.
2. Audio chunks from the pipeline input are forwarded via `send_audio()`.
3. The gateway's built-in VAD detects speech boundaries and emits transcription events.
4. Events are mapped to Pipecat frames and pushed into the pipeline.

### Frame Mapping

| Shunyalabs Event | Pipecat Frame |
|------------------|---------------|
| `PARTIAL` | `InterimTranscriptionFrame` |
| `FINAL_SEGMENT` | `TranscriptionFrame` |
| `FINAL` | `TranscriptionFrame` |

### Example

```python
from pipecat_shunyalabs import ShunyalabsSTTService

stt = ShunyalabsSTTService(
    language="auto",
    sample_rate=16000,
)
```

### Auto-Reconnect

If the WebSocket connection drops during audio streaming, the service automatically reconnects and resumes sending audio.

---

## TTS — `ShunyalabsTTSService`

Streaming text-to-speech over WebSocket. Each synthesis request opens a connection, streams audio chunks back as `TTSAudioRawFrame` frames.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `url` | `str` | `wss://tts.shunyalabs.ai/ws` | WebSocket endpoint URL. |
| `model` | `str` | `"zero-indic"` | TTS model name. |
| `voice` | `str` | `"Rajesh"` | Voice name for the API. |
| `speaker` | `str` | `"Rajesh"` | Speaker name prefix for text formatting. |
| `style` | `str` | `"<Neutral>"` | Emotion style tag. See [Style Tags](#style-tags). |
| `language` | `str` | `"en"` | Language code for transliteration. |
| `sample_rate` | `int` | `16000` | Output audio sample rate in Hz. |
| `output_format` | `str` | `"pcm"` | Audio format (`"pcm"`, `"wav"`, `"mp3"`, `"ogg_opus"`, `"flac"`, `"mulaw"`, `"alaw"`). |
| `speed` | `float` | `1.0` | Speaking speed multiplier (0.25–4.0). |

### Style Tags

| Tag | Description |
|-----|-------------|
| `<Neutral>` | Neutral tone |
| `<Happy>` | Happy/cheerful |
| `<Sad>` | Sad/melancholic |
| `<Angry>` | Angry/intense |
| `<Fearful>` | Fearful/anxious |
| `<Surprised>` | Surprised/excited |
| `<Disgust>` | Disgusted |
| `<News>` | News anchor style |
| `<Conversational>` | Casual conversational |
| `<Narrative>` | Storytelling/narration |
| `<Enthusiastic>` | Enthusiastic/energetic |

### Text Formatting

The service automatically formats text as `"Speaker: <Style> text"` before sending to the API:

```python
tts = ShunyalabsTTSService(speaker="Rajesh", style="<Happy>")
# Input: "Welcome!"
# Sent:  "Rajesh: <Happy> Welcome!"
```

### Frame Output

| Frame | Description |
|-------|-------------|
| `TTSStartedFrame` | Emitted when synthesis begins. |
| `TTSAudioRawFrame` | Emitted for each audio chunk (PCM, 16kHz, mono). |
| `TTSStoppedFrame` | Emitted when synthesis completes. |

### Example

```python
from pipecat_shunyalabs import ShunyalabsTTSService

tts = ShunyalabsTTSService(
    model="zero-indic",
    voice="Nisha",
    speaker="Nisha",
    style="<Conversational>",
    language="en",
    speed=1.0,
)
```

---

## Full Pipeline Example

```python
import asyncio
import os
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.services.daily import DailyTransport, DailyParams
from pipecat_shunyalabs import ShunyalabsSTTService, ShunyalabsTTSService

async def main():
    transport = DailyTransport(
        room_url=os.environ["DAILY_ROOM_URL"],
        token=os.environ["DAILY_TOKEN"],
        bot_name="Shunya Bot",
        params=DailyParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    stt = ShunyalabsSTTService(language="auto")
    tts = ShunyalabsTTSService(
        speaker="Rajesh",
        voice="Rajesh",
        style="<Conversational>",
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        # your LLM / logic processor here
        tts,
        transport.output(),
    ])

    runner = PipelineRunner()
    await runner.run(pipeline)

asyncio.run(main())
```

---

## Multilingual Example

```python
# Hindi conversational bot
tts = ShunyalabsTTSService(
    speaker="Rajesh",
    voice="Rajesh",
    language="hi",
    style="<Conversational>",
)

# English news-style bot
tts = ShunyalabsTTSService(
    speaker="Varun",
    voice="Varun",
    language="en",
    style="<News>",
)
```

---

## License

[MIT](../../LICENSE)
