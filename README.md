# Shunyalabs Python SDK

[![PyPI](https://img.shields.io/pypi/v/shunyalabs.svg)](https://pypi.org/project/shunyalabs/)
[![Python](https://img.shields.io/pypi/pyversions/shunyalabs.svg)](https://pypi.org/project/shunyalabs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

The official Python SDK for [Shunyalabs](https://shunyalabs.ai) Speech AI APIs — **ASR** (speech-to-text), **TTS** (text-to-speech), and **Flow** (conversational AI).

Supports both **synchronous** and **asynchronous** clients with HTTP batch and WebSocket streaming modes.

## Installation

```bash
pip install shunyalabs[all]
```

Install only what you need:

```bash
pip install shunyalabs[ASR]     # Speech-to-text only
pip install shunyalabs[TTS]     # Text-to-speech only
pip install shunyalabs[flow]    # Conversational AI
pip install shunyalabs[extras]  # Audio playback helpers (sounddevice)
```

## Authentication

All API calls use `Authorization: Bearer <api_key>` header authentication.

```python
# Option 1: Pass directly
client = AsyncShunyaClient(api_key="your-api-key")

# Option 2: Environment variable (recommended)
# export SHUNYALABS_API_KEY="your-api-key"
client = AsyncShunyaClient()  # auto-reads from env
```

---

## Quick Start

### TTS — Batch (HTTP)

```python
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.tts import TTSConfig

async def main():
    async with AsyncShunyaClient() as client:
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
    async with AsyncShunyaClient() as client:
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
    async with AsyncShunyaClient() as client:
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
    async with AsyncShunyaClient() as client:
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

### Synchronous Client

```python
from shunyalabs import ShunyaClient
from shunyalabs.tts import TTSConfig

with ShunyaClient() as client:
    result = client.tts.synthesize("Hello!", config=TTSConfig(model="zero-indic", voice="Varun"))
    result.save("hello.mp3")

    result = client.asr.transcribe("audio.wav")
    print(result.text)
```

---

## API Reference

### Client Configuration

#### `AsyncShunyaClient` / `ShunyaClient`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | `None` | API key. Falls back to `SHUNYALABS_API_KEY` env var. |
| `timeout` | `float` | `60.0` | Request timeout in seconds. |
| `max_retries` | `int` | `2` | Retries for failed requests (5xx, connection errors). |
| `asr_url` | `str` | `https://asr.shunyalabs.ai` | ASR batch API base URL. |
| `asr_ws_url` | `str` | `wss://asr.shunyalabs.ai/ws` | ASR streaming WebSocket URL. |
| `tts_url` | `str` | `https://tts.shunyalabs.ai` | TTS batch API base URL. |
| `tts_ws_url` | `str` | `wss://tts.shunyalabs.ai/ws` | TTS streaming WebSocket URL. |
| `flow_url` | `str` | `wss://flow.api.shunyalabs.com/v1/flow` | Flow WebSocket URL. |

All URL parameters can also be set via environment variables: `SHUNYALABS_ASR_URL`, `SHUNYALABS_ASR_WS_URL`, `SHUNYALABS_TTS_URL`, `SHUNYALABS_TTS_WS_URL`, `SHUNYALABS_FLOW_URL`.

---

### TTS API

#### `TTSConfig`

Configuration for synthesis requests. Passed as `config=` to `synthesize()` and `stream()`.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str` | **required** | Model name (e.g. `"zero-indic"`). |
| `voice` | `str` | **required** | Speaker voice name (e.g. `"Varun"`, `"Nisha"`, `"Rajesh"`). |
| `response_format` | `OutputFormat` | `"mp3"` | Output audio format. See [Output Formats](#output-formats). |
| `speed` | `float` | `1.0` | Speaking speed multiplier (0.25–4.0). |
| `language` | `str` | `None` | ISO 639-1/639-2 language code (2–3 chars). |
| `trim_silence` | `bool` | `False` | Trim leading/trailing silence from audio. |
| `volume_normalization` | `str` | `None` | `"peak"` or `"loudness"`. |
| `word_timestamps` | `bool` | `False` | Return word-level timestamps (batch only). |
| `background_audio` | `str` | `None` | Preset name or base64-encoded background audio. |
| `background_volume` | `float` | `0.1` | Background volume relative to speech (0.0–1.0). |
| `max_tokens` | `int` | `2048` | Maximum tokens for LLM generation (1–8192). |
| `reference_wav` | `str` | `None` | Base64-encoded reference audio for voice cloning. |
| `reference_text` | `str` | `""` | Transcript of the reference audio. |

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
# Async
result = await client.tts.synthesize("text", config=TTSConfig(...))
result.save("output.mp3")       # Save to file
result.audio_data               # Raw bytes
result.duration_seconds          # Audio duration
result.sample_rate               # Sample rate (Hz)
result.word_timestamps           # List[WordTimestamp] if requested

# Sync
result = client.tts.synthesize("text", config=TTSConfig(...))
```

**Streaming (WebSocket)**

```python
# Async — iterate audio chunks
async for audio_bytes in await client.tts.stream("text", config=TTSConfig(...)):
    play(audio_bytes)

# Async — with chunk metadata
async for chunk_meta, audio_bytes in await client.tts.stream("text", config=TTSConfig(...), detailed=True):
    print(chunk_meta.chunk_index, len(audio_bytes))

# Async — collect all and return combined bytes
audio = await client.tts.synthesize_stream("text", config=TTSConfig(...))

# Async — stream directly to file
await client.tts.stream_to_file("text", "output.pcm", config=TTSConfig(...))

# Sync
for audio_bytes in client.tts.stream("text", config=TTSConfig(...)):
    play(audio_bytes)
```

#### `TTSResult`

Returned by `synthesize()`.

| Attribute | Type | Description |
|-----------|------|-------------|
| `request_id` | `str` | Unique request identifier. |
| `audio_data` | `bytes` | Decoded audio bytes. |
| `sample_rate` | `int` | Audio sample rate in Hz. |
| `duration_seconds` | `float` | Total audio duration. |
| `format` | `str` | Audio format string. |
| `word_timestamps` | `list[WordTimestamp]` | Word-level timestamps (if requested). |

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
| `use_vad_chunking` | `bool` | `True` | Use VAD-based audio chunking. |
| `chunk_size` | `int` | `30` | Audio chunk size in seconds. |
| `enable_diarization` | `bool` | `False` | Enable speaker diarization. |
| `enable_denoising` | `bool` | `False` | Enable audio denoising. |

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
| `enable_medical_correction` | `bool` | `False` | Apply medical term correction. |
| `enable_translation` | `bool` | `False` | Translate transcript. |
| `target_language` | `str` | `None` | Target language for translation. |
| `enable_transliteration` | `bool` | `False` | Transliterate transcript. |
| `enable_code_switch_correction` | `bool` | `False` | Fix code-switching artifacts. |
| `enable_language_identification` | `bool` | `False` | Identify spoken language. |
| `project` | `str` | `None` | Project name for tracking. |

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
| `detected_language` | `str` | Detected language code. |
| `audio_duration` | `float` | Audio duration in seconds. |
| `inference_time_ms` | `float` | Server inference time in ms. |
| `nlp_analysis` | `NLPAnalysis` | NLP results (if any `enable_*` flags were set). |

---

### Endpoints Summary

| Service | Mode | Endpoint | Protocol |
|---------|------|----------|----------|
| ASR | Batch | `POST /v1/audio/transcriptions` | HTTP |
| ASR | Streaming | `/ws` | WebSocket |
| TTS | Batch | `POST /` | HTTP |
| TTS | Streaming | `/ws` | WebSocket |

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

## Framework Plugins

| Framework | Package | Install |
|-----------|---------|---------|
| [LiveKit Agents](plugins/livekit/) | `livekit-plugins-shunyalabs` | `pip install livekit-plugins-shunyalabs` |
| [Pipecat](plugins/pipecat/) | `pipecat-shunyalabs` | `pip install pipecat-shunyalabs` |

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
