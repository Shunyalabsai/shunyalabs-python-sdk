"""Example: Conversational AI using the Shunyalabs Flow module.

Demonstrates starting a real-time conversation with audio I/O.

Usage:
    export SHUNYALABS_API_KEY="your-api-key"
    python examples/flow_conversation.py
"""

import asyncio

from shunyalabs import AsyncShunyaClient
from shunyalabs.flow import ConversationConfig, AudioFormat, AudioEncoding


async def main():
    async with AsyncShunyaClient() as client:
        # Configure conversation
        config = ConversationConfig(
            input_audio_format=AudioFormat(
                encoding=AudioEncoding.PCM,
                sample_rate=16000,
                channels=1,
            ),
            output_audio_format=AudioFormat(
                encoding=AudioEncoding.PCM,
                sample_rate=16000,
                channels=1,
            ),
            system_prompt="You are a helpful voice assistant.",
        )

        # Register event handlers
        @client.flow.on("transcript")
        def on_transcript(data):
            print(f"User said: {data.get('text', '')}")

        @client.flow.on("response")
        def on_response(data):
            print(f"Assistant: {data.get('text', '')}")

        @client.flow.on("audio")
        def on_audio(data):
            # data contains audio bytes from the assistant
            print(f"Received {len(data)} bytes of audio")

        # Start conversation (pass an audio source)
        # In practice, this would be a microphone stream
        async def audio_source():
            with open("input_audio.raw", "rb") as f:
                while chunk := f.read(4096):
                    yield chunk
                    await asyncio.sleep(0.1)  # simulate real-time

        await client.flow.start_conversation(
            source=audio_source(),
            config=config,
        )


if __name__ == "__main__":
    asyncio.run(main())
