"""Example: Streaming TTS synthesis using the Shunyalabs SDK.

Demonstrates iterator-based streaming and saving to file.

Usage:
    export SHUNYALABS_API_KEY="your-api-key"
    python examples/tts_streaming.py
"""

from shunyalabs import ShunyaClient
from shunyalabs.tts import TTSConfig


def main():
    client = ShunyaClient()

    # Stream audio chunks
    print("Streaming TTS...")
    total_bytes = 0
    for chunk in client.tts.stream("This is a long text being streamed."):
        total_bytes += len(chunk)
        print(f"  Received chunk: {len(chunk)} bytes")
    print(f"Total: {total_bytes} bytes")

    # Stream with detailed metadata
    print("\nStreaming with metadata...")
    for meta, audio in client.tts.stream(
        "Detailed streaming example.",
        detailed=True,
    ):
        print(f"  Chunk {meta.chunk_index}: {len(audio)} bytes (final={meta.is_final})")

    # Stream directly to file
    print("\nStreaming to file...")
    client.tts.stream_to_file(
        "Save this audio directly to a file.",
        "output_streamed.pcm",
    )
    print("Saved to output_streamed.pcm")

    client.close()


async def main_async():
    """Async streaming example."""
    from shunyalabs import AsyncShunyaClient

    async with AsyncShunyaClient() as client:
        async for chunk in await client.tts.stream("Hello from async streaming!"):
            print(f"  Async chunk: {len(chunk)} bytes")


if __name__ == "__main__":
    main()
