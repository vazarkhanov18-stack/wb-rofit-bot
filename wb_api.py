from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

import httpx


BALANCE_URL = "https://finance-api.wildberries.ru/api/v1/account/balance"
SALES_REPORT_URL = "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed"
CAMPAIGNS_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
CAMPAIGN_STATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"


class WbApiError(RuntimeError):
    """Понятная ошибка при обращении к WB API."""


@dataclass(frozen=True)
class WbBalance:
    currency: str
    current: float
    for_withdraw: float


@dataclass(frozen=True)
class AdvertisingStats:
    by_nm_id: dict[int, float]
    names: dict[int, str]
    total: float
    unallocated: float
    campaign_count: int


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _headers(token: str) -> dict[str, str]:
    token = token.strip()
    if not token:
        raise WbApiError("В Railway не задана переменная WB_API_TOKEN.")
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "WB-Profit-Bot/4.0",
    }


def _retry_hint(response: httpx.Response) -> str:
    raw = (
        response.headers.get("X-Ratelimit-Retry")
        or response.headers.get("x-ratelimit-retry")
        or response.headers.get("Retry-After")
        or response.headers.get("retry-after")
    )
    if not raw:
        return "Подожди немного и повтори команду."
    try:
        seconds = max(1, int(float(raw)))
        if seconds >= 60:
            minutes = (seconds + 59) // 60
            return f"Повтори примерно через {minutes} мин."
        return f"Повтори примерно через {seconds} сек."
    except ValueError:
        return f"WB просит повторить запрос позже ({raw})."


def _error_from_response(
    response: httpx.Response,
    action: str,
    *,
    category: str | None = None,
) -> WbApiError:
    if response.status_code == 401:
        suffix = f" и содержит категорию «{category}»" if category else ""
        return WbApiError(
            "WB отклонил токен. Проверь, что он скопирован полностью"
            f"{suffix}."
        )
    if response.status_code == 402:
        return WbApiError("WB API вернул ошибку платного доступа 402.")
    if response.status_code == 403:
        suffix = f" категории «{category}»" if category else ""
        return WbApiError(f"У токена нет доступа к методу{suffix}.")
    if response.status_code == 429:
        return WbApiError(f"Слишком частый запрос к WB. {_retry_hint(response)}")

    request_id = response.headers.get("x-request-id") or response.headers.get("X-Request-Id")
    details = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            details = str(
                payload.get("detail")
                or payload.get("title")
                or payload.get("message")
                or payload.get("errorText")
                or payload.get("error")
                or ""
            ).strip()
    except ValueError:
        details = ""

    suffix = f" ID запроса: {request_id}." if request_id else ""
    if details:
        return WbApiError(f"WB API не смог выполнить {action}: {details}.{suffix}")
    return WbApiError(
        f"WB API вернул ошибку {response.status_code} при операции «{action}». {suffix}".strip()
    )


async def get_balance(token: str) -> WbBalance:
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(BALANCE_URL, headers=_headers(token))
    except httpx.TimeoutException as exc:
        raise WbApiError("WB API не ответил вовремя. Повтори запрос позже.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API.") from exc

    if response.status_code >= 400:
        raise _error_from_response(response, "проверку баланса", category="Финансы")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB API вернул баланс в неизвестном формате.") from exc
    if not isinstance(payload, dict):
        raise WbApiError("WB API вернул неожиданный ответ по балансу.")

    return WbBalance(
        currency=str(payload.get("currency") or "RUB"),
        current=_as_float(payload.get("current")),
        for_withdraw=_as_float(payload.get("for_withdraw")),
    )


REPORT_FIELDS = [
    "reportId",
    "dateFrom",
    "dateTo",
    "rrdId",
    "nmId",
    "vendorCode",
    "title",
    "sku",
    "docTypeName",
    "sellerOperName",
    "quantity",
    "retailAmount",
    "forPay",
    "deliveryService",
    "rebillLogisticCost",
    "paidAcceptance",
    "paidStorage",
    "deduction",
    "penalty",
    "saleDt",
]


async def get_sales_report(
    token: str,
    date_from: date,
    date_to: date,
    *,
    period: str = "weekly",
) -> list[dict[str, Any]]:
    """Загружает детализацию отчётов реализации через Finance API."""
    if date_to < date_from:
        raise WbApiError("Дата окончания не может быть раньше даты начала.")
    if period not in {"weekly", "daily"}:
        raise WbApiError("Неизвестный тип периода отчёта.")

    body = {
        "dateFrom": date_from.isoformat(),
        "dateTo": date_to.isoformat(),
        "limit": 100000,
        "rrdId": 0,
        "period": period,
        "fields": REPORT_FIELDS,
    }

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(SALES_REPORT_URL, headers=_headers(token), json=body)
    except httpx.TimeoutException as exc:
        raise WbApiError("Финансовый отчёт WB загружается слишком долго. Повтори позже.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API для загрузки отчёта.") from exc

    if response.status_code == 204:
        return []
    if response.status_code >= 400:
        raise _error_from_response(response, "загрузку финансового отчёта", category="Финансы")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB API вернул финансовый отчёт в неизвестном формате.") from exc
    if not isinstance(payload, list):
        raise WbApiError("WB API вернул неожиданный формат финансового отчёта.")

    rows = [row for row in payload if isinstance(row, dict)]
    if len(rows) >= 100000:
        raise WbApiError(
            "Отчёт содержит не меньше 100 000 строк. Пока выбери более короткий период."
        )
    return rows


async def get_campaign_ids(token: str) -> list[int]:
    """Возвращает ID завершённых, активных и приостановленных кампаний."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(CAMPAIGNS_URL, headers=_headers(token))
    except httpx.TimeoutException as exc:
        raise WbApiError("WB долго загружает список рекламных кампаний.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API рекламы.") from exc

    if response.status_code >= 400:
        raise _error_from_response(response, "получение списка рекламных кампаний", category="Продвижение")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB вернул список кампаний в неизвестном формате.") from exc
    if not isinstance(payload, dict):
        raise WbApiError("WB вернул неожиданный формат списка кампаний.")

    ids: set[int] = set()
    groups = payload.get("adverts")
    if not isinstance(groups, list):
        return []

    for group in groups:
        if not isinstance(group, dict):
            continue
        status = _as_int(group.get("status"))
        if status not in {7, 9, 11}:
            continue
        adverts = group.get("advert_list") or group.get("advertList") or []
        if not isinstance(adverts, list):
            continue
        for advert in adverts:
            if not isinstance(advert, dict):
                continue
            advert_id = _as_int(advert.get("advertId") or advert.get("advert_id") or advert.get("id"))
            if advert_id:
                ids.add(advert_id)
    return sorted(ids)


def _chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _date_chunks(start: date, end: date, max_days: int = 31) -> Iterable[tuple[date, date]]:
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


async def get_advertising_stats(
    token: str,
    date_from: date,
    date_to: date,
) -> AdvertisingStats:
    """Получает расходы WB Продвижение и раскладывает их по nmId."""
    if date_to < date_from:
        raise WbApiError("Дата окончания не может быть раньше даты начала.")

    campaign_ids = await get_campaign_ids(token)
    if not campaign_ids:
        return AdvertisingStats({}, {}, 0.0, 0.0, 0)

    by_nm_id: dict[int, float] = {}
    names: dict[int, str] = {}
    unallocated = 0.0
    total = 0.0
    request_number = 0

    async with httpx.AsyncClient(timeout=90.0) as client:
        for range_start, range_end in _date_chunks(date_from, date_to):
            for id_chunk in _chunks(campaign_ids, 50):
                # fullstats: не чаще одного запроса примерно в 20 секунд.
                if request_number:
                    await asyncio.sleep(20.5)
                request_number += 1

                params = {
                    "ids": ",".join(str(value) for value in id_chunk),
                    "beginDate": range_start.isoformat(),
                    "endDate": range_end.isoformat(),
                }
                try:
                    response = await client.get(
                        CAMPAIGN_STATS_URL,
                        headers=_headers(token),
                        params=params,
                    )
                except httpx.TimeoutException as exc:
                    raise WbApiError("WB долго загружает рекламную статистику.") from exc
                except httpx.HTTPError as exc:
                    raise WbApiError("Не удалось соединиться со статистикой рекламы WB.") from exc

                if response.status_code >= 400:
                    raise _error_from_response(
                        response,
                        "загрузку рекламной статистики",
                        category="Продвижение",
                    )

                try:
                    payload = response.json()
                except ValueError as exc:
                    raise WbApiError("WB вернул рекламную статистику в неизвестном формате.") from exc
                if not isinstance(payload, list):
                    raise WbApiError("WB вернул неожиданный формат рекламной статистики.")

                for campaign in payload:
                    if not isinstance(campaign, dict):
                        continue
                    campaign_total = _as_float(campaign.get("sum"))
                    allocated = 0.0

                    days = campaign.get("days") or []
                    if isinstance(days, list):
                        for day in days:
                            if not isinstance(day, dict):
                                continue
                            apps = day.get("apps") or []
                            if not isinstance(apps, list):
                                continue
                            for app in apps:
                                if not isinstance(app, dict):
                                    continue
                                nms = app.get("nms") or []
                                if not isinstance(nms, list):
                                    continue
                                for nm in nms:
                                    if not isinstance(nm, dict):
                                        continue
                                    nm_id = _as_int(nm.get("nmId") or nm.get("nmID") or nm.get("nm"))
                                    spend = _as_float(nm.get("sum"))
                                    if not nm_id or abs(spend) < 1e-9:
                                        continue
                                    by_nm_id[nm_id] = by_nm_id.get(nm_id, 0.0) + spend
                                    allocated += spend
                                    name = str(nm.get("name") or "").strip()
                                    if name:
                                        names[nm_id] = name

                    if abs(campaign_total) < 1e-9:
                        campaign_total = allocated
                    total += campaign_total
                    remainder = campaign_total - allocated
                    if remainder > 0.01:
                        unallocated += remainder

    # Защита от небольших расхождений округления в ответе WB.
    allocated_total = sum(by_nm_id.values()) + unallocated
    if total < allocated_total - 0.05:
        total = allocated_total
    elif total > allocated_total + 0.05:
        unallocated += total - allocated_total

    return AdvertisingStats(
        by_nm_id=by_nm_id,
        names=names,
        total=total,
        unallocated=unallocated,
        campaign_count=len(campaign_ids),
    )
