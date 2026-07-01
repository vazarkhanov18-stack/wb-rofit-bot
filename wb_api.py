from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx


BALANCE_URL = "https://finance-api.wildberries.ru/api/v1/account/balance"
SALES_REPORT_URL = "https://finance-api.wildberries.ru/api/finance/v1/sales-reports/detailed"


class WbApiError(RuntimeError):
    """Понятная ошибка при обращении к WB API."""


@dataclass(frozen=True)
class WbBalance:
    currency: str
    current: float
    for_withdraw: float


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _headers(token: str) -> dict[str, str]:
    token = token.strip()
    if not token:
        raise WbApiError("В Railway не задана переменная WB_API_TOKEN.")
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "WB-Profit-Bot/3.0",
    }


def _error_from_response(response: httpx.Response, action: str) -> WbApiError:
    if response.status_code == 401:
        return WbApiError(
            "WB отклонил токен. Проверь, что он скопирован полностью и содержит категорию «Финансы»."
        )
    if response.status_code == 402:
        return WbApiError("WB API вернул ошибку платного доступа 402.")
    if response.status_code == 429:
        return WbApiError(
            "Слишком частый запрос к WB. Подожди одну минуту и повтори команду."
        )

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
                or ""
            ).strip()
    except ValueError:
        details = ""

    suffix = f" ID запроса: {request_id}." if request_id else ""
    if details:
        return WbApiError(f"WB API не смог выполнить {action}: {details}.{suffix}")
    return WbApiError(f"WB API вернул ошибку {response.status_code} при операции «{action}». {suffix}".strip())


async def get_balance(token: str) -> WbBalance:
    headers = _headers(token)
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(BALANCE_URL, headers=headers)
    except httpx.TimeoutException as exc:
        raise WbApiError("WB API не ответил вовремя. Повтори запрос через минуту.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API.") from exc

    if response.status_code >= 400:
        raise _error_from_response(response, "проверку баланса")

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
    """Загружает детализацию отчётов реализации за период через новый Finance API."""
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
        raise WbApiError("Финансовый отчёт WB загружается слишком долго. Повтори через минуту.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API для загрузки отчёта.") from exc

    if response.status_code == 204:
        return []
    if response.status_code >= 400:
        raise _error_from_response(response, "загрузку финансового отчёта")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB API вернул финансовый отчёт в неизвестном формате.") from exc
    if not isinstance(payload, list):
        raise WbApiError("WB API вернул неожиданный формат финансового отчёта.")

    rows = [row for row in payload if isinstance(row, dict)]
    if len(rows) >= 100000:
        raise WbApiError(
            "Отчёт содержит не меньше 100 000 строк. Для такого объёма нужна постраничная загрузка; "
            "пока выбери более короткий период."
        )
    return rows
