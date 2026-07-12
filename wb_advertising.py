from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable


ELIGIBLE_CAMPAIGN_STATUSES = {7, 9, 11}
ACTIVE_CAMPAIGN_STATUS = 9
FULLSTATS_MAX_DAYS = 31
FULLSTATS_MAX_CAMPAIGNS = 50

logger = logging.getLogger("wb-profit-dashboard.advertising")


def split_date_range(date_from: date, date_to: date, max_days: int = FULLSTATS_MAX_DAYS) -> list[tuple[date, date]]:
    """Split an inclusive period into adjacent, non-overlapping intervals."""
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    if max_days < 1:
        raise ValueError("max_days должен быть больше нуля")
    result: list[tuple[date, date]] = []
    cursor = date_from
    while cursor <= date_to:
        end = min(date_to, cursor + timedelta(days=max_days - 1))
        result.append((cursor, end))
        cursor = end + timedelta(days=1)
    return result


def chunked(values: Iterable[int], size: int = FULLSTATS_MAX_CAMPAIGNS) -> list[list[int]]:
    if size < 1:
        raise ValueError("size должен быть больше нуля")
    items = list(values)
    return [items[index:index + size] for index in range(0, len(items), size)]


def extract_campaign_catalog(payload: Any) -> dict[int, dict[str, Any]]:
    """Read the real fields returned by /adv/v1/promotion/count."""
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return result
    for group in payload.get("adverts", []) or []:
        if not isinstance(group, dict):
            continue
        status = _as_int(group.get("status"))
        campaign_type = group.get("type")
        if status not in ELIGIBLE_CAMPAIGN_STATUSES:
            continue
        for item in group.get("advert_list", []) or []:
            if not isinstance(item, dict):
                continue
            campaign_id = _as_int(item.get("advertId"))
            if campaign_id <= 0:
                continue
            result[campaign_id] = {
                "campaign_id": campaign_id,
                "name": "",
                "type": campaign_type,
                "status": status,
                "payment_type": "",
                "change_time": str(item.get("changeTime") or ""),
            }
    return result


def merge_campaign_details(catalog: dict[int, dict[str, Any]], payload: Any) -> None:
    """Enrich catalog from /api/advert/v2/adverts without inventing fields."""
    adverts = payload.get("adverts", []) if isinstance(payload, dict) else []
    for item in adverts or []:
        if not isinstance(item, dict):
            continue
        campaign_id = _as_int(item.get("id") or item.get("advertId"))
        if campaign_id not in catalog:
            continue
        settings = item.get("settings") if isinstance(item.get("settings"), dict) else {}
        catalog[campaign_id].update(
            {
                "name": str(settings.get("name") or item.get("name") or ""),
                "status": _as_int(item.get("status")) or catalog[campaign_id].get("status"),
                "payment_type": str(settings.get("payment_type") or item.get("payment_type") or ""),
                "bid_type": str(item.get("bid_type") or ""),
            }
        )


def normalize_fullstats_payload(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Return campaign rows from the official array or a supported wrapper object."""
    warnings: list[str] = []
    source = "official_list"
    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        source = "unknown_object"
        raw_items = None
        for key in ("data", "result", "items"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                source = key
                raw_items = candidate
                break
        if raw_items is None:
            raw_items = []
            warnings.append(
                "WB вернул рекламную статистику в неизвестном формате: массив кампаний не найден."
            )
    else:
        raw_items = []
        warnings.append(
            "WB вернул рекламную статистику в неизвестном формате: ожидался JSON-массив кампаний."
        )

    items = [item for item in raw_items if isinstance(item, dict)]
    if len(items) != len(raw_items):
        warnings.append("Часть элементов рекламной статистики WB не является объектами кампаний.")
    _log_fullstats_shape(payload, items, source, len(raw_items))
    return items, warnings


def _log_fullstats_shape(payload: Any, items: list[dict[str, Any]], source: str, items_count: int) -> None:
    first = items[0] if items else (payload if isinstance(payload, dict) else {})
    first_keys = sorted(str(key) for key in first.keys()) if isinstance(first, dict) else []
    days = first.get("days") if isinstance(first, dict) else None
    first_day = days[0] if isinstance(days, list) and days and isinstance(days[0], dict) else None
    apps = first_day.get("apps") if isinstance(first_day, dict) else None
    first_app = apps[0] if isinstance(apps, list) and apps and isinstance(apps[0], dict) else None
    nms = first_app.get("nms") if isinstance(first_app, dict) else None
    logger.info(
        "WB fullstats shape: top_type=%s source=%s items_count=%s first_keys=%s days_type=%s apps_type=%s nms_type=%s",
        type(payload).__name__,
        source,
        items_count,
        first_keys,
        type(days).__name__,
        type(apps).__name__,
        type(nms).__name__,
    )


def aggregate_fullstats(
    payload: Any,
    catalog: dict[int, dict[str, Any]],
    by_nm: dict[int, dict[str, Any]],
    *,
    requested_from: date,
    requested_to: date,
    seen: set[tuple[int, str, int, int, int, int]] | None = None,
) -> list[str]:
    """Merge /adv/v3/fullstats at its most precise nmID level."""
    campaigns, warnings = normalize_fullstats_payload(payload)
    seen = seen if seen is not None else set()
    for campaign in campaigns:
        campaign_id = _as_int(campaign.get("advertId"))
        meta = catalog.get(campaign_id, {"campaign_id": campaign_id, "name": "", "type": None, "status": None})
        days = campaign.get("days")
        if not isinstance(days, list) or not days:
            warnings.append(f"Кампания {campaign_id}: отсутствует детализация days.")
            continue
        for day_index, day_row in enumerate(days):
            if not isinstance(day_row, dict):
                warnings.append(f"Кампания {campaign_id}: элемент days имеет неизвестный формат.")
                continue
            stat_date = str(day_row.get("date") or "")[:10]
            try:
                parsed_date = date.fromisoformat(stat_date)
            except ValueError:
                parsed_date = None
            if parsed_date and not (requested_from <= parsed_date <= requested_to):
                continue
            apps = day_row.get("apps")
            if not isinstance(apps, list) or not apps:
                warnings.append(f"Кампания {campaign_id}, дата {stat_date or 'не указана'}: отсутствует детализация apps.")
                continue
            for app_index, app in enumerate(apps):
                if not isinstance(app, dict):
                    warnings.append(f"Кампания {campaign_id}: элемент apps имеет неизвестный формат.")
                    continue
                nm_rows = app.get("nms")
                if not isinstance(nm_rows, list) or not nm_rows:
                    warnings.append(
                        f"Кампания {campaign_id}, дата {stat_date or 'не указана'}: отсутствует разбивка nms по nmID."
                    )
                    continue
                for nm_index, nm_row in enumerate(nm_rows):
                    if not isinstance(nm_row, dict):
                        warnings.append(f"Кампания {campaign_id}: элемент nms имеет неизвестный формат.")
                        continue
                    nm_id = _as_int(nm_row.get("nmId"))
                    if nm_id <= 0:
                        warnings.append(f"Кампания {campaign_id}: в nms отсутствует корректный nmId.")
                        continue
                    dedupe_key = (campaign_id, stat_date, day_index, app_index, nm_index, nm_id)
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    row = by_nm.setdefault(nm_id, _empty_nm_row(nm_id))
                    if nm_row.get("name") and not row["name"]:
                        row["name"] = str(nm_row.get("name"))
                    required_missing = [key for key in ("sum", "sum_price", "orders") if key not in nm_row]
                    if required_missing:
                        row["complete"] = False
                        row["warnings"].append(
                            f"Кампания {campaign_id}: отсутствуют поля {', '.join(required_missing)}."
                        )
                    campaign_row = row["campaigns"].setdefault(campaign_id, _empty_campaign_row(meta))
                    metrics = {
                        "spend": _as_float(nm_row.get("sum")),
                        "revenue": _as_float(nm_row.get("sum_price")),
                        "orders": _as_float(nm_row.get("orders")),
                        "impressions": _as_float(nm_row.get("views")),
                        "clicks": _as_float(nm_row.get("clicks")),
                        "atbs": _as_float(nm_row.get("atbs")),
                        "shks": _as_float(nm_row.get("shks")),
                    }
                    for key, value in metrics.items():
                        row[key] += value
                        campaign_row[key] += value
                    if stat_date:
                        campaign_row["dates"].add(stat_date)
    return warnings


def finalize_by_nm(by_nm: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    for row in by_nm.values():
        campaigns = []
        for campaign in row.pop("campaigns", {}).values():
            dates = sorted(campaign.pop("dates", set()))
            campaign["period_from"] = dates[0] if dates else ""
            campaign["period_to"] = dates[-1] if dates else ""
            campaign["factual_drr"] = _ratio_percent(campaign["spend"], campaign["revenue"])
            campaign["ctr"] = _ratio_percent(campaign["clicks"], campaign["impressions"])
            campaign["cpc"] = _ratio(campaign["spend"], campaign["clicks"])
            campaign["cpo"] = _ratio(campaign["spend"], campaign["orders"])
            campaigns.append(campaign)
        campaigns.sort(key=lambda item: (item.get("campaign_id") or 0))
        row["campaigns"] = campaigns
        row["campaigns_count"] = len(campaigns)
        row["active_campaigns_count"] = sum(1 for item in campaigns if item.get("status") == ACTIVE_CAMPAIGN_STATUS)
        row["factual_drr"] = _ratio_percent(row["spend"], row["revenue"])
        row["ctr"] = _ratio_percent(row["clicks"], row["impressions"])
        row["cpc"] = _ratio(row["spend"], row["clicks"])
        row["cpo"] = _ratio(row["spend"], row["orders"])
        row["warnings"] = list(dict.fromkeys(row["warnings"]))
    return by_nm


def fetch_advertising_stats(
    request_json: Callable[..., Any],
    date_from: date,
    date_to: date,
    *,
    pause_seconds: float = 0.0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Fetch once for the page, batch campaigns/periods, and aggregate by nmID."""
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    fetched_at = datetime.now(timezone.utc)
    try:
        count_payload = request_json("GET", "https://advert-api.wildberries.ru/adv/v1/promotion/count")
        catalog = extract_campaign_catalog(count_payload)
    except Exception as exc:
        return _fetch_result({}, "API_ERROR", [f"Не удалось получить список рекламных кампаний WB: {exc}"], fetched_at)
    if not catalog:
        return _fetch_result({}, "OK", [], fetched_at)

    ids = sorted(catalog)
    warnings: list[str] = []
    for batch in chunked(ids):
        try:
            details = request_json(
                "GET",
                "https://advert-api.wildberries.ru/api/advert/v2/adverts",
                params={"ids": ",".join(str(value) for value in batch)},
            )
            merge_campaign_details(catalog, details)
        except Exception as exc:
            warnings.append(f"Названия кампаний недоступны: {exc}")

    requests = [(period, batch) for period in split_date_range(date_from, date_to) for batch in chunked(ids)]
    by_nm: dict[int, dict[str, Any]] = {}
    seen: set[tuple[int, str, int, int, int, int]] = set()
    successful = 0
    failed = 0
    incomplete = False
    for index, ((chunk_from, chunk_to), batch) in enumerate(requests):
        try:
            payload = request_json(
                "GET",
                "https://advert-api.wildberries.ru/adv/v3/fullstats",
                params={
                    "ids": ",".join(str(value) for value in batch),
                    "beginDate": chunk_from.isoformat(),
                    "endDate": chunk_to.isoformat(),
                },
                timeout=100,
            )
            part_warnings = aggregate_fullstats(
                payload,
                catalog,
                by_nm,
                requested_from=chunk_from,
                requested_to=chunk_to,
                seen=seen,
            )
            if part_warnings:
                incomplete = True
                warnings.extend(part_warnings)
            successful += 1
        except Exception as exc:
            failed += 1
            warnings.append(
                f"Не удалось загрузить рекламу за {chunk_from.isoformat()}–{chunk_to.isoformat()}: {exc}"
            )
        if pause_seconds > 0 and index + 1 < len(requests):
            sleep_fn(pause_seconds)

    finalize_by_nm(by_nm)
    if successful == 0:
        status = "API_ERROR"
    elif failed or incomplete or any(not row.get("complete", True) for row in by_nm.values()):
        status = "PARTIAL_DATA"
    else:
        status = "OK"
    return _fetch_result(by_nm, status, list(dict.fromkeys(warnings)), fetched_at)


def classify_sku_advertising(
    row: dict[str, Any] | None,
    *,
    fetch_status: str = "OK",
    exact_nm_match: bool = True,
) -> dict[str, Any]:
    data = dict(row or _empty_nm_row(0))
    data.setdefault("campaigns", [])
    data.setdefault("campaigns_count", len(data["campaigns"]))
    data.setdefault("active_campaigns_count", 0)
    data.setdefault("warnings", [])
    spend = _as_float(data.get("spend"))
    revenue = _as_float(data.get("revenue"))
    campaigns_count = _as_int(data.get("campaigns_count"))
    warning = ""
    if fetch_status == "API_ERROR":
        status = "API_ERROR"
        warning = "Рекламный API WB недоступен. Показатели рекламы не обновлены."
    elif fetch_status == "PARTIAL_DATA" or not data.get("complete", True) or not exact_nm_match:
        status = "PARTIAL_DATA"
        warning = "Рекламные данные получены частично."
    elif spend > 0 and revenue <= 0:
        status = "SPEND_WITHOUT_SALES"
        warning = "Расходы есть, рекламных продаж нет."
    elif spend <= 0 and revenue > 0:
        status = "SALES_WITHOUT_SPEND"
        warning = "Есть рекламные продажи, но расход равен нулю. Возможна задержка или неполная статистика WB."
    elif spend > 0 and revenue > 0 and exact_nm_match:
        status = "OK"
    elif campaigns_count == 0 and spend <= 0 and revenue <= 0:
        status = "NO_ADS"
        warning = "Реклама не запускалась или данных за период нет."
    else:
        status = "NO_ADS"
        warning = "За выбранный период нет рекламных расходов и продаж."
    data["status"] = status
    data["warning_text"] = warning
    data["factual_drr"] = _ratio_percent(spend, revenue) if status == "OK" else None
    return data


def _empty_nm_row(nm_id: int) -> dict[str, Any]:
    return {
        "nm_id": nm_id,
        "name": "",
        "spend": 0.0,
        "revenue": 0.0,
        "orders": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "atbs": 0.0,
        "shks": 0.0,
        "campaigns": {},
        "complete": True,
        "warnings": [],
    }


def _empty_campaign_row(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign_id": _as_int(meta.get("campaign_id")),
        "name": str(meta.get("name") or ""),
        "type": meta.get("type"),
        "status": meta.get("status"),
        "payment_type": str(meta.get("payment_type") or ""),
        "spend": 0.0,
        "revenue": 0.0,
        "orders": 0.0,
        "impressions": 0.0,
        "clicks": 0.0,
        "atbs": 0.0,
        "shks": 0.0,
        "dates": set(),
    }


def _fetch_result(by_nm: dict[int, dict[str, Any]], status: str, warnings: list[str], fetched_at: datetime) -> dict[str, Any]:
    return {
        "by_nm": by_nm,
        "status": status,
        "warnings": warnings,
        "fetched_at": fetched_at,
    }


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if abs(denominator) > 0.000001 else None


def _ratio_percent(numerator: float, denominator: float) -> float | None:
    value = _ratio(numerator, denominator)
    return value * 100.0 if value is not None else None
