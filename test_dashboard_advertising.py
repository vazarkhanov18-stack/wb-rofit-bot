from __future__ import annotations

import base64
import copy
import io
import os
import threading
import unittest
from contextlib import ExitStack
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dashboard
from wb_advertising import WBRateLimitError


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
        stack.enter_context(
            patch.dict(os.environ, {"DASHBOARD_PASSWORD": "test-password", "DATABASE_URL": ""}, clear=False)
        )
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

    @staticmethod
    def _cache_result(*, age: timedelta = timedelta(minutes=5)) -> dict:
        return {
            "by_nm": {
                123456789: {
                    "nm_id": 123456789,
                    "name": "Cached SKU",
                    "spend": 1000.0,
                    "revenue": 5000.0,
                    "orders": 5.0,
                    "impressions": 100.0,
                    "clicks": 10.0,
                    "atbs": 0.0,
                    "shks": 0.0,
                    "campaigns": [],
                    "campaigns_count": 1,
                    "active_campaigns_count": 1,
                    "complete": True,
                    "warnings": [],
                    "factual_drr": 20.0,
                }
            },
            "status": "OK",
            "warnings": [],
            "fetched_at": datetime.now(timezone.utc) - age,
            "from_cache": True,
            "parser_version": dashboard.AD_CACHE_PARSER_VERSION,
        }

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

    def test_fresh_cache_is_used_even_for_manual_refresh(self):
        cached = self._cache_result()
        api = Mock()
        writer = Mock()
        with patch.object(dashboard, "_read_ad_stats_cache", return_value=cached), patch.object(
            dashboard, "_write_ad_stats_cache", writer
        ), patch.object(dashboard, "_wb_request_json", api):
            result = dashboard._load_ad_stats_detailed(
                date(2026, 6, 1),
                date(2026, 6, 30),
                force=True,
            )
        self.assertTrue(result["from_cache"])
        self.assertEqual(result["by_nm"][123456789]["factual_drr"], 20)
        api.assert_not_called()
        writer.assert_not_called()

    def test_http_429_reads_retry_after_header(self):
        error = dashboard.urllib.error.HTTPError(
            "https://advert-api.wildberries.ru/adv/v3/fullstats",
            429,
            "Too Many Requests",
            {"Retry-After": "7"},
            io.BytesIO(b"{}"),
        )
        with patch.dict(os.environ, {"WB_API_TOKEN": "secret-test-token"}, clear=False), patch.object(
            dashboard.urllib.request, "urlopen", side_effect=error
        ):
            with self.assertRaises(WBRateLimitError) as caught:
                dashboard._wb_request_json(
                    "GET",
                    "https://advert-api.wildberries.ru/adv/v3/fullstats",
                )
        self.assertEqual(caught.exception.retry_after, "7")
        self.assertEqual(caught.exception.retry_after_seconds, 7)

    def test_repeated_429_uses_last_successful_cache(self):
        cached = self._cache_result(age=timedelta(hours=2))
        fullstats_calls = 0

        def rate_limited(method, url, **kwargs):
            nonlocal fullstats_calls
            if url.endswith("promotion/count"):
                return {"adverts": [{"type": 9, "status": 9, "advert_list": [{"advertId": 77}]}]}
            if url.endswith("/api/advert/v2/adverts"):
                return {"adverts": []}
            fullstats_calls += 1
            raise WBRateLimitError(retry_after="0", retry_after_seconds=0)

        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False), patch.object(
            dashboard, "_read_ad_stats_cache", return_value=cached
        ), patch.object(dashboard, "_write_ad_stats_cache") as writer, patch.object(
            dashboard, "_wb_request_json", side_effect=rate_limited
        ):
            result = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(fullstats_calls, 2)
        self.assertTrue(result["from_cache"])
        self.assertTrue(result["rate_limited"])
        self.assertEqual(result["by_nm"][123456789]["factual_drr"], 20)
        self.assertIn("WB временно ограничил запросы. Используются данные из кэша от", result["warnings"][0])
        writer.assert_not_called()

    def test_repeated_429_without_cache_returns_clear_api_error(self):
        fullstats_calls = 0

        def rate_limited(method, url, **kwargs):
            nonlocal fullstats_calls
            if url.endswith("promotion/count"):
                return {"adverts": [{"type": 9, "status": 9, "advert_list": [{"advertId": 77}]}]}
            if url.endswith("/api/advert/v2/adverts"):
                return {"adverts": []}
            fullstats_calls += 1
            raise WBRateLimitError(retry_after=None, retry_after_seconds=0)

        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False), patch.object(
            dashboard, "_read_ad_stats_cache", return_value=None
        ), patch.object(dashboard, "_write_ad_stats_cache") as writer, patch.object(
            dashboard, "_wb_request_json", side_effect=rate_limited
        ):
            result = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30))
        self.assertEqual(fullstats_calls, 2)
        self.assertEqual(result["status"], "API_ERROR")
        self.assertFalse(result["from_cache"])
        self.assertEqual(
            result["warnings"],
            ["WB временно ограничил получение рекламной статистики. Повторите позже"],
        )
        writer.assert_called_once()

    def test_parallel_sync_second_request_does_not_call_wb(self):
        started = threading.Event()
        release = threading.Event()
        first_result: dict[str, dict] = {}

        def slow_fetch(*args, **kwargs):
            started.set()
            if not release.wait(2):
                raise AssertionError("test synchronization timed out")
            return {
                "by_nm": {},
                "status": "OK",
                "warnings": [],
                "fetched_at": datetime.now(timezone.utc),
            }

        def run_first():
            first_result["value"] = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30))

        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False), patch.object(
            dashboard, "database_enabled", return_value=False
        ), patch.object(dashboard, "_read_ad_stats_cache", return_value=None), patch.object(
            dashboard, "_write_ad_stats_cache"
        ), patch.object(dashboard, "fetch_advertising_stats", side_effect=slow_fetch) as fetch:
            worker = threading.Thread(target=run_first)
            worker.start()
            self.assertTrue(started.wait(1))
            try:
                second = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30), force=True)
            finally:
                release.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(second["warnings"], ["Обновление рекламной статистики уже выполняется"])
        self.assertEqual(first_result["value"]["status"], "OK")

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

    def test_funnel_and_unit_matrix_share_one_successful_cache(self):
        cache_box: dict[str, dict] = {}
        fullstats_calls = 0
        cache_writes = 0

        def read_cache(date_from, date_to):
            value = cache_box.get("value")
            return copy.deepcopy(value) if value else None

        def write_cache(date_from, date_to, result):
            nonlocal cache_writes
            cache_writes += 1
            value = copy.deepcopy(result)
            value["parser_version"] = dashboard.AD_CACHE_PARSER_VERSION
            value["from_cache"] = True
            cache_box["value"] = value

        def official_api(method, url, **kwargs):
            nonlocal fullstats_calls
            if url.endswith("promotion/count"):
                return {"adverts": [{"type": 9, "status": 9, "advert_list": [{"advertId": 77}]}]}
            if url.endswith("/api/advert/v2/adverts"):
                return {"adverts": []}
            if url.endswith("/adv/v3/fullstats"):
                fullstats_calls += 1
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
                                            {
                                                "nmId": 123456789,
                                                "sum": 1000,
                                                "sum_price": 5000,
                                                "orders": 5,
                                                "views": 100,
                                                "clicks": 10,
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            raise AssertionError(f"Неожиданный URL в тесте: {url}")

        funnel_item = {
            "product": {"nmId": 123456789, "title": "Общий кэш", "vendorCode": "SKU-SHARED-CACHE"},
            "statistic": {"selected": {}, "comparison": {}},
        }
        product = SimpleNamespace(
            units=10.0,
            revenue=10000.0,
            payout=7000.0,
            cogs=4000.0,
            advertising=500.0,
            tax=600.0,
            external_expenses=100.0,
            profit=1800.0,
            sku="SKU-SHARED-CACHE",
            name="Общий кэш",
            nm_id=123456789,
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch.dict(
                    os.environ,
                    {"DASHBOARD_PASSWORD": "test-password", "DATABASE_URL": ""},
                    clear=False,
                )
            )
            stack.enter_context(patch.object(dashboard, "database_enabled", return_value=True))
            stack.enter_context(patch.object(dashboard, "_read_ad_stats_cache", side_effect=read_cache))
            stack.enter_context(patch.object(dashboard, "_write_ad_stats_cache", side_effect=write_cache))
            stack.enter_context(patch.object(dashboard, "_wb_request_json", side_effect=official_api))
            stack.enter_context(patch.object(dashboard, "_load_sales_funnel", return_value=([funnel_item], [])))
            stack.enter_context(patch.object(dashboard, "_actual_sales_rows", return_value=([], None, None)))
            stack.enter_context(patch.object(dashboard, "list_product_summaries", return_value=[product]))
            stack.enter_context(patch.object(dashboard, "list_sale_operations", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_content_cards_meta", return_value=({}, {}, "")))
            stack.enter_context(patch.object(dashboard, "_load_box_tariff_map", return_value=([], {}, "")))
            stack.enter_context(patch.object(dashboard, "_list_unit_matrix_scenarios", return_value=[]))
            stack.enter_context(patch.object(dashboard, "_list_unit_calc_scenarios", return_value=[]))
            with dashboard.app.test_client() as client:
                funnel_response = client.get(
                    "/funnel-ads?date_from=2026-06-01&date_to=2026-06-30",
                    headers={"Authorization": self.auth},
                )
                unit_response = client.get(
                    "/unit-matrix?period_type=weekly&date_from=2026-06-01&date_to=2026-06-30",
                    headers={"Authorization": self.auth},
                )
        self.assertEqual(funnel_response.status_code, 200)
        self.assertEqual(unit_response.status_code, 200)
        self.assertEqual(fullstats_calls, 1)
        self.assertEqual(cache_writes, 1)
        self.assertIn("SKU-SHARED-CACHE", unit_response.get_data(as_text=True))

    def test_v461_cache_is_refetched_by_v462_sync(self):
        old_cache = {
            "by_nm": {},
            "status": "PARTIAL_DATA",
            "warnings": ["старый формат"],
            "fetched_at": datetime.now(timezone.utc),
            "from_cache": True,
            "parser_version": "46.1",
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

        with patch.dict(os.environ, {"DATABASE_URL": ""}, clear=False), patch.object(
            dashboard, "_read_ad_stats_cache", return_value=old_cache
        ), patch.object(
            dashboard, "_write_ad_stats_cache", cache_writer
        ), patch.object(dashboard, "_wb_request_json", side_effect=official_fullstats):
            result = dashboard._load_ad_stats_detailed(date(2026, 6, 1), date(2026, 6, 30))
        self.assertFalse(result["from_cache"])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["by_nm"][123456789]["factual_drr"], 20)
        cache_writer.assert_called_once()


if __name__ == "__main__":
    unittest.main()
