"""Тонкая обёртка над google-genai с поддержкой мультимодальности и TTS.

Каждый вызов ask() создаёт новую chat-сессию с историей, переданной
вызывающим кодом (хранится в storage.py).
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, Sequence, Union

from google import genai
from google.genai import types

from storage import Turn

logger = logging.getLogger(__name__)


class GeminiError(Exception):
    """Дружелюбная ошибка для показа пользователю в Telegram."""


@dataclass
class GeminiResponse:
    text: str
    audio_bytes: Optional[bytes] = None
    audio_mime_type: Optional[str] = None
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    total_tokens: int = 0


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        default_system_prompt: str = "",
        default_voice: str = "Aoede",
        default_tts_model: str = "gemini-3.1-flash-tts-preview",
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
        """Отправляет сообщение в Gemini с учётом истории диалога."""
        history = self._build_history(history_turns)
        effective_system_prompt = (
            system_prompt.strip()
            if (system_prompt and system_prompt.strip())
            else self.default_system_prompt
        )
        effective_voice = voice_name or self.default_voice

        config_kwargs = {}
        if effective_system_prompt:
            config_kwargs["system_instruction"] = effective_system_prompt

        config_kwargs["safety_settings"] = [
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
            types.SafetySetting(
                category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=types.HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

        is_audio_model = "tts" in model.lower() or "audio" in model.lower()
        if want_audio and is_audio_model:
            config_kwargs["response_modalities"] = ["AUDIO", "TEXT"]
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

        prompt_tokens = 0
        candidates_tokens = 0
        total_tokens = 0
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            prompt_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            candidates_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
            total_tokens = getattr(response.usage_metadata, "total_token_count", 0) or 0

        return GeminiResponse(
            text=text,
            audio_bytes=audio_bytes,
            audio_mime_type=audio_mime_type,
            prompt_tokens=prompt_tokens,
            candidates_tokens=candidates_tokens,
            total_tokens=total_tokens,
        )

    async def generate_speech(
        self, text: str, voice_name: Optional[str] = None, model: Optional[str] = None
    ) -> bytes:
        """Синтезирует речь из текста (TTS) с помощью аудио-модальности Gemini."""
        effective_voice = voice_name or self.default_voice

        # Формируем цепочку моделей для попытки генерации речи
        models_to_try: list[str] = []
        if model and model not in models_to_try:
            models_to_try.append(model)
        if self.default_tts_model and self.default_tts_model not in models_to_try:
            models_to_try.append(self.default_tts_model)
        for candidate in (
            "gemini-2.5-flash-preview-tts",
            "gemini-3.1-flash-tts-preview",
            "gemini-2.5-pro-preview-tts",
            "gemini-2.5-flash",
        ):
            if candidate not in models_to_try:
                models_to_try.append(candidate)

        last_error = None
        for target_model in models_to_try:
            config = types.GenerateContentConfig(
                response_modalities=["AUDIO", "TEXT"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=effective_voice
                        )
                    )
                ),
            )
            try:
                logger.info("Генерация TTS для текста (%d симв.) голосом %s через модель %s...", len(text), effective_voice, target_model)
                response = await self._client.aio.models.generate_content(
                    model=target_model,
                    contents=f"Прочитай следующий текст:\n\n{text}",
                    config=config,
                )
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and getattr(inline_data, "data", None):
                            audio_bytes = inline_data.data
                            logger.info("TTS успешно сгенерирован через модель %s (%d байт)", target_model, len(audio_bytes))
                            return audio_bytes
            except Exception as exc:  # noqa: BLE001
                logger.warning("Попытка TTS через модель %s не удалась: %s", target_model, exc)
                last_error = exc
                continue

        if last_error is not None:
            raise GeminiError(self._friendly_message(last_error)) from last_error

        raise GeminiError("Не удалось сгенерировать аудио из текста.")

    @staticmethod
    def _friendly_message(exc: Exception) -> str:
        status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        text = str(exc)
        if status == 429 or "RESOURCE_EXHAUSTED" in text or "429" in text:
            if "limit: 10" in text or "FreeTier" in text or "free_tier_requests" in text:
                return "Лимит бесплатных запросов Gemini TTS на сегодня исчерпан (Google Free Tier: 10 запросов в сутки)."
            delay_match = re.search(r"retryDelay[\"':\s]+([0-9.]+s?)", text, re.IGNORECASE)
            if delay_match:
                delay_str = delay_match.group(1)
                return f"Лимит запросов Gemini API временно исчерпан (429). Попробуйте снова через {delay_str}."
            return "Лимит запросов Gemini API временно исчерпан (429). Попробуйте через несколько секунд."
        if status == 503 or "503" in text or "UNAVAILABLE" in text or "high demand" in text.lower():
            return "Серверы Google Gemini временно перегружены (503 Service Unavailable). Попробуйте повторить запрос через пару секунд или выберите другую модель через /model."
        if status in (401, 403) or "PERMISSION_DENIED" in text:
            return "Gemini API отклонил ключ (401/403). Проверьте GEMINI_API_KEY."
        if status == 400 or "INVALID_ARGUMENT" in text:
            return f"Некорректный запрос к Gemini API: {text}"
        return f"Ошибка при обращении к Gemini API: {text}"


def build_gemini_client(
    api_key: str,
    default_system_prompt: str = "",
    default_voice: str = "Aoede",
    default_tts_model: str = "gemini-3.1-flash-tts-preview",
) -> GeminiClient:
    return GeminiClient(
        api_key=api_key,
        default_system_prompt=default_system_prompt,
        default_voice=default_voice,
        default_tts_model=default_tts_model,
    )


