from __future__ import annotations

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


def filter_acceptance_slots(
    payload: Iterable[Mapping[str, Any]],
    settings: AcceptanceSettings,
    *,
    today: date | None = None,
) -> list[AcceptanceSlot]:
    today = today or datetime.now(settings.timezone).date()
    last_date = today + timedelta(days=settings.days_ahead)
    name_filters = tuple(value.casefold() for value in settings.warehouse_names)

    result: list[AcceptanceSlot] = []
    for item in payload:
        try:
            slot = _parse_slot(item)
        except (TypeError, ValueError):
            continue

        local_date = slot.acceptance_date.astimezone(settings.timezone).date()
        if not today <= local_date <= last_date:
            continue
        # По документации WB поставка доступна только при коэффициенте 0/1
        # одновременно с allowUnload=true.
        if not slot.allow_unload or slot.coefficient not in (0, 1):
            continue
        if slot.coefficient > settings.max_coefficient:
            continue
        if settings.box_type_ids and slot.box_type_id not in settings.box_type_ids:
            continue
        if name_filters and not any(
            fragment in slot.warehouse_name.casefold() for fragment in name_filters
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


async def fetch_acceptance_slots(
    token: str,
    settings: AcceptanceSettings,
) -> list[AcceptanceSlot]:
    token = token.strip()
    if not token:
        raise WbApiError("В Railway не задана переменная WB_API_TOKEN.")

    params: dict[str, str] = {}
    if settings.warehouse_ids:
        params["warehouseIDs"] = ",".join(map(str, settings.warehouse_ids))
    headers = {
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "WB-Profit-Bot/29.0",
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

    if response.status_code == 401:
        raise WbApiError("WB отклонил WB_API_TOKEN. Проверь, что токен скопирован полностью.")
    if response.status_code == 403:
        raise WbApiError("У токена или кабинета нет доступа к тарифам на поставку.")
    if response.status_code == 429:
        raise WbApiError(
            "WB ограничил частоту проверки приёмки. Увеличь "
            "ACCEPTANCE_CHECK_INTERVAL_SECONDS."
        )
    if response.status_code >= 400:
        raise WbApiError(
            f"WB API вернул ошибку {response.status_code} при проверке приёмки."
        )

    try:
        payload: Any = response.json()
    except ValueError as exc:
        raise WbApiError("WB вернул тарифы на поставку в неизвестном формате.") from exc

    if isinstance(payload, dict):
        payload = payload.get("result") or payload.get("data") or []
    if not isinstance(payload, list):
        raise WbApiError("WB вернул неожиданный ответ по тарифам на поставку.")

    rows = [item for item in payload if isinstance(item, dict)]
    return filter_acceptance_slots(rows, settings)


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

