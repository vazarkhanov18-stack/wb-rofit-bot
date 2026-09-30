from __future__ import annotations

import sqlite3
import unittest
from datetime import date

import dashboard
from wb_db import _recalculate_saved_tax
from wb_profit import ReportResult, SaleOperation, SkuResult, build_messages


class TaxTests(unittest.TestCase):
    def test_sales_returns_and_expenses_use_one_percent(self):
        for revenue, expected_tax in ((1000.0, 10.0), (-1000.0, -10.0), (0.0, 0.0)):
            with self.subTest(revenue=revenue):
                item = SkuResult(sku="sku", revenue=revenue, payout_for_goods=800)
                operation = SaleOperation(
                    operation_id="test", operation_date=date(2026, 9, 30),
                    operation_type="Продажа" if revenue > 0 else "Возврат" if revenue < 0 else "Расход WB",
                    sku="sku", revenue=revenue, payout_for_goods=800,
                )
                for row in (item, operation):
                    self.assertAlmostEqual(row.tax, expected_tax)
                    self.assertAlmostEqual(row.profit, row.profit_before_tax - expected_tax)
                report = ReportResult(date(2026, 9, 30), date(2026, 9, 30), [item], [operation])
                self.assertAlmostEqual(report.total_tax, expected_tax)

    def test_calculator_default_and_report_label(self):
        with dashboard.app.test_request_context("/unit-calculator"):
            self.assertEqual(dashboard._unit_calculator_values()["tax_pct"], 0.01)
        for entered, expected in (("1", 0.01), ("0,5", 0.005), ("0", 0), ("6", 0.06)):
            with dashboard.app.test_request_context(f"/unit-calculator?tax_pct={entered}&price_spp=1000"):
                values = dashboard._unit_calculator_values()
                self.assertEqual(values["tax_pct"], expected)
                calculated, _ = dashboard._calculate_unit_plan(values)
                self.assertAlmostEqual(calculated.tax, 1000 * expected)
        report = ReportResult(
            date(2026, 9, 30), date(2026, 9, 30),
            [SkuResult(sku="sku", revenue=1000, sold_units=1, payout_for_goods=800)],
        )
        text = "\n".join(build_messages(report))
        self.assertIn("УСН 1%", text)
        self.assertNotIn("УСН 6%", text)

    def test_saved_history_recalculation_is_idempotent(self):
        # Execute the migration's actual UPDATE statements against an isolated
        # SQL store, translating only psycopg's named parameter syntax.
        with sqlite3.connect(":memory:") as connection:
            for table in ("report_snapshots", "sku_snapshots", "sale_operations"):
                connection.execute(f"""
                    CREATE TABLE {table} (
                        revenue REAL, tax REAL, profit_before_tax REAL,
                        advertising REAL, profit REAL, margin REAL, profit_before_ads REAL
                    )
                """)
                for revenue, before_tax, advertising in (
                    (437370.82, 57928.11, 23365.61), (-1000, -200, 0), (0, -50, 0),
                ):
                    old_tax = revenue * 0.06
                    connection.execute(
                        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (revenue, old_tax, before_tax, advertising, before_tax - old_tax,
                         (before_tax - old_tax) / revenue if revenue else 0,
                         before_tax + advertising - old_tax),
                    )

            class CursorAdapter:
                def execute(self, sql, params):
                    connection.execute(sql.replace("%(rate)s", ":rate"), params)

            _recalculate_saved_tax(CursorAdapter())
            for table in ("report_snapshots", "sku_snapshots", "sale_operations"):
                rows = connection.execute(f"SELECT * FROM {table}").fetchall()
                self.assertEqual(len(rows), 3)
                self.assertAlmostEqual(rows[0][1], 4373.7082)
                self.assertAlmostEqual(rows[0][4], 53554.4018)
                self.assertEqual(rows[0][2], 57928.11)
                self.assertEqual(rows[0][3], 23365.61)
                for revenue, tax, before_tax, advertising, profit, margin, before_ads in rows:
                    self.assertAlmostEqual(tax, revenue * 0.01)
                    self.assertAlmostEqual(profit, before_tax - tax)
                    self.assertAlmostEqual(margin, profit / revenue if revenue else 0)
                    if table != "sale_operations":
                        self.assertAlmostEqual(before_ads, before_tax + advertising - tax)
            changes = connection.total_changes
            _recalculate_saved_tax(CursorAdapter())
            self.assertEqual(connection.total_changes, changes)


if __name__ == "__main__":
    unittest.main()
