# Transcription mode demo samples

Sample audio and scripts for demoing and testing `CALL_MODE=transcription` locally, without a
Genesys account. See [docs/genesys-transcription.md](../../../docs/genesys-transcription.md) for the
full design.

| File | Purpose |
|------|---------|
| `customer.wav` | Customer side of a short billing call (16 kHz mono, synthetic voice) |
| `agent.wav` | Agent reply, starting after a silence so it follows the customer |
| `generate_sample_audio.py` | Regenerates both WAVs with Azure AI Speech text-to-speech |
| `e2e_live.py` | Streams the call through `/audiohook/ws` and `/web/transcribe/ws` and checks the transcript (no browser) |
| `e2e_browser.js` | Drives the real `/genesys` simulator page in headless Edge (fake mic) and checks `/transcript` |

## 1. Start the server

From `server/`, after signing in with `az login` (your identity needs **Cognitive Services User** on the resource):

```powershell
$env:CALL_MODE = "transcription"
$env:TRANSCRIPTION_BACKEND = "azure_speech"          # or "fake" for an offline UI check
$env:AZURE_SPEECH_ENDPOINT = "https://<your-ai-services>.cognitiveservices.azure.com/"
$env:GENESYS_API_KEY = "test"
.venv\Scripts\python server.py
```

## 2. Manual demo (Genesys simulator)

1. Open http://localhost:8000/genesys in Edge or Chrome.
2. Set **API Key** to `test`, **Channels** to *Stereo — external + internal*, and choose
   `customer.wav` (or any customer recording) as the **Customer audio file**.
3. Put on headphones, click **Connect & Start Call**, and allow the microphone.
4. Open the **Live transcript** link from the protocol log (or http://localhost:8000/transcript).
   The file appears as **Customer** and your voice appears as **Agent**.
5. Click **End Call**. The transcript view shows **Call ended**.

For a simpler two-channel harness, use http://localhost:8000/transcribe-test (file = customer, mic = agent).

## 3. Automated checks

```powershell
# Protocol-level (Python, no browser)
.venv\Scripts\python samples\transcription\e2e_live.py

# Real simulator page in headless Edge (requires Node + Playwright)
$env:PW_PATH = "$(npm root -g)\playwright"
node samples\transcription\e2e_browser.js
```

Both scripts exit with code 0 and print `PASS` when customer and agent lines are transcribed and the call ends cleanly.
