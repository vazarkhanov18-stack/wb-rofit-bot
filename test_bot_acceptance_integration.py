from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import bot
from wb_acceptance import AcceptanceSettings, AcceptanceSlot


class FakeTelegramBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))


class AcceptanceBotIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduled_monitor_sends_once_and_deduplicates(self):
        settings = AcceptanceSettings(
            warehouse_names=("Рязань",),
            warehouse_ids=(),
            box_type_ids=(2,),
            max_coefficient=1,
            days_ahead=14,
            check_interval_seconds=120,
            timezone=ZoneInfo("Europe/Moscow"),
        )
        slot = AcceptanceSlot(
            acceptance_date=datetime.fromisoformat("2026-07-20T00:00:00+00:00"),
            coefficient=0,
            warehouse_id=117501,
            warehouse_name="Рязань (Тюшевское)",
            allow_unload=True,
            box_type_id=2,
            box_type_name="Короба",
        )

        async def load_slots():
            return settings, [slot]

        fake_bot = FakeTelegramBot()
        context = SimpleNamespace(
            job=SimpleNamespace(chat_id=123),
            bot=fake_bot,
            application=SimpleNamespace(bot_data={}),
        )

        with patch.object(bot, "_load_acceptance_slots", load_slots):
            await bot.scheduled_acceptance_monitor(context)
            await bot.scheduled_acceptance_monitor(context)

        self.assertEqual(len(fake_bot.messages), 1)
        self.assertEqual(fake_bot.messages[0][0], 123)
        self.assertIn("Рязань", fake_bot.messages[0][1])

    async def test_configure_jobs_registers_repeating_monitor(self):
        calls = []

        class JobQueue:
            def run_repeating(self, callback, **kwargs):
                calls.append((callback, kwargs))

        application = SimpleNamespace(job_queue=JobQueue())
        environment = {
            "ALLOWED_USER_ID": "123",
            "AUTO_ACCEPTANCE_MONITOR": "true",
            "AUTO_DAILY_REPORT": "false",
            "AUTO_WEEKLY_REPORT": "false",
            "AUTO_STOCK_ALERT": "false",
            "ACCEPTANCE_CHECK_INTERVAL_SECONDS": "120",
        }
        with patch.dict("os.environ", environment, clear=False):
            await bot.configure_jobs(application)

        self.assertEqual(len(calls), 1)
        callback, kwargs = calls[0]
        self.assertIs(callback, bot.scheduled_acceptance_monitor)
        self.assertEqual(kwargs["interval"], 120)
        self.assertEqual(kwargs["chat_id"], 123)


if __name__ == "__main__":
    unittest.main()

