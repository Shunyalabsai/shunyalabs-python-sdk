# livekit-plugins-shunyalabs

[![PyPI](https://img.shields.io/pypi/v/livekit-plugins-shunyalabs.svg)](https://pypi.org/project/livekit-plugins-shunyalabs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](../../LICENSE)

[Shunyalabs](https://shunyalabs.ai) STT and TTS plugin for [LiveKit Agents](https://docs.livekit.io/agents/).

Provides `STT` (speech-to-text) and `TTS` (text-to-speech) classes that integrate with LiveKit's agent framework, backed by the [Shunyalabs Python SDK](https://github.com/Shunyalabsai/shunyalabs-python-sdk).

## Installation

```bash
pip install livekit-plugins-shunyalabs
```

## Authentication

Set your API key as an environment variable:

```bash
export SHUNYALABS_API_KEY="your-api-key"
```

Or pass it directly:

```python
stt = shunyalabs.STT(api_key="your-api-key")
tts = shunyalabs.TTS(api_key="your-api-key")
```

---

## Quick Start

```python
from livekit.agents import AgentSession
from livekit.plugins import shunyalabs, silero

session = AgentSession(
    stt=shunyalabs.STT(language="en"),
    tts=shunyalabs.TTS(speaker="Rajesh", style="<Neutral>"),
    vad=silero.VAD.load(),
)
```

---

## STT (Speech-to-Text)

### `shunyalabs.STT`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `language` | `str` | `"auto"` | BCP-47 language code or `"auto"` for auto-detection. |
| `api_url` | `str` | `https://asr.shunyalabs.ai` | REST batch endpoint base URL. |
| `ws_url` | `str` | `wss://asr.shunyalabs.ai/ws` | WebSocket streaming endpoint URL. |

### Capabilities

| Capability | Supported |
|------------|-----------|
| Streaming (real-time) | Yes |
| Interim results | Yes |
| Offline/batch recognition | Yes |

### Streaming STT

Real-time transcription over WebSocket. Audio frames from LiveKit are forwarded to the Shunyalabs ASR gateway; transcription events are pushed back as `SpeechEvent`s.

```python
from livekit.agents import AgentSession
from livekit.plugins import shunyalabs, silero

session = AgentSession(
    stt=shunyalabs.STT(language="en"),
    vad=silero.VAD.load(),
)

@session.on("user_speech_committed")
def on_speech(ev):
    print(f"User said: {ev.transcript}")
```

**Event mapping:**

| Shunyalabs Event | LiveKit SpeechEventType |
|------------------|------------------------|
| `PARTIAL` | `INTERIM_TRANSCRIPT` |
| `FINAL_SEGMENT` | `FINAL_TRANSCRIPT` + `END_OF_SPEECH` |
| `FINAL` | `FINAL_TRANSCRIPT` + `RECOGNITION_USAGE` |

### Batch STT

Single-shot transcription of an audio buffer. Uses `POST /v1/audio/transcriptions` via the SDK's `AsyncBatchASR`.

```python
from livekit.plugins import shunyalabs

stt = shunyalabs.STT(language="en")

# In an agent context:
event = await stt.recognize(audio_buffer)
print(event.alternatives[0].text)
```

---

## TTS (Text-to-Speech)

### `shunyalabs.TTS`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `api_url` | `str` | `https://tts.shunyalabs.ai` | HTTP batch endpoint base URL. |
| `ws_url` | `str` | `wss://tts.shunyalabs.ai/ws` | WebSocket streaming endpoint URL. |
| `model` | `str` | `"zero-indic"` | TTS model name. |
| `voice` | `str` | `"Rajesh"` | Voice name for the API. |
| `speaker` | `str` | `"Rajesh"` | Speaker name prefix for text formatting. |
| `style` | `str` | `"<Neutral>"` | Emotion style tag. See [Style Tags](#style-tags). |
| `language` | `str` | `"en"` | Language code for transliteration. |
| `sample_rate` | `int` | `16000` | Output audio sample rate in Hz. |
| `output_format` | `str` | `"pcm"` | Audio format (`"pcm"`, `"wav"`, `"mp3"`, `"ogg_opus"`, `"flac"`). |
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

The plugin automatically formats text as `"<Style> text"` before sending to the API. For example:

```python
tts = shunyalabs.TTS(speaker="Rajesh", style="<Happy>")
# Input: "Welcome to our platform"
# Sent:  "<Happy> Welcome to our platform"
```

### Streaming TTS

Token-by-token streaming. Collects text tokens, then synthesizes on flush via WebSocket streaming.

```python
from livekit.agents import AgentSession
from livekit.plugins import shunyalabs

session = AgentSession(
    tts=shunyalabs.TTS(
        speaker="Nisha",
        style="<Conversational>",
        model="zero-indic",
        voice="Nisha",
    ),
)
```

### Chunked (Batch) TTS

Single text → audio synthesis via HTTP batch API.

```python
from livekit.plugins import shunyalabs

tts = shunyalabs.TTS(speaker="Varun", voice="Varun")
stream = tts.synthesize("Hello, how can I help you today?")
```

---

## Full Agent Example

```python
import asyncio
from livekit import api
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import shunyalabs, silero

class MyAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful voice assistant.",
        )

async def entrypoint(ctx):
    session = AgentSession(
        stt=shunyalabs.STT(language="auto"),
        tts=shunyalabs.TTS(
            model="zero-indic",
            voice="Rajesh",
            speaker="Rajesh",
            style="<Conversational>",
        ),
        vad=silero.VAD.load(),
    )
    await session.start(
        agent=MyAgent(),
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )
```

---

## Multilingual Example

```python
# Hindi speaker
tts_hindi = shunyalabs.TTS(
    speaker="Rajesh",
    voice="Rajesh",
    language="hi",
    style="<Neutral>",
)

# English speaker
tts_english = shunyalabs.TTS(
    speaker="Varun",
    voice="Varun",
    language="en",
    style="<Conversational>",
)
```

---

## License

[MIT](../../LICENSE)
