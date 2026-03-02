"""Example: Batch ASR transcription using the Shunyalabs SDK.

Demonstrates both sync and async usage of the batch ASR endpoint.

Usage:
    export SHUNYALABS_API_KEY="your-api-key"
    python examples/asr_batch.py
"""

from shunyalabs import ShunyaClient
from shunyalabs.asr import TranscriptionConfig


def main():
    client = ShunyaClient()

    # Simple transcription from file
    result = client.asr.transcribe("audio.wav")
    print(f"Transcription: {result.text}")
    print(f"Detected language: {result.detected_language}")
    print(f"Audio duration: {result.audio_duration}s")
    print(f"Inference time: {result.inference_time_ms}ms")

    # With segments
    for seg in result.segments:
        print(f"  [{seg.start:.1f}-{seg.end:.1f}] {seg.text}")

    # Transcription with NLP features
    config = TranscriptionConfig(
        language_code="en",
        enable_summarization=True,
        enable_sentiment_analysis=True,
    )
    result = client.asr.transcribe("meeting.wav", config=config)
    if result.nlp_analysis:
        print(f"\nSummary: {result.nlp_analysis.summary}")
        print(f"Sentiment: {result.nlp_analysis.sentiment}")

    # Transcription from URL
    result = client.asr.transcribe(url="https://example.com/audio.mp3")
    print(f"\nURL transcription: {result.text}")

    client.close()


async def main_async():
    """Same example using the async client."""
    from shunyalabs import AsyncShunyaClient

    async with AsyncShunyaClient() as client:
        result = await client.asr.transcribe("audio.wav")
        print(f"Async transcription: {result.text}")


if __name__ == "__main__":
    main()
