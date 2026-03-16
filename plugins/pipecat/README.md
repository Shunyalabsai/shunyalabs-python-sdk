# pipecat-shunyalabs

[![PyPI](https://img.shields.io/pypi/v/pipecat-shunyalabs.svg)](https://pypi.org/project/pipecat-shunyalabs/)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](../../LICENSE)

[Shunyalabs](https://shunyalabs.ai) STT and TTS services for [Pipecat](https://github.com/pipecat-ai/pipecat).

Provides `ShunyalabsSTTService` and `ShunyalabsTTSService` that integrate with Pipecat's pipeline framework, backed by the [Shunyalabs Python SDK](https://github.com/Shunyalabsai/shunyalabs-python-sdk).

**Key capabilities:**

- Real-time streaming ASR with interim and final transcription frames
- High-fidelity voice synthesis with 46 speakers across 23 languages
- 11 emotion/delivery style tags for expressive voice responses
- Native Pipecat frame protocol — drop-in with any Pipecat pipeline
- Persistent WebSocket for STT; per-request WebSocket for TTS
- Output formats: PCM, WAV, MP3, OGG Opus, FLAC, mu-law, A-law

## Installation

**Requirements:** Python 3.8+, Pipecat framework, a valid Shunyalabs API key.

```bash
pip install pipecat-shunyalabs
```

Install with a transport:

```bash
# Daily WebRTC transport
pip install pipecat-shunyalabs pipecat-ai[daily]
```

## Authentication

Set your API key as an environment variable (recommended):

```bash
export SHUNYALABS_API_KEY="your-api-key"
```

Or pass it directly:

```python
stt = ShunyalabsSTTService(api_key="your-api-key")
tts = ShunyalabsTTSService(api_key="your-api-key")
```

> **Security:** Never commit API keys to source control. Use a secrets manager (GCP Secret Manager, AWS Secrets Manager, HashiCorp Vault) in production.

---

## Quick Start

```python
import asyncio, os
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.local.audio import LocalAudioTransport
from pipecat_shunyalabs import ShunyalabsSTTService, ShunyalabsTTSService

async def main():
    transport = LocalAudioTransport()

    stt = ShunyalabsSTTService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        language="en",
    )

    llm = OpenAILLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        model="gpt-4o",
    )

    tts = ShunyalabsTTSService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        voice="Rajesh",
        language="en",
        style="<Conversational>",
    )

    pipeline = Pipeline([transport.input(), stt, llm, tts, transport.output()])
    task = PipelineTask(pipeline, PipelineParams(allow_interruptions=True))
    await PipelineRunner().run(task)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## STT — `ShunyalabsSTTService`

Real-time streaming speech-to-text over WebSocket. Maintains a persistent connection for the lifetime of the pipeline. Supports 23 Indian and international languages with automatic language detection.

### Parameters

| Parameter     | Type  | Default                      | Description                                                         |
| ------------- | ----- | ---------------------------- | ------------------------------------------------------------------- |
| `api_key`     | `str` | `None`                       | API key. Falls back to `SHUNYALABS_API_KEY` env var.                |
| `language`    | `str` | `"auto"`                     | Language code (e.g. `"en"`, `"hi"`) or `"auto"` for auto-detection. |
| `url`         | `str` | `wss://asr.shunyalabs.ai/ws` | WebSocket endpoint URL.                                             |
| `sample_rate` | `int` | `16000`                      | Expected audio sample rate in Hz. Must match transport input.       |

### How It Works

1. On pipeline `start`, opens a WebSocket connection to the Shunyalabs ASR gateway.
2. Audio chunks from the pipeline input are forwarded via `send_audio()`.
3. The gateway's built-in VAD detects speech boundaries and emits transcription events.
4. Events are mapped to Pipecat frames and pushed into the pipeline.

### Frame Mapping

| Shunyalabs Event | Pipecat Frame                                                              |
| ---------------- | -------------------------------------------------------------------------- |
| `PARTIAL`        | `InterimTranscriptionFrame` — emitted continuously as speech is recognized |
| `FINAL_SEGMENT`  | `TranscriptionFrame` — emitted at speech segment boundary                  |
| `FINAL`          | `TranscriptionFrame` — emitted when full utterance is finalized            |

### Example

```python
from pipecat_shunyalabs import ShunyalabsSTTService

stt = ShunyalabsSTTService(
    language="hi",  # Hindi; or 'auto' for detection
    sample_rate=16000,
)
```

### Auto-Reconnect

If the WebSocket connection drops during audio streaming, the service automatically reconnects and resumes sending audio.

---

## TTS — `ShunyalabsTTSService`

Streaming text-to-speech over WebSocket. Each synthesis request opens a new connection, streams audio chunks back as `TTSAudioRawFrame` frames. Supports 46 speakers across 23 languages — any speaker can synthesize in any language.

### Parameters

| Parameter       | Type    | Default                      | Description                                                   |
| --------------- | ------- | ---------------------------- | ------------------------------------------------------------- |
| `api_key`       | `str`   | `None`                       | API key. Falls back to `SHUNYALABS_API_KEY` env var.          |
| `url`           | `str`   | `wss://tts.shunyalabs.ai/ws` | WebSocket endpoint URL.                                       |
| `model`         | `str`   | `"zero-indic"`               | TTS model identifier.                                         |
| `voice`         | `str`   | `"Rajesh"`                   | Speaker voice. See [Available Speakers](#available-speakers). |
| `speaker`       | `str`   | `"Rajesh"`                   | Speaker identifier (typically same as `voice`).               |
| `style`         | `str`   | `"<Neutral>"`                | Emotion/delivery style tag. See [Style Tags](#style-tags).    |
| `language`      | `str`   | `"en"`                       | Output language code (e.g. `"en"`, `"hi"`, `"ta"`).           |
| `output_format` | `str`   | `"pcm"`                      | Audio encoding. See [Output Formats](#output-formats).        |
| `speed`         | `float` | `1.0`                        | Speaking speed multiplier (0.25–4.0).                         |

### Output Formats

| Format           | Value      | Recommended Use                                 |
| ---------------- | ---------- | ----------------------------------------------- |
| PCM (raw 16-bit) | `pcm`      | Real-time pipelines, Pipecat `TTSAudioRawFrame` |
| WAV              | `wav`      | Uncompressed storage, offline processing        |
| MP3              | `mp3`      | Compressed storage, web delivery                |
| OGG Opus         | `ogg_opus` | Compressed web streaming                        |
| FLAC             | `flac`     | Lossless compressed storage                     |
| mu-law           | `mulaw`    | Telephony systems (G.711)                       |
| A-law            | `alaw`     | Telephony systems (G.711 European)              |

### Style Tags

| Tag                | Description                                            |
| ------------------ | ------------------------------------------------------ |
| `<Neutral>`        | Clean read-speech — default                            |
| `<Happy>`          | Joyful, upbeat tone                                    |
| `<Sad>`            | Somber, melancholic tone                               |
| `<Angry>`          | Forceful, intense tone                                 |
| `<Fearful>`        | Anxious, trembling tone                                |
| `<Surprised>`      | Exclamatory, astonished tone                           |
| `<Disgust>`        | Repulsed, disapproving tone                            |
| `<News>`           | Formal news-anchor style                               |
| `<Conversational>` | Casual, everyday speech — recommended for voice agents |
| `<Narrative>`      | Storytelling / audiobook delivery style                |
| `<Enthusiastic>`   | Energetic, passionate tone                             |

### Text Formatting

The service automatically formats text as `"<Style> text"` before sending to the API:

```python
tts = ShunyalabsTTSService(speaker="Rajesh", style="<Happy>")
# Input: "Welcome!"
# Sent:  "<Happy> Welcome!"
```

### Available Speakers

46 speakers across 23 languages (1 male + 1 female per language). Every speaker can synthesize in any language.

| Language  | Male               | Female   |
| --------- | ------------------ | -------- |
| English   | Varun              | Nisha    |
| Hindi     | Rajesh _(default)_ | Sunita   |
| Bengali   | Arjun              | Priyanka |
| Tamil     | Murugan            | Thangam  |
| Telugu    | Vishnu             | Lakshmi  |
| Kannada   | Kiran              | Shreya   |
| Malayalam | Krishnan           | Deepa    |
| Marathi   | Siddharth          | Ananya   |
| Gujarati  | Rakesh             | Pooja    |
| Punjabi   | Gurpreet           | Simran   |
| Urdu      | Salman             | Fatima   |
| Odia      | Bijay              | Sujata   |
| Assamese  | Bimal              | Anjana   |
| Maithili  | Suresh             | Meera    |
| Nepali    | Bikash             | Sapana   |
| Sanskrit  | Vedant             | Gayatri  |
| Kashmiri  | Farooq             | Habba    |
| Konkani   | Mohan              | Sarita   |
| Dogri     | Vishal             | Neelam   |
| Sindhi    | Amjad              | Kavita   |
| Manipuri  | Tomba              | Ibemhal  |
| Santali   | Chandu             | Roshni   |
| Bodo      | Daimalu            | Hasina   |

### Frame Output

| Frame              | Description                                      |
| ------------------ | ------------------------------------------------ |
| `TTSStartedFrame`  | Emitted when synthesis begins.                   |
| `TTSAudioRawFrame` | Emitted for each audio chunk (PCM, 16kHz, mono). |
| `TTSStoppedFrame`  | Emitted when synthesis completes.                |

### Example

```python
from pipecat_shunyalabs import ShunyalabsTTSService

tts = ShunyalabsTTSService(
    model="zero-indic",
    voice="Nisha",
    speaker="Nisha",
    style="<Enthusiastic>",
    language="en",
    speed=1.1,
    output_format="pcm",
)
```

---

## Full Pipeline Example

A complete voice agent using Shunyalabs STT and TTS with OpenAI LLM on the Daily WebRTC transport:

```python
import asyncio, os
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import (
    OpenAILLMContext, OpenAILLMContextAggregator,
)
from pipecat.services.openai import OpenAILLMService
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat_shunyalabs import ShunyalabsSTTService, ShunyalabsTTSService

async def run_voice_agent(room_url: str, token: str):
    transport = DailyTransport(
        room_url, token, "Shunyalabs Agent",
        DailyParams(audio_out_enabled=True, transcription_enabled=False),
    )

    stt = ShunyalabsSTTService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        language="auto",
        sample_rate=16000,
    )

    llm = OpenAILLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        model="gpt-4o",
    )

    messages = [{
        "role": "system",
        "content": (
            "You are a helpful voice assistant powered by Shunyalabs. "
            "Keep responses concise and natural for voice delivery."
        ),
    }]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)

    tts = ShunyalabsTTSService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        voice="Rajesh",
        language="hi",
        style="<Conversational>",
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        PipelineParams(allow_interruptions=True, enable_metrics=True),
    )

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    await PipelineRunner().run(task)

if __name__ == "__main__":
    asyncio.run(run_voice_agent(
        room_url=os.environ["DAILY_ROOM_URL"],
        token=os.environ["DAILY_TOKEN"],
    ))
```

---

## Multilingual Example

```python
# Hindi conversational bot
tts = ShunyalabsTTSService(
    voice="Rajesh",
    language="hi",
    style="<Conversational>",
)

# English news-style bot
tts = ShunyalabsTTSService(
    voice="Varun",
    language="en",
    style="<News>",
)
```

---

## Error Reference

All Shunyalabs SDK exceptions inherit from `ShunyalabsError`.

| Exception               | HTTP Code | Description                                           |
| ----------------------- | --------- | ----------------------------------------------------- |
| `AuthenticationError`   | 401       | Invalid or missing API key.                           |
| `PermissionDeniedError` | 403       | API key lacks permission for the resource.            |
| `NotFoundError`         | 404       | Requested resource not found.                         |
| `RateLimitError`        | 429       | Rate limit exceeded. Implement exponential backoff.   |
| `ServerError`           | 5xx       | Server-side error. Retried automatically.             |
| `TimeoutError`          | —         | Request exceeded timeout (default 60s).               |
| `ConnectionError`       | —         | Network connectivity issue.                           |
| `TranscriptionError`    | —         | ASR-specific failure (e.g. unsupported audio format). |
| `SynthesisError`        | —         | TTS-specific failure (e.g. invalid voice parameter).  |

```python
from shunyalabs.exceptions import AuthenticationError, RateLimitError, ShunyalabsError

try:
    result = await client.tts.synthesize(text, config=config)
except AuthenticationError:
    print("Invalid API key — check SHUNYALABS_API_KEY")
except RateLimitError as e:
    print(f"Rate limited — retry after {e.retry_after}s")
except ShunyalabsError as e:
    print(f"Unexpected error: {e}")
```

---

## Troubleshooting

| Symptom                           | Resolution                                                                                 |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `AuthenticationError` on startup  | Verify `SHUNYALABS_API_KEY` is set and valid.                                              |
| WebSocket connection refused      | Ensure outbound WSS (port 443) is open to `asr.shunyalabs.ai` and `tts.shunyalabs.ai`.     |
| No transcription output           | Check `sample_rate` matches your transport input. Verify audio source is active.           |
| TTS audio silent or missing       | Ensure `output_format=pcm` matches transport output. Verify `TTSStartedFrame` is received. |
| High latency on first TTS chunk   | Deploy closer to the Shunyalabs gateway region (`asia-south1`).                            |
| `RateLimitError`                  | Implement exponential backoff. Check `e.retry_after`.                                      |
| `ImportError: pipecat_shunyalabs` | Run `pip install pipecat-shunyalabs`. Confirm virtual environment is activated.            |

---

## License

[MIT](../../LICENSE)
