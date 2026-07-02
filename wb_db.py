from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime

import psycopg
from psycopg.rows import dict_row

from wb_profit import ReportResult


@dataclass(frozen=True)
class HistoryRow:
    period_start: date
    period_end: date
    period_type: str
    units: float
    revenue: float
    advertising: float
    profit: float
    margin: float
    drr: float
    created_at: datetime


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def database_enabled() -> bool:
    return bool(database_url())


def _connect():
    url = database_url()
    if not url:
        raise RuntimeError("Не задана переменная DATABASE_URL.")
    return psycopg.connect(url, connect_timeout=12)


def init_database() -> None:
    """Создаёт таблицы при первом запуске. Повторный запуск безопасен."""
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS report_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    period_start DATE NOT NULL,
                    period_end DATE NOT NULL,
                    period_type TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'api',
                    units DOUBLE PRECISION NOT NULL DEFAULT 0,
                    revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
                    payout DOUBLE PRECISION NOT NULL DEFAULT 0,
                    cogs DOUBLE PRECISION NOT NULL DEFAULT 0,
                    tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    advertising DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_ads DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                    margin DOUBLE PRECISION NOT NULL DEFAULT 0,
                    drr DOUBLE PRECISION NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (period_start, period_end, period_type)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sku_snapshots (
                    id BIGSERIAL PRIMARY KEY,
                    report_id BIGINT NOT NULL REFERENCES report_snapshots(id) ON DELETE CASCADE,
                    sku TEXT NOT NULL,
                    nm_id BIGINT,
                    name TEXT NOT NULL DEFAULT '',
                    units DOUBLE PRECISION NOT NULL DEFAULT 0,
                    revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
                    payout DOUBLE PRECISION NOT NULL DEFAULT 0,
                    cogs DOUBLE PRECISION NOT NULL DEFAULT 0,
                    tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    advertising DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_ads DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                    margin DOUBLE PRECISION NOT NULL DEFAULT 0,
                    drr DOUBLE PRECISION NOT NULL DEFAULT 0,
                    UNIQUE (report_id, sku)
                )
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_report_snapshots_period_end ON report_snapshots(period_end DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sku_snapshots_nm_id ON sku_snapshots(nm_id)"
            )


def save_report(result: ReportResult, *, period_type: str, source: str = "api") -> int | None:
    """Сохраняет или обновляет отчёт и его строки по SKU."""
    if result.period_start is None or result.period_end is None:
        return None

    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO report_snapshots (
                    period_start, period_end, period_type, source,
                    units, revenue, payout, cogs, tax, advertising,
                    profit_before_ads, profit, margin, drr, created_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, NOW()
                )
                ON CONFLICT (period_start, period_end, period_type)
                DO UPDATE SET
                    source = EXCLUDED.source,
                    units = EXCLUDED.units,
                    revenue = EXCLUDED.revenue,
                    payout = EXCLUDED.payout,
                    cogs = EXCLUDED.cogs,
                    tax = EXCLUDED.tax,
                    advertising = EXCLUDED.advertising,
                    profit_before_ads = EXCLUDED.profit_before_ads,
                    profit = EXCLUDED.profit,
                    margin = EXCLUDED.margin,
                    drr = EXCLUDED.drr,
                    created_at = NOW()
                RETURNING id
                """,
                (
                    result.period_start,
                    result.period_end,
                    period_type,
                    source,
                    result.total_units,
                    result.total_revenue,
                    result.total_payout,
                    result.total_cogs,
                    result.total_tax,
                    result.total_advertising,
                    result.total_profit_before_ads,
                    result.total_profit,
                    result.margin,
                    result.drr,
                ),
            )
            row = cursor.fetchone()
            report_id = int(row[0]) if row else None
            if report_id is None:
                return None

            cursor.execute("DELETE FROM sku_snapshots WHERE report_id = %s", (report_id,))
            records = [
                (
                    report_id,
                    item.sku,
                    item.nm_id,
                    item.name,
                    item.sold_units,
                    item.revenue,
                    item.calculated_payout,
                    item.cogs,
                    item.tax,
                    item.advertising,
                    item.profit_before_ads,
                    item.profit,
                    item.margin,
                    item.drr,
                )
                for item in result.active_items
            ]
            if records:
                cursor.executemany(
                    """
                    INSERT INTO sku_snapshots (
                        report_id, sku, nm_id, name, units, revenue, payout,
                        cogs, tax, advertising, profit_before_ads, profit, margin, drr
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    records,
                )
            return report_id


def list_history(limit: int = 10) -> list[HistoryRow]:
    limit = max(1, min(int(limit), 30))
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT period_start, period_end, period_type, units, revenue,
                       advertising, profit, margin, drr, created_at
                FROM report_snapshots
                ORDER BY period_end DESC, period_start DESC, created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            return [HistoryRow(**dict(row)) for row in cursor.fetchall()]


def database_status() -> tuple[int, datetime | None]:
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*), MAX(created_at) FROM report_snapshots")
            row = cursor.fetchone()
            if not row:
                return 0, None
            return int(row[0]), row[1]


@dataclass(frozen=True)
class DashboardReportRow:
    report_id: int
    period_start: date
    period_end: date
    period_type: str
    source: str
    units: float
    revenue: float
    payout: float
    cogs: float
    tax: float
    advertising: float
    profit_before_ads: float
    profit: float
    margin: float
    drr: float
    created_at: datetime


@dataclass(frozen=True)
class DashboardSkuRow:
    sku: str
    nm_id: int | None
    name: str
    units: float
    revenue: float
    payout: float
    cogs: float
    tax: float
    advertising: float
    profit_before_ads: float
    profit: float
    margin: float
    drr: float


def list_dashboard_reports(
    limit: int = 30,
    *,
    period_type: str | None = None,
) -> list[DashboardReportRow]:
    """Возвращает последние сохранённые периоды для веб-дашборда."""
    limit = max(1, min(int(limit), 250))
    where_sql = ""
    params: list[object] = []
    if period_type:
        where_sql = "WHERE period_type = %s"
        params.append(period_type)
    params.append(limit)

    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT id AS report_id, period_start, period_end, period_type, source,
                       units, revenue, payout, cogs, tax, advertising,
                       profit_before_ads, profit, margin, drr, created_at
                FROM report_snapshots
                {where_sql}
                ORDER BY created_at DESC, period_end DESC, period_start DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return [DashboardReportRow(**dict(row)) for row in cursor.fetchall()]


def get_dashboard_report(report_id: int) -> DashboardReportRow | None:
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT id AS report_id, period_start, period_end, period_type, source,
                       units, revenue, payout, cogs, tax, advertising,
                       profit_before_ads, profit, margin, drr, created_at
                FROM report_snapshots
                WHERE id = %s
                """,
                (int(report_id),),
            )
            row = cursor.fetchone()
            return DashboardReportRow(**dict(row)) if row else None


def list_dashboard_skus(report_id: int, limit: int = 200) -> list[DashboardSkuRow]:
    limit = max(1, min(int(limit), 500))
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT sku, nm_id, name, units, revenue, payout, cogs, tax,
                       advertising, profit_before_ads, profit, margin, drr
                FROM sku_snapshots
                WHERE report_id = %s
                ORDER BY profit DESC, revenue DESC, sku ASC
                LIMIT %s
                """,
                (int(report_id), limit),
            )
            return [DashboardSkuRow(**dict(row)) for row in cursor.fetchall()]
