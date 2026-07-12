from __future__ import annotations

import base64
import os
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dashboard


class DashboardAdvertisingFormatTests(unittest.TestCase):
    def setUp(self):
        self.auth = "Basic " + base64.b64encode(b"admin:test-password").decode("ascii")

    @staticmethod
    def _unknown_fullstats(method, url, **kwargs):
        if url.endswith("/adv/v1/promotion/count"):
            return {"adverts": [{"type": 9, "status": 9, "advert_list": [{"advertId": 77}]}]}
        if url.endswith("/api/advert/v2/adverts"):
            return {"adverts": []}
        if url.endswith("/adv/v3/fullstats"):
            return {"unexpected": []}
        raise AssertionError(f"Неожиданный URL в тесте: {url}")

    def _common_patches(self, cache_writer: Mock, request_side_effect=None) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.dict(os.environ, {"DASHBOARD_PASSWORD": "test-password"}, clear=False))
        stack.enter_context(patch.object(dashboard, "database_enabled", return_value=True))
        stack.enter_context(patch.object(dashboard, "_read_ad_stats_cache", return_value=None))
        stack.enter_context(patch.object(dashboard, "_write_ad_stats_cache", cache_writer))
        stack.enter_context(
            patch.object(
                dashboard,
                "_wb_request_json",
                side_effect=request_side_effect or self._unknown_fullstats,
            )
        )
        return stack

    def test_unit_matrix_unknown_fullstats_is_partial_and_does_not_crash(self):
        cache_writer = Mock()
        product = SimpleNamespace(
            units=10.0,
            revenue=10000.0,
            payout=7000.0,
            cogs=4000.0,
            advertising=500.0,
            tax=600.0,
            external_expenses=100.0,
            profit=1800.0,
            sku="SKU-UNKNOWN-FORMAT",
            name="Тест неизвестного формата",
            nm_id=123456789,
        )
        with self._common_patches(cache_writer) as stack:
            stack.enter_context(patch.object(dashboard, "list_product_summaries", return_value=[product]))
            stack.enter_context(patch.object(dashboard, "list_sale_operations", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_content_cards_meta", return_value=({}, {}, "")))
            stack.enter_context(patch.object(dashboard, "_load_box_tariff_map", return_value=([], {}, "")))
            stack.enter_context(patch.object(dashboard, "_list_unit_matrix_scenarios", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_list_unit_calc_scenarios", return_value=[]))
            with dashboard.app.test_client() as client:
                response = client.get(
                    "/unit-matrix?period_type=weekly&date_from=2026-06-01&date_to=2026-06-30",
                    headers={"Authorization": self.auth},
                )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SKU-UNKNOWN-FORMAT", body)
        self.assertIn("PARTIAL_DATA", body)
        self.assertIn('class="js-factDrr">Нет данных</td>', body)
        self.assertTrue(cache_writer.called)
        self.assertTrue(any(call.args[2].get("status") == "PARTIAL_DATA" for call in cache_writer.call_args_list))

    def test_unit_matrix_ad_api_error_is_api_error_and_does_not_crash(self):
        cache_writer = Mock()
        product = SimpleNamespace(
            units=10.0,
            revenue=10000.0,
            payout=7000.0,
            cogs=4000.0,
            advertising=500.0,
            tax=600.0,
            external_expenses=100.0,
            profit=1800.0,
            sku="SKU-API-ERROR",
            name="Тест ошибки рекламного API",
            nm_id=123456789,
        )

        def api_error(*args, **kwargs):
            raise RuntimeError("WB advertising API unavailable")

        with self._common_patches(cache_writer, api_error) as stack:
            stack.enter_context(patch.object(dashboard, "list_product_summaries", return_value=[product]))
            stack.enter_context(patch.object(dashboard, "list_sale_operations", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_content_cards_meta", return_value=({}, {}, "")))
            stack.enter_context(patch.object(dashboard, "_load_box_tariff_map", return_value=([], {}, "")))
            stack.enter_context(patch.object(dashboard, "_list_unit_matrix_scenarios", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_list_unit_calc_scenarios", return_value=[]))
            with dashboard.app.test_client() as client:
                response = client.get(
                    "/unit-matrix?period_type=weekly&date_from=2026-06-01&date_to=2026-06-30",
                    headers={"Authorization": self.auth},
                )

        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SKU-API-ERROR", body)
        self.assertIn("API_ERROR", body)
        self.assertIn('class="js-factDrr">Нет данных</td>', body)
        self.assertTrue(cache_writer.called)
        self.assertTrue(all(call.args[2].get("status") == "API_ERROR" for call in cache_writer.call_args_list))

    def test_funnel_unknown_fullstats_shows_no_data_instead_of_zero(self):
        cache_writer = Mock()
        funnel_item = {
            "product": {"nmId": 123456789, "title": "Тестовый товар", "vendorCode": "SKU-FUNNEL"},
            "statistic": {"selected": {}, "comparison": {}},
        }
        with self._common_patches(cache_writer) as stack:
            stack.enter_context(patch.object(dashboard, "_load_sales_funnel", return_value=([funnel_item], [])))
            stack.enter_context(patch.object(dashboard, "_actual_sales_rows", return_value=([], None, None)))
            with dashboard.app.test_client() as client:
                response = client.get(
                    "/funnel-ads?date_from=2026-06-01&date_to=2026-06-30",
                    headers={"Authorization": self.auth},
                )
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("SKU-FUNNEL", body)
        self.assertIn("Рекламные данные неполные", body)
        self.assertIn("Нет данных", body)
        self.assertNotIn("WB вернул рекламную статистику в неожиданном формате", body)
        self.assertTrue(any(call.args[2].get("status") == "PARTIAL_DATA" for call in cache_writer.call_args_list))

    def test_v46_cache_is_refetched_by_v461_parser(self):
        old_cache = {
            "by_nm": {},
            "status": "PARTIAL_DATA",
            "warnings": ["старый формат"],
            "fetched_at": datetime.now(timezone.utc),
            "from_cache": True,
            "parser_version": "",
        }
        cache_writer = Mock()

        def official_fullstats(method, url, **kwargs):
            if url.endswith("/adv/v1/promotion/count"):
                return {"adverts": [{"type": 9, "status": 9, "advert_list": [{"advertId": 77}]}]}
            if url.endswith("/api/advert/v2/adverts"):
                return {"adverts": []}
            return [
                {
                    "advertId": 77,
                    "days": [
                        {
                            "date": "2026-06-15",
                            "apps": [
                                {
                                    "appType": 1,
                                    "nms": [
                                        {"nmId": 123456789, "sum": 1000, "sum_price": 5000, "orders": 5, "views": 100, "clicks": 10}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]

        with patch.object(dashboard, "_read_ad_stats_cache", return_value=old_cache), patch.object(
            dashboard, "_write_ad_stats_cache", cache_writer
        ), patch.object(dashboard, "_wb_request_json", side_effect=official_fullstats):
            result = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30))
        self.assertFalse(result["from_cache"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["by_nm"][123456789]["factual_drr"], 20)
        cache_writer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
