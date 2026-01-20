# Ambient Audio Files

Audio files for the **Ambient Scenes** feature. These are mixed with TTS output to simulate real-world call environments.

## Usage

Set `AMBIENT_PRESET` in your `.env` file to enable:
```
AMBIENT_PRESET=call_center
```

Options: `none` (disabled), `office`, `call_center`

## Audio Requirements

- **Format:** WAV (uncompressed PCM)
- **Sample Rate:** 24000 Hz (must match Voice Live API)
- **Bit Depth:** 16-bit signed
- **Channels:** Mono
- **Duration:** 30-60 seconds (loops seamlessly)

## Included Files

| File | Preset Name | Description |
|------|-------------|-------------|
| `office.wav` | `office` | Quiet office ambient |
| `callcenter.wav` | `call_center` | Busy call center background |

## Adding Custom Audio

1. Prepare your audio file matching the requirements above
2. Add it to this directory
3. Update `PRESETS` in `handler/ambient_mixer.py`
