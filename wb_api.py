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
PRODUCT_CARDS_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
WB_WAREHOUSE_STOCKS_URL = "https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses"
SELLER_WAREHOUSES_URL = "https://marketplace-api.wildberries.ru/api/v3/warehouses"
SELLER_STOCKS_URL = "https://marketplace-api.wildberries.ru/api/v3/stocks/{warehouse_id}"


class WbApiError(RuntimeError):
    """Понятная ошибка при обращении к WB API."""


@dataclass(frozen=True)
class WbBalance:
    currency: str
    current: float
    for_withdraw: float




@dataclass(frozen=True)
class ProductCard:
    nm_id: int
    vendor_code: str
    title: str
    chrt_ids: tuple[int, ...]


@dataclass(frozen=True)
class SellerWarehouse:
    warehouse_id: int
    name: str


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
        "User-Agent": "WB-Profit-Bot/9.0",
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

                # WB может вернуть пустое тело, null, пустой объект или обёртку
                # вместо массива, когда за период не было рекламной активности.
                raw_text = response.text.strip()
                if not raw_text:
                    campaigns: list[dict[str, Any]] = []
                else:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise WbApiError("WB вернул рекламную статистику в неизвестном формате.") from exc

                    if payload is None:
                        campaigns = []
                    elif isinstance(payload, list):
                        campaigns = [item for item in payload if isinstance(item, dict)]
                    elif isinstance(payload, dict):
                        # Некоторые ответы WB приходят в обёртке. Пустая обёртка
                        # означает, что расходов за период нет.
                        wrapped = (
                            payload.get("adverts")
                            or payload.get("items")
                            or payload.get("data")
                            or payload.get("result")
                        )
                        if isinstance(wrapped, list):
                            campaigns = [item for item in wrapped if isinstance(item, dict)]
                        elif not payload:
                            campaigns = []
                        elif any(key in payload for key in ("advertId", "advert_id", "days", "sum")):
                            campaigns = [payload]
                        else:
                            logger_payload = str(payload)[:300]
                            raise WbApiError(
                                "WB вернул неожиданный формат рекламной статистики. "
                                f"Начало ответа: {logger_payload}"
                            )
                    else:
                        raise WbApiError("WB вернул неожиданный формат рекламной статистики.")

                for campaign in campaigns:
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


async def get_product_cards(token: str) -> list[ProductCard]:
    """Получает все карточки товаров и их chrtId для сопоставления остатков."""
    cards: list[ProductCard] = []
    cursor: dict[str, Any] = {"limit": 100}
    page = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            page += 1
            if page > 1000:
                raise WbApiError("Слишком много страниц карточек товаров. Остановил загрузку для безопасности.")
            body = {
                "settings": {
                    "sort": {"ascending": True},
                    "filter": {"withPhoto": -1},
                    "cursor": cursor,
                }
            }
            try:
                response = await client.post(
                    PRODUCT_CARDS_URL,
                    headers=_headers(token),
                    params={"locale": "ru"},
                    json=body,
                )
            except httpx.TimeoutException as exc:
                raise WbApiError("WB долго загружает список карточек товаров.") from exc
            except httpx.HTTPError as exc:
                raise WbApiError("Не удалось соединиться с API карточек WB.") from exc

            if response.status_code >= 400:
                raise _error_from_response(
                    response,
                    "получение карточек товаров",
                    category="Продвижение",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise WbApiError("WB вернул карточки товаров в неизвестном формате.") from exc
            if not isinstance(payload, dict):
                raise WbApiError("WB вернул неожиданный формат карточек товаров.")

            raw_cards = payload.get("cards") or []
            if not isinstance(raw_cards, list):
                raise WbApiError("WB вернул неожиданный список карточек товаров.")

            for raw in raw_cards:
                if not isinstance(raw, dict):
                    continue
                nm_id = _as_int(raw.get("nmID") or raw.get("nmId"))
                if not nm_id:
                    continue
                chrt_ids: list[int] = []
                sizes = raw.get("sizes") or []
                if isinstance(sizes, list):
                    for size in sizes:
                        if not isinstance(size, dict):
                            continue
                        chrt_id = _as_int(size.get("chrtID") or size.get("chrtId"))
                        if chrt_id:
                            chrt_ids.append(chrt_id)
                cards.append(
                    ProductCard(
                        nm_id=nm_id,
                        vendor_code=str(raw.get("vendorCode") or "").strip(),
                        title=str(raw.get("title") or "").strip(),
                        chrt_ids=tuple(sorted(set(chrt_ids))),
                    )
                )

            response_cursor = payload.get("cursor") or {}
            total = _as_int(response_cursor.get("total")) or 0
            if total < int(cursor.get("limit", 100)):
                break
            updated_at = response_cursor.get("updatedAt")
            nm_id = response_cursor.get("nmID") or response_cursor.get("nmId")
            if not updated_at or not nm_id:
                break
            cursor = {"limit": 100, "updatedAt": updated_at, "nmID": nm_id}
            await asyncio.sleep(0.65)

    return cards


async def get_wb_warehouse_stocks(token: str) -> list[dict[str, Any]]:
    """Текущие остатки на складах WB (FBW)."""
    rows: list[dict[str, Any]] = []
    limit = 250000
    offset = 0

    async with httpx.AsyncClient(timeout=90.0) as client:
        while True:
            body = {"nmIds": [], "chrtIds": [], "limit": limit, "offset": offset}
            try:
                response = await client.post(
                    WB_WAREHOUSE_STOCKS_URL,
                    headers=_headers(token),
                    json=body,
                )
            except httpx.TimeoutException as exc:
                raise WbApiError("WB долго загружает остатки на своих складах.") from exc
            except httpx.HTTPError as exc:
                raise WbApiError("Не удалось соединиться с API остатков на складах WB.") from exc

            if response.status_code >= 400:
                raise _error_from_response(
                    response,
                    "получение остатков на складах WB",
                    category="Аналитика",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise WbApiError("WB вернул остатки на своих складах в неизвестном формате.") from exc
            data = payload.get("data") if isinstance(payload, dict) else None
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                raise WbApiError("WB вернул неожиданный формат остатков на своих складах.")
            batch = [item for item in items if isinstance(item, dict)]
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
            await asyncio.sleep(20.5)

    return rows


async def get_seller_warehouses(token: str) -> list[SellerWarehouse]:
    """Список складов продавца для FBS/DBW/DBS."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(SELLER_WAREHOUSES_URL, headers=_headers(token))
    except httpx.TimeoutException as exc:
        raise WbApiError("WB долго загружает список складов продавца.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с API складов продавца.") from exc

    if response.status_code >= 400:
        raise _error_from_response(
            response,
            "получение складов продавца",
            category="Маркетплейс",
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB вернул список складов продавца в неизвестном формате.") from exc
    if not isinstance(payload, list):
        raise WbApiError("WB вернул неожиданный формат списка складов продавца.")

    result: list[SellerWarehouse] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        warehouse_id = _as_int(item.get("id"))
        if not warehouse_id:
            continue
        result.append(
            SellerWarehouse(
                warehouse_id=warehouse_id,
                name=str(item.get("name") or f"Склад {warehouse_id}").strip(),
            )
        )
    return result


async def get_seller_warehouse_stocks(
    token: str,
    warehouse_id: int,
    chrt_ids: list[int],
) -> dict[int, int]:
    """Остатки по chrtId на одном складе продавца."""
    result: dict[int, int] = {}
    if not chrt_ids:
        return result

    async with httpx.AsyncClient(timeout=45.0) as client:
        for index, chunk in enumerate(_chunks(chrt_ids, 1000)):
            if index:
                await asyncio.sleep(0.25)
            try:
                response = await client.post(
                    SELLER_STOCKS_URL.format(warehouse_id=warehouse_id),
                    headers=_headers(token),
                    json={"chrtIds": chunk},
                )
            except httpx.TimeoutException as exc:
                raise WbApiError("WB долго загружает остатки на складе продавца.") from exc
            except httpx.HTTPError as exc:
                raise WbApiError("Не удалось соединиться с API остатков склада продавца.") from exc

            if response.status_code >= 400:
                raise _error_from_response(
                    response,
                    "получение остатков на складе продавца",
                    category="Маркетплейс",
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise WbApiError("WB вернул остатки склада продавца в неизвестном формате.") from exc
            stocks = payload.get("stocks") if isinstance(payload, dict) else None
            if not isinstance(stocks, list):
                raise WbApiError("WB вернул неожиданный формат остатков склада продавца.")
            for stock in stocks:
                if not isinstance(stock, dict):
                    continue
                chrt_id = _as_int(stock.get("chrtId") or stock.get("chrtID"))
                if not chrt_id:
                    continue
                amount = int(max(0, _as_float(stock.get("amount"))))
                result[chrt_id] = amount

    return result
