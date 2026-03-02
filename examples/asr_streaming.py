"""Example: Streaming ASR transcription using the Shunyalabs SDK.

Demonstrates real-time speech-to-text with event-driven callbacks.

Usage:
    export SHUNYALABS_API_KEY="your-api-key"
    python examples/asr_streaming.py
"""

import asyncio

from shunyalabs import AsyncShunyaClient
from shunyalabs.asr import StreamingConfig, StreamingMessageType


async def main():
    async with AsyncShunyaClient() as client:
        # Open a streaming connection
        config = StreamingConfig(language="en", sample_rate=16000)
        connection = await client.asr.stream(config=config)

        print(f"Session started: {connection.session_id}")

        # Register event handlers (Deepgram-style)
        @connection.on(StreamingMessageType.PARTIAL)
        def on_partial(msg):
            print(f"  Partial: {msg.text}", end="\r")

        @connection.on(StreamingMessageType.FINAL_SEGMENT)
        def on_final_segment(msg):
            print(f"\n  Segment: {msg.text}")

        @connection.on(StreamingMessageType.FINAL)
        def on_final(msg):
            print(f"\n  Final: {msg.text}")
            print(f"  Duration: {msg.audio_duration_sec}s")

        @connection.on(StreamingMessageType.DONE)
        def on_done(msg):
            print(f"\n  Done! Total segments: {msg.total_segments}")

        @connection.on(StreamingMessageType.ERROR)
        def on_error(msg):
            print(f"\n  Error: {msg.message}")

        # Stream audio from a file
        await connection.stream_file("audio.raw", chunk_size=4096)

        # Or send chunks manually:
        # with open("audio.raw", "rb") as f:
        #     while chunk := f.read(4096):
        #         await connection.send_audio(chunk)
        # await connection.end()

        await connection.close()
        print("Connection closed")


if __name__ == "__main__":
    asyncio.run(main())
