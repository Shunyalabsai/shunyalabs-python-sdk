# Changelog

All notable changes to the Shunyalabs Python SDK and plugins are documented here.

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
