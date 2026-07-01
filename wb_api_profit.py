from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable

from wb_profit import (
    UNALLOCATED_SKU,
    ReportResult,
    SkuResult,
    as_date,
    as_number,
    load_costs,
    normalize_sku,
    normalize_text,
    valid_sku,
)


def _value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    value = row.get(key, default)
    return default if value is None else value


def analyze_api_report(
    rows: Iterable[dict[str, Any]],
    costs_path: str | Path,
    requested_start: date | None = None,
    requested_end: date | None = None,
) -> ReportResult:
    """Считает прибыль из строк нового Finance API WB."""
    rows = list(rows)
    costs = load_costs(costs_path)

    results: dict[str, SkuResult] = {
        key: SkuResult(sku=cost.sku, name=cost.name, unit_cost=cost.unit_cost)
        for key, cost in costs.items()
    }

    barcode_to_sku: dict[str, str] = {}
    report_names: dict[str, str] = {}
    for row in rows:
        vendor_code = _value(row, "vendorCode")
        barcode = _value(row, "sku")
        if valid_sku(vendor_code):
            sku_key = normalize_sku(vendor_code)
            if barcode not in (None, ""):
                barcode_to_sku[str(barcode).strip()] = sku_key
            title = str(_value(row, "title", "") or "").strip()
            if title:
                report_names[sku_key] = title

    starts: list[date] = []
    ends: list[date] = []

    for row in rows:
        reason = normalize_text(_value(row, "sellerOperName") or _value(row, "docTypeName"))
        is_sale = reason == "продажа" or reason.startswith("продажа ")
        is_return = (
            "возврат" in reason
            and reason != "возмещение издержек по перевозке/по складским операциям с товаром"
        )

        vendor_code = _value(row, "vendorCode")
        sku_key = normalize_sku(vendor_code) if valid_sku(vendor_code) else ""

        if not sku_key and (is_sale or is_return):
            barcode = _value(row, "sku")
            sku_key = barcode_to_sku.get(str(barcode).strip(), "")

        if not sku_key:
            sku_key = normalize_sku(UNALLOCATED_SKU)

        if sku_key not in results:
            display_sku = str(vendor_code).strip() if valid_sku(vendor_code) else UNALLOCATED_SKU
            display_name = report_names.get(
                sku_key,
                "Расходы без артикула в отчёте WB" if display_sku == UNALLOCATED_SKU else display_sku,
            )
            results[sku_key] = SkuResult(sku=display_sku, name=display_name, unit_cost=0.0)

        item = results[sku_key]
        if not item.name:
            item.name = report_names.get(sku_key, item.sku)

        start = as_date(_value(row, "dateFrom"))
        end = as_date(_value(row, "dateTo"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
        if not start and not end:
            sale_date = as_date(_value(row, "saleDt"))
            if sale_date:
                starts.append(sale_date)
                ends.append(sale_date)

        if is_sale or is_return:
            qty = as_number(_value(row, "quantity"))
            revenue = as_number(_value(row, "retailAmount"))
            payout = as_number(_value(row, "forPay"))

            if is_return:
                qty = -abs(qty) if qty >= 0 else qty
                revenue = -abs(revenue) if revenue >= 0 else revenue
                payout = -abs(payout) if payout >= 0 else payout

            item.sold_units += qty
            item.revenue += revenue
            item.payout_for_goods += payout

        item.logistics += as_number(_value(row, "deliveryService"))
        item.transport += as_number(_value(row, "rebillLogisticCost"))
        item.handling += as_number(_value(row, "paidAcceptance"))
        item.storage += as_number(_value(row, "paidStorage"))
        item.other_withholdings += as_number(_value(row, "deduction"))
        item.fines += as_number(_value(row, "penalty"))

    active = [item for item in results.values() if item.has_activity]
    missing = sorted(
        item.sku
        for item in active
        if item.sold_units != 0 and normalize_sku(item.sku) not in costs
    )
    active.sort(key=lambda item: (item.revenue, item.profit), reverse=True)

    return ReportResult(
        period_start=min(starts) if starts else requested_start,
        period_end=max(ends) if ends else requested_end,
        items=active,
        missing_cost_skus=missing,
    )
