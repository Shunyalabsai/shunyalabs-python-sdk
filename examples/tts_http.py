"""TTS HTTP batch synthesis."""
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.tts import TTSConfig

async def main():
    async with AsyncShunyaClient(api_key="YOUR_API_KEY") as client:
        result = await client.tts.synthesize("Hello, world!", config=TTSConfig(model="zero-indic", voice="Varun"))
        result.save("output.mp3")
        print(f"{len(result.audio_data)} bytes saved")

asyncio.run(main())
