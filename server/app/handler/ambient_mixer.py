"""Ambient Scenes Mixer - DSP-based background audio mixing for phone calls."""

import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Audio format constants (matching ACS/Voice Live)
SAMPLE_RATE = 24000  # 24kHz
BYTES_PER_SAMPLE = 2  # 16-bit PCM
CHANNELS = 1  # Mono


class AmbientMixer:
    """Mixes ambient background noise with TTS audio for phone calls."""

    PRESETS = {
        "none": {"file": None},
        "office": {"file": "office.wav"},
        "call_center": {"file": "callcenter.wav"},
    }

    def __init__(self, preset: str = "office"):
        """
        Initialize the ambient mixer.

        Args:
            preset: One of 'none', 'office', 'call_center' (or add custom presets)
        """
        if preset not in self.PRESETS:
            raise ValueError(f"Unknown preset: {preset}. Choose from {list(self.PRESETS.keys())}")

        self.preset = preset
        
        # Load noise buffer (None for 'none' preset)
        if preset != "none" and self.PRESETS[preset]["file"]:
            self._noise_buffer = self._load_noise(preset)
        else:
            self._noise_buffer = None
        self._noise_position = 0
        
        # Fixed ambient gain - used for both TTS mixing and ambient-only
        # This ensures consistent ambient volume at all times
        # Lower value = quieter ambient. Range: 0.05 (very quiet) to 0.3 (noticeable)
        self._ambient_gain = 0.08  # Consistent low ambient level
        
        logger.info(f"AmbientMixer initialized: preset={preset}, ambient_gain={self._ambient_gain}")

    def _load_noise(self, preset: str) -> np.ndarray:
        """Load ambient audio file as float32 numpy array."""
        audio_dir = Path(__file__).parent.parent / "audio"
        audio_path = audio_dir / self.PRESETS[preset]["file"]
        
        if not audio_path.exists():
            logger.warning(f"Audio file not found: {audio_path}, using synthetic noise")
            return self._generate_synthetic_noise()
        
        try:
            import wave
            
            with wave.open(str(audio_path), 'rb') as wav:
                n_channels = wav.getnchannels()
                sampwidth = wav.getsampwidth()
                framerate = wav.getframerate()
                n_frames = wav.getnframes()
                
                # Read raw bytes
                raw_data = wav.readframes(n_frames)
            
            # Convert to numpy array
            if sampwidth == 2:
                audio = np.frombuffer(raw_data, dtype=np.int16).astype(np.float32) / 32768.0
            elif sampwidth == 1:
                audio = (np.frombuffer(raw_data, dtype=np.uint8).astype(np.float32) - 128) / 128.0
            else:
                raise ValueError(f"Unsupported sample width: {sampwidth}")
            
            # Convert stereo to mono if needed
            if n_channels == 2:
                audio = audio.reshape(-1, 2).mean(axis=1)
            
            # Resample if needed
            if framerate != SAMPLE_RATE:
                ratio = SAMPLE_RATE / framerate
                new_length = int(len(audio) * ratio)
                audio = np.interp(
                    np.linspace(0, len(audio), new_length),
                    np.arange(len(audio)),
                    audio
                )
            
            # Normalize to -20dB RMS
            rms = np.sqrt(np.mean(audio**2))
            target_rms = 10 ** (-20 / 20)  # -20dB
            if rms > 1e-10:
                audio = audio * (target_rms / rms)
            
            logger.info(f"Loaded ambient audio: {audio_path} ({len(audio)/SAMPLE_RATE:.1f}s)")
            return audio.astype(np.float32)
            
        except Exception as e:
            logger.error(f"Failed to load {audio_path}: {e}, using synthetic noise")
            return self._generate_synthetic_noise()

    def _generate_synthetic_noise(self, duration_sec: float = 30.0) -> np.ndarray:
        """Generate synthetic brown noise as fallback."""
        num_samples = int(SAMPLE_RATE * duration_sec)
        rng = np.random.default_rng(seed=42)
        
        # Brown noise (more natural sounding than white noise)
        noise = rng.standard_normal(num_samples).astype(np.float32)
        for i in range(1, len(noise)):
            noise[i] = 0.98 * noise[i - 1] + 0.02 * noise[i]
        
        # Normalize
        noise = noise / (np.max(np.abs(noise)) + 1e-10) * 0.1
        return noise

    def _get_noise_chunk(self, num_samples: int) -> np.ndarray:
        """Get next chunk of noise, looping seamlessly."""
        if self._noise_buffer is None:
            return np.zeros(num_samples, dtype=np.float32)
            
        chunk = np.zeros(num_samples, dtype=np.float32)
        remaining = num_samples
        offset = 0

        while remaining > 0:
            available = len(self._noise_buffer) - self._noise_position
            to_copy = min(available, remaining)
            chunk[offset:offset + to_copy] = self._noise_buffer[
                self._noise_position:self._noise_position + to_copy
            ]
            self._noise_position += to_copy
            offset += to_copy
            remaining -= to_copy

            # Loop back to start
            if self._noise_position >= len(self._noise_buffer):
                self._noise_position = 0

        return chunk

    def _soft_clip(self, audio: np.ndarray, threshold: float = 0.95) -> np.ndarray:
        """Apply soft clipping using tanh to prevent harsh distortion."""
        return np.tanh(audio / threshold) * threshold

    def is_enabled(self) -> bool:
        """Check if ambient mixing is enabled (preset != 'none')."""
        return self.preset != "none" and self._noise_buffer is not None

    def get_ambient_only_chunk(self, chunk_size_bytes: int) -> bytes:
        """
        Get ambient-only audio chunk (for when no TTS is playing).
        
        Args:
            chunk_size_bytes: Size of output chunk in bytes
            
        Returns:
            PCM 16-bit mono audio bytes
        """
        if not self.is_enabled():
            # Return silence if ambient is disabled
            return b'\x00' * chunk_size_bytes
            
        num_samples = chunk_size_bytes // BYTES_PER_SAMPLE
        noise = self._get_noise_chunk(num_samples)
        
        # Apply fixed ambient gain (same as during TTS mixing)
        output = noise * self._ambient_gain
        
        # Soft clip and convert to int16
        output = self._soft_clip(output)
        return (output * 32767).astype(np.int16).tobytes()

    def mix_tts_with_ambient(self, tts_bytes: bytes) -> bytes:
        """
        Mix TTS audio with ambient noise at a fixed ambient level.
        
        Args:
            tts_bytes: PCM 16-bit mono TTS audio bytes
            
        Returns:
            Mixed PCM 16-bit mono audio bytes
        """
        if not self.is_enabled():
            return tts_bytes  # Return original TTS if ambient disabled
            
        # Convert TTS bytes to float32
        tts = np.frombuffer(tts_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        
        # Get matching noise chunk
        noise = self._get_noise_chunk(len(tts))
        
        # Apply fixed ambient gain (consistent volume)
        scaled_noise = noise * self._ambient_gain
        
        # Mix TTS with ambient
        mixed = tts + scaled_noise
        
        # Soft clip to prevent distortion
        mixed = self._soft_clip(mixed)
        
        # Convert back to int16 bytes
        return (mixed * 32767).astype(np.int16).tobytes()

    def reset(self) -> None:
        """Reset noise position to start (for new calls)."""
        self._noise_position = 0
