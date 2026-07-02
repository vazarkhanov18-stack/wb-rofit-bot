from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
import math
import re

from openpyxl import load_workbook


TAX_RATE = 0.06
UNALLOCATED_SKU = "НЕРАСПРЕДЕЛЕНО"


@dataclass
class CostItem:
    sku: str
    name: str
    unit_cost: float


@dataclass
class SkuResult:
    sku: str
    name: str = ""
    unit_cost: float = 0.0
    nm_id: int | None = None
    sold_units: float = 0.0
    revenue: float = 0.0
    payout_for_goods: float = 0.0
    logistics: float = 0.0
    transport: float = 0.0
    handling: float = 0.0
    storage: float = 0.0
    other_withholdings: float = 0.0
    fines: float = 0.0
    advertising: float = 0.0

    @property
    def calculated_payout(self) -> float:
        return (
            self.payout_for_goods
            - self.logistics
            - self.transport
            - self.handling
            - self.storage
            - self.other_withholdings
            - self.fines
        )

    @property
    def cogs(self) -> float:
        return self.sold_units * self.unit_cost

    @property
    def tax(self) -> float:
        return self.revenue * TAX_RATE

    @property
    def profit_before_ads(self) -> float:
        return self.calculated_payout - self.cogs - self.tax

    @property
    def profit(self) -> float:
        return self.profit_before_ads - self.advertising

    @property
    def margin(self) -> float:
        return self.profit / self.revenue if self.revenue else 0.0

    @property
    def drr(self) -> float:
        return self.advertising / self.revenue if self.revenue else 0.0

    @property
    def profit_per_unit(self) -> float:
        return self.profit / self.sold_units if self.sold_units else 0.0

    @property
    def has_activity(self) -> bool:
        return any(
            abs(v) > 1e-9
            for v in (
                self.sold_units,
                self.revenue,
                self.payout_for_goods,
                self.logistics,
                self.transport,
                self.handling,
                self.storage,
                self.other_withholdings,
                self.fines,
                self.advertising,
            )
        )


@dataclass
class ReportResult:
    period_start: date | None
    period_end: date | None
    items: list[SkuResult]
    missing_cost_skus: list[str] = field(default_factory=list)
    unmatched_ad_nm_ids: list[int] = field(default_factory=list)
    advertising_warning: str = ""
    advertising_campaign_count: int = 0

    @property
    def active_items(self) -> list[SkuResult]:
        return [item for item in self.items if item.has_activity]

    def total(self) -> SkuResult:
        total = SkuResult(sku="ИТОГО", name="Итого")
        for item in self.active_items:
            total.sold_units += item.sold_units
            total.revenue += item.revenue
            total.payout_for_goods += item.payout_for_goods
            total.logistics += item.logistics
            total.transport += item.transport
            total.handling += item.handling
            total.storage += item.storage
            total.other_withholdings += item.other_withholdings
            total.fines += item.fines
            total.advertising += item.advertising
            total.unit_cost = 0.0
        # Себестоимость суммируется отдельно, поэтому подменяем вычисляемое поле
        total._cogs_override = sum(item.cogs for item in self.active_items)  # type: ignore[attr-defined]
        return total

    @property
    def total_cogs(self) -> float:
        return sum(item.cogs for item in self.active_items)

    @property
    def total_profit_before_ads(self) -> float:
        return sum(item.profit_before_ads for item in self.active_items)

    @property
    def total_advertising(self) -> float:
        return sum(item.advertising for item in self.active_items)

    @property
    def total_profit(self) -> float:
        return sum(item.profit for item in self.active_items)

    @property
    def total_revenue(self) -> float:
        return sum(item.revenue for item in self.active_items)

    @property
    def total_units(self) -> float:
        return sum(item.sold_units for item in self.active_items)

    @property
    def total_payout(self) -> float:
        return sum(item.calculated_payout for item in self.active_items)

    @property
    def total_tax(self) -> float:
        return sum(item.tax for item in self.active_items)

    @property
    def margin(self) -> float:
        return self.total_profit / self.total_revenue if self.total_revenue else 0.0

    @property
    def drr(self) -> float:
        return self.total_advertising / self.total_revenue if self.total_revenue else 0.0

    @property
    def profit_per_unit(self) -> float:
        return self.total_profit / self.total_units if self.total_units else 0.0


HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "sku": ("Артикул поставщика",),
    "name": ("Название",),
    "barcode": ("Баркод",),
    "reason": ("Обоснование для оплаты",),
    "date": ("Дата продажи", "Дата заказа покупателем"),
    "qty": ("Кол-во",),
    "revenue": ("Вайлдберриз реализовал Товар (Пр)",),
    "payout": ("К перечислению Продавцу за реализованный Товар",),
    "logistics": ("Услуги по доставке товара покупателю",),
    "transport": ("Возмещение издержек по перевозке/по складским операциям с товаром",),
    "handling": ("Операции на приемке",),
    "storage": ("Хранение",),
    "withholdings": ("Удержания",),
    "fines": ("Общая сумма штрафов",),
}


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower().replace("ё", "е")
    return re.sub(r"\s+", " ", text)


def normalize_header(value: Any) -> str:
    return normalize_text(value).replace("\n", " ")


def normalize_sku(value: Any) -> str:
    return normalize_text(value)


def valid_sku(value: Any) -> bool:
    sku = normalize_sku(value)
    return sku not in {"", "0", "90", "none", "nan", "null"}


def as_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return 0.0
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if text.lower() in {"", "none", "nan", "90"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def find_header_row(rows: Iterable[tuple[Any, ...]], required_header: str, max_rows: int = 15) -> tuple[int, list[Any]]:
    target = normalize_header(required_header)
    for index, row in enumerate(rows, start=1):
        normalized = [normalize_header(cell) for cell in row]
        if target in normalized:
            return index, list(row)
        if index >= max_rows:
            break
    raise ValueError(f"Не найдена строка заголовков с колонкой «{required_header}».")


def resolve_columns(headers: list[Any]) -> dict[str, int]:
    normalized = [normalize_header(h) for h in headers]
    result: dict[str, int] = {}
    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            alias_norm = normalize_header(alias)
            if alias_norm in normalized:
                result[key] = normalized.index(alias_norm)
                break
    required = ("sku", "reason", "qty", "revenue", "payout")
    missing = [key for key in required if key not in result]
    if missing:
        raise ValueError("В отчёте не хватает обязательных колонок: " + ", ".join(missing))
    return result


def load_costs(costs_path: str | Path) -> dict[str, CostItem]:
    path = Path(costs_path)
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл себестоимости: {path}")

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = ws.iter_rows(values_only=True)
    _, headers = find_header_row(rows, "Артикул поставщика")
    normalized = [normalize_header(h) for h in headers]

    def col(name: str) -> int:
        target = normalize_header(name)
        if target not in normalized:
            raise ValueError(f"В файле себестоимости нет колонки «{name}».")
        return normalized.index(target)

    sku_col = col("Артикул поставщика")
    name_col = col("Название")
    cost_col = col("себестоимость")

    costs: dict[str, CostItem] = {}
    for row in rows:
        if sku_col >= len(row) or not valid_sku(row[sku_col]):
            continue
        sku_raw = str(row[sku_col]).strip()
        key = normalize_sku(sku_raw)
        name = str(row[name_col] or sku_raw).strip() if name_col < len(row) else sku_raw
        cost = as_number(row[cost_col]) if cost_col < len(row) else 0.0
        costs[key] = CostItem(sku=sku_raw, name=name, unit_cost=cost)
    wb.close()
    return costs


def analyze_report(report_path: str | Path, costs_path: str | Path) -> ReportResult:
    costs = load_costs(costs_path)
    wb = load_workbook(report_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    all_rows = list(ws.iter_rows(values_only=True))
    header_index, headers = find_header_row(iter(all_rows), "Обоснование для оплаты")
    columns = resolve_columns(headers)
    data_rows = all_rows[header_index:]

    # Карта штрихкод → артикул используется только для строк продаж/возвратов.
    # Прочие затраты без артикула остаются отдельной строкой, как в первой Excel-модели.
    barcode_to_sku: dict[str, str] = {}
    report_names: dict[str, str] = {}
    for row in data_rows:
        sku_value = row[columns["sku"]] if columns["sku"] < len(row) else None
        barcode_value = row[columns.get("barcode", -1)] if columns.get("barcode", -1) >= 0 and columns["barcode"] < len(row) else None
        if valid_sku(sku_value):
            sku_key = normalize_sku(sku_value)
            if barcode_value not in (None, ""):
                barcode_to_sku[str(barcode_value).strip()] = sku_key
            if "name" in columns and columns["name"] < len(row):
                name = str(row[columns["name"]] or "").strip()
                if name and name != "90":
                    report_names[sku_key] = name

    results: dict[str, SkuResult] = {}
    for key, cost in costs.items():
        results[key] = SkuResult(sku=cost.sku, name=cost.name, unit_cost=cost.unit_cost)

    dates: list[date] = []

    for row in data_rows:
        if not any(cell not in (None, "") for cell in row):
            continue

        reason = normalize_text(row[columns["reason"]] if columns["reason"] < len(row) else None)
        is_sale = reason == "продажа" or reason.startswith("продажа ")
        is_return = "возврат" in reason and reason != "возмещение издержек по перевозке/по складским операциям с товаром"

        sku_value = row[columns["sku"]] if columns["sku"] < len(row) else None
        sku_key = normalize_sku(sku_value) if valid_sku(sku_value) else ""

        if not sku_key and (is_sale or is_return) and "barcode" in columns:
            barcode_value = row[columns["barcode"]] if columns["barcode"] < len(row) else None
            sku_key = barcode_to_sku.get(str(barcode_value).strip(), "")

        if not sku_key:
            sku_key = normalize_sku(UNALLOCATED_SKU)

        if sku_key not in results:
            display_sku = str(sku_value).strip() if valid_sku(sku_value) else UNALLOCATED_SKU
            display_name = report_names.get(sku_key, "Расходы без артикула в отчёте WB" if display_sku == UNALLOCATED_SKU else display_sku)
            results[sku_key] = SkuResult(sku=display_sku, name=display_name, unit_cost=0.0)

        item = results[sku_key]
        if not item.name:
            item.name = report_names.get(sku_key, item.sku)

        if "date" in columns and columns["date"] < len(row):
            parsed_date = as_date(row[columns["date"]])
            if parsed_date:
                dates.append(parsed_date)

        if is_sale or is_return:
            sign = -1.0 if is_return else 1.0

            qty = as_number(row[columns["qty"]])
            revenue = as_number(row[columns["revenue"]])
            payout = as_number(row[columns["payout"]])

            # В некоторых версиях отчёта возвраты уже отрицательные.
            if is_return:
                qty = -abs(qty) if qty >= 0 else qty
                revenue = -abs(revenue) if revenue >= 0 else revenue
                payout = -abs(payout) if payout >= 0 else payout
            else:
                qty *= sign
                revenue *= sign
                payout *= sign

            item.sold_units += qty
            item.revenue += revenue
            item.payout_for_goods += payout

        optional_fields = {
            "logistics": "logistics",
            "transport": "transport",
            "handling": "handling",
            "storage": "storage",
            "withholdings": "other_withholdings",
            "fines": "fines",
        }
        for column_key, attribute in optional_fields.items():
            if column_key in columns and columns[column_key] < len(row):
                setattr(item, attribute, getattr(item, attribute) + as_number(row[columns[column_key]]))

    wb.close()

    active = [item for item in results.values() if item.has_activity]
    missing = sorted(
        item.sku
        for item in active
        if item.sold_units != 0 and normalize_sku(item.sku) not in costs
    )

    active.sort(key=lambda item: (item.revenue, item.profit), reverse=True)
    return ReportResult(
        period_start=min(dates) if dates else None,
        period_end=max(dates) if dates else None,
        items=active,
        missing_cost_skus=missing,
    )


def format_money(value: float) -> str:
    sign = "−" if value < 0 else ""
    number = f"{abs(value):,.2f}".replace(",", " ")
    return f"{sign}{number} ₽"


def format_units(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def format_period(start: date | None, end: date | None) -> str:
    if not start and not end:
        return "период не определён"
    if start == end or end is None:
        return start.strftime("%d.%m.%Y") if start else end.strftime("%d.%m.%Y")
    return f"{start.strftime('%d.%m.%Y')}–{end.strftime('%d.%m.%Y')}"


def build_messages(result: ReportResult) -> list[str]:
    total_profit = result.total_profit
    total_revenue = result.total_revenue

    summary = [
        f"📊 Отчёт WB за {format_period(result.period_start, result.period_end)}",
        "",
        f"Продано: {format_units(result.total_units)} шт.",
        f"Доход покупателей: {format_money(total_revenue)}",
        f"Расчётная выплата: {format_money(result.total_payout)}",
        f"Себестоимость: {format_money(result.total_cogs)}",
        f"УСН 6%: {format_money(result.total_tax)}",
        f"Прибыль до рекламы: {format_money(result.total_profit_before_ads)}",
        f"Реклама WB: {format_money(result.total_advertising)}",
        f"ДРР: {result.drr * 100:.1f}%",
        "",
        f"💰 Чистая прибыль: {format_money(total_profit)}",
        f"Маржинальность: {result.margin * 100:.1f}%",
        f"Прибыль на единицу: {format_money(result.profit_per_unit)}",
    ]

    if result.advertising_warning:
        summary.extend(
            [
                "",
                "⚠️ Реклама не была учтена полностью:",
                result.advertising_warning,
            ]
        )

    if result.missing_cost_skus:
        summary.extend(
            [
                "",
                "⚠️ Не найдена себестоимость: " + ", ".join(result.missing_cost_skus),
                "Итоговая прибыль по этим товарам может быть завышена.",
            ]
        )

    if result.unmatched_ad_nm_ids:
        shown = ", ".join(str(value) for value in result.unmatched_ad_nm_ids[:10])
        if len(result.unmatched_ad_nm_ids) > 10:
            shown += "…"
        summary.extend(
            [
                "",
                "⚠️ Реклама найдена, но не связана с артикулом продавца для WB-артикулов: " + shown,
                "Общая прибыль учтена верно, но расходы по товарам могут отображаться отдельными строками.",
            ]
        )

    detail = ["📦 По товарам:"]
    for item in result.active_items:
        label = item.name or item.sku
        if len(label) > 55:
            label = label[:52] + "…"
        detail.extend(
            [
                "",
                f"{label}",
                f"Артикул: {item.sku}",
                f"Продано: {format_units(item.sold_units)} | Доход: {format_money(item.revenue)}",
                f"Реклама: {format_money(item.advertising)} | ДРР: {item.drr * 100:.1f}%",
                f"Прибыль: {format_money(item.profit)} | Маржа: {item.margin * 100:.1f}%",
            ]
        )

    notes = [
        "",
        "Расчёт предварительный: учтены финансовый отчёт WB, себестоимость, УСН 6% и расходы WB Продвижение. Внешняя доставка, зарплаты, аренда и другие расходы пока не включены.",
    ]

    return ["\n".join(summary), "\n".join(detail + notes)]

