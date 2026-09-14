# Changelog

All notable changes to the Shunyalabs Python SDK and plugins are documented here.

## [shunyalabsai 1.1.0 · pipecat-shunyalabsai 1.1.0 · livekit-plugins-shunyalabsai 1.1.0] - 2026-09-14

A latency release. `/v1/realtime` grew per-connection tuning and a turn-end
signal some time ago; the SDK had no way to reach any of it, so every consumer
was running server defaults and doing its own turn detection. This closes that
gap.

**The short version for a voice agent:** set `endpoint_silence_ms`, drive turns
from `utterance_end`, finalise with `commit()` instead of `end()`, and pass a
lower `min_buffer_frames` if your transport is not WebRTC.

**`pipecat-shunyalabsai` 1.1.0 and `livekit-plugins-shunyalabsai` 1.1.0 require
`shunyalabsai>=1.1.0`** — both use config fields and a message type that older
cores do not define.

### Added — core (`shunyalabsai` 1.1.0)

- **`StreamingConfig` live tuning.** `endpoint_silence_ms`, `decode_every_ms`,
  `vad`, `codeswitch` and `model`. `endpoint_silence_ms` is the one that matters
  most: it is pure wall-clock delay after the speaker stops, before a `final` can
  be emitted. Unset fields are omitted from the wire so server defaults apply.
- **`StreamingMessageType.UTTERANCE_END`** and `StreamingUtteranceEnd`. The
  turn-end signal, emitted after a `final` whose `end_of_utterance` is true.
  Previously this event arrived, failed to match the enum, and was logged as
  `Unknown streaming message type: utterance_end` on every turn.
- **`StreamingFinal.end_of_utterance`.** Distinguishes a real endpoint from the
  server hitting its maximum segment length and cutting mid-speech. A `final`
  alone does not tell you the turn is over.
- **`ASRStreamingConnection.commit()`** (and `flush()`, an alias). Finalises the
  current utterance and **keeps the socket open**. `end()` finalises and closes,
  so using it per turn costs a TCP+TLS handshake every turn — measured at ~300 ms
  from India, against an ASR that finalises in 39 ms.
- **`ASRStreamingConnection.effective_config`.** The tuning values from the
  server's `ready` frame, after clamping. Out-of-range requests are clamped
  silently, not rejected, so this is how you confirm what took effect.
- **`reset_token_cache()`** for tests and credential rotation.

### Changed — core

- **Token state is now shared per credential, not per instance.** A pipeline
  builds one service object per stream, so 50 concurrent calls used to mean 100
  mint requests, all on the critical path of call setup. Now one mint per
  credential, refreshed once for everyone. The `TokenAuth` API is unchanged.
- **`StreamingConfig` rejects unknown fields.** Pydantic's default silently
  dropped them, which is the worst possible behaviour for a tuning knob: the
  setting appears applied and is not. `StreamingConfig(endpoint_silence=250)`
  now raises instead of doing nothing. Nothing in the documented surface changes.

### Fixed — core

- **`final_refined` was unusable.** It was missing from the message map, so it
  fell through to the unknown-type branch and subscribers received a
  `StreamingError` — which has no `.text`. 1.0.1 started subscribing to this
  event but could not read it.
- **Streaming result fields were never populated.** `/v1/realtime` sends `seg`,
  `delta` and `elapsed_ms`; the models declared `segment_id`, `latency_ms` and
  `inference_time_ms`, so in practice only `text` ever arrived. Both spellings
  now populate the same field, and `delta` is exposed.
- **`__version__` said 1.0.0 in all three packages** while the distributions said
  1.0.1. That string is reported to the gateway as `sm-sdk`, so our own telemetry
  attributed traffic to the wrong version. Now asserted by a test.

### Added — Pipecat plugin (`pipecat-shunyalabsai` 1.1.0)

- **Turn frames, behind `emit_turn_frames=True` (default off).** `utterance_end`
  becomes `UserStoppedSpeakingFrame` and the first partial of an utterance
  becomes `UserStartedSpeakingFrame`, so turn boundaries come from the ASR's own
  endpointing instead of a transport VAD guessing from the same audio. The
  service previously emitted neither.

  **This must be paired with `ExternalUserTurnStrategies` on the user
  aggregator**, and it is off by default because neither half works alone.
  Measured, with one turn driven through a real pipeline:

  | strategies | `emit_turn_frames` | `UserStartedSpeakingFrame` seen |
  | ---------- | ------------------ | ------------------------------- |
  | default    | `False` (1.0.x)    | 1                               |
  | default    | `True`             | **2 — duplicated**              |
  | external   | `True`             | 1                               |
  | external   | `False`            | **0 — no turn signal**          |

  The default strategies consume `VADUserStartedSpeakingFrame` from the
  transport, not the public frame, so the aggregator broadcasts its own and ours
  passes through as well.

  Note the start frame is only as prompt as `decode_every_ms`, so keep a
  transport VAD for barge-in — that needs to be faster than turn-taking does.
- **`ShunyalabsSTTService.commit()`** — force a turn boundary from your own
  endpointing.
- **STT tuning arguments**: `endpoint_silence_ms`, `decode_every_ms`, `vad`,
  `codeswitch`, `model`. A clamped value is logged rather than left to be
  discovered as "the ASR is slow".
- **TTS arguments** `quality`, `clause_first`, `min_buffer_frames`, `frame_ms`.
  `quality="low"` roughly halves synthesis time but drops a word in a measurable
  fraction of short clips — fine for acknowledgements, not for reading out a
  number or a reference code. It is a session setting, not per utterance.
- **`ShunyalabsTTSService.discard_current_turn()`** — stop the bot mid-utterance
  without waiting for an interruption frame to propagate.

### Changed — Pipecat plugin

- **`min_send_bytes` default 4096 → 3200** (256 ms → 100 ms at 16 kHz). The old
  value rested on the gateway needing ~4 KB blocks for its VAD; the opposite is
  true. The server takes one silence reading per frame it is fed, so larger
  frames give it *coarser* resolution — which is why it defensively chops
  anything over ~1 s into 100 ms sub-frames. Every buffered byte was latency in
  front of an ASR that finalises in 39 ms.
- **Barge-in no longer reconnects.** It now sends `{"type": "cancel"}` and drains
  to the server's `cancelled` acknowledgement, leaving the session reusable.
  Previously it dropped the socket — always correct, but it paid a full TCP+TLS
  dial and `ready` handshake on the next turn, precisely while the caller was
  waiting to be answered. Falls back to the old behaviour if the fast path is not
  safe, so recovery is never worse than before.
- `MIN_BUFFER_FRAMES` and `FRAME_MS` are now instance settings. **Defaults are
  unchanged**: the 480 ms pre-buffer is sized for WebRTC, where an encoder
  starves audibly, and Daily and LiveKit users should keep it.

### Fixed — Pipecat plugin

- Removed the `final_segment` handler, which could never fire on
  `/v1/realtime`, and corrected the docstrings and README table that described
  it as the source of `TranscriptionFrame`.
- A reconnect mid-utterance no longer strands the speaking latch, which would
  otherwise have suppressed every subsequent turn-start frame for the life of the
  pipeline.

### Fixed — LiveKit plugin (`livekit-plugins-shunyalabsai` 1.1.0)

- **`END_OF_SPEECH` was never emitted.** Its only emission sat inside the
  `final_segment` handler, which `/v1/realtime` never triggers — so this plugin
  produced no end-of-speech event at all on the current gateway. It now comes
  from `utterance_end`.
- `final_refined` is now delivered as a final transcript instead of being
  dropped.
- Added the same STT tuning arguments as the Pipecat plugin. `STT.model` now
  reflects an explicitly configured model rather than always reporting
  `vak-v3`.

## [shunyalabsai 1.0.1 · pipecat-shunyalabsai 1.0.1 · livekit-plugins-shunyalabsai 1.0.1] - 2026-09-08

A correctness fix in the Pipecat plugin, plus documentation that stops three settings
looking like something they are not. **`pipecat-shunyalabsai` 1.0.1 requires
`shunyalabsai>=1.0.1`** — it subscribes to an event the older core does not define.

### Fixed — Pipecat (`pipecat-shunyalabsai` 1.0.1)

- **Code-switch refinements were being discarded.** When the service re-renders a
  finalized segment in the correct scripts it sends a separate `final_refined` event.
  The plugin had no handler for it, so the improved text was dropped — while it *did*
  register a handler for `final_segment`, which `/v1/realtime` never sends and which
  therefore could never fire. Refinements now arrive as their own transcription.

### Changed — core (`shunyalabsai` 1.0.1)

- **`StreamingMessageType.FINAL_REFINED` added.** The service emits it; the enum did
  not list it, so it could not be subscribed to.
- `FINAL_SEGMENT` and `DONE` are documented as legacy. They remain importable, but
  `/v1/realtime` never sends them and a handler on either can never fire.
- **`dtype`, `chunk_size_sec` and `silence_threshold_sec` are documented as inert.**
  They are accepted and ignored — the real-time endpoint endpoints on its own VAD.
  They read like latency controls and are not; nothing about them has changed, only
  the docstring that now says so.
- `StreamingConfig` was headed `WS /ws`; corrected to `/v1/realtime`.

### Changed — both plugins

- **`language="auto"` now warns once at construction.** It stays the default and stays
  supported, but a live stream must commit to a language from the opening seconds of
  audio, so detection there is best-effort in a way batch transcription is not. For a
  voice agent the language is nearly always known in advance, and passing it removes an
  avoidable source of wrong-script transcripts on the first turns of a call. The LiveKit
  example previously annotated it `# auto-detects language`, which oversold it; both
  examples now pass an explicit language.

`livekit-plugins-shunyalabsai` still requires only `shunyalabsai>=1.0.0` — its change
uses nothing new from the core.


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
