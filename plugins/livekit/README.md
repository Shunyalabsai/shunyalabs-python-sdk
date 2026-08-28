# livekit-plugins-shunyalabs

[![PyPI](https://img.shields.io/pypi/v/livekit-plugins-shunyalabsai.svg)](https://pypi.org/project/livekit-plugins-shunyalabsai/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](../../LICENSE)

[Shunyalabs](https://shunyalabs.ai) STT and TTS plugin for [LiveKit Agents](https://docs.livekit.io/agents/).

Provides `STT` (speech-to-text) and `TTS` (text-to-speech) classes that integrate with LiveKit's agent framework, backed by the [Shunyalabs Python SDK](https://github.com/Shunyalabsai/shunyalabs-python-sdk).

## Installation

```bash
pip install livekit-plugins-shunyalabsai
```

## Authentication

Pass your API key. The SDK exchanges your API key for a short-lived access token automatically and refreshes it in the background — you never handle tokens yourself.

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
    tts=shunyalabs.TTS(voice="Rajesh", style="<Neutral>"),
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
| `api_url` | `str` | `https://asrv2prod.shunyalabs.ai` | REST batch endpoint base URL. |
| `ws_url` | `str` | `wss://asrv2prod.shunyalabs.ai/v1/realtime` | WebSocket streaming endpoint URL. |

### Capabilities

| Capability | Supported |
|------------|-----------|
| Streaming (real-time) | Yes |
| Interim results | Yes |
| Offline/batch recognition | Yes |

### Streaming STT

Real-time transcription over WebSocket. The SDK opens a connection to the real-time ASR service and sends a JSON init message (`{language, sample_rate}`); once the service replies `{"type": "ready"}`, audio frames from LiveKit are streamed as binary data and `{"type": "partial"}` / `{"type": "final"}` messages are surfaced as `SpeechEvent`s. A bare `"end"` marker finalizes the stream. The SDK handles this handshake for you.

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
| `api_url` | `str` | `https://ttsv2.shunyalabs.ai` | HTTP batch endpoint base URL. |
| `ws_url` | `str` | `wss://ttsv2.shunyalabs.ai/v1/realtime` | WebSocket streaming endpoint URL. |
| `model` | `str` | `"zero-indic"` | TTS model name. |
| `voice` | `str` | `"Rajesh"` | Voice name for the API. |
| `style` | `str` | `None` | Emotion style tag. See [Style Tags](#style-tags). When omitted, a default style is applied. |
| `language` | `str` | `"en"` | Language code for transliteration. |
| `sample_rate` | `int` | `24000` | Output audio sample rate in Hz. The gateway emits 24 kHz PCM on both the streaming and batch paths; override only if you resample the audio yourself. |
| `output_format` | `str` | `"pcm"` | Audio format for the **batch** (`synthesize`) path (`"pcm"`, `"wav"`, `"mp3"`, `"ogg_opus"`, `"flac"`). The real-time stream is always PCM. |
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
tts = shunyalabs.TTS(voice="Rajesh", style="<Happy>")
# Input: "Welcome to our platform"
# Sent:  "<Happy> Welcome to our platform"
```

### Streaming TTS

Token-by-token streaming. The SDK opens a connection to the real-time TTS service and sends a JSON init message (`{voice, language}`); once the service replies `{"type": "ready"}`, collected text is sent as `{"type": "text", ...}` followed by `{"type": "flush"}`, and the service returns `{"type": "speaking"}`, binary PCM audio, and `{"type": "done"}`. The SDK handles this handshake for you.

```python
from livekit.agents import AgentSession
from livekit.plugins import shunyalabs

session = AgentSession(
    tts=shunyalabs.TTS(
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

tts = shunyalabs.TTS(voice="Varun")
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
    voice="Rajesh",
    language="hi",
    style="<Neutral>",
)

# English speaker
tts_english = shunyalabs.TTS(
    voice="Varun",
    language="en",
    style="<Conversational>",
)
```

---

## Custom endpoints

The services can be repointed **without changing code or upgrading the package**.
Resolution precedence: **explicit argument → endpoint returned by the token service
→ environment variable → built-in default.**

```bash
export SHUNYALABS_ASR_URL="https://<host>"          # batch
export SHUNYALABS_ASR_WS_URL="wss://<host>/v1/realtime"   # streaming
export SHUNYALABS_TTS_URL="https://<host>"
export SHUNYALABS_TTS_WS_URL="wss://<host>/v1/realtime"
```

```python
# or explicitly per instance
stt = shunyalabs.STT(api_url="https://<host>", ws_url="wss://<host>/v1/realtime")
tts = shunyalabs.TTS(api_url="https://<host>", ws_url="wss://<host>/v1/realtime")
```

If the token service returns an `endpoints` object, the SDK uses it automatically —
so Shunya Labs can move an endpoint centrally with no change on your side.

---

## License

[MIT](../../LICENSE)
