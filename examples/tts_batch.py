"""Example: Batch TTS synthesis using the Shunyalabs SDK.

Demonstrates both sync and async usage of the batch TTS endpoint.

Usage:
    export SHUNYALABS_API_KEY="your-api-key"
    python examples/tts_batch.py
"""

from shunyalabs import ShunyaClient
from shunyalabs.tts import TTSConfig, OutputFormat


def main():
    # Create a sync client (reads API key from SHUNYALABS_API_KEY env var)
    client = ShunyaClient()

    # Simple synthesis with defaults
    result = client.tts.synthesize("Hello world! Welcome to Shunyalabs.")
    result.save("output_hello.pcm")
    print(f"Saved {len(result.audio_data)} bytes to output_hello.pcm")
    print(f"  Duration: {result.duration_seconds:.2f}s")
    print(f"  Sample rate: {result.sample_rate} Hz")

    # Synthesis with custom config
    config = TTSConfig(
        language="en",
        response_format=OutputFormat.WAV,
        speed=1.2,
        word_timestamps=True,
    )
    result = client.tts.synthesize(
        "This is an example of configurable text-to-speech.",
        config=config,
    )
    result.save("output_configured.wav")
    print(f"\nSaved configured output ({result.format})")
    if result.word_timestamps:
        for wt in result.word_timestamps:
            print(f"  {wt.word}: {wt.start:.3f}s - {wt.end:.3f}s")

    client.close()


async def main_async():
    """Same example using the async client."""
    from shunyalabs import AsyncShunyaClient

    async with AsyncShunyaClient() as client:
        result = await client.tts.synthesize("Hello from async!")
        result.save("output_async.pcm")
        print(f"\nAsync: Saved {len(result.audio_data)} bytes")


if __name__ == "__main__":
    main()

    # Uncomment to run async version:
    # import asyncio
    # asyncio.run(main_async())
