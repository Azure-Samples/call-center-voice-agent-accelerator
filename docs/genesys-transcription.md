# Transcription Mode (Agent Assist) with Genesys Cloud

Transcription mode turns the accelerator from a **voice agent** (AI answers the call) into a
**live transcription service** for calls between a human customer and a human agent. The server
listens only; it never sends audio back. Each utterance is labeled **customer** or **agent**
and streamed to a browser view and to any downstream consumer of the transcript event stream
(for example an agent-assist model).

Enable it with `CALL_MODE=transcription`. The default, `voice_agent`, keeps the original behavior.
For a file-by-file list of what changed from the base accelerator, see
[transcription-mode-changes.md](transcription-mode-changes.md).

## Architecture

```
                         ┌───────────────────────────── Container App (CALL_MODE=transcription) ─────────────────────────────┐
Customer ⇄ Genesys ⇄ Agent│                                                                                                    │
            │             │  /audiohook/ws ── GenesysTranscriptionHandler ─┐                                                  │
            │ AudioHook   │   (stereo PCMU 8 kHz: external=customer,       │                                                  │
            └────────────►│    internal=agent)                             ├─► TranscriptionSession ─► TranscriptionBackend    │
                          │                                                │     (per speaker)         (Azure AI Speech /     │
Browser harness ─────────►│  /web/transcribe/ws ── WebTranscriptionHandler ┘                            fake)                 │
 (file = customer,        │   (stereo PCM16 16 kHz: left=customer, right=agent)                              │                │
  mic = agent)            │                                                                                  ▼                │
                          │                                     TranscriptHub (per-conversation pub/sub) ◄── TranscriptEvent  │
                          │                                                │                                                  │
Agent's browser ◄─────────│  /transcript (page) + /transcript/ws ◄─────────┘                                                  │
                          └────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **One recognizer per speaker.** Each channel is transcribed independently, so speaker labels come
  from the audio channel rather than from diarization guesses.
- **Pluggable backend.** `TRANSCRIPTION_BACKEND=azure_speech` uses Azure AI Speech real-time STT
  with partial and final results. `fake` emits placeholder lines with no Azure dependency, for UI
  demos and tests. Other engines (for example GPT-4o transcribe or MAI models) can be added by
  implementing `TranscriptionBackend` in `server/app/transcription/backend.py`.
- **Resilience.** If the STT stream errors, the backend reports `degraded`, retries with backoff,
  and returns to `connected`. The call is never dropped because of transcription errors.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CALL_MODE` | `voice_agent` | Set to `transcription` to enable this mode |
| `TRANSCRIPTION_BACKEND` | `azure_speech` | `azure_speech` or `fake` |
| `TRANSCRIPTION_LANGUAGE` | `en-US` | Recognition locale |
| `AZURE_SPEECH_ENDPOINT` | `AZURE_VOICE_LIVE_ENDPOINT` | AI Services / Speech endpoint |
| `AZURE_SPEECH_KEY` | `AZURE_VOICE_LIVE_API_KEY` | Optional key. Without a key, managed identity (deployed) or `DefaultAzureCredential` (local `az login`) is used; the identity needs **Cognitive Services User** |
| `GENESYS_API_KEY` | — | Shared secret for AudioHook connections (enables `/audiohook/ws`) |
| `WEB_ACCESS_TOKEN` | — | Optional token for `/transcript`, `/transcript/ws`, `/transcript/active` and `/web/transcribe/ws` (`?token=`) |

To deploy with `azd`, run `azd env set CALL_MODE transcription` before `azd up` or `azd provision`.

## Endpoints

| Path | Purpose |
|------|---------|
| `/transcript` | Agent transcript view. Pick an active conversation, or pass `?conversationId=` |
| `/transcribe-test` | Browser harness: an audio file plays as the customer and your mic is the agent |
| `/genesys` | Genesys AudioHook simulator. Choose **Stereo** to test transcription end to end |
| `/audiohook/ws` | Genesys AudioHook WebSocket |
| `/transcript/ws?conversationId=` | Transcript event stream (JSON) |
| `/transcript/active` | Active conversation IDs |
| `/web/transcribe/ws` | Browser harness audio ingest |

In transcription mode, `/` redirects to `/transcribe-test`, and the voice-agent web client (`/web/ws`) is not registered.

## Testing without a Genesys account

1. **Offline UI check:** run `CALL_MODE=transcription TRANSCRIPTION_BACKEND=fake` and open
   `/transcribe-test`. Placeholder lines appear for each speaker, which confirms the plumbing.
2. **Real transcription:** switch to `TRANSCRIPTION_BACKEND=azure_speech`, choose a recorded
   customer audio file on `/transcribe-test`, and speak as the agent.
3. **AudioHook protocol:** set `GENESYS_API_KEY` and open `/genesys`. Select **Stereo**, choose a
   customer audio file, and connect. The simulator sends interleaved stereo PCMU exactly as Genesys
   AudioHook Monitor does. Open the logged `/transcript?conversationId=…` link to watch.
4. **Sample audio and automated checks:** see
   [`server/samples/transcription/`](../server/samples/transcription/README.md) for ready-made
   customer/agent WAVs and scripts that verify both ingest paths end to end.

## Connecting Genesys Cloud

Transcription mode uses the **AudioHook Monitor** integration, which forks call audio to an
external service without affecting the call. The voice-agent mode uses the Audio Connector
instead.

1. In Genesys Cloud Admin, install **AudioHook Monitor** and create an integration.
2. Set the **Connection URI** to `wss://<your-container-app-url>/audiohook/ws` and the **API Key**
   to the value of `GENESYS_API_KEY`.
3. Enable AudioHook for the queues or flows whose calls you want to transcribe.
4. When a call connects, Genesys opens the WebSocket and offers stereo media. The server
   selects `["external", "internal"]`:

   | AudioHook channel | Speaker |
   |-------------------|---------|
   | `external` | customer |
   | `internal` | agent |

5. The agent opens `https://<your-container-app-url>/transcript` (plus `?token=` if
   `WEB_ACCESS_TOKEN` is set) and selects the conversation. The Genesys conversation ID is
   used as the transcript `conversationId`.

If Genesys offers only mono (`external`), the call is still transcribed and labeled
`customer`, and the server logs a warning.

Confirm with your Genesys administrator that AudioHook Monitor is licensed and that stereo
(external + internal) audio is enabled for the target queues. Production deployments should
also embed the transcript in the agent desktop (for example a Genesys interaction widget pointing
at `/transcript?conversationId={{conversationId}}`) and feed `/transcript/ws` events to the
downstream assist model.

## Transcript event schema

Messages on `/transcript/ws` are JSON:

```json
{"type": "transcript", "conversationId": "c-123", "speaker": "customer",
 "text": "I'd like to check my order", "isFinal": true,
 "offsetMs": 4200, "durationMs": 1800, "seq": 7}
```

```json
{"type": "status", "conversationId": "c-123", "state": "connected", "detail": ""}
```

- `speaker`: `customer` | `agent` | `unknown`.
- `isFinal`: `false` for an in-progress hypothesis that may change, `true` for a stable result. Partials for the same speaker are replaced by the next partial or final.
- `offsetMs` / `durationMs`: position within that speaker's audio stream.
- `seq`: increases per conversation.
- `state`: `connected` | `degraded` | `ended`.

Subscribers receive events from the moment they connect; there is no history replay.

## Limitations

- Only Genesys is supported as a telephony source in transcription mode. Other providers are
  skipped with a warning.
- Telephony audio is 8 kHz narrowband. Accuracy is lower than on wideband audio, so validate with
  representative recordings.
- Transcripts are kept in memory only; nothing is persisted. Scale-out across replicas needs a
  shared pub/sub layer (for example Azure Web PubSub) in place of the in-process `TranscriptHub`.
