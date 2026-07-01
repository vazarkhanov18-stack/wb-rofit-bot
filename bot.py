from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from wb_profit import analyze_report, build_messages


BASE_DIR = Path(__file__).resolve().parent
COSTS_FILE = BASE_DIR / "data" / "costs.xlsx"
MAX_FILE_SIZE = 20 * 1024 * 1024

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if await reject_if_not_allowed(update):
        return
    await update.effective_message.reply_text(
        "Привет! Я считаю прибыль по еженедельной детализации WB.\n\n"
        "1. Скачай детализацию отчёта реализации в формате XLSX.\n"
        "2. Отправь файл сюда как документ.\n"
        "3. Я посчитаю выплату, себестоимость, УСН 6%, прибыль и маржу.\n\n"
        "Команда /id покажет твой Telegram ID для закрытия доступа к боту."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def show_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_message:
        await update.effective_message.reply_text(
            f"Твой Telegram ID: {update.effective_user.id}\n"
            "Добавь его в Railway как переменную ALLOWED_USER_ID, чтобы бот отвечал только тебе."
        )


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
        await update.effective_message.reply_text("Пришли детализацию WB файлом .xlsx или используй /help.")


def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Не задана переменная BOT_TOKEN.")

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.ALL, unknown_message))

    logger.info("WB Profit Bot запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
