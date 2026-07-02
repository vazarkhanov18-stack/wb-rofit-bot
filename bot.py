from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from wb_api import (
    WbApiError,
    get_advertising_stats,
    get_balance,
    get_product_cards,
    get_sales_report,
    get_seller_warehouse_stocks,
    get_seller_warehouses,
    get_wb_warehouse_stocks,
)
from wb_api_profit import analyze_api_report, apply_advertising
from wb_db import database_enabled, database_status, init_database, list_history, save_report
from dashboard import start_dashboard_server
from wb_profit import ReportResult, analyze_report, build_messages, format_money, format_period, format_units
from wb_stock import (
    build_inventory_snapshot,
    build_stock_alert_message,
    build_stock_messages,
    build_supply_messages,
)


BASE_DIR = Path(__file__).resolve().parent
COSTS_FILE = BASE_DIR / "data" / "costs.xlsx"
MAX_FILE_SIZE = 20 * 1024 * 1024
MOSCOW_TZ = ZoneInfo("Europe/Moscow")

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("wb-profit-bot")

FINANCE_MIN_INTERVAL_SECONDS = 62.0
_finance_request_lock = asyncio.Lock()
_last_finance_request_at = 0.0

BACKFILL_EARLIEST_DATE = date(2024, 1, 29)
_backfill_task: asyncio.Task[None] | None = None
_backfill_stop_event: asyncio.Event | None = None
_backfill_state: dict[str, object] = {
    "running": False,
    "started_at": None,
    "start_date": None,
    "end_date": None,
    "current_start": None,
    "current_end": None,
    "processed": 0,
    "total": 0,
    "saved": 0,
    "empty": 0,
    "last_error": "",
    "finished_at": None,
    "stopped": False,
}


def allowed_user_id() -> int | None:
    raw = os.getenv("ALLOWED_USER_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("ALLOWED_USER_ID указан неверно: %s", raw)
        return None


def env_flag(name: str, default: bool = False) -> bool:
    raw_default = "true" if default else "false"
    raw = os.getenv(name, raw_default).strip().lower()
    return raw in {"1", "true", "yes", "on", "да"}


def auto_daily_enabled() -> bool:
    return env_flag("AUTO_DAILY_REPORT")


def auto_weekly_enabled() -> bool:
    return env_flag("AUTO_WEEKLY_REPORT")


def auto_stock_alert_enabled() -> bool:
    return env_flag("AUTO_STOCK_ALERT")


def configured_timezone() -> ZoneInfo:
    name = os.getenv("REPORT_TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:
        logger.warning("Неизвестный REPORT_TIMEZONE=%s. Использую Europe/Moscow.", name)
        return MOSCOW_TZ


def configured_time(variable: str, default: str) -> tuple[int, int]:
    raw = os.getenv(variable, default).strip()
    try:
        hour_text, minute_text = raw.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except ValueError:
        logger.warning("Неверный %s=%s. Использую %s.", variable, raw, default)
        hour_text, minute_text = default.split(":", maxsplit=1)
        return int(hour_text), int(minute_text)


def configured_daily_time() -> tuple[int, int]:
    return configured_time("DAILY_REPORT_TIME", "12:00")


def configured_weekly_time() -> tuple[int, int]:
    return configured_time("WEEKLY_REPORT_TIME", "13:00")


def configured_stock_alert_time() -> tuple[int, int]:
    return configured_time("STOCK_ALERT_TIME", "14:00")


def configured_percent(variable: str, default: float) -> float:
    raw = os.getenv(variable, str(default)).strip().replace(",", ".")
    try:
        value = float(raw)
        if value < 0:
            raise ValueError
        return value
    except ValueError:
        logger.warning("Неверный %s=%s. Использую %.1f.", variable, raw, default)
        return default


def configured_int(variable: str, default: int, *, minimum: int = 1, maximum: int = 365) -> int:
    raw = os.getenv(variable, str(default)).strip()
    try:
        value = int(raw)
        if not (minimum <= value <= maximum):
            raise ValueError
        return value
    except ValueError:
        logger.warning("Неверный %s=%s. Использую %d.", variable, raw, default)
        return default

def is_allowed(update: Update) -> bool:
    allowed = allowed_user_id()
    user = update.effective_user
    return allowed is None or (user is not None and user.id == allowed)


async def reject_if_not_allowed(update: Update) -> bool:
    if is_allowed(update):
        return False
    if update.effective_message:
        await update.effective_message.reply_text("У этого бота закрытый доступ.")
    return True


def last_completed_week(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(MOSCOW_TZ).date()
    current_monday = today - timedelta(days=today.weekday())
    end = current_monday - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def previous_completed_week(today: date | None = None) -> tuple[date, date]:
    current_start, _ = last_completed_week(today)
    end = current_start - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def yesterday_period(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(MOSCOW_TZ).date()
    yesterday = today - timedelta(days=1)
    return yesterday, yesterday


def current_month_period(today: date | None = None) -> tuple[date, date]:
    today = today or datetime.now(MOSCOW_TZ).date()
    end = today - timedelta(days=1)
    start = end.replace(day=1)
    return start, end


def parse_user_date(value: str) -> date:
    value = value.strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError("Используй формат ДД.ММ.ГГГГ, например 04.05.2026.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    await update.effective_message.reply_text(
        "Привет! Я считаю прибыль по финансовым отчётам WB.\n\n"
        "Команды:\n"
        "/yesterday — отчёт за вчера.\n"
        "/week — последняя завершённая неделя.\n"
        "/compare — сравнить две последние завершённые недели.\n"
        "/month — текущий месяц по вчерашний день.\n"
        "/report 04.05.2026 10.05.2026 — свой период.\n"
        "/backfill 01.04.2026 — загрузить историю по закрытым неделям.\n"
        "/backfillstatus — прогресс исторической загрузки.\n"
        "/backfillstop — аккуратно остановить загрузку после текущей недели.\n"
        "/balance — проверить подключение и баланс кабинета.\n"
        "/stocks — текущие остатки FBW и FBS.\n"
        "/supply — прогноз, на сколько дней хватит товара и сколько поставить.\n"
        "/stockalerts — проверить товары с низким остатком.\n"
        "/history — последние сохранённые финансовые периоды.\n"
        "/dbstatus — проверить базу данных.\n"
        "/schedule — статус автоматических отчётов.\n"
        "/id — показать твой Telegram ID.\n\n"
        "Также можно по-прежнему прислать детализацию WB файлом .xlsx."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(
            f"Твой Telegram ID: {update.effective_user.id}\n"
            "Добавь его в Railway как переменную ALLOWED_USER_ID, чтобы бот отвечал только тебе."
        )


async def wb_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return

    token = os.getenv("WB_API_TOKEN", "").strip()
    status = await message.reply_text("Проверяю подключение к WB API…")
    try:
        balance = await get_balance(token)
        await status.edit_text(
            "✅ Подключение к WB API работает.\n\n"
            f"Баланс кабинета: {balance.current:,.2f} {balance.currency}".replace(",", " ")
            + "\n"
            + f"Доступно к выводу: {balance.for_withdraw:,.2f} {balance.currency}".replace(",", " ")
        )
    except WbApiError as exc:
        await status.edit_text(f"❌ Не удалось подключиться к WB API.\n\n{exc}")
    except Exception as exc:
        logger.exception("Непредвиденная ошибка при запросе баланса WB")
        await status.edit_text(
            "❌ Возникла непредвиденная ошибка при проверке WB API.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def get_sales_report_throttled(
    token: str,
    date_from: date,
    date_to: date,
    *,
    period: str,
) -> list[dict]:
    """Не даёт двум финансовым запросам уйти в WB слишком близко друг к другу."""
    global _last_finance_request_at
    async with _finance_request_lock:
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - _last_finance_request_at
        wait_seconds = FINANCE_MIN_INTERVAL_SECONDS - elapsed
        if _last_finance_request_at and wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        _last_finance_request_at = loop.time()
        return await get_sales_report(token, date_from, date_to, period=period)


async def calculate_api_result(
    token: str,
    date_from: date,
    date_to: date,
    *,
    period: str,
    status=None,
    prefix: str = "",
) -> ReportResult:
    rows = await get_sales_report_throttled(
        token,
        date_from,
        date_to,
        period=period,
    )
    result = await asyncio.to_thread(
        analyze_api_report,
        rows,
        COSTS_FILE,
        date_from,
        date_to,
    )

    if status is not None:
        await status.edit_text(
            prefix
            + f"Финансовый отчёт получен. Загружаю рекламу за "
            f"{date_from.strftime('%d.%m.%Y')}–{date_to.strftime('%d.%m.%Y')}…"
        )
    try:
        ad_stats = await get_advertising_stats(token, date_from, date_to)
        result = apply_advertising(result, ad_stats)
    except WbApiError as ad_exc:
        logger.warning("Не удалось загрузить рекламу WB: %s", ad_exc)
        result.advertising_warning = str(ad_exc)
    return result


def _relative_change(current: float, previous: float) -> str:
    if abs(previous) < 1e-9:
        if abs(current) < 1e-9:
            return "без изменений"
        return "новое значение"
    change = (current - previous) / abs(previous) * 100
    arrow = "↑" if change > 0.05 else "↓" if change < -0.05 else "→"
    return f"{arrow} {abs(change):.1f}%"


def _percentage_points(current: float, previous: float) -> str:
    change = (current - previous) * 100
    arrow = "↑" if change > 0.05 else "↓" if change < -0.05 else "→"
    return f"{arrow} {abs(change):.1f} п.п."


def _short_label(value: str, limit: int = 42) -> str:
    value = value.strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def build_weekly_comparison_message(current: ReportResult, previous: ReportResult) -> str:
    margin_limit = configured_percent("ALERT_MARGIN_PERCENT", 10.0) / 100
    drr_limit = configured_percent("ALERT_DRR_PERCENT", 20.0) / 100

    lines = [
        f"📈 Сравнение недель {format_period(current.period_start, current.period_end)}",
        f"с {format_period(previous.period_start, previous.period_end)}",
        "",
        f"Продано: {format_units(current.total_units)} шт. ({_relative_change(current.total_units, previous.total_units)})",
        f"Доход: {format_money(current.total_revenue)} ({_relative_change(current.total_revenue, previous.total_revenue)})",
        f"Реклама: {format_money(current.total_advertising)} ({_relative_change(current.total_advertising, previous.total_advertising)})",
        f"ДРР: {current.drr * 100:.1f}% ({_percentage_points(current.drr, previous.drr)})",
        f"Чистая прибыль: {format_money(current.total_profit)} ({_relative_change(current.total_profit, previous.total_profit)})",
        f"Маржинальность: {current.margin * 100:.1f}% ({_percentage_points(current.margin, previous.margin)})",
    ]

    alerts: list[str] = []
    if previous.total_profit > 0 and current.total_profit < previous.total_profit * 0.85:
        alerts.append("Чистая прибыль снизилась более чем на 15% к предыдущей неделе.")
    if previous.total_revenue > 0 and current.total_revenue < previous.total_revenue * 0.80:
        alerts.append("Доход снизился более чем на 20% к предыдущей неделе.")
    if current.total_revenue > 0 and current.margin < margin_limit:
        alerts.append(
            f"Общая маржинальность {current.margin * 100:.1f}% ниже порога {margin_limit * 100:.0f}%."
        )
    if current.total_revenue > 0 and current.drr > drr_limit:
        alerts.append(f"Общий ДРР {current.drr * 100:.1f}% выше порога {drr_limit * 100:.0f}%.")
    if current.missing_cost_skus:
        alerts.append("Не найдена себестоимость: " + ", ".join(current.missing_cost_skus[:6]))

    negative_items = sorted(
        [item for item in current.active_items if item.sold_units > 0 and item.profit < -0.01],
        key=lambda item: item.profit,
    )
    for item in negative_items[:5]:
        alerts.append(
            f"{_short_label(item.name or item.sku)} продаётся в минус: {format_money(item.profit)}."
        )

    low_margin_items = sorted(
        [
            item
            for item in current.active_items
            if item.sold_units > 0
            and item.revenue > 0
            and item.profit >= -0.01
            and item.margin < margin_limit
        ],
        key=lambda item: item.margin,
    )
    for item in low_margin_items[:4]:
        alerts.append(
            f"Низкая маржа у {_short_label(item.name or item.sku)}: {item.margin * 100:.1f}%."
        )

    high_drr_items = sorted(
        [item for item in current.active_items if item.advertising > 0 and item.drr > drr_limit],
        key=lambda item: item.drr,
        reverse=True,
    )
    for item in high_drr_items[:4]:
        alerts.append(
            f"Высокий ДРР у {_short_label(item.name or item.sku)}: {item.drr * 100:.1f}%."
        )

    lines.extend(["", "⚠️ Что требует внимания:"] if alerts else ["", "✅ Критичных проблем по заданным порогам не найдено."] )
    if alerts:
        lines.extend(f"• {alert}" for alert in alerts[:12])
    lines.extend(
        [
            "",
            f"Пороги: маржа ниже {margin_limit * 100:.0f}%, ДРР выше {drr_limit * 100:.0f}%.",
        ]
    )
    return "\n".join(lines)


async def save_report_safely(
    result: ReportResult,
    *,
    period_type: str,
    source: str = "api",
) -> None:
    if not database_enabled() or not result.active_items:
        return
    try:
        await asyncio.to_thread(
            save_report,
            result,
            period_type=period_type,
            source=source,
        )
    except Exception:
        logger.exception("Не удалось сохранить отчёт в PostgreSQL")


def _history_type_label(value: str) -> str:
    return {
        "daily": "день/период",
        "weekly": "неделя",
        "xlsx": "Excel",
    }.get(value, value)


async def report_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    if not database_enabled():
        await message.reply_text(
            "История пока не подключена: в Railway нет переменной DATABASE_URL."
        )
        return
    try:
        rows = await asyncio.to_thread(list_history, 10)
        if not rows:
            await message.reply_text(
                "База подключена, но сохранённых отчётов ещё нет. "
                "Запусти /yesterday, /week или /month."
            )
            return
        lines = ["🗂 Последние сохранённые отчёты:"]
        for row in rows:
            period = format_period(row.period_start, row.period_end)
            lines.extend(
                [
                    "",
                    f"{period} · {_history_type_label(row.period_type)}",
                    f"Продано: {format_units(row.units)} шт. | Доход: {format_money(row.revenue)}",
                    f"Внешние: {format_money(row.external_expenses)} | Реклама: {format_money(row.advertising)}",
                    f"До налога: {format_money(row.profit_before_tax)} | УСН: {format_money(row.tax)}",
                    f"Чистая: {format_money(row.profit)} | Маржа: {row.margin * 100:.1f}%",
                ]
            )
        await message.reply_text("\n".join(lines))
    except Exception as exc:
        logger.exception("Ошибка чтения истории PostgreSQL")
        await message.reply_text(
            "❌ Не удалось прочитать историю.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def db_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    if not database_enabled():
        await message.reply_text("❌ DATABASE_URL не подключён к сервису бота.")
        return
    try:
        count, last_saved = await asyncio.to_thread(database_status)
        last_text = (
            last_saved.astimezone(configured_timezone()).strftime("%d.%m.%Y %H:%M")
            if last_saved
            else "ещё нет"
        )
        await message.reply_text(
            "✅ PostgreSQL подключён.\n"
            f"Сохранено периодов: {count}.\n"
            f"Последнее обновление: {last_text}."
        )
    except Exception as exc:
        logger.exception("Ошибка проверки PostgreSQL")
        await message.reply_text(
            "❌ Не удалось подключиться к PostgreSQL.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def send_api_report_to_chat(
    bot,
    chat_id: int,
    date_from: date,
    date_to: date,
    *,
    period: str = "weekly",
    automatic: bool = False,
) -> None:
    if not COSTS_FILE.exists():
        await bot.send_message(chat_id, "На сервере не найден файл себестоимости data/costs.xlsx.")
        return

    token = os.getenv("WB_API_TOKEN", "").strip()
    prefix = "⏰ Автоматический отчёт.\n\n" if automatic else ""
    status = await bot.send_message(
        chat_id,
        prefix
        + f"Загружаю отчёт WB за {date_from.strftime('%d.%m.%Y')}–{date_to.strftime('%d.%m.%Y')}…",
    )
    try:
        result = await calculate_api_result(
            token,
            date_from,
            date_to,
            period=period,
            status=status,
            prefix=prefix,
        )

        if not result.active_items:
            if period == "daily":
                await status.edit_text(
                    prefix
                    + "WB пока не сформировал ежедневный финансовый отчёт за этот период. "
                    "Такие данные могут появляться с задержкой. Попробуй команду позже. "
                    "Команда /week использует уже закрытый недельный отчёт и обычно работает стабильнее."
                )
            else:
                await status.edit_text(
                    prefix + "WB не вернул финансовых операций и рекламных расходов за этот период."
                )
            return

        await save_report_safely(result, period_type=period, source="api")
        messages = build_messages(result)
        await status.edit_text(prefix + messages[0])
        for text in messages[1:]:
            await bot.send_message(chat_id, text)
    except WbApiError as exc:
        await status.edit_text(prefix + f"❌ Не удалось получить отчёт WB.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка автоматического финансового отчёта")
        await status.edit_text(
            prefix
            + "❌ Не смог посчитать отчёт через WB API.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def send_api_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    date_from: date,
    date_to: date,
    *,
    period: str = "weekly",
) -> None:
    chat = update.effective_chat
    if not chat:
        return
    await send_api_report_to_chat(
        context.bot,
        chat.id,
        date_from,
        date_to,
        period=period,
    )


async def send_weekly_comparison_to_chat(
    bot,
    chat_id: int,
    *,
    automatic: bool = False,
) -> None:
    if not COSTS_FILE.exists():
        await bot.send_message(chat_id, "На сервере не найден файл себестоимости data/costs.xlsx.")
        return

    current_start, current_end = last_completed_week(datetime.now(configured_timezone()).date())
    previous_start, previous_end = previous_completed_week(datetime.now(configured_timezone()).date())
    prefix = "📅 Автоматический недельный отчёт.\n\n" if automatic else ""
    status = await bot.send_message(
        chat_id,
        prefix + f"Считаю неделю {format_period(current_start, current_end)}…",
    )
    token = os.getenv("WB_API_TOKEN", "").strip()

    try:
        current = await calculate_api_result(
            token,
            current_start,
            current_end,
            period="weekly",
            status=status,
            prefix=prefix,
        )
        if not current.active_items:
            await status.edit_text(prefix + "WB не вернул операций за последнюю завершённую неделю.")
            return

        await status.edit_text(
            prefix
            + f"Последняя неделя готова. Загружаю предыдущую {format_period(previous_start, previous_end)}. "
            "Из-за лимита WB это может занять около минуты…"
        )
        previous = await calculate_api_result(
            token,
            previous_start,
            previous_end,
            period="weekly",
        )
        await save_report_safely(current, period_type="weekly", source="api")
        await save_report_safely(previous, period_type="weekly", source="api")
        comparison = build_weekly_comparison_message(current, previous)
        await status.edit_text(prefix + comparison)

        current_messages = build_messages(current)
        await bot.send_message(chat_id, "📦 Детализация последней недели:\n\n" + current_messages[1])
    except WbApiError as exc:
        await status.edit_text(prefix + f"❌ Не удалось сравнить недели.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка сравнения недель")
        await status.edit_text(
            prefix
            + "❌ Не смог сравнить недели.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def compare_weeks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    chat = update.effective_chat
    if not chat:
        return
    await send_weekly_comparison_to_chat(context.bot, chat.id)


async def last_week_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = last_completed_week()
    await send_api_report(update, context, start_date, end_date, period="weekly")


async def yesterday_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = yesterday_period()
    await send_api_report(update, context, start_date, end_date, period="daily")


async def month_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = current_month_period()
    await send_api_report(update, context, start_date, end_date, period="daily")


async def custom_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    if len(context.args) != 2:
        await message.reply_text(
            "Укажи две даты. Пример:\n/report 04.05.2026 10.05.2026"
        )
        return
    try:
        start_date = parse_user_date(context.args[0])
        end_date = parse_user_date(context.args[1])
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    if end_date < start_date:
        await message.reply_text("Дата окончания не может быть раньше даты начала.")
        return
    if (end_date - start_date).days > 62:
        await message.reply_text("Пока выбирай период не длиннее 63 дней.")
        return
    is_full_weeks = (
        start_date.weekday() == 0
        and end_date.weekday() == 6
        and ((end_date - start_date).days + 1) % 7 == 0
    )
    await send_api_report(
        update,
        context,
        start_date,
        end_date,
        period="weekly" if is_full_weeks else "daily",
    )


def _week_ranges(start_date: date, end_date: date) -> list[tuple[date, date]]:
    """Возвращает полные недели понедельник–воскресенье, начиная с недели start_date."""
    first_monday = start_date - timedelta(days=start_date.weekday())
    ranges: list[tuple[date, date]] = []
    cursor = first_monday
    while cursor <= end_date:
        week_end = cursor + timedelta(days=6)
        if week_end <= end_date:
            ranges.append((cursor, week_end))
        cursor += timedelta(days=7)
    return ranges


def _backfill_state_text() -> str:
    running = bool(_backfill_state.get("running"))
    processed = int(_backfill_state.get("processed") or 0)
    total = int(_backfill_state.get("total") or 0)
    saved = int(_backfill_state.get("saved") or 0)
    empty = int(_backfill_state.get("empty") or 0)
    current_start = _backfill_state.get("current_start")
    current_end = _backfill_state.get("current_end")
    start_date = _backfill_state.get("start_date")
    end_date = _backfill_state.get("end_date")
    last_error = str(_backfill_state.get("last_error") or "")
    stopped = bool(_backfill_state.get("stopped"))

    if not total:
        return (
            "Историческая загрузка ещё не запускалась.\n"
            "Пример: /backfill 01.04.2026"
        )

    icon = "⏳" if running else "⏹" if stopped else "✅"
    status = "идёт" if running else "остановлена" if stopped else "завершена"
    lines = [
        f"{icon} Историческая загрузка {status}.",
        f"Период: {format_period(start_date, end_date)}",
        f"Обработано недель: {processed} из {total}",
        f"Сохранено: {saved} | Пустых: {empty}",
    ]
    if running and isinstance(current_start, date) and isinstance(current_end, date):
        lines.append(f"Сейчас: {format_period(current_start, current_end)}")
    if last_error:
        lines.append(f"Последняя ошибка: {last_error}")
    return "\n".join(lines)


async def _run_backfill(
    bot,
    chat_id: int,
    status_message,
    weeks: list[tuple[date, date]],
) -> None:
    global _backfill_task, _backfill_stop_event

    token = os.getenv("WB_API_TOKEN", "").strip()
    stop_event = _backfill_stop_event
    try:
        for index, (week_start, week_end) in enumerate(weeks, start=1):
            if stop_event is not None and stop_event.is_set():
                _backfill_state["stopped"] = True
                _backfill_state["running"] = False
                await status_message.edit_text(
                    "⏹ Историческая загрузка остановлена.\n\n"
                    + _backfill_state_text()
                    + "\n\nУже сохранённые недели не удалены. Повторный /backfill безопасен."
                )
                return

            _backfill_state["current_start"] = week_start
            _backfill_state["current_end"] = week_end
            remaining = len(weeks) - index + 1
            await status_message.edit_text(
                "⏳ Загружаю историю WB.\n\n"
                f"Неделя {index} из {len(weeks)}: {format_period(week_start, week_end)}\n"
                f"Сохранено: {_backfill_state['saved']} | Пустых: {_backfill_state['empty']}\n"
                f"Осталось примерно: {remaining} мин.\n\n"
                "Бот работает на Railway — компьютер можно выключить."
            )

            try:
                result = await calculate_api_result(
                    token,
                    week_start,
                    week_end,
                    period="weekly",
                )
            except WbApiError as exc:
                _backfill_state["last_error"] = str(exc)
                _backfill_state["running"] = False
                await status_message.edit_text(
                    "❌ Историческая загрузка приостановлена из-за ошибки WB API.\n\n"
                    + _backfill_state_text()
                    + "\n\nУже сохранённые недели остались в базе. После устранения ошибки "
                    "запусти ту же команду ещё раз — дубликатов не будет."
                )
                return

            if result.active_items:
                report_id = await asyncio.to_thread(
                    save_report,
                    result,
                    period_type="weekly",
                    source="api-backfill",
                )
                if report_id is None:
                    raise RuntimeError("Не удалось сохранить неделю в PostgreSQL.")
                _backfill_state["saved"] = int(_backfill_state["saved"]) + 1
            else:
                _backfill_state["empty"] = int(_backfill_state["empty"]) + 1

            _backfill_state["processed"] = index

        _backfill_state["finished_at"] = datetime.now(configured_timezone())
        _backfill_state["running"] = False
        await status_message.edit_text(
            "✅ Историческая загрузка завершена.\n\n"
            + _backfill_state_text()
            + "\n\nОбнови страницу веб-дашборда — график и список периодов расширились."
        )
    except asyncio.CancelledError:
        _backfill_state["stopped"] = True
        logger.info("Историческая загрузка отменена")
        raise
    except Exception as exc:
        _backfill_state["last_error"] = f"{type(exc).__name__}: {exc}"
        _backfill_state["running"] = False
        logger.exception("Ошибка исторической загрузки")
        try:
            await status_message.edit_text(
                "❌ Историческая загрузка остановилась.\n\n"
                + _backfill_state_text()
                + "\n\nУже сохранённые недели остались в базе. Повтори команду позже."
            )
        except Exception:
            logger.exception("Не удалось обновить сообщение о backfill")
    finally:
        _backfill_state["running"] = False
        _backfill_state["current_start"] = None
        _backfill_state["current_end"] = None
        _backfill_task = None
        _backfill_stop_event = None


async def backfill_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global _backfill_task, _backfill_stop_event

    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    chat = update.effective_chat
    if not message or not chat:
        return
    if not database_enabled():
        await message.reply_text("Сначала подключи PostgreSQL: в Railway нужна переменная DATABASE_URL.")
        return
    if not COSTS_FILE.exists():
        await message.reply_text("На сервере не найден файл себестоимости data/costs.xlsx.")
        return
    if _backfill_task is not None and not _backfill_task.done():
        await message.reply_text(_backfill_state_text())
        return
    if len(context.args) != 1:
        await message.reply_text(
            "Укажи дату, немного раньше первой продажи.\n"
            "Пример: /backfill 01.04.2026"
        )
        return
    try:
        requested_start = parse_user_date(context.args[0])
    except ValueError as exc:
        await message.reply_text(str(exc))
        return
    if requested_start < BACKFILL_EARLIEST_DATE:
        await message.reply_text(
            "Финансовая детализация WB через этот API доступна с 29.01.2024.\n"
            "Укажи дату не раньше 29.01.2024."
        )
        return

    _, last_sunday = last_completed_week(datetime.now(configured_timezone()).date())
    weeks = _week_ranges(requested_start, last_sunday)
    if not weeks:
        await message.reply_text(
            "Между указанной датой и последней завершённой неделей нет полных недель для загрузки."
        )
        return

    first_start = weeks[0][0]
    last_end = weeks[-1][1]
    _backfill_state.update(
        {
            "running": True,
            "started_at": datetime.now(configured_timezone()),
            "start_date": first_start,
            "end_date": last_end,
            "current_start": first_start,
            "current_end": weeks[0][1],
            "processed": 0,
            "total": len(weeks),
            "saved": 0,
            "empty": 0,
            "last_error": "",
            "finished_at": None,
            "stopped": False,
        }
    )
    _backfill_stop_event = asyncio.Event()
    status = await message.reply_text(
        "🚀 Историческая загрузка запущена.\n\n"
        f"Период: {format_period(first_start, last_end)}\n"
        f"Недель: {len(weeks)}\n"
        f"Ориентировочное время: около {len(weeks)}–{len(weeks) + 5} мин.\n\n"
        "Историческая прибыль будет рассчитана по текущей себестоимости из data/costs.xlsx.\n"
        "Можно закрыть Telegram и выключить компьютер. Для проверки: /backfillstatus"
    )
    _backfill_task = asyncio.create_task(
        _run_backfill(context.bot, chat.id, status, weeks),
        name="wb-history-backfill",
    )


async def backfill_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    if update.effective_message:
        await update.effective_message.reply_text(_backfill_state_text())


async def backfill_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    if _backfill_task is None or _backfill_task.done() or _backfill_stop_event is None:
        await message.reply_text("Сейчас историческая загрузка не выполняется.")
        return
    _backfill_stop_event.set()
    await message.reply_text(
        "Запрос на остановку принят. Бот закончит текущий запрос к WB и остановится перед следующей неделей."
    )


async def calculate_inventory_snapshot(
    token: str,
    *,
    include_sales: bool,
    status=None,
):
    if status is not None:
        await status.edit_text("Загружаю карточки и текущие остатки WB…")

    cards, wb_rows = await asyncio.gather(
        get_product_cards(token),
        get_wb_warehouse_stocks(token),
    )

    fbs_by_chrt: dict[int, int] = {}
    seller_warehouses = []
    fbs_warning = ""
    try:
        seller_warehouses = await get_seller_warehouses(token)
        all_chrt_ids = sorted(
            {chrt_id for card in cards for chrt_id in card.chrt_ids}
        )
        for warehouse in seller_warehouses:
            warehouse_stocks = await get_seller_warehouse_stocks(
                token,
                warehouse.warehouse_id,
                all_chrt_ids,
            )
            for chrt_id, amount in warehouse_stocks.items():
                fbs_by_chrt[chrt_id] = fbs_by_chrt.get(chrt_id, 0) + amount
    except WbApiError as exc:
        fbs_warning = str(exc)
        logger.warning("FBS-остатки не загружены: %s", exc)

    sales_result = None
    sales_window_days = configured_int("STOCK_SALES_WINDOW_DAYS", 28, minimum=7, maximum=63)
    if include_sales:
        today = datetime.now(configured_timezone()).date()
        sales_end = today - timedelta(days=1)
        sales_start = sales_end - timedelta(days=sales_window_days - 1)
        if status is not None:
            await status.edit_text(
                "Остатки получены. Загружаю продажи за "
                f"{sales_start.strftime('%d.%m.%Y')}–{sales_end.strftime('%d.%m.%Y')}…"
            )
        rows = await get_sales_report_throttled(
            token,
            sales_start,
            sales_end,
            period="daily",
        )
        sales_result = await asyncio.to_thread(
            analyze_api_report,
            rows,
            COSTS_FILE,
            sales_start,
            sales_end,
        )

    return build_inventory_snapshot(
        as_of=datetime.now(configured_timezone()).date(),
        cards=cards,
        wb_stock_rows=wb_rows,
        fbs_by_chrt=fbs_by_chrt,
        seller_warehouses=seller_warehouses,
        sales_result=sales_result,
        sales_window_days=sales_window_days,
        fbs_warning=fbs_warning,
    )


async def stocks_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    token = os.getenv("WB_API_TOKEN", "").strip()
    status = await message.reply_text("Загружаю текущие остатки WB…")
    try:
        snapshot = await calculate_inventory_snapshot(
            token,
            include_sales=False,
            status=status,
        )
        messages = build_stock_messages(snapshot)
        await status.edit_text(messages[0])
        for text in messages[1:]:
            await message.reply_text(text)
    except WbApiError as exc:
        await status.edit_text(f"❌ Не удалось получить остатки WB.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка отчёта по остаткам")
        await status.edit_text(
            "❌ Не смог построить отчёт по остаткам.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def supply_forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    token = os.getenv("WB_API_TOKEN", "").strip()
    status = await message.reply_text(
        "Собираю остатки и продажи для прогноза поставки. Это может занять около минуты…"
    )
    try:
        snapshot = await calculate_inventory_snapshot(
            token,
            include_sales=True,
            status=status,
        )
        low_days = configured_int("STOCK_LOW_DAYS", 14, minimum=1, maximum=180)
        target_days = configured_int("STOCK_TARGET_DAYS", 30, minimum=1, maximum=365)
        if target_days < low_days:
            target_days = low_days
        messages = build_supply_messages(
            snapshot,
            low_days=low_days,
            target_days=target_days,
        )
        await status.edit_text(messages[0])
        for text in messages[1:]:
            await message.reply_text(text)
    except WbApiError as exc:
        await status.edit_text(f"❌ Не удалось построить прогноз поставки.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка прогноза поставки")
        await status.edit_text(
            "❌ Не смог построить прогноз поставки.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def stock_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return
    token = os.getenv("WB_API_TOKEN", "").strip()
    status = await message.reply_text(
        "Проверяю остатки и скорость продаж. Это может занять около минуты…"
    )
    try:
        snapshot = await calculate_inventory_snapshot(
            token,
            include_sales=True,
            status=status,
        )
        low_days = configured_int("STOCK_LOW_DAYS", 14, minimum=1, maximum=180)
        target_days = configured_int("STOCK_TARGET_DAYS", 30, minimum=1, maximum=365)
        if target_days < low_days:
            target_days = low_days
        alert_text = build_stock_alert_message(
            snapshot,
            low_days=low_days,
            target_days=target_days,
        )
        if alert_text:
            await status.edit_text(alert_text)
        else:
            await status.edit_text(
                f"✅ Товаров с запасом менее {low_days} дней не найдено."
            )
    except WbApiError as exc:
        await status.edit_text(f"❌ Не удалось проверить низкие остатки.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка контроля низких остатков")
        await status.edit_text(
            "❌ Не смог проверить низкие остатки.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def schedule_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    if not message:
        return

    timezone = configured_timezone()
    daily_hour, daily_minute = configured_daily_time()
    weekly_hour, weekly_minute = configured_weekly_time()
    stock_hour, stock_minute = configured_stock_alert_time()

    daily_status = (
        f"✅ Ежедневный: каждый день в {daily_hour:02d}:{daily_minute:02d}"
        if auto_daily_enabled()
        else "⏸ Ежедневный: выключен"
    )
    weekly_status = (
        f"✅ Недельный: каждый понедельник в {weekly_hour:02d}:{weekly_minute:02d}"
        if auto_weekly_enabled()
        else "⏸ Недельный: выключен"
    )
    stock_status = (
        f"✅ Остатки: каждый день в {stock_hour:02d}:{stock_minute:02d}"
        if auto_stock_alert_enabled()
        else "⏸ Остатки: автоматические предупреждения выключены"
    )
    await message.reply_text(
        f"{daily_status}\n{weekly_status}\n{stock_status}\nЧасовой пояс: {timezone.key}\n\n"
        "Настройки Railway Variables:\n"
        "AUTO_DAILY_REPORT, DAILY_REPORT_TIME, AUTO_WEEKLY_REPORT, WEEKLY_REPORT_TIME, "
        "AUTO_STOCK_ALERT, STOCK_ALERT_TIME, REPORT_TIMEZONE."
    )


async def scheduled_daily_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id if context.job else allowed_user_id()
    if not chat_id:
        logger.error("Автоматический отчёт пропущен: не задан ALLOWED_USER_ID.")
        return

    start_date, end_date = yesterday_period(datetime.now(configured_timezone()).date())
    await send_api_report_to_chat(
        context.bot,
        int(chat_id),
        start_date,
        end_date,
        period="daily",
        automatic=True,
    )


async def scheduled_weekly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id if context.job else allowed_user_id()
    if not chat_id:
        logger.error("Автоматический недельный отчёт пропущен: не задан ALLOWED_USER_ID.")
        return
    await send_weekly_comparison_to_chat(context.bot, int(chat_id), automatic=True)


async def scheduled_stock_alert(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.chat_id if context.job else allowed_user_id()
    if not chat_id:
        logger.error("Автоматический контроль остатков пропущен: не задан ALLOWED_USER_ID.")
        return

    token = os.getenv("WB_API_TOKEN", "").strip()
    try:
        snapshot = await calculate_inventory_snapshot(token, include_sales=True)
        low_days = configured_int("STOCK_LOW_DAYS", 14, minimum=1, maximum=180)
        target_days = configured_int("STOCK_TARGET_DAYS", 30, minimum=1, maximum=365)
        if target_days < low_days:
            target_days = low_days
        alert_text = build_stock_alert_message(
            snapshot,
            low_days=low_days,
            target_days=target_days,
        )
        if alert_text:
            await context.bot.send_message(int(chat_id), "📅 Автоматическая проверка.\n\n" + alert_text)
        else:
            logger.info("Автоматический контроль остатков: критичных позиций нет.")
    except Exception:
        logger.exception("Ошибка автоматического контроля остатков")


async def initialize_application(application: Application) -> None:
    if database_enabled():
        try:
            await asyncio.to_thread(init_database)
            logger.info("PostgreSQL подключён, таблицы истории готовы.")
        except Exception:
            logger.exception("Не удалось инициализировать PostgreSQL. Бот продолжит работу без истории.")
    else:
        logger.info("DATABASE_URL не задан: история отчётов отключена.")

    try:
        start_dashboard_server()
    except Exception:
        logger.exception("Не удалось запустить веб-дашборд. Telegram-бот продолжит работу.")

    await configure_jobs(application)


async def configure_jobs(application: Application) -> None:
    chat_id = allowed_user_id()
    if not chat_id:
        logger.error("Автоматические отчёты не запущены: не задан ALLOWED_USER_ID.")
        return

    job_queue = application.job_queue
    if job_queue is None:
        logger.error(
            "JobQueue недоступен. Проверь requirements.txt: нужен python-telegram-bot[job-queue]."
        )
        return

    timezone = configured_timezone()

    if auto_daily_enabled():
        hour, minute = configured_daily_time()
        job_queue.run_daily(
            scheduled_daily_report,
            time=dt_time(hour=hour, minute=minute, tzinfo=timezone),
            chat_id=chat_id,
            user_id=chat_id,
            name="daily-yesterday-profit-report",
            job_kwargs={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 3600,
            },
        )
        logger.info(
            "Автоматический ежедневный отчёт назначен на %02d:%02d (%s)",
            hour,
            minute,
            timezone.key,
        )
    else:
        logger.info("Автоматический ежедневный отчёт выключен.")

    if auto_weekly_enabled():
        hour, minute = configured_weekly_time()
        job_queue.run_daily(
            scheduled_weekly_report,
            time=dt_time(hour=hour, minute=minute, tzinfo=timezone),
            days=(1,),  # 0 — воскресенье, 1 — понедельник в PTB 22.x
            chat_id=chat_id,
            user_id=chat_id,
            name="weekly-profit-comparison",
            job_kwargs={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 7200,
            },
        )
        logger.info(
            "Автоматический недельный отчёт назначен на понедельник %02d:%02d (%s)",
            hour,
            minute,
            timezone.key,
        )
    else:
        logger.info("Автоматический недельный отчёт выключен.")


    if auto_stock_alert_enabled():
        hour, minute = configured_stock_alert_time()
        job_queue.run_daily(
            scheduled_stock_alert,
            time=dt_time(hour=hour, minute=minute, tzinfo=timezone),
            chat_id=chat_id,
            user_id=chat_id,
            name="daily-low-stock-alert",
            job_kwargs={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 3600,
            },
        )
        logger.info(
            "Автоматический контроль остатков назначен на %02d:%02d (%s)",
            hour,
            minute,
            timezone.key,
        )
    else:
        logger.info("Автоматический контроль остатков выключен.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    message = update.effective_message
    document = message.document if message else None
    if not message or not document:
        return

    filename = document.file_name or "report.xlsx"
    if not filename.lower().endswith(".xlsx"):
        await message.reply_text("Нужен Excel-файл в формате .xlsx.")
        return
    if document.file_size and document.file_size > MAX_FILE_SIZE:
        await message.reply_text("Файл больше 20 МБ. Пришли более компактный отчёт.")
        return
    if not COSTS_FILE.exists():
        await message.reply_text("На сервере не найден файл себестоимости data/costs.xlsx.")
        return

    status = await message.reply_text("Получил файл. Считаю прибыль…")
    temp_path: Path | None = None
    try:
        telegram_file = await document.get_file()
        suffix = Path(filename).suffix or ".xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = Path(tmp.name)
        await telegram_file.download_to_drive(custom_path=temp_path)

        result = await asyncio.to_thread(analyze_report, temp_path, COSTS_FILE)
        await save_report_safely(result, period_type="xlsx", source="telegram-xlsx")
        messages = build_messages(result)
        await status.edit_text(messages[0])
        for text in messages[1:]:
            await message.reply_text(text)
    except Exception as exc:
        logger.exception("Ошибка при обработке отчёта")
        await status.edit_text(
            "Не смог обработать файл. Проверь, что это именно детализация еженедельного отчёта WB в формате XLSX.\n\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


async def unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    if update.effective_message:
        await update.effective_message.reply_text(
            "Используй /yesterday, /week, /compare, /month, /report, /backfill, "
            "/backfillstatus, /stocks, /supply, /stockalerts, /history, /dbstatus, "
            "/schedule или пришли файл .xlsx."
        )


def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задана переменная BOT_TOKEN.")

    app = Application.builder().token(token).post_init(initialize_application).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("balance", wb_balance))
    app.add_handler(CommandHandler("yesterday", yesterday_report))
    app.add_handler(CommandHandler("week", last_week_report))
    app.add_handler(CommandHandler("compare", compare_weeks))
    app.add_handler(CommandHandler("month", month_report))
    app.add_handler(CommandHandler("report", custom_report))
    app.add_handler(CommandHandler("backfill", backfill_history))
    app.add_handler(CommandHandler("backfillstatus", backfill_status))
    app.add_handler(CommandHandler("backfillstop", backfill_stop))
    app.add_handler(CommandHandler("stocks", stocks_report))
    app.add_handler(CommandHandler("supply", supply_forecast))
    app.add_handler(CommandHandler("stockalerts", stock_alerts))
    app.add_handler(CommandHandler("history", report_history))
    app.add_handler(CommandHandler("dbstatus", db_status_command))
    app.add_handler(CommandHandler("schedule", schedule_status))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    logger.info("WB Profit Bot v13 запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
