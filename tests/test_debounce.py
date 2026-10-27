import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from debounce import AccumulatedMessage, DebounceManager


class TestAccumulatedMessage(unittest.TestCase):
    def test_default_values(self):
        msg = AccumulatedMessage()
        self.assertEqual(msg.text, "")
        self.assertEqual(msg.message_id, 0)
        self.assertEqual(msg.counterparty_name, "Собеседник")
        self.assertIsNone(msg.raw_message)
        self.assertAlmostEqual(msg.created_at, time.time(), delta=2.0)

    def test_custom_values(self):
        raw = {"id": 123}
        msg = AccumulatedMessage(
            text="Hello world",
            message_id=42,
            counterparty_name="Alice",
            raw_message=raw,
            created_at=1000.0,
        )
        self.assertEqual(msg.text, "Hello world")
        self.assertEqual(msg.message_id, 42)
        self.assertEqual(msg.counterparty_name, "Alice")
        self.assertEqual(msg.raw_message, raw)
        self.assertEqual(msg.created_at, 1000.0)


class TestDebounceManager(unittest.IsolatedAsyncioTestCase):
    async def test_rapid_sequential_pushes_resets_timer(self):
        flushed_batches = []

        async def on_flush(key, items):
            flushed_batches.append((key, items))

        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush)
        key = ("conn_1", 1001)

        msg1 = AccumulatedMessage(text="Msg 1", message_id=1)
        msg2 = AccumulatedMessage(text="Msg 2", message_id=2)
        msg3 = AccumulatedMessage(text="Msg 3", message_id=3)

        # 3 pushes spaced by 0.03s (< 0.1s delay)
        await manager.push(key, msg1)
        self.assertEqual(manager.active_keys_count(), 1)
        self.assertEqual(len(manager.get_pending(key)), 1)

        await asyncio.sleep(0.03)
        await manager.push(key, msg2)
        self.assertEqual(len(manager.get_pending(key)), 2)

        await asyncio.sleep(0.03)
        await manager.push(key, msg3)
        self.assertEqual(len(manager.get_pending(key)), 3)

        # At this point, 0.06s elapsed since msg1. Timer should have been reset by msg2 and msg3.
        # Wait 0.05s: total 0.11s from start, but only 0.05s since msg3. Should not fire yet.
        await asyncio.sleep(0.05)
        self.assertEqual(len(flushed_batches), 0)
        self.assertEqual(manager.active_keys_count(), 1)

        # Now wait another 0.08s (total 0.13s since msg3, > 0.1s delay).
        await asyncio.sleep(0.08)

        # Should fire exactly once with all 3 messages in order
        self.assertEqual(len(flushed_batches), 1)
        batch_key, batch_items = flushed_batches[0]
        self.assertEqual(batch_key, key)
        self.assertEqual(len(batch_items), 3)
        self.assertEqual([m.text for m in batch_items], ["Msg 1", "Msg 2", "Msg 3"])
        self.assertEqual(manager.active_keys_count(), 0)
        self.assertEqual(len(manager.get_pending(key)), 0)

    async def test_manual_flush_immediate_returns_items_and_cancels_timer(self):
        on_flush_mock = AsyncMock()
        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush_mock)
        key = ("conn_1", 2002)

        msg1 = AccumulatedMessage(text="Immediate 1", message_id=10)
        msg2 = AccumulatedMessage(text="Immediate 2", message_id=11)

        await manager.push(key, msg1)
        await manager.push(key, msg2)

        # Manual flush
        flushed_items = await manager.flush(key)
        self.assertIsNotNone(flushed_items)
        self.assertEqual(len(flushed_items), 2)
        self.assertEqual([m.text for m in flushed_items], ["Immediate 1", "Immediate 2"])
        on_flush_mock.assert_awaited_once_with(key, flushed_items)

        # Wait past delay to ensure timer does not trigger another flush
        await asyncio.sleep(0.15)
        self.assertEqual(on_flush_mock.await_count, 1)
        self.assertEqual(manager.active_keys_count(), 0)

    async def test_cancel_discards_messages_and_prevents_flush(self):
        on_flush_mock = AsyncMock()
        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush_mock)
        key = ("conn_1", 3003)

        await manager.push(key, AccumulatedMessage(text="To be cancelled", message_id=20))
        self.assertEqual(manager.active_keys_count(), 1)

        await manager.cancel(key)
        self.assertEqual(manager.active_keys_count(), 0)
        self.assertEqual(manager.get_pending(key), [])

        # Wait past delay to verify on_flush was NOT called
        await asyncio.sleep(0.15)
        on_flush_mock.assert_not_awaited()

    async def test_multiple_independent_keys(self):
        flushed = {}

        async def on_flush(key, items):
            flushed[key] = items

        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush)
        key_a = ("conn_A", 100)
        key_b = ("conn_B", 200)

        msg_a1 = AccumulatedMessage(text="A1", message_id=1)
        msg_a2 = AccumulatedMessage(text="A2", message_id=2)
        msg_b1 = AccumulatedMessage(text="B1", message_id=3)

        await manager.push(key_a, msg_a1)
        await manager.push(key_a, msg_a2)
        await manager.push(key_b, msg_b1)

        self.assertEqual(manager.active_keys_count(), 2)

        # Manually flush key_a
        items_a = await manager.flush(key_a)
        self.assertEqual(len(items_a), 2)
        self.assertIn(key_a, flushed)
        self.assertNotIn(key_b, flushed)
        self.assertEqual(manager.active_keys_count(), 1)

        # Wait for key_b timer to fire automatically
        await asyncio.sleep(0.15)
        self.assertIn(key_b, flushed)
        self.assertEqual(len(flushed[key_b]), 1)
        self.assertEqual(flushed[key_b][0].text, "B1")
        self.assertEqual(manager.active_keys_count(), 0)

    async def test_flush_nonexistent_or_empty_key(self):
        on_flush_mock = AsyncMock()
        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush_mock)
        key = ("conn_none", 9999)

        res = await manager.flush(key)
        self.assertIsNone(res)
        on_flush_mock.assert_not_awaited()

    async def test_get_pending_returns_independent_copy(self):
        manager = DebounceManager(delay_seconds=0.5)
        key = ("conn_1", 5005)
        msg = AccumulatedMessage(text="Copy test", message_id=50)

        await manager.push(key, msg)
        pending1 = manager.get_pending(key)
        self.assertEqual(len(pending1), 1)

        # Mutate the returned list
        pending1.append(AccumulatedMessage(text="Fake", message_id=999))
        self.assertEqual(len(pending1), 2)

        # Original internal queue should remain size 1
        pending2 = manager.get_pending(key)
        self.assertEqual(len(pending2), 1)
        self.assertEqual(pending2[0].message_id, 50)

        await manager.cancel(key)

    async def test_flush_without_on_flush_callback(self):
        # DebounceManager without callback should still return items on flush
        manager = DebounceManager(delay_seconds=0.1, on_flush=None)
        key = ("conn_1", 6006)
        msg = AccumulatedMessage(text="No callback", message_id=60)

        await manager.push(key, msg)
        items = await manager.flush(key)
        self.assertEqual(items, [msg])

    async def test_on_flush_exception_does_not_corrupt_state(self):
        async def failing_flush(key, items):
            raise RuntimeError("Flush failed intentionally")

        manager = DebounceManager(delay_seconds=0.05, on_flush=failing_flush)
        key = ("conn_err", 7007)

        await manager.push(key, AccumulatedMessage(text="Err test", message_id=70))
        self.assertEqual(manager.active_keys_count(), 1)

        # Wait for timeout to fire and raise
        await asyncio.sleep(0.1)

        # Keys and pending items should be cleared despite callback error
        self.assertEqual(manager.active_keys_count(), 0)
        self.assertEqual(manager.get_pending(key), [])

    async def test_push_immediately_after_flush_works_independently(self):
        flushes = []

        async def on_flush(key, items):
            flushes.append((key, [m.text for m in items]))

        manager = DebounceManager(delay_seconds=0.08, on_flush=on_flush)
        key = ("conn_repush", 8008)

        await manager.push(key, AccumulatedMessage(text="First batch", message_id=1))
        # Wait for first batch to flush
        await asyncio.sleep(0.12)
        self.assertEqual(len(flushes), 1)
        self.assertEqual(flushes[0][1], ["First batch"])

        # Push again to same key
        await manager.push(key, AccumulatedMessage(text="Second batch", message_id=2))
        self.assertEqual(manager.active_keys_count(), 1)
        await asyncio.sleep(0.12)
        self.assertEqual(len(flushes), 2)
        self.assertEqual(flushes[1][1], ["Second batch"])
        self.assertEqual(manager.active_keys_count(), 0)

    async def test_concurrent_pushes_safety(self):
        flushed_items = []

        async def on_flush(key, items):
            flushed_items.extend(items)

        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush)
        key = ("conn_conc", 9009)

        # 20 concurrent pushes
        async def push_item(idx):
            await manager.push(key, AccumulatedMessage(text=f"Concurrent {idx}", message_id=idx))

        await asyncio.gather(*(push_item(i) for i in range(20)))

        self.assertEqual(len(manager.get_pending(key)), 20)
        await asyncio.sleep(0.15)
        self.assertEqual(len(flushed_items), 20)
        self.assertEqual(manager.active_keys_count(), 0)

    async def test_close_cancels_all_active_keys(self):
        on_flush_mock = AsyncMock()
        manager = DebounceManager(delay_seconds=0.1, on_flush=on_flush_mock)

        await manager.push(("c1", 1), AccumulatedMessage(text="1"))
        await manager.push(("c2", 2), AccumulatedMessage(text="2"))
        self.assertEqual(manager.active_keys_count(), 2)

        await manager.close()
        self.assertEqual(manager.active_keys_count(), 0)
        self.assertEqual(manager.get_pending(("c1", 1)), [])
        self.assertEqual(manager.get_pending(("c2", 2)), [])

        await asyncio.sleep(0.15)
        on_flush_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
