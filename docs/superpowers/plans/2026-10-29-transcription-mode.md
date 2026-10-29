# Transcription Mode Implementation Plan

> **For agentic workers:** Implement task-by-task with TDD (red → green → commit).

**Goal:** Add `CALL_MODE=transcription` that streams speaker-labeled (customer/agent) live transcripts to a web UI, from Genesys AudioHook (stereo) or a browser harness, keeping the existing voice agent as default.

**Architecture:** New `server/app/transcription/` package (events, audio utils, hub, pluggable backend, session, web handler, routes). Genesys AudioHook protocol extracted into a shared `audiohook.py` used by both the voice-agent and new transcription handler. Azure AI Speech backend runs one recognizer per speaker channel.

**Tech stack:** Python 3.12, Quart, azure-cognitiveservices-speech, pytest + pytest-asyncio, vanilla JS static pages, Bicep.

Spec: `docs/superpowers/specs/2026-10-29-transcription-mode-design.md`

## Tasks

1. **Test infra.** `uv add --dev pytest pytest-asyncio`; `[tool.pytest.ini_options] pythonpath=["."]`, `asyncio_mode="auto"`, `testpaths=["tests"]`. Verify: `cd server; uv run pytest -q`.
2. **Events + audio utils.** `app/transcription/events.py` (`Speaker`, `StreamState`, `TranscriptEvent`, `StatusEvent`, camelCase `to_dict`), `audio.py` (`deinterleave`, `pcmu_to_pcm16`). Tests: `tests/test_events.py`, `tests/test_audio.py`.
3. **TranscriptHub.** Pub/sub per conversation, bounded queues dropping oldest partial first, active-conversation registry, `get_hub()`. Tests: `tests/test_transcript_hub.py`.
4. **Backend protocol, FakeBackend, factory, TranscriptionSession.** Tests: `tests/test_backend.py`, `tests/test_session.py`.
5. **AudioHook extraction.** Regression tests for `GenesysMediaHandler` first, then extract `providers/genesys/audiohook.py` (`AudioHookSession`, `select_media`, `validate_api_key`) and refactor. Tests: `tests/test_audiohook.py`, `tests/test_genesys_media_handler.py`.
6. **GenesysTranscriptionHandler.** Stereo preferred `["external","internal"]`; external→customer, internal→agent; mono fallback labeled customer. Never sends audio back. Tests: `tests/test_genesys_transcription_handler.py`.
7. **Call loop generalization.** `run_call_loop` calls `handler.start()`; `VoiceLiveMediaHandler.start()` → `connect_voicelive()`.
8. **AzureSpeechBackend.** `uv add azure-cognitiveservices-speech`; endpoint + key or managed identity (`token_credential`); per-speaker PushAudioInputStream; callbacks marshalled to loop; restart on error with backoff (max 3). Tests with fake recognizer factory: `tests/test_azure_speech_backend.py`.
9. **WebTranscriptionHandler + routes.** Stereo PCM16 16 kHz (L=customer, R=agent). Routes: `/transcript`, `/transcribe-test`, `/transcript/active`, `/transcript/ws`, `/web/transcribe/ws`. Tests: `tests/test_web_handler.py`, `tests/test_routes.py`.
10. **Mode-aware server + validator.** `CALL_MODE`, `TRANSCRIPTION_BACKEND` (`azure_speech`|`fake`), `TRANSCRIPTION_LANGUAGE`; fake backend skips Azure checks; Genesys provider picks handler by mode; `/health` reports mode. Tests: `tests/test_config_validator.py`.
11. **Static UI.** `static/transcript.html`, `static/transcribe-test.html`; Genesys simulator stereo option (file = customer, mic = agent).
12. **Infra + container.** `callMode` param → `CALL_MODE` env; `libasound2` in Dockerfile; `.env.sample`. Verify `docker build --build-arg TELEPHONY=genesys`.
13. **Docs.** README section + `docs/genesys-transcription.md`.
14. **E2E verification.** Full test suite; run locally with fake backend and check pages; voice-agent mode unchanged.
