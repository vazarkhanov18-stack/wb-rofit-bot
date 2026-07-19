from __future__ import annotations

import asyncio
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import httpx

from wb_api import WbApiError


logger = logging.getLogger("wb-profit-bot.acceptance")

ACCEPTANCE_URL = (
    "https://common-api.wildberries.ru/api/tariffs/v1/acceptance/coefficients"
)
ACCEPTANCE_OPTIONS_URL = (
    "https://supplies-api.wildberries.ru/api/v1/acceptance/options"
)
ACCEPTANCE_OPTIONS_CACHE_SECONDS = 6 * 60 * 60

DEFAULT_WAREHOUSE_NAMES = (
    "Рязань",
    "Коледино",
    "Электросталь",
    "Подольск",
    "Тула",
    "Алексин",
    "Котовск",
    "Софьино",
    "Белая дача",
    "Чехов",
    "Владимир",
    "Ярославль",
)

BOX_TYPE_NAMES = {
    2: "Короба",
    5: "Монопаллеты",
    6: "Суперсейф",
}

BOX_TYPE_CAPABILITY_FIELDS = {
    2: "canBox",
    5: "canMonopallet",
    6: "canSupersafe",
}

_acceptance_options_cache: dict[
    tuple[tuple[str, ...], tuple[int, ...]],
    tuple[datetime, frozenset[int]],
] = {}
_acceptance_options_lock = asyncio.Lock()


def _csv_strings(value: str | None, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _csv_ints(value: str | None, default: tuple[int, ...] = ()) -> tuple[int, ...]:
    if value is None:
        return default
    result: list[int] = []
    for item in _csv_strings(value):
        try:
            result.append(int(item))
        except ValueError:
            logger.warning("Пропускаю неверное числовое значение: %s", item)
    return tuple(result)


def _bounded_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
        if not minimum <= value <= maximum:
            raise ValueError
        return value
    except ValueError:
        logger.warning(
            "Неверный %s=%s. Использую %d.",
            name,
            raw,
            default,
        )
        return default


@dataclass(frozen=True)
class AcceptanceSettings:
    warehouse_names: tuple[str, ...]
    warehouse_ids: tuple[int, ...]
    box_type_ids: tuple[int, ...]
    max_coefficient: int
    days_ahead: int
    check_interval_seconds: int
    timezone: ZoneInfo
    required_warehouse_names: tuple[str, ...] = ()
    barcodes: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "AcceptanceSettings":
        timezone_name = os.getenv("REPORT_TIMEZONE", "Europe/Moscow").strip()
        try:
            timezone = ZoneInfo(timezone_name or "Europe/Moscow")
        except Exception:
            logger.warning(
                "Неизвестный REPORT_TIMEZONE=%s. Использую Europe/Moscow.",
                timezone_name,
            )
            timezone = ZoneInfo("Europe/Moscow")

        return cls(
            warehouse_names=_csv_strings(
                os.getenv("ACCEPTANCE_WAREHOUSE_NAMES"),
                DEFAULT_WAREHOUSE_NAMES,
            ),
            warehouse_ids=_csv_ints(os.getenv("ACCEPTANCE_WAREHOUSE_IDS")),
            box_type_ids=_csv_ints(
                os.getenv("ACCEPTANCE_BOX_TYPE_IDS"),
                (2,),
            ),
            max_coefficient=_bounded_int(
                "ACCEPTANCE_MAX_COEFFICIENT",
                1,
                minimum=0,
                maximum=1,
            ),
            days_ahead=_bounded_int(
                "ACCEPTANCE_DAYS_AHEAD",
                14,
                minimum=1,
                maximum=14,
            ),
            check_interval_seconds=_bounded_int(
                "ACCEPTANCE_CHECK_INTERVAL_SECONDS",
                120,
                minimum=60,
                maximum=3600,
            ),
            timezone=timezone,
            required_warehouse_names=_csv_strings(
                os.getenv("ACCEPTANCE_REQUIRED_WAREHOUSE_NAMES"),
                ("Питание",),
            ),
            barcodes=_csv_strings(os.getenv("ACCEPTANCE_BARCODES")),
        )


@dataclass(frozen=True)
class AcceptanceSlot:
    acceptance_date: datetime
    coefficient: int
    warehouse_id: int
    warehouse_name: str
    allow_unload: bool
    box_type_id: int
    box_type_name: str

    @property
    def key(self) -> str:
        return (
            f"{self.warehouse_id}:{self.box_type_id}:"
            f"{self.acceptance_date.date().isoformat()}"
        )


@dataclass(frozen=True)
class AcceptanceSnapshot:
    available_slots: tuple[AcceptanceSlot, ...]
    monitored_warehouses: tuple[str, ...]
    compatible_warehouse_ids: tuple[int, ...]
    barcode_filter_enabled: bool

    @property
    def unavailable_warehouses(self) -> tuple[str, ...]:
        available_names = {slot.warehouse_name for slot in self.available_slots}
        return tuple(
            name for name in self.monitored_warehouses if name not in available_names
        )


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("нет даты")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_slot(item: Mapping[str, Any]) -> AcceptanceSlot:
    box_type_id = int(item.get("boxTypeID") or 0)
    return AcceptanceSlot(
        acceptance_date=_parse_datetime(item.get("date")),
        coefficient=int(item.get("coefficient", -1)),
        warehouse_id=int(item.get("warehouseID") or 0),
        warehouse_name=str(item.get("warehouseName") or "").strip(),
        allow_unload=bool(item.get("allowUnload", False)),
        box_type_id=box_type_id,
        box_type_name=str(
            item.get("boxTypeName")
            or BOX_TYPE_NAMES.get(box_type_id, f"Тип поставки {box_type_id}")
        ).strip(),
    )


def _matches_warehouse(
    slot: AcceptanceSlot,
    settings: AcceptanceSettings,
    *,
    compatible_warehouse_ids: set[int] | frozenset[int] | None = None,
) -> bool:
    warehouse_name = slot.warehouse_name.casefold()
    name_filters = tuple(value.casefold() for value in settings.warehouse_names)
    required_filters = tuple(
        value.casefold() for value in settings.required_warehouse_names
    )

    if settings.warehouse_ids and slot.warehouse_id not in settings.warehouse_ids:
        return False
    if name_filters and not any(fragment in warehouse_name for fragment in name_filters):
        return False
    if required_filters and not any(
        fragment in warehouse_name for fragment in required_filters
    ):
        return False
    if (
        compatible_warehouse_ids is not None
        and slot.warehouse_id not in compatible_warehouse_ids
    ):
        return False
    return True


def filter_monitored_slots(
    payload: Iterable[Mapping[str, Any]],
    settings: AcceptanceSettings,
    *,
    today: date | None = None,
    compatible_warehouse_ids: set[int] | frozenset[int] | None = None,
) -> list[AcceptanceSlot]:
    """Return matching warehouse/date rows, including dates without capacity."""
    today = today or datetime.now(settings.timezone).date()
    last_date = today + timedelta(days=settings.days_ahead)

    result: list[AcceptanceSlot] = []
    for item in payload:
        try:
            slot = _parse_slot(item)
        except (TypeError, ValueError):
            continue

        local_date = slot.acceptance_date.astimezone(settings.timezone).date()
        if not today <= local_date <= last_date:
            continue
        if settings.box_type_ids and slot.box_type_id not in settings.box_type_ids:
            continue
        if not _matches_warehouse(
            slot,
            settings,
            compatible_warehouse_ids=compatible_warehouse_ids,
        ):
            continue
        result.append(slot)

    return sorted(
        result,
        key=lambda slot: (
            slot.acceptance_date,
            slot.warehouse_name.casefold(),
            slot.box_type_id,
        ),
    )


def filter_acceptance_slots(
    payload: Iterable[Mapping[str, Any]],
    settings: AcceptanceSettings,
    *,
    today: date | None = None,
    compatible_warehouse_ids: set[int] | frozenset[int] | None = None,
) -> list[AcceptanceSlot]:
    result: list[AcceptanceSlot] = []
    for slot in filter_monitored_slots(
        payload,
        settings,
        today=today,
        compatible_warehouse_ids=compatible_warehouse_ids,
    ):
        # По документации WB поставка доступна только при коэффициенте 0/1
        # одновременно с allowUnload=true.
        if not slot.allow_unload or slot.coefficient not in (0, 1):
            continue
        if slot.coefficient > settings.max_coefficient:
            continue
        result.append(slot)

    return sorted(
        result,
        key=lambda slot: (
            slot.acceptance_date,
            slot.warehouse_name.casefold(),
            slot.box_type_id,
        ),
    )


def _response_error(response: httpx.Response, *, action: str) -> WbApiError:
    if response.status_code == 401:
        return WbApiError(
            "WB отклонил WB_API_TOKEN. Проверь, что токен скопирован полностью."
        )
    if response.status_code == 402:
        return WbApiError("WB API требует платный доступ для проверки баркодов.")
    if response.status_code == 403:
        return WbApiError(
            "У токена нет доступа к поставкам. Добавь категорию «Поставки» в токен WB."
        )
    if response.status_code == 429:
        return WbApiError(
            f"WB ограничил частоту запросов при операции «{action}». Повтори позже."
        )
    return WbApiError(
        f"WB API вернул ошибку {response.status_code} при операции «{action}»."
    )


def _supports_configured_box_type(
    warehouse: Mapping[str, Any],
    box_type_ids: tuple[int, ...],
) -> bool:
    if not box_type_ids:
        return any(bool(warehouse.get(field)) for field in BOX_TYPE_CAPABILITY_FIELDS.values())
    return any(
        bool(warehouse.get(BOX_TYPE_CAPABILITY_FIELDS[box_type_id]))
        for box_type_id in box_type_ids
        if box_type_id in BOX_TYPE_CAPABILITY_FIELDS
    )


async def fetch_compatible_warehouse_ids(
    token: str,
    barcodes: tuple[str, ...],
    box_type_ids: tuple[int, ...],
) -> frozenset[int]:
    """Return warehouses that accept every configured barcode and package type."""
    normalized_barcodes = tuple(dict.fromkeys(value.strip() for value in barcodes if value.strip()))
    if not normalized_barcodes:
        return frozenset()

    cache_key = (normalized_barcodes, tuple(sorted(set(box_type_ids))))
    cached = _acceptance_options_cache.get(cache_key)
    now = datetime.now(UTC)
    if cached and (now - cached[0]).total_seconds() < ACCEPTANCE_OPTIONS_CACHE_SECONDS:
        return cached[1]

    async with _acceptance_options_lock:
        cached = _acceptance_options_cache.get(cache_key)
        now = datetime.now(UTC)
        if cached and (now - cached[0]).total_seconds() < ACCEPTANCE_OPTIONS_CACHE_SECONDS:
            return cached[1]

        body = [
            {"barcode": barcode, "quantity": 1}
            for barcode in normalized_barcodes
        ]
        headers = {
            "Authorization": token.strip(),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "WB-Profit-Bot/30.0",
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    ACCEPTANCE_OPTIONS_URL,
                    headers=headers,
                    json=body,
                )
        except httpx.TimeoutException as exc:
            raise WbApiError("WB долго проверяет доступность складов по баркодам.") from exc
        except httpx.HTTPError as exc:
            raise WbApiError(
                "Не удалось соединиться с WB API вариантов приёмки."
            ) from exc

        if response.status_code >= 400:
            raise _response_error(response, action="проверка складов по баркодам")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise WbApiError(
                "WB вернул варианты приёмки по баркодам в неизвестном формате."
            ) from exc

        rows = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WbApiError("WB вернул неожиданный ответ по вариантам приёмки.")

        by_barcode: dict[str, set[int]] = {}
        rejected: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            barcode = str(row.get("barcode") or "").strip()
            if not barcode:
                continue
            warehouses = row.get("warehouses")
            if row.get("isError") or not isinstance(warehouses, list):
                rejected.append(barcode)
                continue
            supported: set[int] = set()
            for warehouse in warehouses:
                if not isinstance(warehouse, dict):
                    continue
                try:
                    warehouse_id = int(warehouse.get("warehouseID") or 0)
                except (TypeError, ValueError):
                    continue
                if warehouse_id and _supports_configured_box_type(
                    warehouse,
                    box_type_ids,
                ):
                    supported.add(warehouse_id)
            by_barcode[barcode] = supported

        missing = [barcode for barcode in normalized_barcodes if barcode not in by_barcode]
        rejected = list(dict.fromkeys(rejected + missing))
        if rejected:
            preview = ", ".join(rejected[:5])
            suffix = "…" if len(rejected) > 5 else ""
            raise WbApiError(
                "WB не смог проверить баркоды: " + preview + suffix + ". Проверь значения."
            )

        compatible = set.intersection(
            *(by_barcode[barcode] for barcode in normalized_barcodes)
        )
        result = frozenset(compatible)
        _acceptance_options_cache[cache_key] = (now, result)
        return result


async def _fetch_acceptance_rows(token: str, settings: AcceptanceSettings) -> list[dict[str, Any]]:
    token = token.strip()
    if not token:
        raise WbApiError("В Railway не задана переменная WB_API_TOKEN.")

    params: dict[str, str] = {}
    if settings.warehouse_ids:
        params["warehouseIDs"] = ",".join(map(str, settings.warehouse_ids))
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "WB-Profit-Bot/30.0",
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(
                ACCEPTANCE_URL,
                headers=headers,
                params=params,
            )
    except httpx.TimeoutException as exc:
        raise WbApiError("WB долго отвечает при проверке приёмки.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API тарифов на поставку.") from exc

    if response.status_code >= 400:
        raise _response_error(response, action="проверка приёмки")

    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise WbApiError("WB вернул тарифы на поставку в неизвестном формате.") from exc

    if isinstance(payload, dict):
        payload = payload.get("result") or payload.get("data") or []
    if not isinstance(payload, list):
        raise WbApiError("WB вернул неожиданный ответ по тарифам на поставку.")
    return [item for item in payload if isinstance(item, dict)]


async def fetch_acceptance_snapshot(
    token: str,
    settings: AcceptanceSettings,
) -> AcceptanceSnapshot:
    rows = await _fetch_acceptance_rows(token, settings)
    compatible_ids: frozenset[int] | None = None
    if settings.barcodes:
        compatible_ids = await fetch_compatible_warehouse_ids(
            token,
            settings.barcodes,
            settings.box_type_ids,
        )

    monitored = filter_monitored_slots(
        rows,
        settings,
        compatible_warehouse_ids=compatible_ids,
    )
    available = filter_acceptance_slots(
        rows,
        settings,
        compatible_warehouse_ids=compatible_ids,
    )
    monitored_names = tuple(
        sorted(
            {slot.warehouse_name for slot in monitored},
            key=str.casefold,
        )
    )
    return AcceptanceSnapshot(
        available_slots=tuple(available),
        monitored_warehouses=monitored_names,
        compatible_warehouse_ids=tuple(sorted(compatible_ids or ())),
        barcode_filter_enabled=bool(settings.barcodes),
    )


async def fetch_acceptance_slots(
    token: str,
    settings: AcceptanceSettings,
) -> list[AcceptanceSlot]:
    snapshot = await fetch_acceptance_snapshot(token, settings)
    return list(snapshot.available_slots)


def select_new_or_better_slots(
    slots: Iterable[AcceptanceSlot],
    previous: Mapping[str, int] | None,
) -> tuple[list[AcceptanceSlot], dict[str, int]]:
    previous = previous or {}
    current = {slot.key: slot.coefficient for slot in slots}
    changed = [
        slot
        for slot in slots
        if slot.key not in previous or slot.coefficient < int(previous[slot.key])
    ]
    return changed, current


def build_acceptance_messages(
    slots: Iterable[AcceptanceSlot],
    *,
    title: str,
    timezone: ZoneInfo,
) -> list[str]:
    grouped: dict[tuple[str, str, int], set[date]] = defaultdict(set)
    for slot in slots:
        local_date = slot.acceptance_date.astimezone(timezone).date()
        grouped[
            (slot.warehouse_name, slot.box_type_name, slot.coefficient)
        ].add(local_date)

    blocks: list[str] = []
    for (warehouse, box_type, coefficient), dates in sorted(
        grouped.items(),
        key=lambda item: (
            min(item[1]),
            item[0][0].casefold(),
            item[0][2],
        ),
    ):
        coefficient_text = (
            "бесплатно" if coefficient == 0 else "доступно, коэффициент ×1"
        )
        date_text = ", ".join(
            value.strftime("%d.%m.%Y") for value in sorted(dates)
        )
        blocks.append(
            f"🏬 {warehouse}\n"
            f"📦 {box_type}\n"
            f"💰 {coefficient_text}\n"
            f"📅 {date_text}"
        )

    if not blocks:
        return [f"{title}\n\nПодходящих дат сейчас нет."]

    messages: list[str] = []
    current = title
    footer = "\n\nОткрой кабинет WB и забронируй поставку как можно быстрее."
    for block in blocks:
        candidate = f"{current}\n\n{block}"
        if len(candidate) + len(footer) > 3800 and current != title:
            messages.append(current + footer)
            current = f"{title} — продолжение\n\n{block}"
        else:
            current = candidate
    messages.append(current + footer)
    return messages
