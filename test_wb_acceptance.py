from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date
from unittest.mock import patch
from zoneinfo import ZoneInfo

import wb_acceptance
from wb_acceptance import (
    AcceptanceSettings,
    build_acceptance_messages,
    filter_acceptance_slots,
    filter_monitored_slots,
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

    def test_food_filter_excludes_regular_warehouse_and_keeps_closed_food_warehouse(self):
        settings = replace(
            self.settings,
            required_warehouse_names=("Питание",),
        )
        regular = self.row(warehouseName="Рязань (Тюшевское)", warehouseID=117501)
        food_closed = self.row(
            warehouseName="Рязань Питание",
            warehouseID=204939,
            coefficient=-1,
            allowUnload=False,
        )

        available = filter_acceptance_slots(
            [regular, food_closed],
            settings,
            today=date(2026, 7, 19),
        )
        monitored = filter_monitored_slots(
            [regular, food_closed],
            settings,
            today=date(2026, 7, 19),
        )

        self.assertEqual(available, [])
        self.assertEqual(
            [slot.warehouse_name for slot in monitored],
            ["Рязань Питание"],
        )

    def test_barcode_compatible_warehouse_ids_are_applied(self):
        settings = replace(
            self.settings,
            required_warehouse_names=("Питание",),
        )
        result = filter_acceptance_slots(
            [
                self.row(warehouseName="Рязань Питание", warehouseID=100),
                self.row(warehouseName="Рязань Питание 2", warehouseID=200),
            ],
            settings,
            today=date(2026, 7, 19),
            compatible_warehouse_ids={200},
        )
        self.assertEqual([slot.warehouse_id for slot in result], [200])

    def test_environment_defaults_to_food_warehouses(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = AcceptanceSettings.from_env()
        self.assertEqual(settings.required_warehouse_names, ("Питание",))
        self.assertEqual(settings.barcodes, ())


class AcceptanceOptionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_barcode_options_keep_warehouse_that_accepts_every_barcode_in_boxes(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "result": [
                        {
                            "barcode": "111",
                            "warehouses": [
                                {"warehouseID": 10, "canBox": True},
                                {"warehouseID": 20, "canBox": True},
                            ],
                            "isError": False,
                        },
                        {
                            "barcode": "222",
                            "warehouses": [
                                {"warehouseID": 20, "canBox": True},
                                {"warehouseID": 30, "canBox": False},
                            ],
                            "isError": False,
                        },
                    ]
                }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *args, **kwargs):
                return FakeResponse()

        wb_acceptance._acceptance_options_cache.clear()
        with patch.object(wb_acceptance.httpx, "AsyncClient", return_value=FakeClient()):
            result = await wb_acceptance.fetch_compatible_warehouse_ids(
                "token",
                ("111", "222"),
                (2,),
            )

        self.assertEqual(result, frozenset({20}))


if __name__ == "__main__":
    unittest.main()
