"""Example: Daily.co voice bot using Shunyalabs ASR via Pipecat.

Run with:
    python pipecat_bot.py

Requirements:
    pip install pipecat-ai[daily,silero,openai] pipecat-shunyalabs

Environment variables:
    DAILY_API_KEY       Daily.co API key
    DAILY_ROOM_URL      Daily.co room URL
    SHUNYALABS_API_KEY  Shunyalabs API key
    OPENAI_API_KEY      OpenAI API key (for LLM + TTS)
"""

from __future__ import annotations

import asyncio
import os

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.openai import OpenAILLMService, OpenAITTSService
from pipecat.transports.services.daily import DailyParams, DailyTransport

from pipecat_shunyalabs import ShunyalabsSTTService


async def main() -> None:
    transport = DailyTransport(
        room_url=os.environ["DAILY_ROOM_URL"],
        token=None,
        bot_name="Shunyalabs Bot",
        params=DailyParams(
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    stt = ShunyalabsSTTService(
        api_key=os.environ["SHUNYALABS_API_KEY"],
        language="auto",
    )

    llm = OpenAILLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        model="gpt-4o-mini",
    )

    tts = OpenAITTSService(
        api_key=os.environ["OPENAI_API_KEY"],
        voice="alloy",
    )

    context = OpenAILLMContext(
        messages=[
            {
                "role": "system",
                "content": "You are a helpful voice assistant. Be concise and friendly.",
            }
        ]
    )
    context_aggregator = llm.create_context_aggregator(context)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(pipeline)
    runner = PipelineRunner()

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        await transport.capture_participant_transcription(participant["id"])
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
