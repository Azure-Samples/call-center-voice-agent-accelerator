# Static Audio Files

This directory is for serving audio files to web clients. 

> **Note:** The Ambient Scenes feature uses backend mixing from `server/app/audio/`.
> See the main [README.md](../../../README.md#-ambient-scenes) for usage instructions.

## Audio Files

Audio files in this directory are served at `/static/audio/<filename>`.

| File | Description |
|------|-------------|
| `office.wav` | Office ambient audio |
| `callcenter.wav` | Call center ambient audio |

## Adding Files

Place WAV files here if you need to serve audio directly to web browsers.
For ambient scene mixing, add files to `server/app/audio/` instead.
