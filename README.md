# Shunyalabs Python SDK

[![PyPI](https://img.shields.io/pypi/v/shunyalabs.svg)](https://pypi.org/project/shunyalabs/)
[![Python](https://img.shields.io/pypi/pyversions/shunyalabs.svg)](https://pypi.org/project/shunyalabs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

The official Python SDK for [Shunyalabs](https://shunyalabs.ai) Speech AI APIs — **ASR** (speech-to-text) and **TTS** (text-to-speech).

Supports HTTP batch and WebSocket streaming modes with a fully async client.

## Installation

```bash
pip install shunyalabs[all]
```

Install only what you need:

```bash
pip install shunyalabs[ASR]     # Speech-to-text only
pip install shunyalabs[TTS]     # Text-to-speech only
pip install shunyalabs[extras]  # Audio playback helpers (sounddevice)
```

## Authentication

All API calls use `Authorization: Bearer <api_key>` header authentication.

```python
from shunyalabs import AsyncShunyaClient

client = AsyncShunyaClient(api_key="your-api-key")
```

Or set the `SHUNYALABS_API_KEY` environment variable and omit `api_key=`.

---

## Quick Start

### TTS — Batch (HTTP)

```python
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.tts import TTSConfig

async def main():
    async with AsyncShunyaClient(api_key="your-api-key") as client:
        result = await client.tts.synthesize(
            "Hello, world!",
            config=TTSConfig(model="zero-indic", voice="Varun"),
        )
        result.save("output.mp3")
        print(f"{len(result.audio_data)} bytes saved")

asyncio.run(main())
```

### TTS — Streaming (WebSocket)

```python
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.tts import TTSConfig

async def main():
    async with AsyncShunyaClient(api_key="your-api-key") as client:
        chunks = []
        async for audio in await client.tts.stream(
            "Hello, world!",
            config=TTSConfig(model="zero-indic", voice="Varun"),
        ):
            chunks.append(audio)
        print(f"{len(chunks)} chunks, {sum(len(c) for c in chunks)} bytes")

asyncio.run(main())
```

### ASR — Batch (HTTP)

```python
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.asr import TranscriptionConfig

async def main():
    async with AsyncShunyaClient(api_key="your-api-key") as client:
        result = await client.asr.transcribe(
            "audio.wav",
            config=TranscriptionConfig(model="zero-indic"),
        )
        print(result.text)

asyncio.run(main())
```

### ASR — Streaming (WebSocket)

```python
import asyncio, subprocess
from shunyalabs import AsyncShunyaClient
from shunyalabs.asr import StreamingConfig, StreamingMessageType

async def main():
    async with AsyncShunyaClient(api_key="your-api-key") as client:
        conn = await client.asr.stream(
            config=StreamingConfig(language="en", sample_rate=16000),
        )

        @conn.on(StreamingMessageType.FINAL_SEGMENT)
        def on_seg(msg):
            print(f"[seg] {msg.text}")

        @conn.on(StreamingMessageType.FINAL)
        def on_final(msg):
            print(f"[final] {msg.text}")

        # Convert audio to 16kHz mono PCM and stream
        pcm = subprocess.run(
            ["ffmpeg", "-i", "audio.wav", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
            capture_output=True,
        ).stdout

        for i in range(0, len(pcm), 4096):
            await conn.send_audio(pcm[i : i + 4096])

        await conn.end()
        await conn.close()

asyncio.run(main())
```

---

## API Reference

### Client Configuration

#### `AsyncShunyaClient`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `timeout` | `float` | `60.0` | Request timeout in seconds. |
| `max_retries` | `int` | `2` | Retries for failed requests (5xx, connection errors). |
| `asr_url` | `str` | `https://asr.shunyalabs.ai` | ASR batch API base URL. |
| `asr_ws_url` | `str` | `wss://asr.shunyalabs.ai/ws` | ASR streaming WebSocket URL. |
| `tts_url` | `str` | `https://tts.shunyalabs.ai` | TTS batch API base URL. |
| `tts_ws_url` | `str` | `wss://tts.shunyalabs.ai/ws` | TTS streaming WebSocket URL. |

All URL parameters can also be set via environment variables: `SHUNYALABS_ASR_URL`, `SHUNYALABS_ASR_WS_URL`, `SHUNYALABS_TTS_URL`, `SHUNYALABS_TTS_WS_URL`.

**Examples:**

```python
# Default — uses production endpoints
client = AsyncShunyaClient(api_key="your-api-key")

# Custom timeout and retries
client = AsyncShunyaClient(api_key="your-api-key", timeout=120.0, max_retries=5)

# Self-hosted endpoints
client = AsyncShunyaClient(
    api_key="your-api-key",
    asr_url="https://my-asr-server.example.com",
    tts_url="https://my-tts-server.example.com",
    tts_ws_url="wss://my-tts-server.example.com/ws",
)
```

---

### TTS API

#### `TTSConfig`

Configuration for synthesis requests. Passed as `config=` to `synthesize()` and `stream()`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | **required** | Model name (e.g. `"zero-indic"`). |
| `voice` | `str` | **required** | Speaker voice name. See [Available Speakers](#available-speakers). |
| `response_format` | `OutputFormat` | `"mp3"` | Output audio format. See [Output Formats](#output-formats). |
| `speed` | `float` | `1.0` | Speaking speed multiplier (0.25–4.0). |
| `trim_silence` | `bool` | `False` | Trim leading/trailing silence from audio. |
| `volume_normalization` | `str` | `None` | `"peak"` or `"loudness"`. |
| `background_audio` | `str` | `None` | Preset name or base64-encoded background audio. |
| `background_volume` | `float` | `0.1` | Background volume relative to speech (0.0–1.0). |

#### TTS Parameter Examples

**`model` — Select the TTS model**

```python
# Currently available: "zero-indic"
config = TTSConfig(model="zero-indic", voice="Rajesh")
result = await client.tts.synthesize("Hello!", config=config)
# Output: 48000 bytes saved to output.mp3
```

**`voice` — Choose a speaker**

```python
# Male English speaker
config = TTSConfig(model="zero-indic", voice="Varun")

# Female Hindi speaker
config = TTSConfig(model="zero-indic", voice="Sunita")

# Any speaker can speak any language — voice only controls vocal characteristics
config = TTSConfig(model="zero-indic", voice="Murugan")  # Tamil-native male speaking English
result = await client.tts.synthesize("Good morning, how are you?", config=config)
```

**`response_format` — Output audio format**

Values: `"pcm"`, `"wav"`, `"mp3"`, `"ogg_opus"`, `"flac"`, `"mulaw"`, `"alaw"`

```python
# MP3 (default) — compressed, good for storage
config = TTSConfig(model="zero-indic", voice="Varun", response_format="mp3")
result = await client.tts.synthesize("Hello!", config=config)
result.save("output.mp3")
# Output: 12480 bytes (compressed)

# WAV — uncompressed, good for processing
config = TTSConfig(model="zero-indic", voice="Varun", response_format="wav")
result = await client.tts.synthesize("Hello!", config=config)
result.save("output.wav")
# Output: 96044 bytes (uncompressed with header)

# PCM — raw samples, for real-time pipelines
config = TTSConfig(model="zero-indic", voice="Varun", response_format="pcm")
result = await client.tts.synthesize("Hello!", config=config)
# Output: 96000 bytes (raw 16-bit samples)

# OGG Opus — compressed, good for web streaming
config = TTSConfig(model="zero-indic", voice="Varun", response_format="ogg_opus")

# mu-law / A-law — for telephony systems
config = TTSConfig(model="zero-indic", voice="Varun", response_format="mulaw")
config = TTSConfig(model="zero-indic", voice="Varun", response_format="alaw")
```

**`speed` — Speaking speed multiplier**

Range: `0.25` (very slow) to `4.0` (very fast). Default: `1.0`.

```python
# Slow — good for language learning
config = TTSConfig(model="zero-indic", voice="Nisha", speed=0.75)
result = await client.tts.synthesize("Take your time to understand this.", config=config)
# Output: longer audio, ~33% slower than normal

# Normal speed (default)
config = TTSConfig(model="zero-indic", voice="Nisha", speed=1.0)

# Fast — good for notifications or summaries
config = TTSConfig(model="zero-indic", voice="Nisha", speed=1.5)
result = await client.tts.synthesize("Quick update: your order has shipped.", config=config)
# Output: shorter audio, ~50% faster than normal

# Very fast
config = TTSConfig(model="zero-indic", voice="Nisha", speed=2.0)
```

**`trim_silence` — Remove silence padding**

```python
# Without trim (default) — audio may have leading/trailing silence
config = TTSConfig(model="zero-indic", voice="Rajesh", trim_silence=False)
result = await client.tts.synthesize("Hello.", config=config)
# Output: 64000 bytes (includes silence padding)

# With trim — tighter audio, no dead air
config = TTSConfig(model="zero-indic", voice="Rajesh", trim_silence=True)
result = await client.tts.synthesize("Hello.", config=config)
# Output: 48000 bytes (silence stripped)
```

**`volume_normalization` — Normalize audio loudness**

Values: `None` (off), `"peak"`, `"loudness"`

```python
# No normalization (default)
config = TTSConfig(model="zero-indic", voice="Rajesh")

# Peak normalization — scale so the loudest sample hits 0 dBFS
config = TTSConfig(model="zero-indic", voice="Rajesh", volume_normalization="peak")
result = await client.tts.synthesize("This audio will have consistent peak levels.", config=config)

# Loudness normalization — perceptually even loudness (EBU R128)
config = TTSConfig(model="zero-indic", voice="Rajesh", volume_normalization="loudness")
result = await client.tts.synthesize("This audio will sound equally loud regardless of content.", config=config)
```

**`background_audio` + `background_volume` — Add background music**

```python
import base64

# Using a preset name
config = TTSConfig(
    model="zero-indic",
    voice="Nisha",
    background_audio="cafe-ambience",
    background_volume=0.15,  # 15% volume relative to speech
)
result = await client.tts.synthesize("Welcome to our podcast.", config=config)

# Using custom audio (base64-encoded)
with open("background.mp3", "rb") as f:
    bg_b64 = base64.b64encode(f.read()).decode()

config = TTSConfig(
    model="zero-indic",
    voice="Nisha",
    background_audio=bg_b64,
    background_volume=0.1,  # 10% volume (subtle background)
)
result = await client.tts.synthesize("Welcome to our podcast.", config=config)
result.save("podcast_intro.mp3")
```

#### Available Speakers

Each speaker has a native language listed below, but **every speaker can speak in any language** — the native language only indicates the speaker's voice characteristics and accent.

| Language | Male Speaker | Female Speaker |
|----------|-------------|----------------|
| Assamese | `Bimal` | `Anjana` |
| Bengali | `Arjun` | `Priyanka` |
| Bodo | `Daimalu` | `Hasina` |
| Dogri | `Vishal` | `Neelam` |
| English | `Varun` | `Nisha` |
| Gujarati | `Rakesh` | `Pooja` |
| Hindi | `Rajesh` | `Sunita` |
| Kannada | `Kiran` | `Shreya` |
| Kashmiri | `Farooq` | `Habba` |
| Konkani | `Mohan` | `Sarita` |
| Maithili | `Suresh` | `Meera` |
| Malayalam | `Krishnan` | `Deepa` |
| Manipuri | `Tomba` | `Ibemhal` |
| Marathi | `Siddharth` | `Ananya` |
| Nepali | `Bikash` | `Sapana` |
| Odia | `Bijay` | `Sujata` |
| Punjabi | `Gurpreet` | `Simran` |
| Sanskrit | `Vedant` | `Gayatri` |
| Santali | `Chandu` | `Roshni` |
| Sindhi | `Amjad` | `Kavita` |
| Tamil | `Murugan` | `Thangam` |
| Telugu | `Vishnu` | `Lakshmi` |
| Urdu | `Salman` | `Fatima` |

**23 languages, 46 speakers** (1 male + 1 female per language).

#### Expression Styles

Control the emotional tone by passing a `style` tag in the text prefix (e.g. `"Rajesh: <Happy> Hello!"`).

| Style Tag | Description |
|-----------|-------------|
| `<Happy>` | Joyful, upbeat tone |
| `<Sad>` | Somber, melancholic tone |
| `<Angry>` | Forceful, intense tone |
| `<Fearful>` | Anxious, trembling tone |
| `<Surprised>` | Exclamatory, astonished tone |
| `<Disgust>` | Repulsed, disapproving tone |
| `<News>` | Formal news-anchor style |
| `<Conversational>` | Casual, everyday speech |
| `<Narrative>` | Storytelling / audiobook style |
| `<Enthusiastic>` | Energetic, passionate tone |
| `<Neutral>` | Clean read-speech (default, no tag needed) |

**Expression style examples:**

```python
# Happy greeting
config = TTSConfig(model="zero-indic", voice="Rajesh")
result = await client.tts.synthesize("Rajesh: <Happy> Welcome aboard! We're thrilled to have you.", config=config)

# News anchor reading
config = TTSConfig(model="zero-indic", voice="Nisha")
result = await client.tts.synthesize("Nisha: <News> Breaking news: the markets rallied today.", config=config)

# Storytelling
config = TTSConfig(model="zero-indic", voice="Krishnan")
result = await client.tts.synthesize("Krishnan: <Narrative> Once upon a time, in a land far away...", config=config)

# Conversational chatbot
config = TTSConfig(model="zero-indic", voice="Simran")
result = await client.tts.synthesize("Simran: <Conversational> Hey! How's it going?", config=config)

# Neutral (default — no tag needed)
config = TTSConfig(model="zero-indic", voice="Varun")
result = await client.tts.synthesize("Varun: Your account balance is five thousand rupees.", config=config)
```

#### Output Formats

| Format | Value |
|--------|-------|
| PCM (raw) | `"pcm"` |
| WAV | `"wav"` |
| MP3 | `"mp3"` |
| OGG Opus | `"ogg_opus"` |
| FLAC | `"flac"` |
| mu-law | `"mulaw"` |
| A-law | `"alaw"` |

#### TTS Methods

**Batch (HTTP)**

```python
result = await client.tts.synthesize("text", config=TTSConfig(...))
result.save("output.mp3")       # Save to file
result.audio_data               # Raw bytes
result.sample_rate               # Sample rate (Hz)
```

**Streaming (WebSocket)**

```python
# Iterate audio chunks
async for audio_bytes in await client.tts.stream("text", config=TTSConfig(...)):
    play(audio_bytes)

# With chunk metadata
async for chunk_meta, audio_bytes in await client.tts.stream("text", config=TTSConfig(...), detailed=True):
    print(chunk_meta.chunk_index, len(audio_bytes))

# Collect all and return combined bytes
audio = await client.tts.synthesize_stream("text", config=TTSConfig(...))

# Stream directly to file
await client.tts.stream_to_file("text", "output.pcm", config=TTSConfig(...))
```

#### `TTSResult`

Returned by `synthesize()`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `audio_data` | `bytes` | Decoded audio bytes. |
| `sample_rate` | `int` | Audio sample rate in Hz. |
| `format` | `str` | Audio format string. |

---

### ASR API

#### `TranscriptionConfig`

Configuration for batch transcription. Passed as `config=` to `transcribe()`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | **required** | Model name (e.g. `"zero-indic"`). |
| `language_code` | `str` | `"auto"` | Language code or `"auto"` for auto-detection. |
| `task` | `str` | `"transcribe"` | Task type (`"transcribe"`). |
| `output_script` | `str` | `"auto"` | Output script (`"auto"`, `"latin"`, `"native"`). |
| `enable_diarization` | `bool` | `False` | Enable speaker diarization. |

**NLP Features:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enable_intent_detection` | `bool` | `False` | Detect intent from transcript. |
| `intent_choices` | `list[str]` | `None` | Constrain intent to specific choices. |
| `enable_summarization` | `bool` | `False` | Generate transcript summary. |
| `summary_max_length` | `int` | `150` | Maximum summary length. |
| `enable_sentiment_analysis` | `bool` | `False` | Analyze sentiment. |
| `enable_emotion_diarization` | `bool` | `False` | Detect emotions per segment. |

**Post-processing:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enable_profanity_hashing` | `bool` | `False` | Hash profane words. |
| `hash_keywords` | `list[str]` | `None` | Custom keywords to hash. |
| `enable_keyterm_normalization` | `bool` | `False` | Normalize key terms. |
| `enable_translation` | `bool` | `False` | Translate transcript. |
| `target_language` | `str` | `None` | Target language for translation. |
| `enable_transliteration` | `bool` | `False` | Transliterate transcript. |
| `project` | `str` | `None` | Project name for tracking. |

#### ASR Parameter Examples

**`model` + `language_code` — Basic transcription**

```python
# Auto-detect language (default)
config = TranscriptionConfig(model="zero-indic", language_code="auto")
result = await client.asr.transcribe("audio.wav", config=config)
print(result.text)
print(f"Detected: {result.detected_language}")
# Output:
#   "Hello, how are you doing today?"
#   Detected: English

# Specify language for better accuracy
config = TranscriptionConfig(model="zero-indic", language_code="hi")
result = await client.asr.transcribe("hindi_audio.wav", config=config)
print(result.text)
# Output: "नमस्ते, आप कैसे हैं?"
```

**`output_script` — Control output script**

Values: `"auto"`, `"latin"`, `"native"`

```python
# Native script (default for auto)
config = TranscriptionConfig(model="zero-indic", language_code="hi", output_script="native")
result = await client.asr.transcribe("hindi_audio.wav", config=config)
print(result.text)
# Output: "नमस्ते, आप कैसे हैं?"

# Latin/Roman script — transliterated output
config = TranscriptionConfig(model="zero-indic", language_code="hi", output_script="latin")
result = await client.asr.transcribe("hindi_audio.wav", config=config)
print(result.text)
# Output: "namaste, aap kaise hain?"

# Auto — server decides based on language
config = TranscriptionConfig(model="zero-indic", output_script="auto")
```

**`enable_diarization` — Speaker identification**

```python
config = TranscriptionConfig(model="zero-indic", enable_diarization=True)
result = await client.asr.transcribe("meeting.wav", config=config)
for seg in result.segments:
    print(f"  [{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}")
# Output:
#   [0.0s - 3.2s] [Speaker 1] Good morning, let's begin the meeting.
#   [3.5s - 6.8s] [Speaker 2] Sure, I have the report ready.
#   [7.0s - 10.1s] [Speaker 1] Great, please go ahead.
```

**`enable_intent_detection` + `intent_choices` — Detect user intent**

```python
# Open intent detection
config = TranscriptionConfig(
    model="zero-indic",
    enable_intent_detection=True,
)
result = await client.asr.transcribe("customer_call.wav", config=config)
print(result.text)
print(result.nlp_analysis.intent)
# Output:
#   "I want to cancel my subscription"
#   {"label": "cancellation", "confidence": 0.94}

# Constrained intent — pick from specific choices
config = TranscriptionConfig(
    model="zero-indic",
    enable_intent_detection=True,
    intent_choices=["booking", "cancellation", "complaint", "inquiry"],
)
result = await client.asr.transcribe("customer_call.wav", config=config)
print(result.nlp_analysis.intent)
# Output: {"label": "cancellation", "confidence": 0.97}
```

**`enable_summarization` + `summary_max_length` — Summarize transcript**

```python
config = TranscriptionConfig(
    model="zero-indic",
    enable_summarization=True,
    summary_max_length=100,  # max 100 characters
)
result = await client.asr.transcribe("meeting_recording.wav", config=config)
print(f"Full transcript: {result.text[:80]}...")
print(f"Summary: {result.nlp_analysis.summary}")
# Output:
#   Full transcript: Good morning everyone. Today we'll review Q3 results. Revenue grew by...
#   Summary: Q3 review meeting covering revenue growth, cost optimization, and next quarter targets.
```

**`enable_sentiment_analysis` — Detect sentiment**

```python
config = TranscriptionConfig(
    model="zero-indic",
    enable_sentiment_analysis=True,
)
result = await client.asr.transcribe("feedback.wav", config=config)
print(result.text)
print(result.nlp_analysis.sentiment)
# Output:
#   "The product is amazing, I absolutely love it!"
#   {"label": "positive", "score": {"positive": 0.96, "negative": 0.02, "neutral": 0.02}}
```

**`enable_emotion_diarization` — Detect emotions per segment**

```python
config = TranscriptionConfig(
    model="zero-indic",
    enable_emotion_diarization=True,
)
result = await client.asr.transcribe("conversation.wav", config=config)
print(result.nlp_analysis.emotion)
# Output:
#   {"segments": [
#     {"start": 0.0, "end": 3.2, "emotion": "neutral", "text": "Hello, how can I help?"},
#     {"start": 3.5, "end": 7.1, "emotion": "angry", "text": "I've been waiting for an hour!"},
#     {"start": 7.4, "end": 10.0, "emotion": "empathetic", "text": "I'm sorry about that."}
#   ]}
```

**`enable_profanity_hashing` + `hash_keywords` — Redact sensitive words**

```python
# Hash common profanity
config = TranscriptionConfig(
    model="zero-indic",
    enable_profanity_hashing=True,
)
result = await client.asr.transcribe("audio.wav", config=config)
print(result.text)
# Output: "What the #### is going on?"

# Hash custom keywords (e.g., names, account numbers)
config = TranscriptionConfig(
    model="zero-indic",
    enable_profanity_hashing=True,
    hash_keywords=["John", "Acme Corp", "Project Alpha"],
)
result = await client.asr.transcribe("meeting.wav", config=config)
print(result.text)
# Output: "#### from ######### said ############# is on track."
```

**`enable_translation` + `target_language` — Translate transcript**

```python
config = TranscriptionConfig(
    model="zero-indic",
    language_code="hi",
    enable_translation=True,
    target_language="en",
)
result = await client.asr.transcribe("hindi_audio.wav", config=config)
print(f"Original: {result.text}")
print(f"Translation: {result.nlp_analysis.translation}")
# Output:
#   Original: नमस्ते, आज मौसम बहुत अच्छा है।
#   Translation: Hello, the weather is very nice today.
```

**`enable_transliteration` — Transliterate to Latin script**

```python
config = TranscriptionConfig(
    model="zero-indic",
    language_code="hi",
    enable_transliteration=True,
)
result = await client.asr.transcribe("hindi_audio.wav", config=config)
print(f"Native: {result.text}")
print(f"Transliterated: {result.nlp_analysis.transliteration}")
# Output:
#   Native: नमस्ते, आज मौसम बहुत अच्छा है।
#   Transliterated: namaste, aaj mausam bahut achha hai.
```

**`enable_keyterm_normalization` — Normalize domain terms**

```python
config = TranscriptionConfig(
    model="zero-indic",
    enable_keyterm_normalization=True,
)
result = await client.asr.transcribe("tech_audio.wav", config=config)
print(result.text)
# Output: "The API returns JSON over HTTPS." (normalized from "A P I", "jay son", "H T T P S")
```

**`project` — Tag requests for tracking**

```python
config = TranscriptionConfig(
    model="zero-indic",
    project="customer-support-q1",
)
result = await client.asr.transcribe("call.wav", config=config)
# Request is tagged with project name for usage tracking and analytics
```

#### ASR Methods

**Batch (HTTP)**

```python
# From file path
result = await client.asr.transcribe("audio.wav", config=TranscriptionConfig(model="zero-indic"))

# From file object
with open("audio.wav", "rb") as f:
    result = await client.asr.transcribe_file(f, config=TranscriptionConfig(model="zero-indic"))

# From URL
result = await client.asr.transcribe_url("https://example.com/audio.wav", config=TranscriptionConfig(model="zero-indic"))
```

**Streaming (WebSocket)**

```python
conn = await client.asr.stream(config=StreamingConfig(language="en"))
```

#### `StreamingConfig`

Configuration for the WebSocket streaming session.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `language` | `str` | `"auto"` | Language code or `"auto"`. |
| `sample_rate` | `int` | `16000` | Audio sample rate in Hz. |
| `dtype` | `str` | `"int16"` | Audio data type (`"int16"`, `"float32"`). |
| `chunk_size_sec` | `float` | `1.0` | Processing chunk size in seconds. |
| `silence_threshold_sec` | `float` | `0.5` | Silence duration to trigger segmentation. |

#### Streaming Parameter Examples

**`language` — Set recognition language**

```python
# Auto-detect (default)
conn = await client.asr.stream(config=StreamingConfig(language="auto"))

# Specific language for better accuracy
conn = await client.asr.stream(config=StreamingConfig(language="en"))
conn = await client.asr.stream(config=StreamingConfig(language="hi"))
conn = await client.asr.stream(config=StreamingConfig(language="ta"))
```

**`sample_rate` + `dtype` — Match your audio source**

```python
# Standard microphone input: 16kHz, 16-bit integer (default)
conn = await client.asr.stream(config=StreamingConfig(
    sample_rate=16000,
    dtype="int16",
))

# High-quality audio: 48kHz, 32-bit float
conn = await client.asr.stream(config=StreamingConfig(
    sample_rate=48000,
    dtype="float32",
))
```

**`chunk_size_sec` — Processing window size**

```python
# Smaller chunks = lower latency, more partial results
conn = await client.asr.stream(config=StreamingConfig(chunk_size_sec=0.5))

# Larger chunks = more context, potentially better accuracy
conn = await client.asr.stream(config=StreamingConfig(chunk_size_sec=2.0))
```

**`silence_threshold_sec` — Control segment boundaries**

```python
# Quick segmentation — short pauses trigger a new segment
conn = await client.asr.stream(config=StreamingConfig(silence_threshold_sec=0.3))
# Good for: fast-paced dialogue, command recognition

# Patient segmentation — only split on longer pauses
conn = await client.asr.stream(config=StreamingConfig(silence_threshold_sec=1.5))
# Good for: lectures, monologues, dictation
```

#### Streaming Events

Register event handlers on the connection object:

```python
conn = await client.asr.stream(config=StreamingConfig(language="en"))

@conn.on(StreamingMessageType.PARTIAL)
def on_partial(msg):
    print(f"Interim: {msg.text}")

@conn.on(StreamingMessageType.FINAL_SEGMENT)
def on_segment(msg):
    print(f"Segment: {msg.text}")

@conn.on(StreamingMessageType.FINAL)
def on_final(msg):
    print(f"Final: {msg.text} ({msg.audio_duration_sec}s)")

@conn.on(StreamingMessageType.DONE)
def on_done(msg):
    print(f"Done. {msg.total_segments} segments, {msg.total_audio_duration_sec}s")

@conn.on(StreamingMessageType.ERROR)
def on_error(msg):
    print(f"Error: {msg.message}")
```

| Event | Model | Key Attributes |
|-------|-------|----------------|
| `PARTIAL` | `StreamingPartial` | `text`, `language`, `segment_id`, `latency_ms` |
| `FINAL_SEGMENT` | `StreamingFinalSegment` | `text`, `language`, `segment_id`, `silence_duration_ms` |
| `FINAL` | `StreamingFinal` | `text`, `language`, `audio_duration_sec`, `inference_time_ms` |
| `DONE` | `StreamingDone` | `total_segments`, `total_audio_duration_sec` |
| `ERROR` | `StreamingError` | `message`, `code` |

#### Streaming Connection Methods

```python
await conn.send_audio(pcm_bytes)   # Send raw PCM audio
await conn.end()                    # Signal end of audio stream
await conn.close()                  # Close WebSocket connection
conn.is_closed                      # Check connection status
conn.session_id                     # Server-assigned session ID
```

#### `TranscriptionResult`

Returned by `transcribe()`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `success` | `bool` | Whether transcription succeeded. |
| `request_id` | `str` | Unique request identifier. |
| `text` | `str` | Full transcription text. |
| `segments` | `list[SegmentResult]` | Time-aligned segments (`start`, `end`, `text`). |
| `detected_language` | `str` | Detected language name (e.g. `"English"`, `"Hindi"`, `"Kannada"`). |
| `audio_duration` | `float` | Audio duration in seconds. |
| `inference_time_ms` | `float` | Server inference time in ms. |
| `nlp_analysis` | `NLPAnalysis` | NLP results (if any `enable_*` flags were set). |

---

### Exceptions

All exceptions inherit from `ShunyalabsError`.

| Exception | Description |
|-----------|-------------|
| `AuthenticationError` | Invalid or missing API key (401). |
| `PermissionDeniedError` | Insufficient permissions (403). |
| `NotFoundError` | Resource not found (404). |
| `RateLimitError` | Rate limit exceeded (429). |
| `ServerError` | Server-side error (5xx). |
| `TimeoutError` | Request timed out. |
| `ConnectionError` | Network connectivity issue. |
| `TranscriptionError` | ASR-specific error. |
| `SynthesisError` | TTS-specific error. |

---

## Development

```bash
git clone https://github.com/Shunyalabsai/shunyalabs-python-sdk.git
cd shunyalabs-python-sdk

python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/
black --check src/
mypy src/
```

## License

[MIT](LICENSE)
