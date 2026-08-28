"""Обработка и конвертация аудиопотока Gemini API для Telegram.

Gemini API при response_modalities=["AUDIO"] возвращает сырой 16-битный PCM
с частотой дискретизации 24 кГц (24000 Hz, 1 channel, 16-bit LE).
Для Telegram Voice необходим контейнер OGG с кодеком Opus, либо стандартный WAV.
"""

import io
import logging
import shutil
import subprocess
import wave

logger = logging.getLogger(__name__)


def pcm_to_wav(
    pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2
) -> bytes:
    """Оборачивает сырой PCM в валидный WAV-файл с заголовком RIFF."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def convert_gemini_audio(
    pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1
) -> tuple[bytes, str, str]:
    """Конвертирует аудио Gemini в OGG Opus (если доступен ffmpeg) или WAV.

    Возвращает:
        (audio_bytes, filename, mime_type)
    """
    if shutil.which("ffmpeg"):
        try:
            # Кодируем PCM s16le в валидный ogg opus
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "s16le",
                    "-ar",
                    str(sample_rate),
                    "-ac",
                    str(channels),
                    "-i",
                    "pipe:0",
                    "-c:a",
                    "libopus",
                    "-b:a",
                    "48k",
                    "-vbr",
                    "on",
                    "-f",
                    "ogg",
                    "pipe:1",
                ],
                input=pcm_bytes,
                capture_output=True,
                check=True,
            )
            return proc.stdout, "voice.ogg", "audio/ogg"
        except Exception:
            logger.exception("ffmpeg encoding failed, falling back to WAV")

    # Fallback на валидный WAV файл
    wav_bytes = pcm_to_wav(pcm_bytes, sample_rate=sample_rate, channels=channels)
    return wav_bytes, "voice.wav", "audio/wav"
