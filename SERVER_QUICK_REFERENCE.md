# Server Implementation Quick Reference

## Essential Message Flow

```
Client → Server: StartRecognition (JSON)
Server → Client: RecognitionStarted (JSON)  ← MUST respond within 5 seconds
Client → Server: Binary audio frames
Server → Client: AddPartialTranscript (JSON, if enabled)
Server → Client: AddTranscript (JSON, final results)
Client → Server: EndOfStream (JSON)
Server → Client: EndOfTranscript (JSON)  ← MUST send to complete
```

## Required Server Messages

### 1. RecognitionStarted (REQUIRED)
```json
{
  "message": "RecognitionStarted",
  "id": "unique-session-id"
}
```
**Timing**: Send immediately after receiving `StartRecognition` (< 5 seconds)

### 2. AddTranscript (REQUIRED for results)
```json
{
  "message": "AddTranscript",
  "format": "2.1",
  "metadata": {
    "transcript": "Hello world",
    "start_time": 1.5,
    "end_time": 2.8
  },
  "results": []
}
```

### 3. EndOfTranscript (REQUIRED)
```json
{
  "message": "EndOfTranscript"
}
```
**Timing**: Send after processing all audio (after `EndOfStream`)

## Client Messages to Handle

### StartRecognition
```json
{
  "message": "StartRecognition",
  "audio_format": {
    "type": "raw",
    "encoding": "pcm_s16le",
    "sample_rate": 16000
  },
  "transcription_config": {
    "language": "en",
    "operating_point": "enhanced",
    "enable_partials": true
  }
}
```

### Binary Audio
- Raw binary WebSocket frames (not JSON)
- Format matches `audio_format.encoding` and `audio_format.sample_rate`

### EndOfStream
```json
{
  "message": "EndOfStream",
  "last_seq_no": 123
}
```

## Key Variables from StartRecognition

**audio_format:**
- `type`: `"raw"` or `"file"`
- `encoding`: `"pcm_s16le"`, `"pcm_f32le"`, `"mulaw"`
- `sample_rate`: Integer (16000, 44100, etc.)

**transcription_config:**
- `language`: ISO 639-1 code (`"en"`, `"es"`, etc.)
- `operating_point`: `"enhanced"` or `"standard"`
- `enable_partials`: Boolean (send partial transcripts if true)
- `max_delay`: Float (seconds) - max delay before sending transcripts

## Authentication

- Extract `Authorization: Bearer <token>` from WebSocket handshake headers
- Validate token before processing

## Minimal Implementation

1. Accept WebSocket connection
2. Extract Authorization header
3. Receive `StartRecognition` (JSON)
4. **Send `RecognitionStarted` immediately**
5. Receive binary audio frames
6. Process with ASR model
7. Send `AddTranscript` (and `AddPartialTranscript` if enabled)
8. Receive `EndOfStream`
9. **Send `EndOfTranscript`**
10. Close connection

## Critical Timing

- `RecognitionStarted`: < 5 seconds (client timeout)
- `EndOfTranscript`: After all audio processed

## Error Handling

```json
{
  "message": "Error",
  "reason": "Error description"
}
```

