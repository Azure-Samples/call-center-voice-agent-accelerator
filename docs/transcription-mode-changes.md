# Transcription mode: changes from the base accelerator

This document lists every change made to the upstream accelerator (base commit `312ff9b`) to add
**transcription mode** — live, speaker-labeled (customer / agent) transcription of human-handled
calls, ingested from Genesys Cloud AudioHook Monitor. For setup and usage, see
[genesys-transcription.md](genesys-transcription.md).

## Summary

- A new `CALL_MODE` setting selects the behavior. `voice_agent` (default) keeps the original
  accelerator unchanged; `transcription` turns the app into a listen-only transcriber.
- Speaker labels come from **separate audio channels** (Genesys stereo: `external` = customer,
  `internal` = agent), not diarization. Each channel feeds its own Azure AI Speech recognizer.
- Transcripts are pushed to a browser view (`/transcript`) over WebSocket.
- No Genesys account is needed to test: a stereo mode in the existing `/genesys` simulator and a
  new browser harness (`/transcribe-test`) generate the same audio Genesys would send.

```
Genesys AudioHook Monitor ──stereo PCMU 8 kHz──┐
Browser harness ──────────stereo PCM16 16 kHz──┼─► split channels ─► Azure AI Speech (1 recognizer
                                               │                     per speaker) ─► TranscriptHub
                                               │                                       │
                                               └──────────── /transcript (agent view) ◄┘
```

## Behavior by mode

| | `voice_agent` (default) | `transcription` |
|---|---|---|
| `/` | Voice agent web client | Redirects to `/transcribe-test` |
| `/web/ws` | Registered | Not registered |
| `/audiohook/ws` (Genesys) | Voice Live agent (bidirectional) | Listen-only transcription |
| `/transcript`, `/transcript/ws`, `/transcript/active` | — | Agent transcript view + feed |
| `/transcribe-test`, `/web/transcribe/ws` | — | Browser test harness |
| `/health` | `{"status","mode"}` | `{"status","mode"}` |
| Telephony providers | All | Genesys only (others log a warning; web harness still works) |

## New code — `server/app/transcription/`

| File | Responsibility |
|---|---|
| `events.py` | `Speaker`, `StreamState`, `TranscriptEvent`, `StatusEvent` and their camelCase wire format (`to_dict`). |
| `audio.py` | `deinterleave()` splits multi-channel frames; `pcmu_to_pcm16()` decodes G.711 μ-law. |
| `backend.py` | `TranscriptionBackend` protocol, `FakeBackend` (offline placeholder text), `create_backend()` factory. |
| `azure_speech_backend.py` | Azure AI Speech real-time STT: one `SpeechRecognizer` + push stream per speaker, SDK-thread → event-loop hop, restart with backoff (3 attempts) on recognizer errors, auth via managed identity → key → `DefaultAzureCredential`. |
| `session.py` | `TranscriptionSession`: binds one call to a backend and the hub. A backend failure publishes `degraded` but never drops the call; `close()` always publishes `ended`. |
| `transcript_hub.py` | In-process pub/sub per conversation with bounded queues (drop oldest on overflow) and an active-conversation registry. |
| `web_handler.py` | Browser harness handler: stereo PCM16 16 kHz, left = customer, right = agent. |
| `routes.py` | Registers the transcription pages and WebSockets; validates the web token and `conversationId`. |

## New / refactored Genesys code — `server/app/providers/genesys/`

| File | Change |
|---|---|
| `audiohook.py` (new) | AudioHook protocol state machine extracted from `media_handler.py` so both handlers share it: open/close/ping/pause/resume, media negotiation (`select_media`), API-key check. |
| `media_handler.py` | Refactor only: now uses `AudioHookSession`. Voice-agent behavior unchanged. |
| `transcription_handler.py` (new) | `GenesysTranscriptionHandler`: listen-only. Prefers stereo media, maps `external`→customer and `internal`→agent, decodes PCMU and feeds the session. Never sends audio back. Mono falls back to a single (customer) channel with a warning. |
| `__init__.py` | Picks the handler by `CALL_MODE`. |
| `simulator.html` | New **Channels** dropdown. *Stereo* plays a chosen audio file as the customer and the mic as the agent, interleaved exactly like AudioHook Monitor. *Mono* is the original behavior. |

## Changes to shared code

| File | Change |
|---|---|
| `server/server.py` | Reads `CALL_MODE`, `TRANSCRIPTION_BACKEND`, `TRANSCRIPTION_LANGUAGE`, `AZURE_SPEECH_ENDPOINT`, `AZURE_SPEECH_KEY`; registers routes by mode; `/` redirect and `/health` mode field. |
| `server/app/config_validator.py` | Mode-aware startup validation: valid `CALL_MODE` / backend, Speech endpoint required (falls back to the Voice Live endpoint), warning when only `DefaultAzureCredential` is available, warning for non-Genesys providers. |
| `server/app/call_loop.py` | The handler contract method is renamed `connect_voicelive()` → `start()` so transcription handlers can use the same loop. |
| `server/app/handler/voicelive_media_handler.py` | Adds `start()` delegating to `connect_voicelive()`. |

## UI — `server/static/`

| File | Purpose |
|---|---|
| `transcript.html` | Agent view: chat-style Customer / Agent lines, live partials replaced by finals, connection status, picker of active conversations. |
| `transcribe-test.html` | Harness: customer from an audio file, agent from the mic, level meters, opens the transcript view. |

## Infrastructure and packaging

| File | Change |
|---|---|
| `infra/main.bicep`, `infra/modules/containerapp.bicep` | New `callMode` parameter (`voice_agent` \| `transcription`) passed to the container as `CALL_MODE`. |
| `infra/main.parameters.json`, `azure.yaml` | Map `CALL_MODE` from the azd environment (default `voice_agent`). |
| `server/Dockerfile` | Installs `libasound2` and `ca-certificates` (Speech SDK runtime deps). |
| `server/pyproject.toml`, `server/uv.lock` | Adds `azure-cognitiveservices-speech`; dev group with `pytest` / `pytest-asyncio` and pytest config. |
| `server/.env.sample` | Documents the new settings. |

No new Azure resources or role assignments are required: the existing AI Services resource serves
Speech, and the app identity already has *Cognitive Services User*.

## Tests and samples

- `server/tests/` (new): 66 unit tests covering events/audio, hub, backend + session lifecycle,
  Azure Speech backend (with a fake recognizer), AudioHook protocol, both Genesys handlers, routes,
  config validation, call loop and per-mode route registration. Run:
  `cd server; uv run pytest -q` (or `.venv\Scripts\python -m pytest -q`).
- `server/samples/transcription/` (new): sample customer/agent WAVs, a generator script, and
  end-to-end checks against a running server (`e2e_live.py` for both ingest paths,
  `e2e_browser.js` driving the `/genesys` simulator). See its README.

## Documentation

- `docs/genesys-transcription.md` (new): architecture, configuration, endpoints, testing without
  Genesys, connecting Genesys Cloud, event schema, limitations.
- `README.md`, `server/README.md`: transcription mode sections.
- `docs/superpowers/specs/…-transcription-mode-design.md` and `docs/superpowers/plans/…`: design spec
  and implementation plan.

## Known limitations

- No diarization: speaker separation requires stereo AudioHook media. Mono labels everything as
  customer; multiple people on one channel share its label.
- Single replica: the hub is in-process; multi-replica fan-out (e.g. Azure Web PubSub) is future work.
- Transcripts are not persisted.
- Default Azure Speech real-time model for the configured locale; 8 kHz telephony audio.
