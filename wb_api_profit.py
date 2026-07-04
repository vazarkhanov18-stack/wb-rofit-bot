from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from wb_api import AdvertisingStats
from wb_profit import (
    UNALLOCATED_SKU,
    ReportResult,
    SaleOperation,
    SkuResult,
    apply_external_expenses,
    as_date,
    as_number,
    load_cost_profiles,
    normalize_sku,
    normalize_text,
    resolve_cost_profile,
    valid_sku,
)


def _value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    value = row.get(key, default)
    return default if value is None else value


def _as_nm_id(value: Any) -> int | None:
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _transaction_date(row: dict[str, Any], fallback: date | None = None) -> date | None:
    for key in ("saleDt", "orderDt", "rrdDt", "dateFrom", "dateTo"):
        parsed = as_date(_value(row, key))
        if parsed:
            return parsed
    return fallback




def _operation_id(row: dict[str, Any], *, sku_key: str, operation_type: str, transaction_date: date | None) -> str:
    for key in ("rrdId", "rrd_id", "srid", "realizationreport_id", "realizationReportId", "giId", "barcode"):
        value = _value(row, key, "")
        if value not in (None, ""):
            base = f"{key}:{value}|{operation_type}|{sku_key}|{_value(row, 'retailAmount', '')}|{_value(row, 'quantity', '')}"
            return hashlib.sha1(base.encode("utf-8", errors="ignore")).hexdigest()
    payload = {
        "sku": sku_key,
        "type": operation_type,
        "date": transaction_date.isoformat() if transaction_date else "",
        "nmId": _value(row, "nmId", ""),
        "barcode": _value(row, "sku", ""),
        "revenue": _value(row, "retailAmount", ""),
        "qty": _value(row, "quantity", ""),
        "payout": _value(row, "forPay", ""),
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _allocate_item_overheads_to_operations(result: ReportResult) -> None:
    """Распределяет рекламу и общие внешние расходы SKU по операциям внутри отчёта."""
    if not result.operations:
        return

    by_key: dict[str, SkuResult] = {}
    by_nm: dict[int, SkuResult] = {}
    for item in result.items:
        by_key[normalize_sku(item.sku)] = item
        if item.nm_id is not None:
            by_nm[int(item.nm_id)] = item

    op_groups: dict[str, list[SaleOperation]] = {}
    for op in result.operations:
        op.advertising = 0.0
        op.general_external_expenses = 0.0
        key = normalize_sku(op.sku)
        op_groups.setdefault(key, []).append(op)

    for key, ops in op_groups.items():
        item = by_key.get(key)
        if item is None and ops and ops[0].nm_id is not None:
            item = by_nm.get(int(ops[0].nm_id))
        if item is None:
            continue
        weight_total = sum(abs(op.revenue) for op in ops) or sum(abs(op.quantity) for op in ops) or len(ops)
        if weight_total <= 0:
            weight_total = len(ops)
        for op in ops:
            weight = abs(op.revenue) or abs(op.quantity) or 1.0
            share = weight / weight_total if weight_total else 0.0
            op.advertising = item.advertising * share
            op.general_external_expenses = item.general_external_expenses * share

def analyze_api_report(
    rows: Iterable[dict[str, Any]],
    costs_path: str | Path,
    requested_start: date | None = None,
    requested_end: date | None = None,
) -> ReportResult:
    """Считает прибыль из строк Finance API WB с историей себестоимости."""
    rows = list(rows)
    profiles = load_cost_profiles(costs_path)

    results: dict[str, SkuResult] = {}
    for key, history in profiles.items():
        latest = history[-1]
        results[key] = SkuResult(
            sku=latest.sku,
            name=latest.name,
            unit_cost=latest.unit_cost,
        )

    barcode_to_sku: dict[str, str] = {}
    nm_to_sku: dict[int, str] = {}
    report_names: dict[str, str] = {}
    for row in rows:
        vendor_code = _value(row, "vendorCode")
        barcode = _value(row, "sku")
        nm_id = _as_nm_id(_value(row, "nmId"))
        if valid_sku(vendor_code):
            sku_key = normalize_sku(vendor_code)
            if barcode not in (None, ""):
                barcode_to_sku[str(barcode).strip()] = sku_key
            if nm_id:
                nm_to_sku[nm_id] = sku_key
            title = str(_value(row, "title", "") or "").strip()
            if title:
                report_names[sku_key] = title

    starts: list[date] = []
    ends: list[date] = []
    missing_keys: set[str] = set()
    operations: list[SaleOperation] = []

    for row in rows:
        reason = normalize_text(_value(row, "sellerOperName") or _value(row, "docTypeName"))
        is_sale = reason == "продажа" or reason.startswith("продажа ")
        is_return = (
            "возврат" in reason
            and reason != "возмещение издержек по перевозке/по складским операциям с товаром"
        )

        vendor_code = _value(row, "vendorCode")
        nm_id = _as_nm_id(_value(row, "nmId"))
        sku_key = normalize_sku(vendor_code) if valid_sku(vendor_code) else ""

        if not sku_key and nm_id:
            sku_key = nm_to_sku.get(nm_id, "")
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
            results[sku_key] = SkuResult(
                sku=display_sku,
                name=display_name,
                nm_id=nm_id,
            )

        item = results[sku_key]
        if nm_id and not item.nm_id:
            item.nm_id = nm_id
        if not item.name:
            item.name = report_names.get(sku_key, item.sku)

        start = as_date(_value(row, "dateFrom"))
        end = as_date(_value(row, "dateTo"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)
        transaction_date = _transaction_date(row, requested_end or requested_start)
        if not start and not end and transaction_date:
            starts.append(transaction_date)
            ends.append(transaction_date)

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

            profile = resolve_cost_profile(profiles, sku_key, transaction_date)
            unit_cost = 0.0
            unit_external_expense = 0.0
            missing_cost = False
            if profile is None:
                missing_keys.add(item.sku)
                missing_cost = True
            else:
                unit_cost = profile.unit_cost
                unit_external_expense = profile.unit_external_expense
                item.unit_cost = profile.unit_cost
                if not item.name:
                    item.name = profile.name
                item.cogs_amount += qty * profile.unit_cost
                item.unit_external_expenses += qty * profile.unit_external_expense

            logistics = as_number(_value(row, "deliveryService"))
            transport = as_number(_value(row, "rebillLogisticCost"))
            handling = as_number(_value(row, "paidAcceptance"))
            storage = as_number(_value(row, "paidStorage"))
            other_withholdings = as_number(_value(row, "deduction"))
            fines = as_number(_value(row, "penalty"))
            op_type = "Возврат" if is_return else "Продажа"
            operations.append(
                SaleOperation(
                    operation_id=_operation_id(row, sku_key=sku_key, operation_type=op_type, transaction_date=transaction_date),
                    operation_date=transaction_date,
                    operation_type=op_type,
                    sku=item.sku,
                    name=item.name or report_names.get(sku_key, item.sku),
                    nm_id=nm_id or item.nm_id,
                    quantity=qty,
                    revenue=revenue,
                    payout_for_goods=payout,
                    logistics=logistics,
                    transport=transport,
                    handling=handling,
                    storage=storage,
                    other_withholdings=other_withholdings,
                    fines=fines,
                    unit_cost=unit_cost,
                    cogs=qty * unit_cost,
                    unit_external_expenses=qty * unit_external_expense,
                    missing_cost=missing_cost,
                )
            )

        item.logistics += as_number(_value(row, "deliveryService"))
        item.transport += as_number(_value(row, "rebillLogisticCost"))
        item.handling += as_number(_value(row, "paidAcceptance"))
        item.storage += as_number(_value(row, "paidStorage"))
        item.other_withholdings += as_number(_value(row, "deduction"))
        item.fines += as_number(_value(row, "penalty"))

    result = ReportResult(
        period_start=min(starts) if starts else requested_start,
        period_end=max(ends) if ends else requested_end,
        items=[item for item in results.values() if item.has_activity],
        operations=operations,
        missing_cost_skus=sorted(missing_keys),
    )
    apply_external_expenses(result, costs_path)
    _allocate_item_overheads_to_operations(result)
    result.items.sort(key=lambda item: (item.revenue, item.profit), reverse=True)
    return result


def apply_advertising(result: ReportResult, stats: AdvertisingStats) -> ReportResult:
    """Добавляет к финансовому отчёту расходы рекламы по nmId."""
    items_by_nm: dict[int, SkuResult] = {
        item.nm_id: item for item in result.items if item.nm_id is not None
    }
    unmatched: list[int] = []

    for nm_id, spend in stats.by_nm_id.items():
        if abs(spend) < 1e-9:
            continue
        item = items_by_nm.get(nm_id)
        if item is None:
            item = SkuResult(
                sku=f"WB-{nm_id}",
                name=stats.names.get(nm_id, f"Товар WB {nm_id}"),
                nm_id=nm_id,
            )
            result.items.append(item)
            items_by_nm[nm_id] = item
            unmatched.append(nm_id)
        item.advertising += spend

    if stats.unallocated > 0.01:
        result.items.append(
            SkuResult(
                sku="РЕКЛАМА-НЕРАСПРЕДЕЛЕНО",
                name="Реклама WB без разбивки по товару",
                advertising=stats.unallocated,
            )
        )

    result.unmatched_ad_nm_ids = sorted(set(unmatched))
    result.advertising_campaign_count = stats.campaign_count
    result.items = [item for item in result.items if item.has_activity]
    result.items.sort(key=lambda item: (item.revenue, item.profit), reverse=True)
    _allocate_item_overheads_to_operations(result)
    return result
