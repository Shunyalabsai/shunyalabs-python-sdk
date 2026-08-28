# Changelog

All notable changes to the Shunyalabs Python SDK and plugins are documented here.

## [shunyalabsai 1.0.0 · pipecat-shunyalabsai 1.0.0 · livekit-plugins-shunyalabsai 1.0.0] - 2026-08-28

Real-time services cutover. ASR and TTS now run on the v2 real-time gateways
with token-based authentication. **Upgrade all three packages together** — the
plugins require `shunyalabsai>=1.0.0`.

### Changed — core (`shunyalabsai` 1.0.0)

- **Token authentication.** You still provide only your API key; the SDK now
  exchanges it for a short-lived access token automatically and keeps it
  refreshed. The raw API key is never sent to the STT/TTS services.
  `AsyncShunyaClient` / `ShunyaClient` mint tokens on your behalf.
- **New default endpoints.** ASR → `asrv2prod.shunyalabs.ai`, TTS →
  `ttsv2.shunyalabs.ai`; real-time streaming on `/v1/realtime` for both. Override
  with `asr_url` / `asr_ws_url` / `tts_url` / `tts_ws_url` (or the matching
  `SHUNYALABS_*` env vars) as before.
- **Streaming TTS** speaks over the `/v1/realtime` protocol and delivers raw PCM
  (24 kHz, 16-bit mono).
- **Configurable endpoints.** URLs resolve by precedence: explicit arg → endpoint
  returned by the token service (an `endpoints` object, used automatically if present)
  → `SHUNYALABS_{ASR,TTS}_URL` / `_WS_URL` env vars → built-in default.

### Fixed — core

- **Batch TTS** now targets `POST /v1/audio/speech` (previously returned 404).
- **Batch ASR** now returns `detected_language` **and** `detected_language_name`,
  and correctly parses the transcription response.

### Changed — Pipecat (`pipecat-shunyalabsai` 1.0.0)

First release of the `pipecat-shunyalabsai` distribution (fresh package name;
functionally identical to the tested cutover build).

- New real-time endpoints and token auth (via the core SDK).
- TTS runs over a **persistent** WebSocket session — each turn speaks with a
  flush on the shared connection rather than reconnecting.
- **Frame-paced streaming**: gateway audio is re-chunked into fixed 40 ms frames
  emitted at wall-clock rate to prevent WebRTC encoder starvation/jitter.
- **Barge-in**: an interruption resets the streaming session so audio from the
  interrupted turn cannot leak into the next one.
- **STT reconnect hardening**: a dropped socket is re-opened before more audio
  is buffered, with reconnects serialized so a burst can't spawn several.
- `TTSAudioRawFrame`s are PCM at **24 kHz** (was documented as 16 kHz).
- `output_format` / `speed` are retained for compatibility but the real-time
  stream is always PCM at natural rate; container formats and speed control are
  batch REST API features.

### Fixed — LiveKit (`livekit-plugins-shunyalabsai` 1.0.0)

- New real-time endpoints and token auth (via the core SDK).
- **TTS `sample_rate` default is now 24000** (was 16000). The gateway emits
  24 kHz PCM on both the streaming and batch paths; the previous default caused
  pitch/tempo-shifted playback.

## [pipecat-shunyalabs 1.0.2] - 2026-04-16

### Fixed

- **Pipecat STT — display-name language crash**: `ShunyalabsSTTService` no
  longer raises `ValueError: 'English' is not a valid Language` in its
  `on_partial` / `on_final_segment` / `on_final` callbacks when the ASR
  gateway reports the detected language as a human-readable display name
  (e.g. `"English"`, `"Hindi"`) rather than the ISO code passed in
  `StreamingConfig`. Display names are now normalised to ISO codes and
  unrecognised values fall back to `None`, so transcription frames are
  always delivered to the pipeline.

## [3.0.3] - 2026-04-11

### Breaking Changes (TTS)

- **`language` is now required** in `TTSConfig`. The TTS gateway returns HTTP 422
  if `language` is omitted. Pass an ISO 639-1/639-2 code such as `"en"`, `"hi"`,
  `"ta"`, etc.
- **Either `voice` or `reference_wav` is now required** in `TTSConfig`. The
  validator rejects requests with neither.
- **`reference_text` requires `reference_wav`**. The validator now enforces this
  pairing rather than silently sending unused data.
- **Removed `volume_normalization`** from `TTSConfig`. The gateway no longer
  supports this option.
- **Removed `max_tokens`** from `TTSConfig`. The gateway computes token caps
  internally based on input text length.

### Changed

- `TTSConfig.model` remains optional with default `"zero-indic"`. It is now a
  plain `str` (no longer a `Literal`) so future model names can be passed.
- `TTSConfig.response_format` default changed from `"mp3"` to `"wav"` to match
  the gateway default.

### Plugins

- **LiveKit plugin (`shunyalabs[livekit]` 1.0.1)**:
  - Removed redundant `speaker` parameter (use `voice` instead).
  - `style` is now optional — the gateway injects a default `<Conversational>`
    tag when none is provided.
- **Pipecat plugin (`pipecat-shunyalabs` 1.0.1)**:
  - Removed redundant `speaker` parameter (use `voice` instead).
  - **Fixed double-prefix bug**: `_format_text` no longer prepends the speaker
    name (e.g. `"Rajesh: ..."`); the gateway prepends it server-side, so
    sending it from the client produced `"Rajesh: Rajesh: ..."` and corrupted
    the prompt.
  - `style` is now optional — the gateway injects a default `<Conversational>`
    tag when none is provided.

### Migration

```python
# Before (3.0.2)
config = TTSConfig(voice="Rajesh", volume_normalization="peak", max_tokens=1024)

# After (3.0.3)
config = TTSConfig(language="en", voice="Rajesh")  # both required
```
