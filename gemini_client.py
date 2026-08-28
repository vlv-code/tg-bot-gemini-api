"""Тонкая обёртка над google-genai с поддержкой мультимодальности и TTS.

Каждый вызов ask() создаёт новую chat-сессию с историей, переданной
вызывающим кодом (хранится в storage.py).
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Union

from google import genai
from google.genai import types

try:
    # структура ошибок SDK может отличаться между версиями,
    # поэтому подстраховываемся и ниже проверяем несколько атрибутов
    from google.genai import errors as genai_errors
except ImportError:  # pragma: no cover
    genai_errors = None

from storage import Turn


class GeminiError(Exception):
    """Дружелюбная ошибка для показа пользователю в Telegram."""


@dataclass
class GeminiResponse:
    text: str
    audio_bytes: Optional[bytes] = None
    audio_mime_type: Optional[str] = None


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        default_system_prompt: str = "",
        default_voice: str = "Aoede",
        default_tts_model: str = "gemini-3.1-flash-tts",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self.default_system_prompt = default_system_prompt
        self.default_voice = default_voice
        self.default_tts_model = default_tts_model


    @staticmethod
    def _build_history(turns: list[Turn]) -> list[types.Content]:
        history = []
        for turn in turns:
            role = "model" if turn.role == "model" else "user"
            history.append(types.Content(role=role, parts=[types.Part.from_text(text=turn.text)]))
        return history

    async def ask(
        self,
        model: str,
        history_turns: list[Turn],
        message: Union[str, Sequence[Union[str, types.Part]]],
        system_prompt: Optional[str] = None,
        want_audio: bool = False,
        voice_name: Optional[str] = None,
    ) -> GeminiResponse:
        """Отправляет запрос (текст или мультимедиа) в Gemini и возвращает ответ."""
        history = self._build_history(history_turns)
        effective_prompt = (
            system_prompt
            if system_prompt is not None and system_prompt != ""
            else self.default_system_prompt
        )
        effective_voice = voice_name or self.default_voice

        config_kwargs = {}
        if effective_prompt:
            config_kwargs["system_instruction"] = effective_prompt

        if want_audio:
            # Для аудио-ответа запрашиваем AUDIO модальность
            config_kwargs["response_modalities"] = ["AUDIO"]
            config_kwargs["speech_config"] = types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=effective_voice
                    )
                )
            )

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        # Формируем тело сообщения (parts)
        if isinstance(message, str):
            content_input = message
        elif isinstance(message, (list, tuple)):
            parts = []
            for item in message:
                if isinstance(item, str):
                    parts.append(types.Part.from_text(text=item))
                elif isinstance(item, types.Part):
                    parts.append(item)
                else:
                    raise ValueError(f"Неподдерживаемый тип части сообщения: {type(item)}")
            content_input = parts
        else:
            content_input = message

        chat = self._client.aio.chats.create(model=model, config=config, history=history)

        try:
            response = await chat.send_message(content_input)
        except Exception as exc:  # noqa: BLE001 — ловим всё от SDK
            raise GeminiError(self._friendly_message(exc)) from exc

        text = getattr(response, "text", "") or ""
        audio_bytes = None
        audio_mime_type = None

        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if getattr(part, "text", None) and not text:
                        text = part.text
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data and getattr(inline_data, "data", None):
                        audio_bytes = inline_data.data
                        audio_mime_type = getattr(inline_data, "mime_type", "audio/wav")

        if not text and not audio_bytes:
            raise GeminiError(
                "Gemini вернул пустой ответ (возможно, сработали safety-фильтры)."
            )

        return GeminiResponse(text=text, audio_bytes=audio_bytes, audio_mime_type=audio_mime_type)

    async def generate_speech(
        self, text: str, voice_name: Optional[str] = None, model: Optional[str] = None
    ) -> bytes:
        """Синтезирует речь из текста (TTS) с помощью аудио-модальности Gemini."""
        effective_voice = voice_name or self.default_voice
        target_model = model or self.default_tts_model
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=effective_voice
                    )
                )
            ),
        )
        try:
            response = await self._client.aio.models.generate_content(
                model=target_model,
                contents=f"Прочитай следующий текст:\n\n{text}",
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise GeminiError(self._friendly_message(exc)) from exc

        if response.candidates and response.candidates[0].content:
            for part in response.candidates[0].content.parts:
                inline_data = getattr(part, "inline_data", None)
                if inline_data and getattr(inline_data, "data", None):
                    return inline_data.data

        raise GeminiError("Не удалось сгенерировать аудио из текста.")

    @staticmethod
    def _friendly_message(exc: Exception) -> str:
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        text = str(exc)
        if status == 429 or "RESOURCE_EXHAUSTED" in text or "429" in text:
            return "Gemini API вернул лимит запросов (429). Попробуй чуть позже."
        if status in (401, 403) or "PERMISSION_DENIED" in text:
            return "Gemini API отклонил ключ (401/403). Проверь GEMINI_API_KEY."
        if status == 400 or "INVALID_ARGUMENT" in text:
            return f"Некорректный запрос к Gemini API: {text}"
        return f"Ошибка при обращении к Gemini API: {text}"


def build_gemini_client(
    api_key: str,
    default_system_prompt: str = "",
    default_voice: str = "Aoede",
    default_tts_model: str = "gemini-3.1-flash-tts",
) -> GeminiClient:
    return GeminiClient(
        api_key=api_key,
        default_system_prompt=default_system_prompt,
        default_voice=default_voice,
        default_tts_model=default_tts_model,
    )

