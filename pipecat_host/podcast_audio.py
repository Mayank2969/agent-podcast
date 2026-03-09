import io
import os
import time
import wave
from pathlib import Path
import logging

import httpx

logger = logging.getLogger(__name__)

DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_TTS_API_KEY", "")
SAMPLE_RATE = 24000  # Deepgram linear16 requires 8000/16000/24000/32000/48000


def deepgram_tts(text: str, voice_model: str) -> bytes:
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
