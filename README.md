# Shunyalabs Python SDK

[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](https://github.com/shunyalabs/shunyalabs-python-sdk/blob/master/LICENSE)

A collection of Python clients for Shunyalabs APIs packaged as separate installable packages.

Each client targets a specific Shunyalabs API (e.g. real-time, batch transcription), making it easier to install only what you need and keep dependencies minimal.

## Packages

This repository contains the following packages:

### (Beta) Real-Time Client (`shunyalabs-rt`)

A Python client for Shunyalabs Real-Time API.

```bash
pip install shunyalabs-rt
```

### (Beta) Batch Client (`shunyalabs-batch`)

An async Python client for Shunyalabs Batch API.

```bash
pip install shunyalabs-batch
```

### (Beta) Flow Client (`shunyalabs-flow`)

An async Python client for Shunyalabs Flow API.

```bash
pip install shunyalabs-flow
```

## Development

### Repository Structure

```
shunyalabs-python-sdk/
├── sdk/
│   ├── batch/
│   │   ├── pyproject.toml
│   │   └── README.md
│   │
│   ├── rt/
│   │   ├── pyproject.toml
│   │   └── README.md
│   │
│   ├── flow/
│   │   ├── pyproject.toml
│   │   └── README.md
│
├── tests/
│   ├── batch/
│   └── rt/
│   └── flow/
│
├── examples/
├── Makefile
├── pyproject.toml
└── LICENSE
```

### Setting Up Development Environment

```bash
git clone https://github.com/shunyalabs/shunyalabs-python-sdk.git
cd shunyalabs-python-sdk

python -m venv .venv
source .venv/bin/activate

# Install development dependencies for SDKs
make install-dev
```

On Windows:

```bash
.venv\Scripts\activate
```

### Install pre-commit hooks

```bash
pre-commit install
```

## Installation

Each package can be installed separately:

```bash
pip install shunyalabs-rt
pip install shunyalabs-batch
pip install shunyalabs-flow
```

## API Gateway Protocol Support

The Real-Time SDK (`shunyalabs-rt`) supports both the standard protocol and custom API Gateway protocol. To use the API Gateway format:

```python
from shunyalabs.rt import AsyncClient, ServerMessageType, TranscriptionConfig, AudioFormat, AudioEncoding

async with AsyncClient(
    api_key="your-api-key",
    url="wss://your-api-gateway-url/"
) as client:
    @client.on(ServerMessageType.ADD_TRANSCRIPT)
    def handle_final(msg):
        print(f"Final: {msg['metadata']['transcript']}")
    
    config = TranscriptionConfig(language="auto", enable_partials=True)
    audio_fmt = AudioFormat(encoding=AudioEncoding.PCM_F32LE, sample_rate=16000)
    
    with open("audio.wav", "rb") as audio_file:
        await client.transcribe(
            audio_file,
            transcription_config=config,
            audio_format=audio_fmt,
            session_id="your-session-id",
            api_key="your-api-key",
            model="pingala-v1-universal",
            deliver_deltas_only=True,
            use_api_gateway_format=True,  # Enable API Gateway protocol
        )
```

See [SDK_CUSTOMIZATION_GUIDE.md](SDK_CUSTOMIZATION_GUIDE.md) for details on API Gateway protocol support.

## Docs

The Shunyalabs API and product documentation can be found at https://docs.shunyalabs.com

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE)
