import asyncio
import io
import logging
import shutil
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


async def convert_gemini_audio(
    pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1
) -> tuple[bytes, str, str]:
    """Асинхронно конвертирует аудио Gemini в OGG Opus через ffmpeg без блокировки event loop.

    Возвращает:
        (audio_bytes, filename, mime_type)
    """
    if shutil.which("ffmpeg"):
        try:
            # Асинхронно запускаем ffmpeg без блокировки основного потока
            proc = await asyncio.create_subprocess_exec(
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
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate(input=pcm_bytes)
            if proc.returncode == 0 and stdout:
                return stdout, "voice.ogg", "audio/ogg"
            logger.warning("ffmpeg завершился с кодом %s, переключаемся на WAV", proc.returncode)
        except Exception:
            logger.exception("Асинхронное кодирование ffmpeg не удалось, fallback на WAV")

    # Fallback на валидный WAV файл
    wav_bytes = pcm_to_wav(pcm_bytes, sample_rate=sample_rate, channels=channels)
    return wav_bytes, "voice.wav", "audio/wav"
