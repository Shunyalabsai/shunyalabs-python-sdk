# Shunyalabs TTS API Client

[![PyPI](https://img.shields.io/pypi/v/shunyalabs-tts)](https://pypi.org/project/shunyalabs-tts/)
![PythonSupport](https://img.shields.io/badge/Python-3.9%2B-green)

Async Python client for Shunyalabs TTS API.

## Features

- Async API client with comprehensive error handling
- Type hints throughout for better IDE support
- Environment variable support for credentials

## Installation

```bash
pip install shunyalabs-tts
```

## Usage

### Quick Start

```python
import asyncio

import wave 
from pathlib import Path

from shunyalabs.tts import AsyncClient, Voice, OutputFormat

async def save_audio(audio_data: bytes, filename: str) -> None:
    with wave.open(filename, "wb") as wav:
        wav.setnchannels(1)           # Mono
        wav.setsampwidth(2)           # 16-bit
        wav.setframerate(16000)       # 16kHz
        wav.writeframes(audio_data)

# Generate speech data from text and save to WAV file
async def main():
    async with AsyncClient() as client:
        async with await client.generate(
            text="Welcome to the future of audio generation from text!",
            voice=Voice.SARAH,
            output_format=OutputFormat.RAW_PCM_16000
        ) as response:
            audio = b''.join([chunk async for chunk in response.content.iter_chunked(1024)])
            await save_audio(audio, "output.wav")


# Run the async main function
if __name__ == "__main__":
    asyncio.run(main())

```

### Error Handling

```python
import asyncio
from shunyalabs.tts import (
    AsyncClient,
    AuthenticationError,
    TimeoutError
)

async def main():
    try:
        async with AsyncClient() as client:
            response = await client.generate(text="Hello, this is the Shunyalabs TTS API. We are excited to have you here!")

    except AuthenticationError:
        print("Invalid API key")
    except JobError as e:
        print(f"Job processing failed: {e}")
    except TimeoutError as e:
        print(f"Job timed out: {e}")
    except FileNotFoundError:
        print("Audio file not found")

asyncio.run(main())
```

### Connection Configuration

```python
import asyncio
from shunyalabs.tts import AsyncClient, ConnectionConfig

async def main():
    # Custom connection settings
    config = ConnectionConfig(
        url="https://preview.tts.Shunyalabs.com",
        api_key="your-api-key",
        connect_timeout=30.0,
        operation_timeout=600.0
    )

    async with AsyncClient(conn_config=config) as client:
        response = await client.generate(text="Hello World")
   

asyncio.run(main())
```

## Logging

The client supports logging with job id tracing for debugging. To increase logging verbosity, set `DEBUG` level in your example code:

```python
import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
```

## Environment Variables

The client supports the following environment variables:

- `SHUNYALABS_API_KEY`: Your Shunyalabs API key
- `SHUNYALABS_TTS_URL`: Custom API endpoint URL (optional)
