import os
import unittest

# Устанавливаем фейковые токены для тестов до импорта модулей
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:FAKE_TOKEN_FOR_TESTS")
os.environ.setdefault("GEMINI_API_KEY", "FAKE_KEY_FOR_TESTS")

from handlers import router, storage, limiter, gemini_client, user_locks, global_queue
from handlers.menu import router as menu_router
from handlers.prompts import router as prompts_router
from handlers.admin import router as admin_router
from handlers.business import router as business_router
from handlers.chat import router as chat_router
from handlers.inline import router as inline_router


class TestHandlersModularization(unittest.TestCase):
    def test_main_router_composition(self):
        """Проверяем, что главный роутер включает все 6 под-роутеров в правильном порядке."""
        self.assertIsNotNone(router)
        sub_routers = router.sub_routers
        self.assertEqual(len(sub_routers), 6)
        self.assertIs(sub_routers[0], menu_router)
        self.assertIs(sub_routers[1], prompts_router)
        self.assertIs(sub_routers[2], admin_router)
        self.assertIs(sub_routers[3], business_router)
        self.assertIs(sub_routers[4], chat_router)
        self.assertIs(sub_routers[5], inline_router)

    def test_shared_services_exports(self):
        """Проверяем, что все синглтоны и сервисы корректно экспортируются из пакета handlers."""
        self.assertIsNotNone(storage)
        self.assertIsNotNone(limiter)
        self.assertIsNotNone(gemini_client)
        self.assertIsNotNone(user_locks)
        self.assertIsNotNone(global_queue)


if __name__ == "__main__":
    unittest.main()

