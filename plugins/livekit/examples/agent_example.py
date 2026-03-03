"""Example: LiveKit voice agent using Shunyalabs ASR.

Run with:
    python agent_example.py dev

Requirements:
    pip install livekit-agents livekit-plugins-shunyalabs livekit-plugins-silero livekit-plugins-openai

Environment variables:
    LIVEKIT_URL        LiveKit server URL
    LIVEKIT_API_KEY    LiveKit API key
    LIVEKIT_API_SECRET LiveKit API secret
    SHUNYALABS_API_KEY Shunyalabs API key
    OPENAI_API_KEY     OpenAI API key (for LLM + TTS)
"""

from __future__ import annotations

import logging

from livekit.agents import AgentSession, AutoSubscribe, JobContext, WorkerOptions, cli, llm
from livekit.plugins import openai, silero, shunyalabs

logging.basicConfig(level=logging.INFO)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    session = AgentSession(
        stt=shunyalabs.STT(language="auto"),   # auto-detects language
        llm=openai.LLM(model="gpt-4o-mini"),
        tts=openai.TTS(voice="alloy"),
        vad=silero.VAD.load(),
    )

    await session.start(ctx.room)
    await session.generate_reply(
        instructions="You are a helpful voice assistant. Be concise and friendly."
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
