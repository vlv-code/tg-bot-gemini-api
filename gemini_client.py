"""Тонкая обёртка над google-genai.

Каждый вызов ask() создаёт новую chat-сессию с историей, переданной
вызывающим кодом (она хранится в storage.py, а не внутри SDK). Сам
GeminiClient историю не хранит и не решает, чистить её или нет — это
делает handlers.py: при смене модели он явно вызывает
storage.clear_history(), потому что история диалога от одной модели не
обязана быть совместима с другой.
"""

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


class GeminiClient:
    def __init__(self, api_key: str, system_prompt: str = "") -> None:
        self._client = genai.Client(api_key=api_key)
        # публичный, можно менять на лету (см. /prompt в handlers.py) —
        # не история, поэтому не в storage.py: применяется к каждому
        # запросу заново и не расходует max_history_messages
        self.system_prompt = system_prompt

    @staticmethod
    def _build_history(turns: list[Turn]) -> list[types.Content]:
        history = []
        for turn in turns:
            role = "model" if turn.role == "model" else "user"
            history.append(types.Content(role=role, parts=[types.Part(text=turn.text)]))
        return history

    async def ask(self, model: str, history_turns: list[Turn], message: str) -> str:
        history = self._build_history(history_turns)
        config = (
            types.GenerateContentConfig(system_instruction=self.system_prompt)
            if self.system_prompt
            else None
        )
        chat = self._client.aio.chats.create(model=model, config=config, history=history)

        try:
            response = await chat.send_message(message)
        except Exception as exc:  # noqa: BLE001 — здесь ловим всё от SDK
            raise GeminiError(self._friendly_message(exc)) from exc

        text = getattr(response, "text", None)
        if not text:
            raise GeminiError(
                "Gemini вернул пустой ответ (возможно, сработали safety-фильтры)."
            )
        return text

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


def build_gemini_client(api_key: str, system_prompt: str = "") -> GeminiClient:
    return GeminiClient(api_key=api_key, system_prompt=system_prompt)
