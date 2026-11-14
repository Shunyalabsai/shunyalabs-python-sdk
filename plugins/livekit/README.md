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
| `language` | `str` | `"auto"` | Language code (`"en"`, `"hi"`, ...), `"hinglish"` for Hindi-English mixed speech, or `"auto"` for auto-detection. |
| `api_url` | `str` | `https://asrv2prod.shunyalabs.ai` | REST batch endpoint base URL. |
| `ws_url` | `str` | `wss://asrv2prod.shunyalabs.ai/v1/realtime` | WebSocket streaming endpoint URL. |
| `endpoint_silence_ms` | `int` | `None` | Silence before the service ends a segment. `None` takes the server default, 700 ms. |
| `codeswitch` | `bool` | `None` | With `"hinglish"` (or `"hi"`/`"en"`), the language is picked per final: Hindi in Devanagari, English in Latin script. |

### Capabilities

| Capability | Supported |
|------------|-----------|
| Streaming (real-time) | Yes |
| Interim results | Yes |
| Offline/batch recognition | Yes |

### Streaming STT

Real-time transcription over WebSocket. The SDK opens a connection to the real-time ASR service and sends a JSON init message (`{language, sample_rate, client, ...}`); once the service replies `{"type": "ready"}`, audio frames from LiveKit are streamed as binary data and `{"type": "partial"}` / `{"type": "final"}` messages are surfaced as `SpeechEvent`s. A bare `"end"` marker finalizes the stream. The SDK handles this handshake for you.

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
| First `PARTIAL` of a turn | `START_OF_SPEECH` + `INTERIM_TRANSCRIPT` |
| Later `PARTIAL` | `INTERIM_TRANSCRIPT` |
| `FINAL` (with text) | `START_OF_SPEECH` (if no partial opened the turn) + `FINAL_TRANSCRIPT` + `RECOGNITION_USAGE` + `END_OF_SPEECH` (when it ends the utterance) |
| `UTTERANCE_END` while a turn is open | `END_OF_SPEECH` (a length-capped final at the end of speech, or a noise final the service dropped) |
| `FINAL_REFINED` | `FINAL_TRANSCRIPT` |

Events reach LiveKit in the order the service sent them. Each turn gets exactly one `START_OF_SPEECH` and one `END_OF_SPEECH`. LiveKit reads those two only with `turn_detection="stt"`; with VAD turn detection they are ignored.

With `turn_detection="stt"` and no VAD, LiveKit's `aec_warmup_duration` (default 3 s) replaces the caller's audio with silence for the STT during the first seconds of every agent reply, so words spoken over the agent in that window are not transcribed and cannot interrupt it. Lower it if your audio path has no echo to cancel.

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
| `speed` | `float` | `1.0` | Speaking speed multiplier (0.25 to 4.0, pitch kept). Applies to streaming and batch. |
| `quality` | `str` | `None` | `"low"`, `"medium"` or `"high"`; `None` takes the service default (medium). Applies to streaming and batch. `"low"` gives the fastest first audio but can drop a word in short clips, so avoid it for numbers, dates and codes. |
| `clause_first` | `bool` | `None` | Streaming only. `True` starts speaking at the first clause instead of the first full sentence while the LLM is still sending text, when that opening clause has at least 12 words (measured: first audio 2.5 s to 1.3 s). A possible audible join. `None` takes the service default (off). |

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

Token-by-token streaming. The SDK opens a connection to the real-time TTS service and sends a JSON init message (`{voice, language, model, client}`, plus `speed` when not 1.0 and `quality` / `clause_first` when set); once the service replies `{"type": "ready"}`, text is sent as `{"type": "text", ...}` as soon as it arrives and each segment ends with `{"type": "flush"}`. The service speaks each sentence as soon as it is complete and returns binary PCM audio and `{"type": "done"}`. The session stays open for the next turn, and an interruption sends `{"type": "cancel"}` instead of closing it.

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

## Version tag

Both init messages carry `"client": "livekit-plugins-shunyalabsai/<version>"`, and the TTS socket sends the
same string as its User-Agent, so Shunya Labs support can see which plugin version a session used.

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
