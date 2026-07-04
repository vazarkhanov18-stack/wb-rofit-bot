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
    external_expenses: float
    profit_before_tax: float
    tax: float
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
    """Создаёт и безопасно обновляет таблицы при запуске."""
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
                    unit_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    general_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    external_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    operating_before_ads_tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_ads DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_tax DOUBLE PRECISION NOT NULL DEFAULT 0,
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
                    unit_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    general_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    external_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    operating_before_ads_tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_ads DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                    margin DOUBLE PRECISION NOT NULL DEFAULT 0,
                    drr DOUBLE PRECISION NOT NULL DEFAULT 0,
                    UNIQUE (report_id, sku)
                )
                """
            )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS sale_operations (
                    id BIGSERIAL PRIMARY KEY,
                    report_id BIGINT NOT NULL REFERENCES report_snapshots(id) ON DELETE CASCADE,
                    operation_id TEXT NOT NULL,
                    operation_date DATE,
                    operation_type TEXT NOT NULL DEFAULT 'Продажа',
                    sku TEXT NOT NULL,
                    nm_id BIGINT,
                    name TEXT NOT NULL DEFAULT '',
                    quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
                    revenue DOUBLE PRECISION NOT NULL DEFAULT 0,
                    payout DOUBLE PRECISION NOT NULL DEFAULT 0,
                    wb_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    logistics DOUBLE PRECISION NOT NULL DEFAULT 0,
                    transport DOUBLE PRECISION NOT NULL DEFAULT 0,
                    handling DOUBLE PRECISION NOT NULL DEFAULT 0,
                    storage DOUBLE PRECISION NOT NULL DEFAULT 0,
                    other_withholdings DOUBLE PRECISION NOT NULL DEFAULT 0,
                    fines DOUBLE PRECISION NOT NULL DEFAULT 0,
                    advertising DOUBLE PRECISION NOT NULL DEFAULT 0,
                    cogs DOUBLE PRECISION NOT NULL DEFAULT 0,
                    unit_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    general_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    external_expenses DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit_before_tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    tax DOUBLE PRECISION NOT NULL DEFAULT 0,
                    profit DOUBLE PRECISION NOT NULL DEFAULT 0,
                    margin DOUBLE PRECISION NOT NULL DEFAULT 0,
                    drr DOUBLE PRECISION NOT NULL DEFAULT 0,
                    missing_cost BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (report_id, operation_id)
                )
                """
            )

            # Миграция баз, созданных предыдущими версиями.
            report_columns = {
                "unit_expenses": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "general_expenses": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "external_expenses": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "operating_before_ads_tax": "DOUBLE PRECISION NOT NULL DEFAULT 0",
                "profit_before_tax": "DOUBLE PRECISION NOT NULL DEFAULT 0",
            }
            sku_columns = dict(report_columns)
            for name, sql_type in report_columns.items():
                cursor.execute(f"ALTER TABLE report_snapshots ADD COLUMN IF NOT EXISTS {name} {sql_type}")
            for name, sql_type in sku_columns.items():
                cursor.execute(f"ALTER TABLE sku_snapshots ADD COLUMN IF NOT EXISTS {name} {sql_type}")

            # Для ранее сохранённых отчётов восстанавливаем прибыль до налога.
            cursor.execute(
                """
                UPDATE report_snapshots
                SET profit_before_tax = profit + tax
                WHERE ABS(profit_before_tax) < 0.000001 AND (ABS(profit) > 0.000001 OR ABS(tax) > 0.000001)
                """
            )
            cursor.execute(
                """
                UPDATE sku_snapshots
                SET profit_before_tax = profit + tax
                WHERE ABS(profit_before_tax) < 0.000001 AND (ABS(profit) > 0.000001 OR ABS(tax) > 0.000001)
                """
            )
            cursor.execute(
                "UPDATE report_snapshots SET external_expenses = unit_expenses + general_expenses"
            )
            cursor.execute(
                "UPDATE sku_snapshots SET external_expenses = unit_expenses + general_expenses"
            )

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_report_snapshots_period_end ON report_snapshots(period_end DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sku_snapshots_nm_id ON sku_snapshots(nm_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sale_operations_date ON sale_operations(operation_date DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sale_operations_sku ON sale_operations(sku)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sale_operations_nm_id ON sale_operations(nm_id)"
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
                    unit_expenses, general_expenses, external_expenses,
                    operating_before_ads_tax, profit_before_ads, profit_before_tax,
                    profit, margin, drr, created_at
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, NOW()
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
                    unit_expenses = EXCLUDED.unit_expenses,
                    general_expenses = EXCLUDED.general_expenses,
                    external_expenses = EXCLUDED.external_expenses,
                    operating_before_ads_tax = EXCLUDED.operating_before_ads_tax,
                    profit_before_ads = EXCLUDED.profit_before_ads,
                    profit_before_tax = EXCLUDED.profit_before_tax,
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
                    result.total_unit_external_expenses,
                    result.total_general_external_expenses,
                    result.total_external_expenses,
                    result.total_operating_profit_before_ads_and_tax,
                    result.total_profit_before_ads,
                    result.total_profit_before_tax,
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
                    item.unit_external_expenses,
                    item.general_external_expenses,
                    item.external_expenses,
                    item.operating_profit_before_ads_and_tax,
                    item.profit_before_ads,
                    item.profit_before_tax,
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
                        cogs, tax, advertising, unit_expenses, general_expenses,
                        external_expenses, operating_before_ads_tax, profit_before_ads,
                        profit_before_tax, profit, margin, drr
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    """,
                    records,
                )


            cursor.execute("DELETE FROM sale_operations WHERE report_id = %s", (report_id,))
            operation_records = [
                (
                    report_id,
                    op.operation_id,
                    op.operation_date,
                    op.operation_type,
                    op.sku,
                    op.nm_id,
                    op.name,
                    op.quantity,
                    op.revenue,
                    op.calculated_payout,
                    op.revenue - op.calculated_payout,
                    op.logistics,
                    op.transport,
                    op.handling,
                    op.storage,
                    op.other_withholdings,
                    op.fines,
                    op.advertising,
                    op.cogs,
                    op.unit_external_expenses,
                    op.general_external_expenses,
                    op.external_expenses,
                    op.profit_before_tax,
                    op.tax,
                    op.profit,
                    op.margin,
                    op.drr,
                    bool(op.missing_cost),
                )
                for op in getattr(result, "operations", [])
            ]
            if operation_records:
                cursor.executemany(
                    """
                    INSERT INTO sale_operations (
                        report_id, operation_id, operation_date, operation_type,
                        sku, nm_id, name, quantity, revenue, payout, wb_expenses,
                        logistics, transport, handling, storage, other_withholdings,
                        fines, advertising, cogs, unit_expenses, general_expenses,
                        external_expenses, profit_before_tax, tax, profit, margin,
                        drr, missing_cost
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (report_id, operation_id) DO UPDATE SET
                        operation_date = EXCLUDED.operation_date,
                        operation_type = EXCLUDED.operation_type,
                        sku = EXCLUDED.sku,
                        nm_id = EXCLUDED.nm_id,
                        name = EXCLUDED.name,
                        quantity = EXCLUDED.quantity,
                        revenue = EXCLUDED.revenue,
                        payout = EXCLUDED.payout,
                        wb_expenses = EXCLUDED.wb_expenses,
                        logistics = EXCLUDED.logistics,
                        transport = EXCLUDED.transport,
                        handling = EXCLUDED.handling,
                        storage = EXCLUDED.storage,
                        other_withholdings = EXCLUDED.other_withholdings,
                        fines = EXCLUDED.fines,
                        advertising = EXCLUDED.advertising,
                        cogs = EXCLUDED.cogs,
                        unit_expenses = EXCLUDED.unit_expenses,
                        general_expenses = EXCLUDED.general_expenses,
                        external_expenses = EXCLUDED.external_expenses,
                        profit_before_tax = EXCLUDED.profit_before_tax,
                        tax = EXCLUDED.tax,
                        profit = EXCLUDED.profit,
                        margin = EXCLUDED.margin,
                        drr = EXCLUDED.drr,
                        missing_cost = EXCLUDED.missing_cost,
                        created_at = NOW()
                    """,
                    operation_records,
                )
            return report_id


def list_history(limit: int = 10) -> list[HistoryRow]:
    limit = max(1, min(int(limit), 30))
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                """
                SELECT period_start, period_end, period_type, units, revenue,
                       advertising, external_expenses, profit_before_tax, tax,
                       profit, margin, drr, created_at
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
    unit_expenses: float
    general_expenses: float
    external_expenses: float
    operating_before_ads_tax: float
    profit_before_ads: float
    profit_before_tax: float
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
    unit_expenses: float
    general_expenses: float
    external_expenses: float
    operating_before_ads_tax: float
    profit_before_ads: float
    profit_before_tax: float
    profit: float
    margin: float
    drr: float


def list_dashboard_reports(
    limit: int = 30,
    *,
    period_type: str | None = None,
) -> list[DashboardReportRow]:
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
                       unit_expenses, general_expenses, external_expenses,
                       operating_before_ads_tax, profit_before_ads, profit_before_tax,
                       profit, margin, drr, created_at
                FROM report_snapshots
                {where_sql}
                ORDER BY period_end DESC, period_start DESC, created_at DESC
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
                       unit_expenses, general_expenses, external_expenses,
                       operating_before_ads_tax, profit_before_ads, profit_before_tax,
                       profit, margin, drr, created_at
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
                       advertising, unit_expenses, general_expenses, external_expenses,
                       operating_before_ads_tax, profit_before_ads, profit_before_tax,
                       profit, margin, drr
                FROM sku_snapshots
                WHERE report_id = %s
                ORDER BY profit DESC, revenue DESC, sku ASC
                LIMIT %s
                """,
                (int(report_id), limit),
            )
            return [DashboardSkuRow(**dict(row)) for row in cursor.fetchall()]

@dataclass(frozen=True)
class ProductSummaryRow:
    sku: str
    nm_id: int | None
    name: str
    periods: int
    first_period: date
    last_period: date
    units: float
    revenue: float
    payout: float
    cogs: float
    tax: float
    advertising: float
    unit_expenses: float
    general_expenses: float
    external_expenses: float
    operating_before_ads_tax: float
    profit_before_ads: float
    profit_before_tax: float
    profit: float
    margin: float
    drr: float


@dataclass(frozen=True)
class ProductPeriodRow:
    report_id: int
    period_start: date
    period_end: date
    period_type: str
    units: float
    revenue: float
    payout: float
    cogs: float
    tax: float
    advertising: float
    unit_expenses: float
    general_expenses: float
    external_expenses: float
    operating_before_ads_tax: float
    profit_before_ads: float
    profit_before_tax: float
    profit: float
    margin: float
    drr: float


def _product_filter_sql(
    *,
    period_type: str | None,
    date_from: date | None,
    date_to: date | None,
    query: str = "",
    exact_sku: str = "",
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if period_type:
        clauses.append("r.period_type = %s")
        params.append(period_type)
    if date_from is not None:
        clauses.append("r.period_end >= %s")
        params.append(date_from)
    if date_to is not None:
        clauses.append("r.period_start <= %s")
        params.append(date_to)
    if exact_sku:
        clauses.append("LOWER(BTRIM(s.sku)) = LOWER(BTRIM(%s))")
        params.append(exact_sku)
    elif query:
        pattern = f"%{query.strip()}%"
        clauses.append(
            "(s.sku ILIKE %s OR COALESCE(s.name, '') ILIKE %s OR COALESCE(CAST(s.nm_id AS TEXT), '') ILIKE %s)"
        )
        params.extend([pattern, pattern, pattern])
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_product_summaries(
    limit: int = 500,
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
) -> list[ProductSummaryRow]:
    """Агрегированная экономика товаров по выбранному непересекающемуся типу периодов."""
    limit = max(1, min(int(limit), 2000))
    where_sql, params = _product_filter_sql(
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
    )
    params.append(limit)
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    s.sku,
                    MAX(s.nm_id) AS nm_id,
                    COALESCE(MAX(NULLIF(s.name, '')), s.sku) AS name,
                    COUNT(DISTINCT r.id)::INTEGER AS periods,
                    MIN(r.period_start) AS first_period,
                    MAX(r.period_end) AS last_period,
                    COALESCE(SUM(s.units), 0) AS units,
                    COALESCE(SUM(s.revenue), 0) AS revenue,
                    COALESCE(SUM(s.payout), 0) AS payout,
                    COALESCE(SUM(s.cogs), 0) AS cogs,
                    COALESCE(SUM(s.tax), 0) AS tax,
                    COALESCE(SUM(s.advertising), 0) AS advertising,
                    COALESCE(SUM(s.unit_expenses), 0) AS unit_expenses,
                    COALESCE(SUM(s.general_expenses), 0) AS general_expenses,
                    COALESCE(SUM(s.external_expenses), 0) AS external_expenses,
                    COALESCE(SUM(s.operating_before_ads_tax), 0) AS operating_before_ads_tax,
                    COALESCE(SUM(s.profit_before_ads), 0) AS profit_before_ads,
                    COALESCE(SUM(s.profit_before_tax), 0) AS profit_before_tax,
                    COALESCE(SUM(s.profit), 0) AS profit,
                    CASE WHEN ABS(COALESCE(SUM(s.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(s.profit), 0) / SUM(s.revenue) ELSE 0 END AS margin,
                    CASE WHEN ABS(COALESCE(SUM(s.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(s.advertising), 0) / SUM(s.revenue) ELSE 0 END AS drr
                FROM sku_snapshots s
                JOIN report_snapshots r ON r.id = s.report_id
                {where_sql}
                GROUP BY s.sku
                ORDER BY profit DESC, revenue DESC, s.sku ASC
                LIMIT %s
                """,
                tuple(params),
            )
            return [ProductSummaryRow(**dict(row)) for row in cursor.fetchall()]


def get_product_summary(
    sku: str,
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
) -> ProductSummaryRow | None:
    where_sql, params = _product_filter_sql(
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
        exact_sku=sku,
    )
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    MIN(s.sku) AS sku,
                    MAX(s.nm_id) AS nm_id,
                    COALESCE(MAX(NULLIF(s.name, '')), MIN(s.sku)) AS name,
                    COUNT(DISTINCT r.id)::INTEGER AS periods,
                    MIN(r.period_start) AS first_period,
                    MAX(r.period_end) AS last_period,
                    COALESCE(SUM(s.units), 0) AS units,
                    COALESCE(SUM(s.revenue), 0) AS revenue,
                    COALESCE(SUM(s.payout), 0) AS payout,
                    COALESCE(SUM(s.cogs), 0) AS cogs,
                    COALESCE(SUM(s.tax), 0) AS tax,
                    COALESCE(SUM(s.advertising), 0) AS advertising,
                    COALESCE(SUM(s.unit_expenses), 0) AS unit_expenses,
                    COALESCE(SUM(s.general_expenses), 0) AS general_expenses,
                    COALESCE(SUM(s.external_expenses), 0) AS external_expenses,
                    COALESCE(SUM(s.operating_before_ads_tax), 0) AS operating_before_ads_tax,
                    COALESCE(SUM(s.profit_before_ads), 0) AS profit_before_ads,
                    COALESCE(SUM(s.profit_before_tax), 0) AS profit_before_tax,
                    COALESCE(SUM(s.profit), 0) AS profit,
                    CASE WHEN ABS(COALESCE(SUM(s.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(s.profit), 0) / SUM(s.revenue) ELSE 0 END AS margin,
                    CASE WHEN ABS(COALESCE(SUM(s.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(s.advertising), 0) / SUM(s.revenue) ELSE 0 END AS drr
                FROM sku_snapshots s
                JOIN report_snapshots r ON r.id = s.report_id
                {where_sql}
                HAVING COUNT(*) > 0
                """,
                tuple(params),
            )
            row = cursor.fetchone()
            return ProductSummaryRow(**dict(row)) if row else None


def list_product_periods(
    sku: str,
    limit: int = 500,
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ProductPeriodRow]:
    limit = max(1, min(int(limit), 1000))
    where_sql, params = _product_filter_sql(
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
        exact_sku=sku,
    )
    params.append(limit)
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    r.id AS report_id, r.period_start, r.period_end, r.period_type,
                    s.units, s.revenue, s.payout, s.cogs, s.tax, s.advertising,
                    s.unit_expenses, s.general_expenses, s.external_expenses,
                    s.operating_before_ads_tax, s.profit_before_ads,
                    s.profit_before_tax, s.profit, s.margin, s.drr
                FROM sku_snapshots s
                JOIN report_snapshots r ON r.id = s.report_id
                {where_sql}
                ORDER BY r.period_start ASC, r.period_end ASC, r.created_at ASC
                LIMIT %s
                """,
                tuple(params),
            )
            return [ProductPeriodRow(**dict(row)) for row in cursor.fetchall()]



@dataclass(frozen=True)
class SaleOperationRow:
    operation_id: str
    operation_date: date | None
    operation_type: str
    period_start: date
    period_end: date
    period_type: str
    sku: str
    nm_id: int | None
    name: str
    quantity: float
    revenue: float
    payout: float
    wb_expenses: float
    logistics: float
    transport: float
    handling: float
    storage: float
    other_withholdings: float
    fines: float
    advertising: float
    cogs: float
    unit_expenses: float
    general_expenses: float
    external_expenses: float
    profit_before_tax: float
    tax: float
    profit: float
    margin: float
    drr: float
    missing_cost: bool


@dataclass(frozen=True)
class SaleOperationSummary:
    operations: int
    quantity: float
    revenue: float
    payout: float
    wb_expenses: float
    advertising: float
    cogs: float
    external_expenses: float
    profit_before_tax: float
    tax: float
    profit: float
    margin: float
    drr: float
    missing_cost_count: int


def _operations_where_sql(
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
    operation_type: str = "",
    only_negative: bool = False,
    only_missing_cost: bool = False,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if period_type:
        clauses.append("r.period_type = %s")
        params.append(period_type)
    if date_from is not None:
        clauses.append("COALESCE(o.operation_date, r.period_start) >= %s")
        params.append(date_from)
    if date_to is not None:
        clauses.append("COALESCE(o.operation_date, r.period_end) <= %s")
        params.append(date_to)
    if query:
        pattern = f"%{query.strip()}%"
        clauses.append(
            "(o.sku ILIKE %s OR COALESCE(o.name, '') ILIKE %s OR COALESCE(CAST(o.nm_id AS TEXT), '') ILIKE %s)"
        )
        params.extend([pattern, pattern, pattern])
    if operation_type:
        clauses.append("o.operation_type = %s")
        params.append(operation_type)
    if only_negative:
        clauses.append("o.profit < -0.000001")
    if only_missing_cost:
        clauses.append("o.missing_cost = TRUE")
    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


def list_sale_operations(
    limit: int = 500,
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
    operation_type: str = "",
    only_negative: bool = False,
    only_missing_cost: bool = False,
) -> list[SaleOperationRow]:
    limit = max(1, min(int(limit), 5000))
    where_sql, params = _operations_where_sql(
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
        operation_type=operation_type,
        only_negative=only_negative,
        only_missing_cost=only_missing_cost,
    )
    params.append(limit)
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    o.operation_id, o.operation_date, o.operation_type,
                    r.period_start, r.period_end, r.period_type,
                    o.sku, o.nm_id, o.name, o.quantity, o.revenue, o.payout,
                    o.wb_expenses, o.logistics, o.transport, o.handling, o.storage,
                    o.other_withholdings, o.fines, o.advertising, o.cogs,
                    o.unit_expenses, o.general_expenses, o.external_expenses,
                    o.profit_before_tax, o.tax, o.profit, o.margin, o.drr,
                    o.missing_cost
                FROM sale_operations o
                JOIN report_snapshots r ON r.id = o.report_id
                {where_sql}
                ORDER BY COALESCE(o.operation_date, r.period_start) DESC, o.id DESC
                LIMIT %s
                """,
                tuple(params),
            )
            return [SaleOperationRow(**dict(row)) for row in cursor.fetchall()]


def sale_operations_summary(
    *,
    period_type: str | None = "weekly",
    date_from: date | None = None,
    date_to: date | None = None,
    query: str = "",
    operation_type: str = "",
    only_negative: bool = False,
    only_missing_cost: bool = False,
) -> SaleOperationSummary:
    where_sql, params = _operations_where_sql(
        period_type=period_type,
        date_from=date_from,
        date_to=date_to,
        query=query,
        operation_type=operation_type,
        only_negative=only_negative,
        only_missing_cost=only_missing_cost,
    )
    with _connect() as connection:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute(
                f"""
                SELECT
                    COUNT(*)::INTEGER AS operations,
                    COALESCE(SUM(o.quantity), 0) AS quantity,
                    COALESCE(SUM(o.revenue), 0) AS revenue,
                    COALESCE(SUM(o.payout), 0) AS payout,
                    COALESCE(SUM(o.wb_expenses), 0) AS wb_expenses,
                    COALESCE(SUM(o.advertising), 0) AS advertising,
                    COALESCE(SUM(o.cogs), 0) AS cogs,
                    COALESCE(SUM(o.external_expenses), 0) AS external_expenses,
                    COALESCE(SUM(o.profit_before_tax), 0) AS profit_before_tax,
                    COALESCE(SUM(o.tax), 0) AS tax,
                    COALESCE(SUM(o.profit), 0) AS profit,
                    CASE WHEN ABS(COALESCE(SUM(o.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(o.profit), 0) / SUM(o.revenue) ELSE 0 END AS margin,
                    CASE WHEN ABS(COALESCE(SUM(o.revenue), 0)) > 0.000001
                         THEN COALESCE(SUM(o.advertising), 0) / SUM(o.revenue) ELSE 0 END AS drr,
                    COALESCE(SUM(CASE WHEN o.missing_cost THEN 1 ELSE 0 END), 0)::INTEGER AS missing_cost_count
                FROM sale_operations o
                JOIN report_snapshots r ON r.id = o.report_id
                {where_sql}
                """,
                tuple(params),
            )
            row = cursor.fetchone() or {}
            return SaleOperationSummary(**dict(row))
