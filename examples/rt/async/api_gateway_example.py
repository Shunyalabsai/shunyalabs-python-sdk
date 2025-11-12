#!/usr/bin/env python3
"""
Example: Using shunyalabs.rt SDK with API Gateway Protocol
----------------------------------------------------------
This example shows how to use the SDK with your custom API Gateway WebSocket protocol.
Set use_api_gateway_format=True to enable API Gateway protocol support.
"""

import asyncio
from shunyalabs.rt import AsyncClient, ServerMessageType, TranscriptionConfig, AudioFormat, AudioEncoding


async def main():
    # Create a client with API Gateway URL
    async with AsyncClient(
        api_key="your-api-key",  # Will be sent in query string and init config
        url="wss://tl.shunyalabs.ai/"  # Your API Gateway WebSocket URL
    ) as client:
        
        # Register event handlers for transcripts
        @client.on(ServerMessageType.ADD_TRANSCRIPT)
        def handle_final_transcript(msg):
            transcript = msg['metadata']['transcript']
            start_time = msg['metadata']['start_time']
            end_time = msg['metadata']['end_time']
            print(f"[FINAL] [{start_time:.1f}s-{end_time:.1f}s] {transcript}")
        
        @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
        def handle_partial_transcript(msg):
            transcript = msg['metadata']['transcript']
            start_time = msg['metadata']['start_time']
            end_time = msg['metadata']['end_time']
            print(f"[PARTIAL] [{start_time:.1f}s-{end_time:.1f}s] {transcript}")
        
        # Configure transcription
        config = TranscriptionConfig(
            language="auto",  # Will be converted to None for API Gateway
            enable_partials=True,
        )
        
        # Configure audio format (Float32, 16kHz - matching your script)
        audio_fmt = AudioFormat(
            encoding=AudioEncoding.PCM_F32LE,  # Float32 encoding
            sample_rate=16000,
            chunk_size=4096,
        )
        
        # Transcribe audio file with API Gateway format
        with open("./examples/example.wav", "rb") as audio_file:
            await client.transcribe(
                audio_file,
                transcription_config=config,
                audio_format=audio_fmt,
                session_id="test-session-123",  # Your session ID
                api_key="your-api-key",  # API key for init config
                model="pingala-v1-universal",  # Your model name
                deliver_deltas_only=True,  # Delta mode
                use_api_gateway_format=True,  # Enable API Gateway protocol
            )


if __name__ == "__main__":
    asyncio.run(main())

