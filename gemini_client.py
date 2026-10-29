"""Тонкая обёртка над google-genai с поддержкой мультимодальности и TTS.

Каждый вызов ask() создаёт новую chat-сессию с историей, переданной
вызывающим кодом (хранится в storage.py).
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Union

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
    used_model: str = ""


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        default_system_prompt: str = "",
        default_voice: str = "Aoede",
        default_tts_model: str = "gemini-3.8-flash-tts",
        default_model: str = "gemini-3.5-flash-lite",
        timeout_seconds: float = 35.0,
    ) -> None:
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=int(timeout_seconds * 1000),
                retry_options=types.HttpRetryOptions(
                    attempts=2,
                    http_status_codes=[503, 504],
                ),
            ),
        )
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
            modalities = ["AUDIO"] if "tts" in model.lower() else ["AUDIO", "TEXT"]
            config_kwargs["response_modalities"] = modalities
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

        models_to_try = [model]
        if (
            self.default_model
            and self.default_model != model
            and not is_audio_model
            and self.default_model not in models_to_try
        ):
            models_to_try.append(self.default_model)

        response = None
        for current_model in models_to_try:
            logger.info(
                "Запрос к Gemini API (модель: %s, history: %d turns)...",
                current_model,
                len(history_turns),
            )
            chat = self._client.aio.chats.create(model=current_model, config=config, history=history)

            try:
                async with asyncio.timeout(self.timeout_seconds):
                    response = await chat.send_message(content_input)
                break
            except TimeoutError as exc:
                if current_model != models_to_try[-1]:
                    logger.warning(
                        "Модель %s не ответила за %.0f сек (таймаут). Фоллбэк на %s...",
                        current_model,
                        self.timeout_seconds,
                        models_to_try[-1],
                    )
                    continue
                raise GeminiError(
                    f"Превышено время ожидания ответа от Gemini API (таймаут {int(self.timeout_seconds)} сек). Попробуйте повторить запрос позже."
                ) from exc
            except Exception as exc:  # noqa: BLE001
                err_str = str(exc).upper()
                is_overloaded = any(x in err_str for x in ("503", "504", "UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED", "HIGH DEMAND"))
                if is_overloaded and current_model != models_to_try[-1]:
                    logger.warning(
                        "Модель %s перегружена (%s). Автоматический фоллбэк на %s...",
                        current_model,
                        exc,
                        models_to_try[-1],
                    )
                    continue
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
            used_model=current_model,
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
            "gemini-3.8-flash-tts",
            "gemini-3.8-flash-lite-tts",
        ):
            if candidate not in models_to_try:
                models_to_try.append(candidate)

        last_error = None
        for target_model in models_to_try:
            modalities = ["AUDIO"] if "tts" in target_model.lower() else ["AUDIO", "TEXT"]
            config = types.GenerateContentConfig(
                response_modalities=modalities,
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=effective_voice
                        )
                    )
                ),
                response_mime_type="audio/L16;codec=pcm;rate=24000",
            )
            try:
                logger.info("Генерация TTS для текста (%d симв.) голосом %s через модель %s...", len(text), effective_voice, target_model)
                async with asyncio.timeout(self.timeout_seconds):
                    response = await self._client.aio.models.generate_content(
                        model=target_model,
                        contents=text,
                        config=config,
                    )
                if response.candidates and response.candidates[0].content:
                    for part in response.candidates[0].content.parts:
                        inline_data = getattr(part, "inline_data", None)
                        if inline_data and getattr(inline_data, "data", None):
                            audio_bytes = inline_data.data
                            logger.info("TTS успешно сгенерирован через модель %s (%d байт)", target_model, len(audio_bytes))
                            return audio_bytes
            except TimeoutError as exc:
                logger.warning("Таймаут TTS через модель %s (%.0f сек)", target_model, self.timeout_seconds)
                last_error = exc
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("Попытка TTS через модель %s не удалась: %s", target_model, exc)
                last_error = exc
                err_text = str(exc)
                status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                if status == 429 or "RESOURCE_EXHAUSTED" in err_text or "429" in err_text:
                    # При исчерпании квоты прерываем цикл, так как лимит общий для аккаунта
                    break
                continue

        if last_error is not None:
            raise GeminiError(self._friendly_message(last_error)) from last_error

        raise GeminiError("Не удалось сгенерировать аудио из текста.")

    async def fetch_and_classify_models(self) -> list[dict]:
        """Получает список моделей из Google GenAI API и классифицирует их."""
        result: list[dict] = []
        try:
            pager = await self._client.aio.models.list()
            models_list = []
            if hasattr(pager, "__aiter__"):
                async for item in pager:
                    models_list.append(item)
            elif hasattr(pager, "__iter__"):
                for item in pager:
                    models_list.append(item)

            for m in models_list:
                raw_name = getattr(m, "name", "") or ""
                mid = raw_name.replace("models/", "") if raw_name.startswith("models/") else raw_name
                if not mid:
                    continue

                # Фильтрация устаревших чекпоинтов с датами и номерами версий gemini-...-001
                if mid.startswith("gemini-") and re.search(r"-\d{3}$", mid):
                    continue
                if re.search(r"(-\d{8}$|-(202\d{5}))", mid):
                    continue
                if "legacy" in mid.lower() or "deprecated" in mid.lower():
                    continue

                disp = getattr(m, "display_name", "") or mid
                desc = (getattr(m, "description", "") or "").lower()
                methods = list(getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or [])

                mid_lower = mid.lower()
                if "tts" in mid_lower or "speech" in desc:
                    category = "tts"
                    input_mod = "text"
                    output_mod = "audio"
                    sort_order = 20
                    icon = "🎧"
                elif "imagen" in mid_lower or ("image" in mid_lower and "generate" in mid_lower):
                    category = "image_gen"
                    input_mod = "text"
                    output_mod = "image"
                    sort_order = 40
                    icon = "🎨"
                elif "embedding" in mid_lower or "embed" in mid_lower or "embedContent" in methods:
                    category = "embed"
                    input_mod = "text"
                    output_mod = "vector"
                    sort_order = 50
                    icon = "📐"
                elif "generateContent" in methods or ("gemini" in mid_lower and "tts" not in mid_lower):
                    category = "text"
                    input_mod = "text,image,audio,pdf"
                    output_mod = "text"
                    if "lite" in mid_lower:
                        sort_order = 10
                        icon = "⚡️"
                    elif "flash" in mid_lower:
                        sort_order = 20
                        icon = "⚖️"
                    elif "argon" in mid_lower or "pro" in mid_lower or "ultra" in mid_lower:
                        sort_order = 30
                        icon = "🧠"
                    else:
                        sort_order = 35
                        icon = "🤖"
                else:
                    category = "other"
                    input_mod = "text"
                    output_mod = "text"
                    sort_order = 90
                    icon = "📦"

                clean_disp = disp
                if not clean_disp.startswith(icon):
                    clean_disp = f"{icon} {clean_disp}"

                result.append(
                    {
                        "model_id": mid,
                        "display_name": clean_disp,
                        "category": category,
                        "input_modalities": input_mod,
                        "output_modalities": output_mod,
                        "is_enabled": 1,
                        "sort_order": sort_order,
                    }
                )
        except Exception as exc:
            logger.warning("Не удалось получить список моделей из Google GenAI API: %s", exc)
            raise

        return result

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
            return "Некорректный запрос к Gemini API (неподдерживаемый формат данных или параметров)."
        return f"Ошибка при обращении к Gemini API: {text}"


def build_gemini_client(
    api_key: str,
    default_system_prompt: str = "",
    default_voice: str = "Aoede",
    default_tts_model: str = "gemini-3.8-flash-tts",
    default_model: str = "gemini-3.5-flash-lite",
    timeout_seconds: float = 35.0,
) -> GeminiClient:
    return GeminiClient(
        api_key=api_key,
        default_system_prompt=default_system_prompt,
        default_voice=default_voice,
        default_tts_model=default_tts_model,
        default_model=default_model,
        timeout_seconds=timeout_seconds,
    )


async def sync_models_catalog(client: GeminiClient, storage: Any) -> dict:
    """Запрашивает актуальные модели из Google GenAI API и синхронизирует их с БД."""
    try:
        models = await client.fetch_and_classify_models()
        if models:
            await storage.upsert_discovered_models(models)
        if hasattr(storage, "seed_baseline_models"):
            await storage.seed_baseline_models()
        stats = {
            "total": len(models),
            "text": sum(1 for m in models if m.get("category") == "text"),
            "tts": sum(1 for m in models if m.get("category") == "tts"),
            "image_gen": sum(1 for m in models if m.get("category") == "image_gen"),
            "embed": sum(1 for m in models if m.get("category") == "embed"),
        }
        logger.info("Успешно синхронизирован каталог моделей: %s", stats)
        return stats
    except Exception as exc:
        logger.warning("Ошибка при синхронизации каталога моделей: %s", exc)
        return {"total": 0, "text": 0, "tts": 0, "image_gen": 0, "embed": 0, "error": str(exc)}



