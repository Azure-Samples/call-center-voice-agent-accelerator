# Twilio Real-time Voice

Modern contact center starter that couples Twilio telephony with the Azure Voice Live API for end-to-end, low-latency speech intelligence. Use it as a baseline to experiment locally, stream twilio phone calls in real time, and later deploy to Azure Container Apps with managed identities and telemetry.

---

## Project Outline

- **Goal** – Demonstrate how to ingest live audio from Twilio Media Streams, feed it to Azure Voice Live for ASR/LLM/TTS, and surface responses back to the caller or a browser.
- **Status** – Developer-ready baseline; infrastructure and docs target a single container app deployment, with environment management handled by `azd` and Bicep.
- **Audience** – Developers building conversational or voice automation prototypes who need quick alignment between telephony, AI inference, and cloud deployment.


## Current Capabilities

- **Real-time phone ingest** – `/twilio/stream` WebSocket converts μ-law frames from Twilio Media Streams into 24 kHz PCM and relays them to Voice Live.
- **Browser test client** – `/web/ws` endpoint plus the static demo page for microphone capture and synthesized speech playback.
- **Voice Live session orchestration** – Async handler manages session lifecycle, streaming audio, transcript callbacks, and binary TTS responses.
- **Azure deployment scaffold** – Bicep modules provision a resource group, Container App, Container Registry, AI Services account, managed identity, Log Analytics, and Application Insights.
- **Secrets & identity** – Supports either Voice Live API keys or a user-assigned managed identity; Key Vault provisioning is ready for future secret storage.
- **Developer bootstrap** – Dev container, Codespaces support, and `uv` dependency management streamline local setup.


## Architecture Snapshot

```
Twilio PSTN Call -> Twilio Media Stream (μ-law) -> Quart /twilio/stream
      -> VoiceLiveStreamingHandler -> Azure Voice Live API (ASR + LLM + TTS)
      <- Transcripts + TTS (PCM/base64) <- VoiceLiveStreamingHandler
Browser Client -> Quart /web/ws -> same Voice Live session (optional loopback)
```


## Key Components

| Area | Path | Purpose |
| --- | --- | --- |
| Web server | `server/server.py` | Quart app exposing `/web/ws`, `/twilio/stream`, and static UI. |
| Voice Live bridge | `server/app/handler/voice_live_handler.py` | Manages Voice Live WebSocket, queues audio, handles transcripts & TTS callbacks. |
| Twilio ingest | `server/app/handler/twilio_handler.py` | Parses Media Stream events, converts μ-law audio, echoes transcripts back to Twilio. |
| Front-end demo | `server/static/` | Simple browser client for mic capture and audio playback. |
| Infra as code | `infra/` | Bicep templates for Azure Container Apps deployment plus optional monitoring resources. |
| Project config | `azure.yaml`, `.devcontainer/` | azd environment definition and dev container tooling. |


## Quickstart (Local)

1. Install prerequisites: Python 3.11+, `uv`, Node-compatible browser, Twilio account, Azure subscription.
2. Copy `server/.env-sample.txt` to `.env` and set `AZURE_VOICE_LIVE_ENDPOINT`, `VOICE_LIVE_MODEL`, and either `AZURE_VOICE_LIVE_API_KEY` or `AZURE_USER_ASSIGNED_IDENTITY_CLIENT_ID`.
3. From `server/`, run `uv sync && uv run server.py`.
4. Open `http://127.0.0.1:8000` for the browser client or connect Twilio Media Streams to `wss://<host>/twilio/stream`.


## Deploying to Azure

```bash
azd auth login
azd up
```

The Bicep templates create a new resource group per environment (`rg-trv-<env>-<hash>`) along with all supporting services. Outputs (Container App URL, Voice Live endpoint, etc.) are stored in `.azure/<env>/.env` for reuse. Redeploy with `azd deploy` after code changes, and tear down everything with `azd down`.


## Project Layout

```
infra/                 # Bicep templates for RG, identity, Voice Live, container app, monitoring
server/                # Quart application and handlers
  app/handler/         # Voice Live and Twilio modules
  static/              # Test UI assets
  README.md            # Local run & Twilio configuration notes
.devcontainer/         # Codespaces / Dev Container setup
azure.yaml             # azd service definition
docs/                  # Supplemental docs and imagery
```


## Roadmap & Gaps

- Validation is limited to development scenarios; no production hardening (auth, scaling policies, retry logic) has been implemented.
- Voice Live session parameters are static—expose configuration or prompt tuning as follow-up work.
- Key Vault is provisioned but not yet wired to store Twilio secrets automatically.
- Automated tests are pending; only `compileall` is used for quick syntax checks today.


## Resources

- [Azure Voice Live API](https://learn.microsoft.com/azure/ai-services/speech-service/voice-live)
- [Twilio Media Streams](https://www.twilio.com/docs/voice/twilio-media-streams)
- [Azure Container Apps](https://learn.microsoft.com/azure/container-apps/)
- [Azure Developer CLI (`azd`)](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview)


## Security & Compliance Notes

- Store Twilio credentials in a secure location such as Azure Key Vault; never commit secrets.
- Review Voice Live responsible AI guidance and ensure consent is handled appropriately.
- Avoid using this template for safety-critical or high-risk workloads without additional safeguards.


## Legal

This repository may reference Microsoft services and other third-party offerings. Your use remains subject to their respective product terms, export regulations, and trademark guidelines. No telemetry is added by default; refer to service-specific documentation for any data collection behavior.
