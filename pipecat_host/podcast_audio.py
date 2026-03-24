import io
import os
import time
import wave
from pathlib import Path
import logging

import httpx

logger = logging.getLogger(__name__)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_TTS_API_KEY", "")
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
SAMPLE_RATE = 24000  # Deepgram linear16 requires 8000/16000/24000/32000/48000

# Cartesia generic voice IDs for fallback if Deepgram fails
CARTESIA_DEFAULT_HOST_VOICE = "694f9389-aac1-45b6-b726-9d9369183238"  # Generic Male
CARTESIA_DEFAULT_GUEST_VOICE = "a0e99841-438c-4a64-b6a9-ae8f1b135182"  # Generic Female

def cartesia_tts(text: str, voice_model: str) -> bytes:
    """Synthesize speech via Cartesia API and return raw WAV bytes. Retries 3x."""
    # Try to heuristically map voice or use default
    voice_id = CARTESIA_DEFAULT_GUEST_VOICE if "asteria" in voice_model.lower() else CARTESIA_DEFAULT_HOST_VOICE
    t0 = time.time()
    logger.info("Cartesia TTS %-18s | %d chars", voice_id, len(text))
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(3):
        try:
            response = httpx.post(
                "https://api.cartesia.ai/tts/bytes",
                headers={
                    "X-API-Key": CARTESIA_API_KEY,
                    "Cartesia-Version": "2024-06-10",
                    "Content-Type": "application/json",
                },
                json={
                    "model_id": "sonic-english",
                    "transcript": text,
                    "voice": {"mode": "id", "id": voice_id},
                    "output_format": {
                        "container": "wav",
                        "encoding": "pcm_s16le",
                        "sample_rate": SAMPLE_RATE
                    }
                },
                timeout=60.0,
            )
            response.raise_for_status()
            elapsed = time.time() - t0
            logger.info("Cartesia TTS done in %.1fs (%s bytes)", elapsed, f"{len(response.content):,}")
            return response.content
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning("Cartesia TTS attempt %d/3 failed (%s), retrying in %ds...", attempt + 1, exc, wait)
                time.sleep(wait)
    raise last_exc

def generate_speech(text: str, voice_model: str) -> bytes:
    """Synthesize speech using Deepgram, with fallback to Cartesia."""
    try:
        if DEEPGRAM_API_KEY:
            return _deepgram_tts(text, voice_model)
        else:
            logger.warning("DEEPGRAM_TTS_API_KEY not set, skipping Deepgram.")
            raise ValueError("No Deepgram key")
    except Exception as e:
        logger.warning(f"Deepgram TTS failed or unavailable: {e}. Falling back to Cartesia.")
        if CARTESIA_API_KEY:
            return cartesia_tts(text, voice_model)
        else:
            logger.error("No CARTESIA_API_KEY set for fallback.")
            raise

def _deepgram_tts(text: str, voice_model: str) -> bytes:
    """Synthesize speech via Deepgram Aura API and return raw WAV bytes. Retries 3x."""
    t0 = time.time()
    logger.info("TTS %-18s | %d chars", voice_model, len(text))
    last_exc: Exception = RuntimeError("No attempts made")
    for attempt in range(3):
        try:
            response = httpx.post(
                f"https://api.deepgram.com/v1/speak?model={voice_model}&encoding=linear16&sample_rate={SAMPLE_RATE}&container=wav",
                headers={
                    "Authorization": f"Token {DEEPGRAM_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"text": text},
                timeout=60.0,
            )
            response.raise_for_status()
            elapsed = time.time() - t0
            logger.info("TTS %-18s | done in %.1fs (%s bytes)", voice_model, elapsed, f"{len(response.content):,}")
            return response.content
        except Exception as exc:
            last_exc = exc
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning("TTS attempt %d/3 failed (%s), retrying in %ds...", attempt + 1, exc, wait)
                time.sleep(wait)
    raise last_exc


def stitch_to_mp3(wav_parts: list[bytes], out_path: Path) -> Path:
    """Concatenate WAV segments (with silence gaps) and export as MP3."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from pydub import AudioSegment  # type: ignore
        silence = AudioSegment.silent(duration=600, frame_rate=SAMPLE_RATE)
        combined = AudioSegment.empty()
        for i, wav_bytes in enumerate(wav_parts):
            seg = AudioSegment.from_wav(io.BytesIO(wav_bytes))
            combined += seg
            if i < len(wav_parts) - 1:
                combined += silence
        combined.export(str(out_path), format="mp3", bitrate="128k")
        logger.info(f"Episode saved: {out_path}")
        return out_path
    except ImportError:
        logger.warning("pydub not found, saving as .wav")
        out_path = out_path.with_suffix(".wav")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(SAMPLE_RATE)
            for wav_bytes in wav_parts:
                with wave.open(io.BytesIO(wav_bytes)) as src:
                    wf.writeframes(src.readframes(src.getnframes()))
        out_path.write_bytes(buf.getvalue())
        logger.info(f"Episode saved (wav fallback): {out_path}")
        return out_path
