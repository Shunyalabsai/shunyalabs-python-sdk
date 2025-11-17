"""
Simple test script for shunyalabs.rt SDK
Run this after installing the SDK in development mode.

This script tests the SDK with API Gateway format (wss://tl.shunyalabs.ai/),
matching the behavior of the working API Gateway WebSocket test script.
"""

import asyncio
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import List, Tuple

import numpy as np
from shunyalabs.rt import AsyncClient, ServerMessageType, TranscriptionConfig, AudioFormat, AudioEncoding
from shunyalabs.rt._exceptions import TransportError

# Fix encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')


async def test_basic_transcription():
    """Test basic transcription with an audio file using API Gateway format."""
    # Hardcoded API key
    api_key = "z8ylFvD7spI8X4Sx"

    print("Testing shunyalabs.rt SDK with API Gateway format...")
    print(f"API Key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else '*'}")
    
    # Generate a unique session ID (matching working script pattern)
    session_id = f"ws-apigw-{int(time.time())}"
    print(f"Session ID: {session_id}")
    
    # Check if audio file exists
    audio_file_path = "/Users/user/Downloads/test.wav"
    if not os.path.exists(audio_file_path):
        print(f"\n⚠ Warning: Audio file not found at {audio_file_path}")
        print("Please ensure female_sample.wav exists in the current directory.")
        return

    print(f"\n✓ Transcribing audio file: {audio_file_path}")
    
    # Configure transcription for API Gateway format
    # (wss://tl.shunyalabs.ai/ uses API Gateway protocol)
    config = TranscriptionConfig(
        language="auto",  # Will be converted to None for API Gateway
        enable_partials=True,
    )
    
    # Configure audio format (Float32, 16kHz - matching API Gateway requirements)
    # Read and convert WAV file to float32 PCM (matching test_apigw_ws_send_passthrough.py)
    def _read_wav_as_float32_mono(wav_path: Path) -> Tuple[np.ndarray, int]:
        """Read WAV file and convert to float32 mono PCM."""
        with wave.open(str(wav_path), "rb") as wf:
            num_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            total_frames = wf.getnframes()

            if num_channels not in (1, 2):
                raise ValueError(f"Unsupported channel count: {num_channels}")
            if sample_width not in (2, 4):
                raise ValueError("Only PCM16 or float32 WAV supported")

            pcm_bytes = wf.readframes(total_frames)

        if sample_width == 2:
            # PCM16 -> float32
            int16_data = np.frombuffer(pcm_bytes, dtype=np.int16)
            if num_channels == 2:
                int16_data = int16_data.reshape(-1, 2)
                int16_data = ((int16_data[:, 0].astype(np.float32) + int16_data[:, 1].astype(np.float32)) * 0.5).astype(np.float32)
            else:
                int16_data = int16_data.astype(np.float32)
            float32_data = int16_data / 32768.0
        else:
            # Already float32
            float32_data = np.frombuffer(pcm_bytes, dtype=np.float32)
            if num_channels == 2:
                float32_data = float32_data.reshape(-1, 2)
                float32_data = ((float32_data[:, 0] + float32_data[:, 1]) * 0.5).astype(np.float32)

        return float32_data.astype(np.float32, copy=False), int(sample_rate)
    
    # Read WAV file and get actual sample rate
    wav_path = Path(audio_file_path)
    if not wav_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_file_path}")
    
    print(f"Reading WAV file: {audio_file_path}")
    audio_data, actual_sample_rate = _read_wav_as_float32_mono(wav_path)
    print(f"WAV: {actual_sample_rate} Hz, {audio_data.shape[0]} frames, {audio_data.shape[0] / max(1, actual_sample_rate):.2f}s")
    
    # Convert to chunks matching the working test format
    chunk_ms = 300  # 300ms chunks like working test
    frames_per_chunk = int(max(1, actual_sample_rate) * chunk_ms / 1000)
    print(f"Chunk: {chunk_ms} ms -> {frames_per_chunk} frames")
    
    audio_chunks: List[bytes] = []
    for start in range(0, audio_data.shape[0], frames_per_chunk):
        end = start + frames_per_chunk
        chunk = audio_data[start:end]
        if chunk.size == 0:
            break
        audio_chunks.append(chunk.astype(np.float32, copy=False).tobytes())
    
    print(f"Prepared {len(audio_chunks)} chunks")
    
    audio_fmt = AudioFormat(
        encoding=AudioEncoding.PCM_F32LE,  # Float32 encoding
        sample_rate=actual_sample_rate,  # Use actual sample rate from WAV
        chunk_size=len(audio_chunks[0]) if audio_chunks else 4096,  # Use actual chunk size
    )
    
    # Create a client with explicit API Gateway URL
    # Using wss://tl.shunyalabs.ai/ for API Gateway format
    api_gateway_url = "wss://tl.shunyalabs.ai/"
    try:
        async with AsyncClient(api_key=api_key, url=api_gateway_url) as client:
            print("✓ Client created successfully")
            
            # Register event handlers
            @client.on(ServerMessageType.RECOGNITION_STARTED)
            def handle_recognition_started(msg):
                print("✓ Recognition started successfully (SERVER_READY received)")
                print(f"  Debug: Message = {msg}")
            
            @client.on(ServerMessageType.ADD_TRANSCRIPT)
            def handle_final_transcript(msg):
                print(f"  Debug: Received ADD_TRANSCRIPT message: {msg}")
                transcript = msg.get('metadata', {}).get('transcript', '')
                start_time = msg.get('metadata', {}).get('start_time', 0.0)
                end_time = msg.get('metadata', {}).get('end_time', 0.0)
                if transcript:
                    print(f"[FINAL] [{start_time:.1f}s-{end_time:.1f}s] {transcript}")
                else:
                    print(f"  Warning: ADD_TRANSCRIPT message has no transcript text")

            @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
            def handle_partial_transcript(msg):
                print(f"  Debug: Received ADD_PARTIAL_TRANSCRIPT message: {msg}")
                transcript = msg.get('metadata', {}).get('transcript', '')
                start_time = msg.get('metadata', {}).get('start_time', 0.0)
                end_time = msg.get('metadata', {}).get('end_time', 0.0)
                if transcript:
                    print(f"[PARTIAL] [{start_time:.1f}s-{end_time:.1f}s] {transcript}")
                else:
                    print(f"  Warning: ADD_PARTIAL_TRANSCRIPT message has no transcript text")
            
            @client.on(ServerMessageType.ERROR)
            def handle_error(msg):
                reason = msg.get('reason', 'Unknown error')
                print(f"✗ Server error: {reason}")
                print(f"  Debug: Error message = {msg}")
            
            # Event to signal when EndOfTranscript is received
            eot_received = asyncio.Event()
            
            @client.on(ServerMessageType.END_OF_TRANSCRIPT)
            def handle_eot(msg):
                print("ℹ Received EndOfTranscript message from server")
                print(f"  Debug: EOT message = {msg}")
                eot_received.set()  # Signal that EOT was received
            
            # Add a catch-all handler to see any other messages
            def handle_any_message(msg):
                msg_type = msg.get('message', 'UNKNOWN')
                if msg_type not in ['RecognitionStarted', 'AddTranscript', 'AddPartialTranscript', 'Error', 'EndOfTranscript']:
                    print(f"  Debug: Received unhandled message type '{msg_type}': {msg}")
            
            # Enable debug logging to see all received messages
            import logging
            logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            
            # Optionally reduce websockets library verbosity (set to INFO to see less detail)
            # logging.getLogger('websockets.client').setLevel(logging.INFO)
            # logging.getLogger('websockets.protocol').setLevel(logging.INFO)
            
            # Start the transcription session manually
            print("Starting transcription session...")
            try:
                await client.start_session(
                    transcription_config=config,
                    audio_format=audio_fmt,
                    session_id=session_id,
                    api_key=api_key,
                    model="pingala-v1-universal",
                    deliver_deltas_only=True,
                    use_api_gateway_format=True,  # Enable API Gateway protocol
                )
                print("✓ Session started, waiting for recognition...")
            except Exception as e:
                print(f"✗ Error starting session: {e}")
                raise
            
            # Wait for recognition to start with longer timeout
            try:
                await client._wait_recognition_started(timeout=10.0)
                print("✓ Recognition started, ready to send audio")
            except asyncio.TimeoutError:
                print("✗ Timeout waiting for recognition to start")
                raise
            except Exception as e:
                print(f"✗ Error waiting for recognition: {e}")
                raise
            
            # Check if client is still open before sending audio
            if client._closed_evt.is_set():
                raise TransportError("Client was closed before sending audio")
            
            # Manually send audio chunks continuously (matching test_apigw_ws_send_passthrough.py)
            print("Sending audio chunks continuously...")
            chunk_count = 0
            
            # Send pre-processed chunks (already converted to float32 PCM from WAV)
            for i, chunk_bytes in enumerate(audio_chunks, start=1):
                # Check if client is still open
                if client._closed_evt.is_set():
                    if chunk_count > 0:
                        print(f"ℹ Client closed after sending {chunk_count} chunks (server may have ended session)")
                    break
                
                if chunk_bytes:  # Only send non-empty chunks
                    try:
                        await client.send_audio(
                            chunk_bytes,
                            session_id=session_id,
                            sample_rate=audio_fmt.sample_rate
                        )
                        chunk_count += 1
                        if i % 5 == 0 or i == len(audio_chunks):
                            print(f"Sent {i}/{len(audio_chunks)} chunks ({len(chunk_bytes)} bytes)")
                    except TransportError as e:
                        if "Client is closed" in str(e):
                            print(f"ℹ Client closed during chunk {chunk_count + 1} (server may have ended session)")
                            break
                        else:
                            print(f"✗ Error sending chunk {chunk_count + 1}: {e}")
                            raise
                    except Exception as e:
                        print(f"✗ Error sending chunk {chunk_count + 1}: {e}")
                        raise
                
                # No sleep - send chunks continuously like test_apigw_ws_send_passthrough.py
                # The working test sends chunks without delays for continuous streaming
            
            print(f"\n✓ Finished sending {chunk_count} audio chunks")
            
            # Send END message immediately after all chunks are sent
            if not client._closed_evt.is_set():
                print("Sending END message to signal end of stream...")
                try:
                    end_msg = {
                        "type": "end",
                        "session_id": session_id,
                        "connection_id": session_id,
                    }
                    await client.send_message(end_msg)
                    print("✓ END message sent")
                    
                    # Wait for EndOfTranscript message from server before closing
                    print("Waiting for EndOfTranscript message from server...")
                    try:
                        await asyncio.wait_for(eot_received.wait(), timeout=30.0)
                        print("✓ EndOfTranscript received, closing connection")
                    except asyncio.TimeoutError:
                        print("⚠ Timeout waiting for EndOfTranscript (30s), closing anyway")
                    
                    await client.close()
                except Exception as e:
                    print(f"Warning: Error sending END message: {e}")
            
            print("\n✓ Transcription completed successfully!")
            
    except Exception as e:
        print(f"\n✗ Error during transcription: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("=" * 60)
    print("Shunyalabs RT SDK Local Test")
    print("=" * 60)
    asyncio.run(test_basic_transcription())
