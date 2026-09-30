from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable

from wb_api import WbApiError, get_advertising_stats, get_sales_report
from wb_api_profit import analyze_api_report, apply_advertising
from wb_db import missing_daily_ranges, report_operation_ids, save_report


logger = logging.getLogger("wb-profit-daily-sync")
BASE_DIR = Path(__file__).resolve().parent
COSTS_FILE = BASE_DIR / "data" / "costs.xlsx"
_sync_lock = threading.Lock()


class DailySyncAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class DailySyncResult:
    date_from: date
    date_to: date
    days_requested: int
    days_updated: int
    rows_received: int
    rows_inserted: int
    rows_updated: int


def _days_inclusive(date_from: date, date_to: date) -> int:
    return max(0, (date_to - date_from).days + 1)


async def sync_daily_range(
    date_from: date,
    date_to: date,
    *,
    token: str | None = None,
    fetch_report: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    analyze_report: Callable[..., Any] | None = None,
    save_report_fn: Callable[..., int | None] | None = None,
    missing_ranges_fn: Callable[[date, date], list[tuple[date, date]]] | None = None,
    operation_ids_fn: Callable[[date, date, str], set[str]] | None = None,
    fetch_advertising_fn: Callable[..., Awaitable[Any]] | None = None,
) -> DailySyncResult:
    """Дозагружает все отсутствующие daily-интервалы без перекрытий и дублей.

    Один непрерывный пропуск запрашивается у WB одним вызовом. Сохранённый интервал
    затем считается покрытым, даже если WB вернул за него пустой отчёт. Повторный
    запуск не создаёт новый report_snapshot и не дублирует sale_operations.
    """
    if date_to < date_from:
        raise ValueError("Дата окончания не может быть раньше даты начала.")
    api_token = (token if token is not None else os.getenv("WB_API_TOKEN", "")).strip()
    if not api_token:
        raise RuntimeError("WB_API_TOKEN не задан.")
    if not _sync_lock.acquire(blocking=False):
        raise DailySyncAlreadyRunning("Синхронизация daily-отчётов уже выполняется.")

    use_default_fetch = fetch_report is None
    fetch = fetch_report or get_sales_report
    analyzer = analyze_report or analyze_api_report
    saver = save_report_fn or save_report
    load_missing = missing_ranges_fn or missing_daily_ranges
    load_ids = operation_ids_fn or report_operation_ids
    rows_received = 0
    rows_inserted = 0
    rows_updated = 0
    days_updated = 0
    try:
        missing_ranges = await asyncio.to_thread(load_missing, date_from, date_to)
        days_requested = sum(_days_inclusive(start, end) for start, end in missing_ranges)
        for gap_start, gap_end in missing_ranges:
            rows = await fetch(api_token, gap_start, gap_end, period="daily")
            rows_received += len(rows)
            result = await asyncio.to_thread(
                analyzer,
                rows,
                COSTS_FILE,
                gap_start,
                gap_end,
            )
            # Граница snapshot должна описывать именно запрошенный непокрытый
            # интервал, а не служебные dateFrom/dateTo внутри строк WB.
            result.period_start = gap_start
            result.period_end = gap_end
            ad_fetcher = fetch_advertising_fn or (get_advertising_stats if use_default_fetch else None)
            if ad_fetcher is not None:
                try:
                    ad_stats = await ad_fetcher(api_token, gap_start, gap_end)
                    result = apply_advertising(result, ad_stats)
                except WbApiError as exc:
                    logger.warning("Не удалось загрузить рекламу WB при daily sync: %s", exc)
                    result.advertising_warning = str(exc)
            before_ids = await asyncio.to_thread(load_ids, gap_start, gap_end, "daily")
            report_id = await asyncio.to_thread(
                saver,
                result,
                period_type="daily",
                source="api-daily-sync",
            )
            if report_id is None:
                raise RuntimeError("Не удалось сохранить daily-отчёт в PostgreSQL.")
            after_ids = {str(op.operation_id) for op in getattr(result, "operations", [])}
            rows_inserted += len(after_ids - before_ids)
            rows_updated += len(after_ids & before_ids)
            days_updated += _days_inclusive(gap_start, gap_end)

        logger.info(
            "sync_daily: from=%s to=%s days_requested=%d rows_received=%d rows_inserted=%d rows_updated=%d",
            date_from,
            date_to,
            days_requested,
            rows_received,
            rows_inserted,
            rows_updated,
        )
        return DailySyncResult(
            date_from=date_from,
            date_to=date_to,
            days_requested=days_requested,
            days_updated=days_updated,
            rows_received=rows_received,
            rows_inserted=rows_inserted,
            rows_updated=rows_updated,
        )
    finally:
        _sync_lock.release()
