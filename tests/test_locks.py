import asyncio
import unittest

from locks import GlobalQueueManager, UserLocks


class TestUserLocks(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_same_lock(self):
        locks = UserLocks()
        lock1 = locks.get(10)
        lock2 = locks.get(10)
        self.assertIs(lock1, lock2)

    async def test_different_user_different_lock(self):
        locks = UserLocks()
        lock1 = locks.get(10)
        lock2 = locks.get(20)
        self.assertIsNot(lock1, lock2)


class TestGlobalQueueManager(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_acquire_under_limit(self):
        manager = GlobalQueueManager(max_concurrent=2)
        self.assertEqual(manager.running_count, 0)
        self.assertEqual(manager.waiting_count, 0)

        async with manager.acquire(user_id=1, priority=2) as pos:
            self.assertEqual(pos, 0)
            self.assertEqual(manager.running_count, 1)

        self.assertEqual(manager.running_count, 0)

    async def test_priority_ordering(self):
        manager = GlobalQueueManager(max_concurrent=1)
        acquired_order = []

        # Занимаем единственный слот задачей 1
        async def task_holder(hold_event, release_event):
            async with manager.acquire(user_id=1, priority=2):
                hold_event.set()
                await release_event.wait()

        hold_event = asyncio.Event()
        release_event = asyncio.Event()
        holder_task = asyncio.create_task(task_holder(hold_event, release_event))
        await hold_event.wait()

        # Ставим в очередь обычного пользователя (priority 2)
        async def regular_user():
            async with manager.acquire(user_id=2, priority=2):
                acquired_order.append("regular")

        # Ставим в очередь суперадмина (priority 0)
        async def superadmin_user():
            async with manager.acquire(user_id=3, priority=0):
                acquired_order.append("admin")

        t_reg = asyncio.create_task(regular_user())
        await asyncio.sleep(0.01)  # даем встать в очередь
        t_admin = asyncio.create_task(superadmin_user())
        await asyncio.sleep(0.01)

        self.assertEqual(manager.waiting_count, 2)
        # Освобождаем слот первоначального холдера
        release_event.set()
        await holder_task

        await asyncio.gather(t_admin, t_reg)
        # Админ должен был пройти раньше обычного пользователя
        self.assertEqual(acquired_order, ["admin", "regular"])

    async def test_cancellation_passes_slot_correctly(self):
        manager = GlobalQueueManager(max_concurrent=1)
        hold_event = asyncio.Event()
        release_event = asyncio.Event()

        async def worker_1():
            async with manager.acquire(user_id=10, priority=2):
                hold_event.set()
                await release_event.wait()

        task1 = asyncio.create_task(worker_1())
        await hold_event.wait()

        # Создаем задачу 2, которая ждет
        task2 = asyncio.create_task(manager.acquire(user_id=20, priority=2).__aenter__())
        await asyncio.sleep(0.01)

        # Создаем задачу 3, которая тоже ждет
        task3_acquired = False

        async def worker_3():
            nonlocal task3_acquired
            async with manager.acquire(user_id=30, priority=2):
                task3_acquired = True

        task3 = asyncio.create_task(worker_3())
        await asyncio.sleep(0.01)

        # Отменяем задачу 2 в очереди
        task2.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task2

        # Освобождаем задачу 1 -> слот должен достаться задаче 3
        release_event.set()
        await task1
        await task3

        self.assertTrue(task3_acquired)
        self.assertEqual(manager.running_count, 0)
        self.assertEqual(manager.waiting_count, 0)


if __name__ == "__main__":
    unittest.main()
