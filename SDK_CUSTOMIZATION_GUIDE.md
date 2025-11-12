# SDK Customization Guide for API Gateway Protocol

This guide explains what needs to be modified in the `shunyalabs.rt` SDK to work with your custom API Gateway WebSocket protocol.

## Protocol Differences

### Your Server Protocol vs Standard SDK Protocol

| Aspect | Standard SDK | Your API Gateway Protocol |
|--------|-------------|---------------------------|
| **Init Message** | `{"message": "StartRecognition", ...}` | `{"action": "send", "type": "init", "session_id": "...", "config": {...}}` |
| **Audio Frames** | Binary WebSocket frames | JSON with base64: `{"action": "send", "type": "frame", "audio_inline_b64": "...", ...}` |
| **End Message** | `{"message": "EndOfStream", "last_seq_no": ...}` | `{"action": "end", "type": "end", "session_id": "..."}` |
| **Server Ready** | `{"message": "RecognitionStarted", "id": "..."}` | `{"message": "SERVER_READY"}` |
| **Transcripts** | `{"message": "AddTranscript", "metadata": {...}}` | `{"segments": [{"text": "...", "completed": true, ...}]}` |

## Required SDK Modifications

### 1. Modify `_utils/message.py` - Build Init Message

**File**: `sdk/rt/shunyalabs/rt/_utils/message.py`

**Current Code**:
```python
def build_start_recognition_message(...):
    start_recognition_message = {
        "message": ClientMessageType.START_RECOGNITION,
        "audio_format": audio_format.to_dict(),
        "transcription_config": transcription_config.to_dict(),
    }
    return start_recognition_message
```

**Modified Code** (for API Gateway):
```python
def build_start_recognition_message(
    transcription_config: TranscriptionConfig,
    audio_format: AudioFormat,
    translation_config: Optional[TranslationConfig] = None,
    audio_events_config: Optional[AudioEventsConfig] = None,
    session_id: Optional[str] = None,
    api_key: Optional[str] = None,
    model: str = "pingala-v1-universal",
    deliver_deltas_only: bool = True,
) -> dict[str, Any]:
    """Build the start recognition message for API Gateway protocol."""
    
    # Map language - handle "auto" or None
    language = transcription_config.language
    if language == "auto" or not language:
        language = None
    
    # Build config dict matching your server's expected format
    config = {
        "uid": session_id or "default-session",
        "language": language,
        "task": "transcribe",
        "model": model,
        "client_sample_rate": audio_format.sample_rate,
        "deliver_deltas_only": deliver_deltas_only,
    }
    
    if api_key:
        config["api_key"] = api_key
    
    # API Gateway format
    init_msg = {
        "action": "send",
        "type": "init",
        "session_id": session_id or "default-session",
        "config": config,
    }
    
    return init_msg
```

### 2. Modify `_base_client.py` - Send Audio as JSON Frames

**File**: `sdk/rt/shunyalabs/rt/_base_client.py`

**Current Code** (line ~109-128):
```python
async def send_audio(self, payload: bytes) -> None:
    if self._closed_evt.is_set() or self._eos_sent:
        raise TransportError("Client is closed")
    
    if not isinstance(payload, bytes):
        raise ValueError("Payload must be bytes")
    
    try:
        await self._transport.send_message(payload)  # Sends as binary
        self._seq_no += 1
    except Exception:
        self._closed_evt.set()
        raise
```

**Modified Code** (for API Gateway):
```python
async def send_audio(self, payload: bytes, session_id: Optional[str] = None, sample_rate: int = 16000) -> None:
    """Send audio as base64-encoded JSON frame for API Gateway protocol."""
    if self._closed_evt.is_set() or self._eos_sent:
        raise TransportError("Client is closed")
    
    if not isinstance(payload, bytes):
        raise ValueError("Payload must be bytes")
    
    try:
        import base64
        b64_audio = base64.b64encode(payload).decode("ascii")
        
        frame_msg = {
            "action": "send",
            "type": "frame",
            "session_id": session_id or getattr(self._session, 'request_id', 'default'),
            "connection_id": None,  # Filled by Lambda
            "frame_seq": self._seq_no + 1,
            "audio_inline_b64": b64_audio,
            "dtype": "float32",  # Or detect from audio_format
            "channels": 1,
            "sr": sample_rate,
        }
        
        await self._transport.send_message(json.dumps(frame_msg))  # Send as JSON string
        self._seq_no += 1
    except Exception:
        self._closed_evt.set()
        raise
```

### 3. Modify `_async_client.py` - Update EndOfStream Message

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

**Current Code** (line ~296-303):
```python
async def _send_eos(self, seq_no: int) -> None:
    """Send EndOfStream message to server."""
    if not self._eos_sent and not self._session_done_evt.is_set():
        try:
            await self.send_message({"message": ClientMessageType.END_OF_STREAM, "last_seq_no": seq_no})
            self._eos_sent = True
        except Exception as e:
            self._logger.error("Failed to send EndOfStream message: %s", e)
```

**Modified Code** (for API Gateway):
```python
async def _send_eos(self, seq_no: int, session_id: Optional[str] = None) -> None:
    """Send EndOfStream message to server (API Gateway format)."""
    if not self._eos_sent and not self._session_done_evt.is_set():
        try:
            # First send END_OF_AUDIO sentinel frame
            import base64
            eos_b64 = base64.b64encode(b"END_OF_AUDIO").decode("ascii")
            eos_frame = {
                "action": "send",
                "type": "frame",
                "session_id": session_id or getattr(self._session, 'request_id', 'default'),
                "connection_id": None,
                "frame_seq": seq_no + 1,
                "audio_inline_b64": eos_b64,
                "dtype": "float32",
                "channels": 1,
                "sr": getattr(self, '_sample_rate', 16000),
            }
            await self.send_message(eos_frame)
            
            # Then send end route
            end_msg = {
                "action": "end",
                "type": "end",
                "session_id": session_id or getattr(self._session, 'request_id', 'default'),
            }
            await self.send_message(end_msg)
            self._eos_sent = True
        except Exception as e:
            self._logger.error("Failed to send EndOfStream message: %s", e)
```

### 4. Modify `_async_client.py` - Handle Server Response Format

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

**Current Code** (line ~309-313):
```python
def _on_recognition_started(self, msg: dict[str, Any]) -> None:
    """Handle RecognitionStarted message from server."""
    self._session.session_id = msg.get("id")
    self._recognition_started_evt.set()
    self._logger.debug("Recognition started (session_id=%s)", self._session.session_id)
```

**Modified Code** (for API Gateway):
```python
def _on_recognition_started(self, msg: dict[str, Any]) -> None:
    """Handle SERVER_READY message from server (API Gateway format)."""
    # Your server sends {"message": "SERVER_READY"} instead of RecognitionStarted
    if msg.get("message") == "SERVER_READY":
        self._session.session_id = msg.get("session_id") or getattr(self._session, 'request_id', 'default')
        self._recognition_started_evt.set()
        self._logger.debug("Server ready (session_id=%s)", self._session.session_id)
    elif msg.get("message") == "RecognitionStarted":
        # Fallback for standard format
        self._session.session_id = msg.get("id")
        self._recognition_started_evt.set()
        self._logger.debug("Recognition started (session_id=%s)", self._session.session_id)
```

### 5. Modify `_base_client.py` - Handle Transcript Response Format

**File**: `sdk/rt/shunyalabs/rt/_base_client.py` (in `_recv_loop` method)

**Current Code** (line ~152-176):
```python
async def _recv_loop(self) -> None:
    try:
        while True:
            msg = await self._transport.receive_message()
            
            if isinstance(msg, dict) and "message" in msg:
                self.emit(msg["message"], msg)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        self._logger.error("Receive loop error: %s", exc)
        # ...
```

**Modified Code** (for API Gateway):
```python
async def _recv_loop(self) -> None:
    """Receive loop that converts API Gateway format to SDK format."""
    try:
        while True:
            msg = await self._transport.receive_message()
            
            # Convert API Gateway format to SDK format
            if isinstance(msg, dict):
                # Handle SERVER_READY
                if msg.get("message") == "SERVER_READY":
                    # Convert to RecognitionStarted format
                    converted_msg = {
                        "message": "RecognitionStarted",
                        "id": msg.get("session_id") or getattr(self._session, 'request_id', 'default')
                    }
                    self.emit("RecognitionStarted", converted_msg)
                    continue
                
                # Handle segments format (your server's transcript format)
                if "segments" in msg and isinstance(msg["segments"], list):
                    for seg in msg["segments"]:
                        text = (seg.get("text") or "").strip()
                        if not text:
                            continue
                        
                        completed = bool(seg.get("completed", False))
                        start_time = float(seg.get("start") or 0.0)
                        end_time = seg.get("end")
                        if end_time is not None:
                            end_time = float(end_time)
                        else:
                            end_time = start_time + 1.0  # Default duration
                        
                        # Convert to SDK format
                        if completed:
                            # Final transcript
                            converted_msg = {
                                "message": "AddTranscript",
                                "format": "2.1",
                                "metadata": {
                                    "transcript": text,
                                    "start_time": start_time,
                                    "end_time": end_time,
                                },
                                "results": [],
                            }
                            self.emit("AddTranscript", converted_msg)
                        else:
                            # Partial transcript
                            converted_msg = {
                                "message": "AddPartialTranscript",
                                "format": "2.1",
                                "metadata": {
                                    "transcript": text,
                                    "start_time": start_time,
                                    "end_time": end_time,
                                },
                                "results": [],
                            }
                            self.emit("AddPartialTranscript", converted_msg)
                    continue
                
                # Handle standard SDK format or other messages
                if "message" in msg:
                    self.emit(msg["message"], msg)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        self._logger.error("Receive loop error: %s", exc)
        # ...
```

### 6. Modify `_base_client.py` - Store Session ID and Sample Rate

**File**: `sdk/rt/shunyalabs/rt/_base_client.py`

Add instance variables to store session_id and sample_rate:

```python
def __init__(self, transport: Transport) -> None:
    super().__init__()
    self._transport = transport
    self._recv_task: Optional[asyncio.Task[None]] = None
    self._closed_evt = asyncio.Event()
    self._eos_sent = False
    self._seq_no = 0
    self._session_id: Optional[str] = None  # Add this
    self._sample_rate: int = 16000  # Add this
    
    self._logger = get_logger("shunyalabs.rt.base_client")
```

### 7. Modify `_async_client.py` - Pass Session ID and Config

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

Update `_start_recognition_session` to pass session_id and store sample_rate:

```python
async def _start_recognition_session(
    self,
    *,
    transcription_config: Optional[TranscriptionConfig] = None,
    audio_format: Optional[AudioFormat] = None,
    translation_config: Optional[TranslationConfig] = None,
    audio_events_config: Optional[AudioEventsConfig] = None,
    ws_headers: Optional[dict] = None,
    session_id: Optional[str] = None,  # Add this
    api_key: Optional[str] = None,  # Add this
    model: str = "pingala-v1-universal",  # Add this
    deliver_deltas_only: bool = True,  # Add this
) -> tuple[TranscriptionConfig, AudioFormat]:
    transcription_config = transcription_config or TranscriptionConfig()
    audio_format = audio_format or AudioFormat()
    
    # Store session_id and sample_rate
    self._session_id = session_id or self._session.request_id
    self._sample_rate = audio_format.sample_rate
    
    start_recognition_message = build_start_recognition_message(
        transcription_config=transcription_config,
        audio_format=audio_format,
        translation_config=translation_config,
        audio_events_config=audio_events_config,
        session_id=self._session_id,  # Pass session_id
        api_key=api_key,  # Pass api_key
        model=model,  # Pass model
        deliver_deltas_only=deliver_deltas_only,  # Pass deltas flag
    )
    
    await self._ws_connect(ws_headers)
    await self.send_message(start_recognition_message)
    await self._wait_recognition_started()
    
    return transcription_config, audio_format
```

### 8. Modify `_async_client.py` - Update Audio Producer

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

Update `_audio_producer` to pass session_id and sample_rate:

```python
async def _audio_producer(self, source: BinaryIO, chunk_size: int) -> None:
    src = FileSource(source, chunk_size=chunk_size)
    
    try:
        async for frame in src:
            if self._session_done_evt.is_set():
                break
            
            try:
                # Pass session_id and sample_rate for API Gateway format
                await self.send_audio(
                    frame,
                    session_id=self._session_id,
                    sample_rate=self._sample_rate
                )
            except Exception as e:
                self._logger.error("Failed to send audio frame: %s", e)
                self._session_done_evt.set()
                break
        
        await self.stop_session()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        self._logger.error("Audio producer error: %s", e)
        self._session_done_evt.set()
```

### 9. Modify `_async_client.py` - Update transcribe() Method Signature

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

Add optional parameters to `transcribe()` method:

```python
async def transcribe(
    self,
    source: BinaryIO,
    *,
    transcription_config: Optional[TranscriptionConfig] = None,
    audio_format: Optional[AudioFormat] = None,
    translation_config: Optional[TranslationConfig] = None,
    audio_events_config: Optional[AudioEventsConfig] = None,
    ws_headers: Optional[dict] = None,
    timeout: Optional[float] = None,
    session_id: Optional[str] = None,  # Add this
    api_key: Optional[str] = None,  # Add this
    model: str = "pingala-v1-universal",  # Add this
    deliver_deltas_only: bool = True,  # Add this
) -> None:
    # ... existing code ...
    
    transcription_config, audio_format = await self._start_recognition_session(
        transcription_config=transcription_config,
        audio_format=audio_format,
        translation_config=translation_config,
        audio_events_config=audio_events_config,
        ws_headers=ws_headers,
        session_id=session_id,  # Pass these
        api_key=api_key,
        model=model,
        deliver_deltas_only=deliver_deltas_only,
    )
    
    # ... rest of method ...
```

### 10. Modify `_base_client.py` - Update _send_eos Call

**File**: `sdk/rt/shunyalabs/rt/_async_client.py`

Update the call to `_send_eos`:

```python
async def stop_session(self) -> None:
    await self._send_eos(self._seq_no, session_id=self._session_id)  # Pass session_id
    await self._session_done_evt.wait()
    await self.close()
```

## Summary of Changes

1. **Message Format**: Convert SDK messages to API Gateway format (`action`, `type`, `session_id`)
2. **Audio Encoding**: Send audio as base64-encoded JSON instead of binary frames
3. **Response Parsing**: Convert API Gateway responses (`SERVER_READY`, `segments`) to SDK format
4. **Session Management**: Track `session_id` and `sample_rate` throughout the client
5. **End of Stream**: Send `END_OF_AUDIO` frame + `end` action instead of `EndOfStream`

## Testing

After making these changes, test with:

```python
from shunyalabs.rt import AsyncClient, ServerMessageType, TranscriptionConfig, AudioFormat, AudioEncoding

async with AsyncClient(
    api_key="your-api-key",
    url="wss://tl.shunyalabs.ai/"
) as client:
    @client.on(ServerMessageType.ADD_TRANSCRIPT)
    def handle_final(msg):
        print(f"Final: {msg['metadata']['transcript']}")
    
    @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
    def handle_partial(msg):
        print(f"Partial: {msg['metadata']['transcript']}")
    
    config = TranscriptionConfig(language="auto", enable_partials=True)
    audio_fmt = AudioFormat(encoding=AudioEncoding.PCM_F32LE, sample_rate=16000)
    
    with open("audio.wav", "rb") as audio_file:
        await client.transcribe(
            audio_file,
            transcription_config=config,
            audio_format=audio_fmt,
            session_id="test-session-123",
            api_key="your-api-key",
            model="pingala-v1-universal",
            deliver_deltas_only=True,
        )
```

