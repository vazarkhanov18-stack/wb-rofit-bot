from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


BALANCE_URL = "https://finance-api.wildberries.ru/api/v1/account/balance"


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


async def get_balance(token: str) -> WbBalance:
    token = token.strip()
    if not token:
        raise WbApiError("В Railway не задана переменная WB_API_TOKEN.")

    headers = {
        # WB принимает полный токен без префикса Bearer.
        "Authorization": token,
        "Accept": "application/json",
        "User-Agent": "WB-Profit-Bot/2.0",
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(BALANCE_URL, headers=headers)
    except httpx.TimeoutException as exc:
        raise WbApiError("WB API не ответил вовремя. Повтори запрос через минуту.") from exc
    except httpx.HTTPError as exc:
        raise WbApiError("Не удалось соединиться с WB API.") from exc

    if response.status_code == 401:
        raise WbApiError(
            "WB отклонил токен: проверь, что он скопирован полностью и содержит категорию «Финансы»."
        )
    if response.status_code == 402:
        raise WbApiError(
            "WB API вернул ошибку доступа 402. Проверь условия доступа к API в кабинете продавца."
        )
    if response.status_code == 429:
        raise WbApiError(
            "Слишком частый запрос. Баланс разрешено проверять не чаще одного раза в минуту."
        )
    if response.status_code >= 400:
        request_id = response.headers.get("x-request-id") or response.headers.get("X-Request-Id")
        suffix = f" ID запроса: {request_id}." if request_id else ""
        raise WbApiError(f"WB API вернул ошибку {response.status_code}.{suffix}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise WbApiError("WB API вернул ответ в неизвестном формате.") from exc

    if not isinstance(payload, dict):
        raise WbApiError("WB API вернул неожиданный ответ.")

    return WbBalance(
        currency=str(payload.get("currency") or "RUB"),
        current=_as_float(payload.get("current")),
        for_withdraw=_as_float(payload.get("for_withdraw")),
    )
