# Transcription Mode (Agent-Assist) — Design

**Date:** 2026-10-29
**Status:** Draft — awaiting review

## 1. Problem

The accelerator today is a **voice agent**: it bridges a phone call to Azure Voice Live, which listens
and *speaks back* to the caller. A customer proof of concept (POC) needs the opposite shape — an
**agent-assist** experience in which a human agent handles the call and the system only *listens*:

- Show a **live, on-screen transcript** of a customer-service call.
- **Label who is speaking** (customer vs. agent), because a downstream model uses that context to
  pick the right outcome from a large option list (on the order of 100–200 options).
- Prove the capability with a **local demo first**; full contact-center/CRM integration comes later,
  but the demo must credibly show **how live calls would connect** (the customer runs Genesys Cloud).
- **Do not lock in a model.** The production speech-to-text model is still being evaluated
  (diarization quality, latency, cost, regional availability, preview access, and model-retirement
  timelines across Azure AI Speech, `gpt-4o-transcribe(-diarize)`, and MAI Transcribe).
- **Functionality over cost** for the POC; production target is roughly tens of thousands of calls
  per month, so the design must not preclude scaling out.

## 2. Goals / Non-goals

### Goals

| ID | Requirement |
|----|-------------|
| G1 | Add a `transcription` call mode alongside the existing voice-agent mode, selected by config. Default behavior is unchanged. |
| G2 | Produce live, speaker-labeled transcript events (`customer` / `agent`) with partial and final results. |
| G3 | Display the transcript in a browser page an agent can keep open during a call. |
| G4 | Ingest real call audio from **Genesys AudioHook** (both call legs). |
| G5 | Provide a **browser two-stream test harness** so the demo runs locally with no telephony. |
| G6 | Put speech-to-text behind a **pluggable backend interface**; ship **Azure AI Speech** first. |
| G7 | Emit a **stable transcript event schema** that a downstream classifier/MCP workflow can consume later. |
| G8 | Document how Genesys connects to transcription mode (architecture the customer can present). |

### Non-goals (this iteration)

- Downstream classification / MCP-driven next-step workflow (consumes G7 later).
- Persisting transcripts to storage; post-call analytics/summaries.
- Backends other than Azure AI Speech (`gpt-4o-transcribe-diarize`, MAI Transcribe) — the interface
  leaves room; adding them is a follow-up once availability is confirmed.
- Transcription mode for ACS, Twilio, Vonage, Infobip, Sinch, Bandwidth.
- Cost optimization and production load testing.

## 3. Key insight: channel-based speaker separation

Genesys AudioHook can stream the call as **stereo PCMU 8 kHz**: one channel for the customer
(`external`) and one for the agent (`internal`). Recognizing each channel separately gives a
speaker label that is **deterministic**, avoiding the errors of model-based diarization on mixed
mono audio. Model diarization (e.g. `ConversationTranscriber` or `gpt-4o-transcribe-diarize`) remains
a fallback for mono sources.

The current `GenesysMediaHandler` deliberately selects the `external`-only offer (bot use case). In
transcription mode we must instead select the `["external","internal"]` offer.

> Genesys configuration note: a voice bot uses the **Audio Connector** integration; a listen-only
> transcription use case uses an **AudioHook Monitor** integration. Both speak the same AudioHook
> WebSocket protocol, so the protocol code is shared. The exact Genesys flow at the customer must be
> confirmed with their Genesys owner (open question Q1).

## 4. Architecture

```
            ┌────────────────────────────┐
 Genesys ──►│ /audiohook/ws              │  CALL_MODE=transcription
 AudioHook  │ GenesysTranscriptionHandler│─┐
 (stereo)   └────────────────────────────┘ │  per-channel PCM16
                                           ▼
 Browser  ─►┌────────────────────────────┐   ┌──────────────────────────┐
 harness    │ /web/transcribe/ws         │──►│ TranscriptionBackend     │
 (2 streams)│ WebTranscriptionHandler    │   │  └ AzureSpeechBackend    │
            └────────────────────────────┘   │    (1 recognizer/channel)│
                                             └────────────┬─────────────┘
                                                          │ TranscriptEvent
                                                          ▼
                                             ┌──────────────────────────┐
                                             │ TranscriptHub            │
                                             │ (pub/sub by conversation)│
                                             └────────────┬─────────────┘
                                                          ▼
                                   /transcript/ws ──► /transcript page (agent view)
```

Voice-agent mode (`CALL_MODE=voice_agent`, default) is untouched: same routes, same handlers.

## 5. Components

All new code lives under `server/app/transcription/` unless noted. Each unit has one purpose and is
testable without Azure.

### 5.1 Configuration — `CALL_MODE`

- `CALL_MODE`: `voice_agent` (default) | `transcription`.
- `TRANSCRIPTION_BACKEND`: `azure_speech` (default) | `fake` (offline placeholder transcripts for
  UI demos and tests; needs no Azure resources).
- `TRANSCRIPTION_LANGUAGE`: default `en-US`.
- Azure AI Speech reuses the existing AI Services resource via its endpoint
  (`AZURE_SPEECH_ENDPOINT` → falls back to `AZURE_VOICE_LIVE_ENDPOINT`). Auth precedence: managed
  identity (`AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID`), then key (`AZURE_SPEECH_KEY` →
  `AZURE_VOICE_LIVE_API_KEY`), then `DefaultAzureCredential` (local `az login`). No region setting.
- `config_validator.py` validates mode-specific settings; Voice Live model settings are not
  required in transcription mode. In transcription mode only the Genesys provider registers
  telephony routes; other providers log a warning and are skipped.

### 5.2 `events.py` — `TranscriptEvent` (integration contract, G7)

```json
{
  "type": "transcript",
  "conversationId": "string",
  "speaker": "customer | agent | unknown",
  "text": "string",
  "isFinal": true,
  "offsetMs": 0,
  "durationMs": 0,
  "seq": 0
}
```

Plus `{"type":"status","conversationId":"...","state":"connected|degraded|ended","detail":"..."}`.
Implemented as dataclasses with `to_dict()`. This schema is the contract for future consumers and is
documented.

### 5.3 `backend.py` — `TranscriptionBackend` interface (G6)

```python
class TranscriptionBackend(Protocol):
    sample_rate: int  # PCM16 mono rate expected per channel

    async def start(
        self,
        conversation_id: str,
        speakers: list[Speaker],
        on_event: Callable[[TranscriptEvent | StatusEvent], None],  # called on the loop thread
    ) -> None: ...
    def push_audio(self, speaker: Speaker, pcm16: bytes) -> None: ...  # non-blocking
    async def stop(self) -> None: ...
```

`create_backend(config, sample_rate)` maps `TRANSCRIPTION_BACKEND` to an implementation; the sample
rate is per source (8 kHz Genesys, 16 kHz browser harness), so no resampling is needed. A
`FakeBackend` emits one deterministic final line per ~2 s of audio per speaker.

### 5.4 `azure_speech_backend.py` — `AzureSpeechBackend`

- Uses `azure-cognitiveservices-speech` (core dependency; the container installs `libasound2`).
- One `SpeechRecognizer` with a `PushAudioInputStream` **per speaker channel**; continuous recognition.
- `recognizing` → partial event; `recognized` → final event; `canceled` → `status: degraded`.
- SDK callbacks fire on SDK threads; marshal to asyncio with `loop.call_soon_threadsafe`.
- Auth: `SpeechConfig(endpoint=..., subscription=key)` or `SpeechConfig(endpoint=...,
  token_credential=...)` (managed identity / `DefaultAzureCredential`).
- Audio: PCM16 mono at 8 kHz per channel for telephony (no up-sampling); fall back to 16 kHz if
  accuracy testing requires it.

### 5.5 `transcript_hub.py` — `TranscriptHub`

In-process pub/sub keyed by `conversationId`: `subscribe(conv_id) -> asyncio.Queue`, `unsubscribe`,
`publish(event)`, `active()`. Bounded per-subscriber queues; on overflow drop the oldest event
with a warning. Single-replica scope for the POC; multi-replica fan-out (e.g. Azure Web PubSub)
is a scale-out follow-up.

### 5.6 `GenesysTranscriptionHandler` (G4)

- Extract the AudioHook protocol (open/ping/close/update/pause/resume, sequence numbers, API-key
  validation, disconnect) from `GenesysMediaHandler` into a reusable `AudioHookSession` so both
  handlers share it without duplication. Voice-agent behavior is unchanged.
- Media negotiation is a strategy parameter: the voice agent keeps preferring `external`-only; the
  transcription handler prefers `PCMU` with `channels: ["external","internal"]` and falls back to
  external-only (all audio labeled `customer`, warning logged).
- Per frame: de-interleave stereo PCMU → per-channel `ulaw2lin` → `push_audio(speaker, pcm)`.
  `external` → `customer`, `internal` → `agent`.
- Never sends audio back to Genesys. No barge-in, no ambient mixer.
- `conversationId` from the AudioHook `open` message is the hub key.

### 5.7 `WebTranscriptionHandler` + browser harness (G5)

- WebSocket `/web/transcribe/ws?conversationId=...&token=...` receives **interleaved stereo PCM16 at
  16 kHz** (left = customer, right = agent), de-interleaves, and pushes per channel (the backend
  runs at 16 kHz for this source).
- Harness page `/transcribe-test`: the **agent** channel is the microphone; the **customer** channel
  is an uploaded audio file (e.g. a recorded customer call) played into the stream. It embeds the
  transcript view for the same `conversationId`.
- The Genesys simulator (`providers/genesys/simulator.html`) gains a stereo option (audio file as
  customer + mic as agent) for end-to-end AudioHook testing.

### 5.8 Transcript view (G3)

- WebSocket `/transcript/ws?conversationId=...&token=...` subscribes to the hub.
- Page `/transcript?conversationId=...`: chat-style list labeled **Customer** / **Agent**; partial
  text renders grey and is replaced in place by the final line; connection-status banner.
- `GET /transcript/active` lists active conversations so the page offers a picker.

### 5.9 Routing by mode

- `server.py` registers voice-agent web routes or transcription routes based on `CALL_MODE`.
- The Genesys provider binds `/audiohook/ws` to the handler for the active mode.
- `/health` includes the mode.

## 6. Data flow

1. Source connects (Genesys `open`, or harness connect) → handler acquires a `CallManager` slot
   (existing limits/timeouts) → `backend.start([customer, agent], hub.publish)`.
2. Audio frames → de-interleave → decode (PCMU → PCM16) → `backend.push_audio`.
3. Backend emits partial/final `TranscriptEvent`s → `TranscriptHub.publish` → subscribed viewers.
4. Final events are logged at `DEBUG` with the correlation ID (no persistence).
5. Source closes → `backend.stop()` → hub publishes `status: ended` → slot released.

`run_call_loop` is generalized so its background task calls a neutral `start()` (Voice Live connect
for the voice agent, backend start for transcription) instead of `connect_voicelive()` by name.

## 7. Error handling

| Failure | Behavior |
|---------|----------|
| Speech recognizer cancels/drops | Publish `status: degraded`; retry up to 3 times with backoff; **keep the call WebSocket open** so the live call is not disrupted. |
| Retries exhausted | Publish `status: degraded` with detail; keep accepting and discarding audio until the source closes. |
| Mono-only Genesys offer | Accept; label `customer`; log warning. |
| No compatible media offer | Send AudioHook `disconnect` (existing behavior). |
| Viewer slow or disconnected | Bounded queue; drop oldest; unsubscribe on disconnect. |
| Bad or missing token | Close `4403` (reuse `WEB_ACCESS_TOKEN` for `/web/transcribe/ws` and `/transcript/ws`); Genesys keeps `X-API-KEY`. |
| Capacity reached | Close `4429` (existing `CallManager`). |

Transcripts may contain customer PII/PHI: they are never written to disk, transcript text is logged
only at `DEBUG`, and the view is token-protected. Production use requires a data-handling review.

## 8. Infrastructure

No new Azure resources: the existing `AIServices` account serves Azure AI Speech, and the container
app identity already holds `Cognitive Services User`. Add `CALL_MODE` and transcription settings to
the container app environment in `infra/` (`callMode` parameter → `CALL_MODE`) and `.env.sample`. Verify that
real-time Speech is available in the deployment region.

## 9. Testing

- Add `pytest` and `pytest-asyncio` as dev dependencies; tests in `server/tests/`.
- Unit: stereo PCMU de-interleave/decode; PCM16 de-interleave; `TranscriptEvent`
  serialization; `TranscriptHub` fan-out and overflow policy; AudioHook media-offer selection
  (stereo preferred, mono fallback); mode-based route registration.
- Handler tests with `FakeBackend`: Genesys `open` → stereo frames → events labeled correctly; no
  audio sent back to Genesys.
- Manual end-to-end: stereo Genesys simulator (mic as agent + WAV as customer) against a real Azure
  AI Speech resource; transcript view shows labeled partial and final lines.
- Regression: voice-agent mode still starts and its routes respond.

## 10. Documentation

- `README.md`: "Transcription mode (agent assist)" section — when to use, configuration, local demo.
- `docs/genesys-transcription.md`: Genesys AudioHook Monitor setup and an architecture diagram for
  explaining how live calls connect.
- `TranscriptEvent` schema documented as the downstream integration contract.

## 11. Open questions

| # | Question | Impact |
|---|----------|--------|
| Q1 | Exact Genesys call flow at the customer; is AudioHook Monitor with stereo media available? | Confirms the G4 path; mono fallback covers the gap. |
| Q2 | Regional availability and preview access for MAI Transcribe; real-time streaming and diarization support for `gpt-4o-transcribe-diarize`. | Determines the next backends; does not block this iteration. |
| Q3 | Retirement timelines of the transcription models under evaluation and their successors. | Production backend choice. |
| Q4 | Azure AI Speech accuracy on 8 kHz telephony audio for the customer's domain vocabulary (phrase lists may help). | Demo quality. |
