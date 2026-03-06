"""Shunyalabs LiveKit Voice Bot — Echo bot for E2E testing.

Run: python voicebot.py dev
Then open https://agents-playground.livekit.io to connect.
"""

import logging
import os
import uuid

from livekit.agents import (
    Agent,
    AgentSession,
    AutoSubscribe,
    DEFAULT_API_CONNECT_OPTIONS,
    JobContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.plugins import silero, shunyalabs

logger = logging.getLogger("voicebot")
logger.setLevel(logging.INFO)


class EchoLLM(llm.LLM):
    def __init__(self):
        super().__init__()

    def chat(self, *, chat_ctx, **kwargs):
        return EchoLLMStream(chat_ctx=chat_ctx, llm_instance=self, **kwargs)


class EchoLLMStream(llm.LLMStream):
    def __init__(self, chat_ctx, llm_instance, **kwargs):
        super().__init__(
            llm=llm_instance,
            chat_ctx=chat_ctx,
            tools=kwargs.get("tools", []),
            conn_options=kwargs.get("conn_options", DEFAULT_API_CONNECT_OPTIONS),
        )

    async def _run(self) -> None:
        last_msg = ""
        for item in reversed(self._chat_ctx.items):
            if not isinstance(item, llm.ChatMessage):
                continue
            if item.role == "user":
                for c in item.content:
                    if isinstance(c, str):
                        last_msg = c
                        break
                    elif hasattr(c, "text") and c.text:
                        last_msg = c.text
                        break
                if last_msg:
                    break

        reply = f"You said: {last_msg}" if last_msg else "I didn't catch that."
        logger.info("EchoLLM: %s", reply)

        self._event_ch.send_nowait(
            llm.ChatChunk(
                id=str(uuid.uuid4()),
                delta=llm.ChoiceDelta(role="assistant", content=reply),
            )
        )


async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    agent = Agent(
        instructions="Echo bot.",
        stt=shunyalabs.STT(
            api_key=os.environ.get("SHUNYALABS_API_KEY", "test"),
            language="en",
        ),
        tts=shunyalabs.TTS(
            api_key=os.environ.get("SHUNYALABS_API_KEY", "test"),
            speaker="Rajesh",
            style="<Happy>",
            language="en",
        ),
        vad=silero.VAD.load(),
        llm=EchoLLM(),
    )

    session = AgentSession()
    await session.start(agent=agent, room=ctx.room)
    logger.info("Voicebot ready")


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
