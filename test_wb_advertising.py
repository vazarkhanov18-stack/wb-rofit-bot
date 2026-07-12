from __future__ import annotations

import unittest
from datetime import date

from wb_advertising import (
    aggregate_fullstats,
    classify_sku_advertising,
    extract_campaign_catalog,
    fetch_advertising_stats,
    finalize_by_nm,
    split_date_range,
)
from wb_unit_math import manual_warehouse_costs


def _campaign(campaign_id: int, nm_id: int, spend: float, revenue: float, orders: float, day: str = "2026-05-01"):
    return {
        "advertId": campaign_id,
        "sum": spend,
        "sum_price": revenue,
        "orders": orders,
        "days": [
            {
                "date": day,
                "apps": [
                    {
                        "appType": 1,
                        "nms": [
                            {
                                "nmId": nm_id,
                                "name": f"Товар {nm_id}",
                                "sum": spend,
                                "sum_price": revenue,
                                "orders": orders,
                                "views": 100,
                                "clicks": 10,
                                "atbs": 2,
                                "shks": orders,
                            }
                        ],
                    }
                ],
            }
        ],
    }


class AdvertisingAggregationTests(unittest.TestCase):
    def test_long_period_is_split_without_gaps_or_duplicates(self):
        chunks = split_date_range(date(2026, 5, 1), date(2026, 7, 15))
        self.assertEqual(
            chunks,
            [
                (date(2026, 5, 1), date(2026, 5, 31)),
                (date(2026, 6, 1), date(2026, 7, 1)),
                (date(2026, 7, 2), date(2026, 7, 15)),
            ],
        )
        days = []
        for start, end in chunks:
            days.extend(range(start.toordinal(), end.toordinal() + 1))
        self.assertEqual(len(days), len(set(days)))
        self.assertEqual(len(days), 76)

    def test_one_campaign_drr_is_20_percent(self):
        catalog = {1: {"campaign_id": 1, "name": "Кампания 1", "type": 9, "status": 9}}
        by_nm = {}
        aggregate_fullstats(
            [_campaign(1, 123, 1000, 5000, 5)],
            catalog,
            by_nm,
            requested_from=date(2026, 5, 1),
            requested_to=date(2026, 5, 31),
        )
        finalize_by_nm(by_nm)
        self.assertEqual(by_nm[123]["spend"], 1000)
        self.assertEqual(by_nm[123]["revenue"], 5000)
        self.assertEqual(by_nm[123]["factual_drr"], 20)

    def test_two_campaigns_use_ratio_of_totals(self):
        catalog = {
            1: {"campaign_id": 1, "name": "Кампания 1", "type": 9, "status": 7},
            2: {"campaign_id": 2, "name": "Кампания 2", "type": 9, "status": 9},
        }
        by_nm = {}
        aggregate_fullstats(
            [_campaign(1, 123, 1000, 5000, 5), _campaign(2, 123, 500, 2000, 2)],
            catalog,
            by_nm,
            requested_from=date(2026, 5, 1),
            requested_to=date(2026, 5, 31),
        )
        finalize_by_nm(by_nm)
        row = by_nm[123]
        self.assertEqual(row["campaigns_count"], 2)
        self.assertEqual(row["active_campaigns_count"], 1)
        self.assertAlmostEqual(row["factual_drr"], 21.4285714286)

    def test_spend_without_sales_is_not_zero_drr(self):
        data = classify_sku_advertising(
            {"spend": 100, "revenue": 0, "campaigns_count": 1, "campaigns": [{}], "complete": True},
            fetch_status="OK",
        )
        self.assertEqual(data["status"], "SPEND_WITHOUT_SALES")
        self.assertIsNone(data["factual_drr"])

    def test_no_campaigns_is_no_ads(self):
        data = classify_sku_advertising(None, fetch_status="OK")
        self.assertEqual(data["status"], "NO_ADS")

    def test_api_error_is_non_numeric_status(self):
        data = classify_sku_advertising(None, fetch_status="API_ERROR")
        self.assertEqual(data["status"], "API_ERROR")
        self.assertIsNone(data["factual_drr"])

    def test_sales_without_spend_is_marked_unreliable(self):
        data = classify_sku_advertising(
            {"spend": 0, "revenue": 500, "campaigns_count": 1, "campaigns": [{}], "complete": True},
            fetch_status="OK",
        )
        self.assertEqual(data["status"], "SALES_WITHOUT_SPEND")
        self.assertIsNone(data["factual_drr"])

    def test_campaign_total_without_nm_is_not_allocated(self):
        by_nm = {}
        warnings = aggregate_fullstats(
            [{"advertId": 1, "sum": 900, "sum_price": 3000, "orders": 3, "days": []}],
            {1: {"campaign_id": 1, "status": 9}},
            by_nm,
            requested_from=date(2026, 5, 1),
            requested_to=date(2026, 5, 31),
        )
        self.assertEqual(by_nm, {})
        self.assertTrue(any("без разбивки по nmID" in warning for warning in warnings))

    def test_fetch_batches_both_campaigns_and_period(self):
        fullstats_calls = []

        def request_json(method, url, **kwargs):
            if url.endswith("promotion/count"):
                return {
                    "adverts": [
                        {
                            "type": 9,
                            "status": 9,
                            "advert_list": [{"advertId": value} for value in range(1, 52)],
                        }
                    ]
                }
            if url.endswith("/api/advert/v2/adverts"):
                return {"adverts": []}
            fullstats_calls.append(kwargs["params"])
            return []

        result = fetch_advertising_stats(
            request_json,
            date(2026, 5, 1),
            date(2026, 6, 15),
            pause_seconds=0,
        )
        self.assertEqual(result["status"], "OK")
        self.assertEqual(len(fullstats_calls), 4)
        self.assertEqual([len(call["ids"].split(",")) for call in fullstats_calls], [50, 1, 50, 1])

    def test_catalog_uses_only_fullstats_supported_statuses(self):
        catalog = extract_campaign_catalog(
            {
                "adverts": [
                    {"type": 9, "status": 9, "advert_list": [{"advertId": 1}]},
                    {"type": 9, "status": 8, "advert_list": [{"advertId": 2}]},
                ]
            }
        )
        self.assertEqual(set(catalog), {1})


class UnitMathTests(unittest.TestCase):
    def test_manual_logistics_keeps_fractional_liters(self):
        volume, logistics, storage = manual_warehouse_costs(
            13,
            11,
            11,
            80.5,
            24.5,
            storage_first_liter=1,
            storage_additional_liter=0.5,
            storage_days=10,
        )
        self.assertAlmostEqual(volume, 1.573)
        self.assertAlmostEqual(logistics, 94.5385)
        self.assertAlmostEqual(storage, 12.865)


if __name__ == "__main__":
    unittest.main()
