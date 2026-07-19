from __future__ import annotations

import unittest
from datetime import date
from zoneinfo import ZoneInfo

from wb_acceptance import (
    AcceptanceSettings,
    build_acceptance_messages,
    filter_acceptance_slots,
    select_new_or_better_slots,
)


class AcceptanceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.settings = AcceptanceSettings(
            warehouse_names=("Рязань",),
            warehouse_ids=(),
            box_type_ids=(2,),
            max_coefficient=1,
            days_ahead=14,
            check_interval_seconds=120,
            timezone=ZoneInfo("Europe/Moscow"),
        )

    @staticmethod
    def row(**changes):
        value = {
            "date": "2026-07-20T00:00:00Z",
            "coefficient": 1,
            "warehouseID": 117501,
            "warehouseName": "Рязань (Тюшевское)",
            "allowUnload": True,
            "boxTypeID": 2,
        }
        value.update(changes)
        return value

    def test_filters_by_warehouse_availability_and_box_type(self):
        valid = self.row()
        result = filter_acceptance_slots(
            [
                valid,
                self.row(warehouseName="Казань"),
                self.row(coefficient=2),
                self.row(allowUnload=False),
                self.row(boxTypeID=5),
            ],
            self.settings,
            today=date(2026, 7, 19),
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].warehouse_name, "Рязань (Тюшевское)")
        self.assertEqual(result[0].box_type_name, "Короба")

    def test_only_new_or_better_slot_generates_notification(self):
        paid = filter_acceptance_slots(
            [self.row(coefficient=1)],
            self.settings,
            today=date(2026, 7, 19),
        )[0]
        free = filter_acceptance_slots(
            [self.row(coefficient=0)],
            self.settings,
            today=date(2026, 7, 19),
        )[0]

        first, state = select_new_or_better_slots([paid], {})
        repeated, state = select_new_or_better_slots([paid], state)
        improved, _ = select_new_or_better_slots([free], state)

        self.assertEqual(first, [paid])
        self.assertEqual(repeated, [])
        self.assertEqual(improved, [free])

    def test_message_contains_warehouse_date_and_coefficient(self):
        slot = filter_acceptance_slots(
            [self.row(coefficient=0)],
            self.settings,
            today=date(2026, 7, 19),
        )[0]
        message = build_acceptance_messages(
            [slot],
            title="Проверка",
            timezone=self.settings.timezone,
        )[0]
        self.assertIn("Рязань", message)
        self.assertIn("20.07.2026", message)
        self.assertIn("бесплатно", message)


if __name__ == "__main__":
    unittest.main()

