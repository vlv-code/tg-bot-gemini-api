import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from audio import convert_gemini_audio, pcm_to_wav


class TestAudioConversion(unittest.IsolatedAsyncioTestCase):
    def test_pcm_to_wav_preserves_riff(self):
        wav_header = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
        result = pcm_to_wav(wav_header)
        self.assertEqual(result, wav_header)

    def test_pcm_to_wav_wraps_raw_pcm(self):
        raw_pcm = b"\x00\x00" * 100
        result = pcm_to_wav(raw_pcm, sample_rate=24000, channels=1)
        self.assertTrue(result.startswith(b"RIFF"))
        self.assertIn(b"WAVE", result)

    @patch("shutil.which", return_value="/usr/bin/ffmpeg")
    @patch("asyncio.create_subprocess_exec")
    async def test_convert_gemini_audio_does_not_force_s16le_on_wav(self, mock_exec, mock_which):
        """Проверяем, что ffmpeg вызывается без '-f s16le', когда передаётся WAV/PCM,
        чтобы заголовок RIFF не декодировался как сэмплы звука (устранение треска/помех)."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"OGG_OPUS_DATA", b""))
        mock_exec.return_value = mock_proc

        wav_bytes = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\xc0]\x00\x00\x80\xbb\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        out_bytes, filename, mime = await convert_gemini_audio(wav_bytes)

        self.assertEqual(out_bytes, b"OGG_OPUS_DATA")
        self.assertEqual(filename, "voice.ogg")
        self.assertEqual(mime, "audio/ogg")

        call_args = mock_exec.call_args[0]
        self.assertNotIn("-f", call_args[:call_args.index("-i")], "Флаг '-f' не должен принудительно задавать входной формат s16le для WAV-данных")
        self.assertNotIn("s16le", call_args, "Входной формат 's16le' не должен передаваться для WAV-файлов")


if __name__ == "__main__":
    unittest.main()
