# Server-Side Implementation Guide for Shunyalabs RT SDK

This guide outlines what you need to implement on your server to work with the `shunyalabs.rt` package for real-time transcription.

## Overview

The client communicates via WebSocket using a JSON message protocol. The flow is:
1. Client connects via WebSocket
2. Client sends `StartRecognition` message with configuration
3. Server responds with `RecognitionStarted`
4. Client sends binary audio data
5. Server sends transcription results (`AddTranscript`, `AddPartialTranscript`)
6. Client sends `EndOfStream` when done
7. Server sends `EndOfTranscript` to signal completion

## 1. WebSocket Connection

### Connection Requirements
- **Protocol**: WebSocket (ws:// or wss://)
- **Authentication**: Bearer token in `Authorization` header during WebSocket handshake
- **URL Format**: Your WebSocket endpoint (e.g., `wss://your-server.com/v2`)

### Authentication Header
The client sends authentication via WebSocket headers:
```
Authorization: Bearer <api_key>
```

### URL Query Parameters
The client automatically adds SDK version info:
```
?sm-sdk=python-rt-sdk-v<version>
```

## 2. Message Types (Client → Server)

### StartRecognition
**Required**: This is the first message after WebSocket connection.

```json
{
  "message": "StartRecognition",
  "audio_format": {
    "type": "raw",  // or "file"
    "encoding": "pcm_s16le",  // "pcm_s16le", "pcm_f32le", or "mulaw"
    "sample_rate": 16000  // or 44100, etc.
  },
  "transcription_config": {
    "language": "en",
    "operating_point": "enhanced",  // or "standard"
    "enable_partials": true,  // optional
    "max_delay": 5.0,  // optional, in seconds
    // ... other optional config fields
  }
}
```

**Key audio_format fields:**
- `type`: `"raw"` for raw PCM audio, `"file"` for file formats (WAV, etc.)
- `encoding`: Required for raw audio. Options: `"pcm_s16le"`, `"pcm_f32le"`, `"mulaw"`
- `sample_rate`: Required for raw audio. Common: `16000`, `44100`, `48000`

**Key transcription_config fields:**
- `language`: ISO 639-1 code (e.g., `"en"`, `"es"`, `"fr"`)
- `operating_point`: `"enhanced"` or `"standard"`
- `enable_partials`: `true` to receive partial/interim transcripts
- `max_delay`: Maximum delay in seconds before sending transcripts

### Binary Audio Data
After `StartRecognition`, the client sends raw binary audio data as WebSocket binary frames.

**Important**: 
- Audio is sent as **binary WebSocket frames** (not JSON)
- For raw PCM: Send audio chunks matching the specified encoding and sample rate
- The client tracks sequence numbers internally

### EndOfStream
```json
{
  "message": "EndOfStream",
  "last_seq_no": 123  // Sequence number of last audio chunk
}
```

### ForceEndOfUtterance (Optional)
```json
{
  "message": "ForceEndOfUtterance"
}
```

## 3. Message Types (Server → Client)

### RecognitionStarted (REQUIRED)
**Must send immediately after receiving `StartRecognition`**

```json
{
  "message": "RecognitionStarted",
  "id": "session-id-12345"  // Unique session identifier
}
```

**Critical**: The client waits for this message (with 5-second timeout). If not received, the client will raise a `TimeoutError`.

### AudioAdded (Optional but Recommended)
Sent after processing each audio chunk:

```json
{
  "message": "AudioAdded",
  "seq_no": 123  // Sequence number of the audio chunk
}
```

### AddPartialTranscript (If enable_partials is true)
Interim/partial transcription results that may change:

```json
{
  "message": "AddPartialTranscript",
  "format": "2.1",  // Output format version
  "metadata": {
    "transcript": "Hello world",
    "start_time": 1.5,  // Seconds from start
    "end_time": 2.8
  },
  "results": [
    {
      "type": "word",
      "start_time": 1.5,
      "end_time": 1.8,
      "alternatives": [
        {
          "content": "Hello",
          "confidence": 0.95
        }
      ]
    },
    {
      "type": "word",
      "start_time": 1.9,
      "end_time": 2.2,
      "alternatives": [
        {
          "content": "world",
          "confidence": 0.92
        }
      ]
    }
  ]
}
```

### AddTranscript (REQUIRED for final results)
Final transcription results that won't change:

```json
{
  "message": "AddTranscript",
  "format": "2.1",
  "metadata": {
    "transcript": "Hello world",
    "start_time": 1.5,
    "end_time": 2.8
  },
  "results": [
    {
      "type": "word",
      "start_time": 1.5,
      "end_time": 1.8,
      "alternatives": [
        {
          "content": "Hello",
          "confidence": 0.95
        }
      ]
    },
    {
      "type": "word",
      "start_time": 1.9,
      "end_time": 2.2,
      "alternatives": [
        {
          "content": "world",
          "confidence": 0.92
        }
      ]
    }
  ]
}
```

**Required fields:**
- `message`: Must be `"AddTranscript"`
- `metadata.transcript`: The full transcript text
- `metadata.start_time`: Start time in seconds (float)
- `metadata.end_time`: End time in seconds (float)
- `results`: Array of word-level results (can be empty `[]` if not needed)

### EndOfTranscript (REQUIRED)
Sent when all transcription is complete (after `EndOfStream`):

```json
{
  "message": "EndOfTranscript"
}
```

**Critical**: The client waits for this message before considering the session complete.

### Error (For error handling)
```json
{
  "message": "Error",
  "reason": "Error description here"
}
```

### Warning (Optional)
```json
{
  "message": "Warning",
  "reason": "Warning message here"
}
```

## 4. Minimal Server Implementation Flow

Here's the minimal flow you need to implement:

```
1. Accept WebSocket connection
   ↓
2. Extract Authorization header (Bearer token)
   ↓
3. Wait for StartRecognition message (JSON)
   ↓
4. Parse audio_format and transcription_config
   ↓
5. Send RecognitionStarted response immediately
   ↓
6. Start receiving binary audio frames
   ↓
7. Process audio with your ASR model
   ↓
8. Send AddPartialTranscript (if enable_partials=true)
   ↓
9. Send AddTranscript for final results
   ↓
10. When client sends EndOfStream:
    - Finish processing remaining audio
    - Send final AddTranscript messages
    - Send EndOfTranscript
   ↓
11. Close WebSocket connection
```

## 5. Required Variables/Configuration

### From StartRecognition Message

**audio_format:**
- `type`: `"raw"` or `"file"`
- `encoding`: `"pcm_s16le"`, `"pcm_f32le"`, or `"mulaw"` (for raw)
- `sample_rate`: Integer (e.g., 16000, 44100)

**transcription_config:**
- `language`: String (ISO 639-1 code)
- `operating_point`: `"enhanced"` or `"standard"`
- `enable_partials`: Boolean (optional)
- `max_delay`: Float in seconds (optional)

### Server State Variables

You'll need to track:
- **Session ID**: Unique identifier for the WebSocket session
- **Sequence Number**: Track audio chunks (if using AudioAdded)
- **Audio Buffer**: Buffer incoming audio chunks
- **Transcription State**: Track what's been transcribed

## 6. Example Server Implementation (Pseudocode)

```python
async def handle_websocket(websocket):
    session_id = generate_session_id()
    audio_buffer = []
    seq_no = 0
    
    # Step 1: Wait for StartRecognition
    start_msg = await websocket.recv()  # JSON
    config = json.loads(start_msg)
    
    audio_format = config["audio_format"]
    transcription_config = config["transcription_config"]
    
    # Step 2: Send RecognitionStarted IMMEDIATELY
    await websocket.send(json.dumps({
        "message": "RecognitionStarted",
        "id": session_id
    }))
    
    # Step 3: Process audio and messages
    async for message in websocket:
        if isinstance(message, bytes):
            # Binary audio data
            audio_buffer.append(message)
            seq_no += 1
            
            # Process audio with your ASR model
            if should_send_partial(transcription_config):
                partial_result = process_audio(audio_buffer)
                await websocket.send(json.dumps({
                    "message": "AddPartialTranscript",
                    "format": "2.1",
                    "metadata": {
                        "transcript": partial_result["text"],
                        "start_time": partial_result["start"],
                        "end_time": partial_result["end"]
                    },
                    "results": []  # Optional word-level results
                }))
            
            # Send final transcript when ready
            if should_send_final():
                final_result = process_audio_final(audio_buffer)
                await websocket.send(json.dumps({
                    "message": "AddTranscript",
                    "format": "2.1",
                    "metadata": {
                        "transcript": final_result["text"],
                        "start_time": final_result["start"],
                        "end_time": final_result["end"]
                    },
                    "results": []  # Optional word-level results
                }))
        
        elif isinstance(message, str):
            # JSON message
            msg = json.loads(message)
            
            if msg["message"] == "EndOfStream":
                # Process remaining audio
                final_result = process_remaining_audio(audio_buffer)
                if final_result:
                    await websocket.send(json.dumps({
                        "message": "AddTranscript",
                        "format": "2.1",
                        "metadata": {
                            "transcript": final_result["text"],
                            "start_time": final_result["start"],
                            "end_time": final_result["end"]
                        },
                        "results": []
                    }))
                
                # Send EndOfTranscript
                await websocket.send(json.dumps({
                    "message": "EndOfTranscript"
                }))
                break
```

## 7. Critical Implementation Notes

### Timing Requirements
1. **RecognitionStarted**: Must be sent within **5 seconds** of receiving `StartRecognition`, or client will timeout
2. **EndOfTranscript**: Must be sent after `EndOfStream` to signal completion

### Message Format
- All JSON messages must be valid JSON
- Binary audio frames must match the specified encoding/sample rate
- Timestamps (`start_time`, `end_time`) should be relative to the start of the audio stream

### Error Handling
- Send `Error` message for any fatal errors
- Send `Warning` for non-fatal issues
- Always send `EndOfTranscript` before closing connection (if session was started)

### Audio Processing
- Buffer incoming audio chunks
- Process according to `audio_format` specifications
- Respect `max_delay` if specified (send transcripts within the delay window)
- Send partial transcripts if `enable_partials` is true

## 8. Testing Your Implementation

Use the example client code:

```python
from shunyalabs.rt import AsyncClient, ServerMessageType

async with AsyncClient(
    api_key="your-api-key",
    url="wss://your-server.com/v2"
) as client:
    @client.on(ServerMessageType.ADD_TRANSCRIPT)
    def handle_final(msg):
        print(f"Final: {msg['metadata']['transcript']}")
    
    @client.on(ServerMessageType.ADD_PARTIAL_TRANSCRIPT)
    def handle_partial(msg):
        print(f"Partial: {msg['metadata']['transcript']}")
    
    with open("audio.wav", "rb") as audio_file:
        await client.transcribe(audio_file)
```

## 9. Optional Features

### Multi-Channel Support
If implementing multi-channel, handle:
- `AddChannelAudio` messages
- `EndOfChannel` messages
- `ChannelAudioAdded` responses
- Channel labels in transcription config

### Translation
If implementing translation:
- `AddTranslation` messages
- `AddPartialTranslation` messages
- `translation_config` in StartRecognition

### Speaker Diarization
If implementing speaker diarization:
- `SpeakersResult` messages
- `GetSpeakers` requests
- Speaker information in transcript results

## Summary Checklist

- [ ] WebSocket server accepts connections
- [ ] Extract `Authorization: Bearer <token>` from handshake
- [ ] Parse `StartRecognition` JSON message
- [ ] Send `RecognitionStarted` response immediately (< 5 seconds)
- [ ] Accept binary audio frames
- [ ] Process audio with ASR model
- [ ] Send `AddPartialTranscript` (if enabled)
- [ ] Send `AddTranscript` for final results
- [ ] Handle `EndOfStream` message
- [ ] Send `EndOfTranscript` to complete session
- [ ] Handle errors with `Error` messages
- [ ] Close WebSocket gracefully

