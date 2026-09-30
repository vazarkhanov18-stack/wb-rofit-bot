from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import dashboard
from wb_api import WbApiError
from wb_daily_sync import sync_daily_range
from wb_db import SalesDataStatus


def operation(
    operation_id: str,
    operation_date: date,
    *,
    period_type: str,
    operation_type: str = "Продажа",
    quantity: float = 1.0,
):
    return SimpleNamespace(
        operation_id=operation_id,
        operation_date=operation_date,
        operation_type=operation_type,
        period_start=operation_date,
        period_end=operation_date,
        period_type=period_type,
        sku="sku-1",
        nm_id=1,
        name="Товар",
        quantity=quantity,
        revenue=100.0,
        payout=80.0,
        wb_expenses=20.0,
        logistics=0.0,
        transport=0.0,
        handling=0.0,
        storage=0.0,
        other_withholdings=0.0,
        fines=0.0,
        advertising=0.0,
        cogs=40.0,
        unit_expenses=0.0,
        general_expenses=0.0,
        external_expenses=0.0,
        profit_before_tax=40.0,
        tax=6.0,
        profit=34.0,
        margin=0.34,
        drr=0.0,
        missing_cost=False,
    )


class ActualSalesTests(unittest.TestCase):
    def setUp(self):
        self.status = SalesDataStatus(
            last_weekly=date(2026, 8, 30),
            last_daily=date(2026, 9, 30),
            latest_operation=date(2026, 9, 30),
        )

    def actual(self, weekly, daily, *, limit=500, date_to=date(2026, 9, 30)):
        def load(_limit, *, period_type, date_from, date_to, **_filters):
            source = weekly if period_type == "weekly" else daily
            return [
                row
                for row in source
                if (date_from is None or row.operation_date >= date_from)
                and (date_to is None or row.operation_date <= date_to)
            ]

        with patch.object(dashboard, "get_sales_data_status", return_value=self.status), patch.object(
            dashboard, "list_sale_operations", side_effect=load
        ):
            return dashboard._actual_sales_rows(
                limit=limit,
                date_from=date(2026, 8, 6),
                date_to=date_to,
                query="",
                operation_type="",
                only_negative=False,
                only_missing_cost=False,
            )

    def test_actual_combines_closed_weekly_and_daily_tail(self):
        weekly = [operation("w1", date(2026, 8, 6), period_type="weekly")]
        daily = [operation("d1", date(2026, 8, 31), period_type="daily"), operation("d2", date(2026, 9, 30), period_type="daily")]
        rows, summary, cutoff = self.actual(weekly, daily)
        self.assertEqual({row.operation_id for row in rows}, {"w1", "d1", "d2"})
        self.assertEqual(summary.operations, 3)
        self.assertEqual(cutoff, date(2026, 8, 30))

    def test_weekly_daily_overlap_and_duplicate_ids_are_not_doubled(self):
        weekly = [operation("same", date(2026, 8, 30), period_type="weekly")]
        daily = [
            operation("same", date(2026, 8, 30), period_type="daily"),
            operation("daily-id", date(2026, 9, 1), period_type="daily"),
            operation("daily-id", date(2026, 9, 1), period_type="daily"),
        ]
        rows, summary, _ = self.actual(weekly, daily)
        self.assertEqual([row.operation_id for row in rows].count("same"), 1)
        self.assertEqual([row.operation_id for row in rows].count("daily-id"), 1)
        self.assertEqual(summary.operations, 2)

    def test_operations_after_14_september_are_visible(self):
        daily = [operation("late", date(2026, 9, 29), period_type="daily")]
        rows, _, _ = self.actual([], daily)
        self.assertEqual(rows[0].operation_id, "late")

    def test_date_to_is_inclusive(self):
        daily = [
            operation("last-day", date(2026, 9, 30), period_type="daily"),
            operation("too-late", date(2026, 10, 1), period_type="daily"),
        ]
        rows, _, _ = self.actual([], daily, date_to=date(2026, 9, 30))
        self.assertEqual([row.operation_id for row in rows], ["last-day"])

    def test_table_limit_does_not_limit_totals(self):
        daily = [operation(f"d-{index}", date(2026, 9, 1), period_type="daily") for index in range(600)]
        rows, summary, _ = self.actual([], daily, limit=500)
        self.assertEqual(len(rows), 500)
        self.assertEqual(summary.operations, 600)

    def test_sold_returned_and_net_are_separate(self):
        rows = [
            operation("sale", date(2026, 9, 1), period_type="daily", quantity=5),
            operation("return", date(2026, 9, 2), period_type="daily", operation_type="Возврат", quantity=-2),
        ]
        summary = dashboard._summarize_sale_rows(rows)
        self.assertEqual(summary.sold_quantity, 5)
        self.assertEqual(summary.returned_quantity, 2)
        self.assertEqual(summary.net_quantity, 3)

    def test_wb_expense_is_an_operation_but_not_a_sold_unit(self):
        expense = operation(
            "expense",
            date(2026, 9, 3),
            period_type="daily",
            operation_type="Расход WB",
            quantity=50,
        )
        summary = dashboard._summarize_sale_rows([expense])
        self.assertEqual(summary.operations, 1)
        self.assertEqual(summary.sold_quantity, 0)
        self.assertEqual(summary.returned_quantity, 0)
        self.assertEqual(summary.net_quantity, 0)


class DailySyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_fills_15_to_30_september(self):
        saved = []

        async def fetch(_token, date_from, date_to, *, period):
            self.assertEqual((date_from, date_to, period), (date(2026, 9, 15), date(2026, 9, 30), "daily"))
            return [{"row": 1}, {"row": 2}]

        report = SimpleNamespace(operations=[SimpleNamespace(operation_id="a"), SimpleNamespace(operation_id="b")])
        result = await sync_daily_range(
            date(2026, 9, 15),
            date(2026, 9, 30),
            token="test-token",
            fetch_report=fetch,
            analyze_report=lambda *_args: report,
            save_report_fn=lambda value, **kwargs: saved.append((value, kwargs)) or 1,
            missing_ranges_fn=lambda *_args: [(date(2026, 9, 15), date(2026, 9, 30))],
            operation_ids_fn=lambda *_args: set(),
        )
        self.assertEqual(result.days_updated, 16)
        self.assertEqual(result.rows_inserted, 2)
        self.assertEqual(len(saved), 1)

    async def test_repeated_sync_creates_no_duplicates(self):
        fetch = AsyncMock(return_value=[])
        result = await sync_daily_range(
            date(2026, 9, 15),
            date(2026, 9, 30),
            token="test-token",
            fetch_report=fetch,
            missing_ranges_fn=lambda *_args: [],
        )
        self.assertEqual(result.rows_inserted, 0)
        self.assertEqual(result.days_updated, 0)
        fetch.assert_not_awaited()


class SalesRouteTests(unittest.TestCase):
    def test_wb_error_from_sync_redirects_instead_of_http_500(self):
        status = SalesDataStatus(date(2026, 8, 30), date(2026, 9, 14), date(2026, 9, 14))
        with dashboard.app.test_request_context(
            "/sales/sync",
            method="POST",
            data={"csrf": "ok", "return_query": "period_type=actual&date_to=2026-09-30"},
        ), patch.object(dashboard, "_valid_csrf", return_value=True), patch.object(
            dashboard, "get_sales_data_status", return_value=status
        ), patch.object(
            dashboard, "_local_today", return_value=date(2026, 9, 30)
        ), patch.object(
            dashboard, "sync_daily_range", AsyncMock(side_effect=WbApiError("WB временно недоступен"))
        ):
            response = dashboard.sales_sync()
        self.assertEqual(response.status_code, 303)
        self.assertIn("sync_error", response.location)


if __name__ == "__main__":
    unittest.main()
