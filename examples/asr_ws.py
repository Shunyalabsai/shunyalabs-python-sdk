"""ASR WebSocket streaming transcription."""
import asyncio, subprocess
from shunyalabs import AsyncShunyaClient
from shunyalabs.asr import StreamingConfig, StreamingMessageType

async def main():
    async with AsyncShunyaClient(api_key="YOUR_API_KEY") as client:
        conn = await client.asr.stream(config=StreamingConfig(language="en", sample_rate=16000))

        @conn.on(StreamingMessageType.FINAL_SEGMENT)
        def on_seg(msg): print(f"[seg] {msg.text}")

        @conn.on(StreamingMessageType.FINAL)
        def on_final(msg): print(f"[final] {msg.text}")

        # Convert to PCM and stream
        pcm = subprocess.run(
            ["ffmpeg", "-i", "audio.wav", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
            capture_output=True,
        ).stdout
        for i in range(0, len(pcm), 4096):
            await conn.send_audio(pcm[i:i+4096])

        await conn.end()
        await conn.close()

asyncio.run(main())
