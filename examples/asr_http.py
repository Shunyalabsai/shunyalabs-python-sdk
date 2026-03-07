"""ASR HTTP batch transcription."""
import asyncio
from shunyalabs import AsyncShunyaClient
from shunyalabs.asr import TranscriptionConfig

async def main():
    async with AsyncShunyaClient(api_key="YOUR_API_KEY") as client:
        result = await client.asr.transcribe("audio.wav", config=TranscriptionConfig(model="zero-indic"))
        print(result.text)

asyncio.run(main())
