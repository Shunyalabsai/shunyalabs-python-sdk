#!/usr/bin/env python3
"""
Direct WebSocket Server Test (bypassing API Gateway)
-------------------------------------------------------
• Connects directly to WebSocket server (ws://host:port)
• Sends JSON init message with type "init"
• Streams audio chunks as base64 Float32 via type "frame"
• Finishes with type "end"

This script connects directly to the WebSocket server, bypassing API Gateway and Lambda.
"""

import asyncio
import argparse
import base64
import json
import logging
import pathlib
import time
import wave
from typing import List, Tuple

import numpy as np
import websockets
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse


log = logging.getLogger("apigw_ws_send_test")


def configure_logging(log_file: str, level=logging.INFO) -> None:
    for handler in list(log.handlers):
        log.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    log_path = pathlib.Path(log_file).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(str(log_path), mode="a", encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    log.setLevel(level)
    log.addHandler(console_handler)
    log.addHandler(file_handler)
    log.propagate = False


def _read_wav_as_float32_mono(wav_path: pathlib.Path) -> Tuple[np.ndarray, int]:
    with wave.open(str(wav_path), "rb") as wf:
        num_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        sample_rate = wf.getframerate()
        total_frames = wf.getnframes()

        if num_channels not in (1, 2):
            raise ValueError(f"Unsupported channel count: {num_channels}")
        if sample_width not in (2, 4):
            raise ValueError("Only PCM16 or float32 WAV supported by this reader")

        pcm_bytes = wf.readframes(total_frames)

    if sample_width == 2:
        int16_data = np.frombuffer(pcm_bytes, dtype=np.int16)
        if num_channels == 2:
            int16_data = int16_data.reshape(-1, 2)
            int16_data = ((int16_data[:, 0].astype(np.float32) + int16_data[:, 1].astype(np.float32)) * 0.5).astype(np.float32)
        else:
            int16_data = int16_data.astype(np.float32)
        float32_data = int16_data / 32768.0
    else:
        float32_data = np.frombuffer(pcm_bytes, dtype=np.float32)
        if num_channels == 2:
            float32_data = float32_data.reshape(-1, 2)
            float32_data = ((float32_data[:, 0] + float32_data[:, 1]) * 0.5).astype(np.float32)

    return float32_data.astype(np.float32, copy=False), int(sample_rate)


def load_chunks(wav_path: pathlib.Path, chunk_ms: int = 300) -> Tuple[List[bytes], int]:
    chunks: List[bytes] = []
    data, sr = _read_wav_as_float32_mono(wav_path)
    frames_per_chunk = int(max(1, sr) * chunk_ms / 1000)

    log.info("WAV: %d Hz, %d frames, %.2fs", sr, data.shape[0], data.shape[0] / max(1, sr))
    log.info("Chunk: %d ms -> %d frames", chunk_ms, frames_per_chunk)

    for start in range(0, data.shape[0], frames_per_chunk):
        end = start + frames_per_chunk
        chunk = data[start:end]
        if chunk.size == 0:
            break
        chunks.append(chunk.astype(np.float32, copy=False).tobytes())
    log.info("Prepared %d chunks", len(chunks))
    return chunks, sr


async def receiver(ws, ready_event: asyncio.Event):
    log.info("Listening for responses...")
    try:
        async for msg in ws:
            if isinstance(msg, bytes):
                continue
            try:
                data = json.loads(msg)
            except Exception:
                log.debug("RX: %s", msg)
                continue

            if data.get("message") == "SERVER_READY":
                log.info("Server ready")
                ready_event.set()
                continue
            if data.get("message") == "DISCONNECT":
                log.info("Server requested disconnect")
                continue
            if data.get("type") == "error":
                log.error("Server error: %s", data.get("message"))
                continue
            if "language" in data and "language_prob" in data:
                log.info("Detected language: %s (p=%.2f)", data.get("language"), float(data.get("language_prob") or 0))
                continue

            segments = data.get("segments")
            if isinstance(segments, list):
                for seg in segments:
                    text = (seg.get("text") or "").strip()
                    if not text:
                        continue
                    completed = bool(seg.get("completed", False))
                    tag = "FINAL" if completed else "partial"
                    try:
                        s = float(seg.get("start") or 0.0)
                        e = seg.get("end")
                        if e is not None:
                            e = float(e)
                            log.info("[%s] [%.1fs-%.1fs] %s", tag, s, e, text)
                        else:
                            log.info("[%s] [%.1fs] %s", tag, s, text)
                    except Exception:
                        log.info("[%s] %s", tag, text)
            else:
                log.debug("RX: %s", data)
    except Exception as e:
        log.error("Receiver error: %s", e)


async def run(url: str, wav_file: str, session_id: str, lang: str, model: str, chunk_ms: int, deltas: bool, api_key: str | None, use_vad: bool = True):
    wav_path = pathlib.Path(wav_file)
    if not wav_path.exists():
        raise FileNotFoundError(f"WAV not found: {wav_file}")

    chunks, input_rate = load_chunks(wav_path, chunk_ms)
    if not chunks:
        raise ValueError("No audio loaded from WAV")

    init_msg = {
        "type": "init",
        "session_id": session_id,
        "connection_id": session_id,
        "config": {
            "uid": session_id,
            "language": (None if lang == "auto" else lang),
            "task": "transcribe",
            "model": model,
            "client_sample_rate": int(input_rate),
            "deliver_deltas_only": bool(deltas),
            "inactivity_timeout": 60.0,  # 60 seconds for direct connections
            "use_vad": use_vad
        },
    }
    if api_key:
        # Include api_key in INIT config so backend can validate/log billing
        init_msg["config"]["api_key"] = api_key
        init_msg["api_key"] = api_key  # Also at top level for compatibility

    # For broad compatibility across websockets versions, pass API key via query string
    final_url = url
    if api_key:
        parts = urlparse(url)
        q = dict(parse_qsl(parts.query))
        q["api_key"] = api_key
        new_query = urlencode(q)
        parts = parts._replace(query=new_query)
        final_url = urlunparse(parts)

    log.info("Connecting: %s", final_url)
    async with websockets.connect(final_url, ping_interval=30, ping_timeout=10) as ws:
        log.info("Connected")
        connection_start_time = time.time()

        # Create event to signal when server is ready
        ready_event = asyncio.Event()

        # Start receiver
        rx_task = asyncio.create_task(receiver(ws, ready_event))

        # Send INIT
        await ws.send(json.dumps(init_msg))
        log.info("Sent INIT for session_id=%s (sr=%d)", session_id, int(input_rate))

        # Wait for SERVER_READY confirmation
        log.info("Waiting for SERVER_READY confirmation...")
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=5.0)
            log.info("SERVER_READY confirmed, starting audio streaming")
        except asyncio.TimeoutError:
            log.error("Timeout waiting for SERVER_READY, proceeding anyway...")

        # Send frames
        for i, f32_bytes in enumerate(chunks, start=1):
            b64 = base64.b64encode(f32_bytes).decode("ascii")
            frame_msg = {
                "type": "frame",
                "session_id": session_id,
                "connection_id": session_id,
                "frame_seq": i,
                "audio": {
                    "inline_b64": b64,
                    "dtype": "float32",
                    "channels": 1,
                    "sr": int(input_rate),
                },
            }
            await ws.send(json.dumps(frame_msg))
            if i % 5 == 0 or i == len(chunks):
                log.info("Sent %d/%d frames", i, len(chunks))
            # No sleep - sending chunks without time gaps for continuous streaming

        # Wait until 1 minute has elapsed since connection start, then send END message
        elapsed_time = time.time() - connection_start_time
        remaining_time = 60.0 - elapsed_time
        if remaining_time > 0:
            log.info("Waiting %.2f seconds before sending END message (1 minute total)...", remaining_time)
            await asyncio.sleep(remaining_time)
        
        # Send END message to signal end of stream (after 1 minute delay)
        try:
            end_msg = {
                "type": "end",
                "session_id": session_id,
                "connection_id": session_id,
            }
            await ws.send(json.dumps(end_msg))
            log.info("Sent END message (after 1 minute delay)")
        except Exception as e:
            log.debug("END message send failed: %s", e)
        
        # Send disconnect message (optional, END already triggers cleanup)
        try:
            disconnect_msg = {
                "type": "disconnect",
                "session_id": session_id,
                "connection_id": session_id,
            }
            await ws.send(json.dumps(disconnect_msg))
            log.info("Sent DISCONNECT message")
        except Exception as e:
            log.debug("DISCONNECT message send failed: %s", e)

        await asyncio.sleep(2.0)  # Brief wait before closing
        rx_task.cancel()


def main():
    ap = argparse.ArgumentParser(description="Direct WebSocket server test (bypassing API Gateway)")
    ap.add_argument("--url", default="ws://127.0.0.1:8000", help="WebSocket server URL (default: ws://127.0.0.1:8000)")
    ap.add_argument("--wav", required=True, help="Path to mono WAV file (PCM16 or float32)")
    ap.add_argument("--session", default=f"ws-direct-{int(time.time())}", help="Session ID (used for grouping frames)")
    ap.add_argument("--lang", choices=["auto", "en", "hi"], default="auto", help="Language or auto")
    ap.add_argument("--model", default="pingala-v1-universal", help="Model id/name")
    ap.add_argument("--chunk", type=int, default=300, help="Chunk size in ms")
    ap.add_argument("--no-deltas", action="store_true", help="Disable delta mode")
    ap.add_argument("--no-vad", action="store_true", help="Disable Voice Activity Detection (VAD)")
    ap.add_argument("--log-file", default="direct_ws_test.log", help="Log file path")
    ap.add_argument("--api-key", default="z8ylFvD7spI8X4Sx", help="API key for authentication; sent in INIT config")
    args = ap.parse_args()

    configure_logging(args.log_file, level=logging.INFO)
    deltas = not args.no_deltas
    use_vad = not args.no_vad

    log.info("Starting Direct WebSocket Server test")
    log.info("URL=%s", args.url)
    log.info("WAV=%s", args.wav)
    log.info("Session=%s", args.session)
    log.info("Lang=%s Model=%s Chunk=%dms Deltas=%s VAD=%s", args.lang, args.model, args.chunk, deltas, use_vad)

    asyncio.run(run(args.url, args.wav, args.session, args.lang, args.model, args.chunk, deltas, args.api_key, use_vad))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Interrupted by user")
    except Exception as e:
        log.error("Failed: %s", e)


