"""Generate the sample customer/agent WAV files with Azure AI Speech text-to-speech.

Usage (from server/):
    az login
    .venv\\Scripts\\python samples\\transcription\\generate_sample_audio.py https://<your-ai-services>.cognitiveservices.azure.com/

Writes 16 kHz mono WAVs next to this script:
    customer.wav  the customer side of the call; upload it in /genesys (Stereo) or /transcribe-test
    agent.wav     the agent's reply, preceded by silence so it starts after the customer finishes;
                  used as the fake microphone in e2e_browser.js
"""

import sys
import wave
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
from azure.identity import DefaultAzureCredential

HERE = Path(__file__).resolve().parent
RATE = 16000

CUSTOMER_TEXT = (
    "Hi, I'm calling about my invoice. I was charged twice for the same order last month "
    "and I'd like a refund for the duplicate charge."
)
AGENT_TEXT = (
    "I'm sorry about that. I can see the duplicate charge on your account. "
    "I've issued a refund and you should see it within five business days."
)


def synthesize(endpoint: str, text: str, voice: str) -> bytes:
    config = speechsdk.SpeechConfig(endpoint=endpoint, token_credential=DefaultAzureCredential())
    config.speech_synthesis_voice_name = voice
    config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm)
    result = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None).speak_text_async(text).get()
    if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
        raise RuntimeError(f"Text-to-speech failed: {result.cancellation_details.error_details}")
    return result.audio_data


def silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(RATE * seconds)


def save(name: str, pcm: bytes) -> None:
    with wave.open(str(HERE / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    print(f"wrote {name} ({len(pcm) / (2 * RATE):.1f}s)")


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    endpoint = sys.argv[1]
    customer = synthesize(endpoint, CUSTOMER_TEXT, "en-US-JennyNeural")
    agent = synthesize(endpoint, AGENT_TEXT, "en-US-GuyNeural")
    customer_seconds = len(customer) / (2 * RATE)
    save("customer.wav", silence(0.5) + customer + silence(1.0))
    save("agent.wav", silence(0.5 + customer_seconds + 1.5) + agent + silence(2.0))


if __name__ == "__main__":
    main()
