from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Iterable

from wb_api import ProductCard, SellerWarehouse
from wb_profit import ReportResult, normalize_sku


@dataclass
class InventoryItem:
    nm_id: int
    sku: str
    name: str
    fbw_quantity: int = 0
    fbs_quantity: int = 0
    in_way_to_client: int = 0
    in_way_from_client: int = 0
    sales_units: float = 0.0
    sales_window_days: int = 28

    @property
    def available(self) -> int:
        return self.fbw_quantity + self.fbs_quantity

    @property
    def daily_sales(self) -> float:
        if self.sales_window_days <= 0:
            return 0.0
        return max(0.0, self.sales_units) / self.sales_window_days

    @property
    def days_left(self) -> float | None:
        rate = self.daily_sales
        if rate <= 1e-9:
            return None
        return self.available / rate

    def recommended_supply(self, target_days: int) -> int:
        rate = self.daily_sales
        if rate <= 1e-9:
            return 0
        return max(0, ceil(rate * target_days - self.available))


@dataclass
class InventorySnapshot:
    as_of: date
    items: list[InventoryItem]
    seller_warehouses: list[SellerWarehouse]
    fbs_warning: str = ""
    sales_period_start: date | None = None
    sales_period_end: date | None = None

    @property
    def total_fbw(self) -> int:
        return sum(item.fbw_quantity for item in self.items)

    @property
    def total_fbs(self) -> int:
        return sum(item.fbs_quantity for item in self.items)

    @property
    def total_available(self) -> int:
        return sum(item.available for item in self.items)

    @property
    def total_to_client(self) -> int:
        return sum(item.in_way_to_client for item in self.items)

    @property
    def total_from_client(self) -> int:
        return sum(item.in_way_from_client for item in self.items)


def _as_int(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def build_inventory_snapshot(
    *,
    as_of: date,
    cards: Iterable[ProductCard],
    wb_stock_rows: Iterable[dict],
    fbs_by_chrt: dict[int, int] | None = None,
    seller_warehouses: list[SellerWarehouse] | None = None,
    sales_result: ReportResult | None = None,
    sales_window_days: int = 28,
    fbs_warning: str = "",
) -> InventorySnapshot:
    cards = list(cards)
    card_by_nm = {card.nm_id: card for card in cards}
    nm_by_chrt: dict[int, int] = {}
    nm_by_sku: dict[str, int] = {}
    for card in cards:
        if card.vendor_code:
            nm_by_sku[normalize_sku(card.vendor_code)] = card.nm_id
        for chrt_id in card.chrt_ids:
            nm_by_chrt[chrt_id] = card.nm_id

    records: dict[int, InventoryItem] = {}

    def get_item(nm_id: int) -> InventoryItem:
        if nm_id not in records:
            card = card_by_nm.get(nm_id)
            records[nm_id] = InventoryItem(
                nm_id=nm_id,
                sku=(card.vendor_code if card and card.vendor_code else f"WB-{nm_id}"),
                name=(card.title if card and card.title else f"Товар WB {nm_id}"),
                sales_window_days=sales_window_days,
            )
        return records[nm_id]

    for row in wb_stock_rows:
        try:
            nm_id = int(row.get("nmId") or row.get("nmID") or 0)
        except (TypeError, ValueError):
            continue
        if nm_id <= 0:
            continue
        item = get_item(nm_id)
        item.fbw_quantity += max(0, _as_int(row.get("quantity")))
        item.in_way_to_client += max(0, _as_int(row.get("inWayToClient")))
        item.in_way_from_client += max(0, _as_int(row.get("inWayFromClient")))

    for chrt_id, amount in (fbs_by_chrt or {}).items():
        nm_id = nm_by_chrt.get(chrt_id)
        if not nm_id:
            continue
        get_item(nm_id).fbs_quantity += max(0, int(amount))

    if sales_result is not None:
        for sale_item in sales_result.active_items:
            nm_id = sale_item.nm_id
            if not nm_id and sale_item.sku:
                nm_id = nm_by_sku.get(normalize_sku(sale_item.sku))
            if not nm_id:
                continue
            item = get_item(nm_id)
            item.sales_units += sale_item.sold_units
            if item.sku.startswith("WB-") and sale_item.sku:
                item.sku = sale_item.sku
            if item.name.startswith("Товар WB") and sale_item.name:
                item.name = sale_item.name

    active = [
        item
        for item in records.values()
        if item.available > 0
        or item.in_way_to_client > 0
        or item.in_way_from_client > 0
        or abs(item.sales_units) > 1e-9
    ]
    active.sort(
        key=lambda item: (
            item.days_left is None,
            item.days_left if item.days_left is not None else float("inf"),
            -item.sales_units,
            item.name.lower(),
        )
    )

    return InventorySnapshot(
        as_of=as_of,
        items=active,
        seller_warehouses=seller_warehouses or [],
        fbs_warning=fbs_warning,
        sales_period_start=sales_result.period_start if sales_result else None,
        sales_period_end=sales_result.period_end if sales_result else None,
    )


def _short(value: str, limit: int = 52) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _split_blocks(header: str, blocks: list[str], footer: str = "", limit: int = 3900) -> list[str]:
    messages: list[str] = []
    current = header.strip()
    for block in blocks:
        candidate = current + "\n\n" + block.strip()
        if len(candidate) > limit and current:
            messages.append(current)
            current = block.strip()
        else:
            current = candidate
    if footer:
        candidate = current + "\n\n" + footer.strip()
        if len(candidate) > limit and current:
            messages.append(current)
            current = footer.strip()
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def build_stock_messages(snapshot: InventorySnapshot) -> list[str]:
    header = (
        f"📦 Текущие остатки на {snapshot.as_of.strftime('%d.%m.%Y')}\n\n"
        f"Всего доступно: {snapshot.total_available} шт.\n"
        f"На складах WB (FBW): {snapshot.total_fbw} шт.\n"
        f"На складах продавца (FBS): {snapshot.total_fbs} шт.\n"
        f"В пути к клиентам: {snapshot.total_to_client} шт.\n"
        f"Возвращается от клиентов: {snapshot.total_from_client} шт."
    )
    blocks: list[str] = []
    for item in snapshot.items:
        blocks.append(
            f"• {_short(item.name)}\n"
            f"  Артикул: {item.sku}\n"
            f"  Остаток: {item.available} шт. "
            f"(FBW {item.fbw_quantity} + FBS {item.fbs_quantity})"
            + (
                f"\n  В пути: к клиенту {item.in_way_to_client}, обратно {item.in_way_from_client}"
                if item.in_way_to_client or item.in_way_from_client
                else ""
            )
        )

    footer_parts: list[str] = []
    if snapshot.seller_warehouses:
        names = ", ".join(_short(w.name, 28) for w in snapshot.seller_warehouses[:8])
        footer_parts.append(f"Склады продавца: {names}.")
    if snapshot.fbs_warning:
        footer_parts.append("⚠️ FBS не учтён полностью: " + snapshot.fbs_warning)
    if not snapshot.items:
        footer_parts.append("WB не вернул активных остатков.")
    return _split_blocks(header, blocks, "\n".join(footer_parts))


def build_supply_messages(
    snapshot: InventorySnapshot,
    *,
    low_days: int = 14,
    target_days: int = 30,
) -> list[str]:
    period_text = "последние 28 дней"
    if snapshot.sales_period_start and snapshot.sales_period_end:
        period_text = (
            f"{snapshot.sales_period_start.strftime('%d.%m.%Y')}–"
            f"{snapshot.sales_period_end.strftime('%d.%m.%Y')}"
        )
    low_items = [
        item
        for item in snapshot.items
        if item.daily_sales > 0
        and item.days_left is not None
        and item.days_left < low_days
    ]
    header = (
        f"🚚 Прогноз поставки на {snapshot.as_of.strftime('%d.%m.%Y')}\n\n"
        f"Скорость продаж: {period_text}.\n"
        f"Предупреждение: запас меньше {low_days} дней.\n"
        f"Цель расчёта поставки: запас на {target_days} дней.\n"
        f"Товаров с низким запасом: {len(low_items)}."
    )

    blocks: list[str] = []
    for item in snapshot.items:
        if item.daily_sales <= 1e-9:
            if item.available <= 0:
                continue
            icon = "⚪"
            detail = "Продаж за период нет — прогноз поставки не рассчитан."
        else:
            days = item.days_left or 0.0
            if item.available <= 0 or days <= 7:
                icon = "🔴"
            elif days < low_days:
                icon = "🟠"
            else:
                icon = "🟢"
            recommended = item.recommended_supply(target_days)
            detail = (
                f"Продажи: {item.sales_units:.0f} шт. | {item.daily_sales:.2f} шт./день\n"
                f"Запаса хватит примерно на {days:.1f} дня\n"
                f"Рекомендуемая поставка до {target_days} дней: {recommended} шт."
            )
        blocks.append(
            f"{icon} {_short(item.name)}\n"
            f"Артикул: {item.sku}\n"
            f"Остаток: {item.available} шт. "
            f"(FBW {item.fbw_quantity} + FBS {item.fbs_quantity})\n"
            f"{detail}"
        )

    footer_parts = [
        "Расчёт ориентировочный: используется темп фактических продаж, а не заказов. "
        "Сезонность, будущая реклама и срок доставки поставки пока не учитываются."
    ]
    if snapshot.fbs_warning:
        footer_parts.append("⚠️ FBS не учтён полностью: " + snapshot.fbs_warning)
    if not blocks:
        footer_parts.append("Нет товаров, для которых можно построить прогноз.")
    return _split_blocks(header, blocks, "\n\n".join(footer_parts))
