"""TTS WebSocket streaming synthesis."""
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.tts import TTSConfig

async def main():
    async with AsyncShunyaClient(api_key="YOUR_API_KEY") as client:
        chunks = []
        async for audio in await client.tts.stream("Hello, world!", config=TTSConfig(model="zero-indic", voice="Varun")):
            chunks.append(audio)
        print(f"{len(chunks)} chunks, {sum(len(c) for c in chunks)} bytes")

asyncio.run(main())
