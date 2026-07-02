from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from wb_api import WbApiError, get_advertising_stats, get_balance, get_sales_report
from wb_api_profit import analyze_api_report, apply_advertising
from wb_profit import analyze_report, build_messages


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


def allowed_user_id() -> int | None:
    raw = os.getenv("ALLOWED_USER_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("ALLOWED_USER_ID указан неверно: %s", raw)
        return None


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
        "/month — текущий месяц по вчерашний день.\n"
        "/report 04.05.2026 10.05.2026 — свой период.\n"
        "/balance — проверить подключение и баланс кабинета.\n"
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


async def send_api_report(
    update: Update,
    date_from: date,
    date_to: date,
) -> None:
    message = update.effective_message
    if not message:
        return
    if not COSTS_FILE.exists():
        await message.reply_text("На сервере не найден файл себестоимости data/costs.xlsx.")
        return

    token = os.getenv("WB_API_TOKEN", "").strip()
    status = await message.reply_text(
        f"Загружаю отчёт WB за {date_from.strftime('%d.%m.%Y')}–{date_to.strftime('%d.%m.%Y')}…"
    )
    try:
        rows = await get_sales_report(token, date_from, date_to, period="weekly")
        result = await asyncio.to_thread(
            analyze_api_report,
            rows,
            COSTS_FILE,
            date_from,
            date_to,
        )

        await status.edit_text(
            f"Финансовый отчёт получен. Загружаю рекламу за "
            f"{date_from.strftime('%d.%m.%Y')}–{date_to.strftime('%d.%m.%Y')}…"
        )
        try:
            ad_stats = await get_advertising_stats(token, date_from, date_to)
            result = apply_advertising(result, ad_stats)
        except WbApiError as ad_exc:
            logger.warning("Не удалось загрузить рекламу WB: %s", ad_exc)
            result.advertising_warning = str(ad_exc)

        if not result.active_items:
            await status.edit_text(
                "WB не вернул финансовых операций и рекламных расходов за этот период."
            )
            return

        messages = build_messages(result)
        await status.edit_text(messages[0])
        for text in messages[1:]:
            await message.reply_text(text)
    except WbApiError as exc:
        await status.edit_text(f"❌ Не удалось получить отчёт WB.\n\n{exc}")
    except Exception as exc:
        logger.exception("Ошибка автоматического финансового отчёта")
        await status.edit_text(
            "❌ Не смог посчитать отчёт через WB API.\n"
            f"Техническая ошибка: {type(exc).__name__}: {exc}"
        )


async def last_week_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = last_completed_week()
    await send_api_report(update, start_date, end_date)


async def yesterday_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = yesterday_period()
    await send_api_report(update, start_date, end_date)


async def month_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    start_date, end_date = current_month_period()
    await send_api_report(update, start_date, end_date)


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
    await send_api_report(update, start_date, end_date)


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
            "Используй /yesterday, /week, /month, /report или пришли файл .xlsx."
        )


def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задана переменная BOT_TOKEN.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("balance", wb_balance))
    app.add_handler(CommandHandler("yesterday", yesterday_report))
    app.add_handler(CommandHandler("week", last_week_report))
    app.add_handler(CommandHandler("month", month_report))
    app.add_handler(CommandHandler("report", custom_report))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    logger.info("WB Profit Bot v5 запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
