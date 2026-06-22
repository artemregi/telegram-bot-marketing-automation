import asyncio
import logging
import os
from pathlib import Path

from aiogram import Router, F, Bot
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, FSInputFile

from app.models.database import async_session_maker
from app.models.models import MessageLog, User
from app.services.keyword_service import keyword_service
from app.services.user_service import user_service
from sqlalchemy import select, update

logger = logging.getLogger(__name__)
router = Router()

FILES_DIR = os.getenv("FILES_DIR", "/data/files")

OPT_OUT_WORDS = {"стоп", "отписаться", "stop", "unsubscribe"}


def detect_file_type(file_path: str) -> str:
    """Detect file type by extension for sending via Telegram."""
    ext = Path(file_path).suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "photo"
    elif ext in {".mp4", ".mov", ".avi", ".mkv"}:
        return "video"
    else:
        return "document"


async def send_file_to_user(bot: Bot, chat_id: int, file_path: str, caption: str | None = None) -> bool:
    """Send a file to user, detecting type automatically. Returns True on success."""
    full_path = os.path.join(FILES_DIR, file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(full_path):
        logger.warning(f"File not found: {full_path}")
        return False

    file_type = detect_file_type(full_path)
    input_file = FSInputFile(full_path)

    try:
        if file_type == "photo":
            await bot.send_photo(chat_id=chat_id, photo=input_file, caption=caption)
        elif file_type == "video":
            await bot.send_video(chat_id=chat_id, video=input_file, caption=caption)
        else:
            await bot.send_document(chat_id=chat_id, document=input_file, caption=caption)
        return True
    except Exception as e:
        logger.error(f"Failed to send file {full_path} to {chat_id}: {e}")
        return False


async def schedule_follow_up(bot: Bot, chat_id: int, message: str, delay_minutes: int):
    """Schedule a follow-up message after a delay."""
    await asyncio.sleep(delay_minutes * 60)
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        logger.info(f"Follow-up sent to {chat_id} after {delay_minutes} minutes.")
    except Exception as e:
        logger.error(f"Failed to send follow-up to {chat_id}: {e}")


async def log_message(session, user_id: int | None, keyword_matched: str | None, file_sent: str | None):
    """Write an entry to messages_log."""
    log_entry = MessageLog(
        user_id=user_id,
        keyword_matched=keyword_matched,
        file_sent=file_sent,
    )
    session.add(log_entry)
    await session.commit()


@router.message(CommandStart())
async def handle_start(message: Message, db_user: User | None = None):
    """Handle /start command."""
    name = message.from_user.first_name or "друг"
    await message.answer(
        f"Привет, <b>{name}</b>! 👋\n\n"
        "Я готов помочь вам. Просто напишите ключевое слово или вопрос.\n\n"
        "Чтобы отписаться от рассылки, напишите <b>стоп</b>."
    )


@router.message(Command("help"))
async def handle_help(message: Message):
    """Handle /help command."""
    await message.answer(
        "<b>Как пользоваться ботом:</b>\n\n"
        "• Напишите ключевое слово и получите ответ\n"
        "• Для отписки напишите: <b>стоп</b>\n\n"
        "По вопросам обращайтесь к администратору."
    )


@router.message(F.text)
async def handle_text(message: Message, bot: Bot, db_user: User | None = None):
    """Handle all text messages — check opt-out, then keyword matching."""
    text = message.text or ""
    normalized = text.strip().lower()

    # --- Opt-out check ---
    if normalized in OPT_OUT_WORDS:
        async with async_session_maker() as session:
            if db_user:
                await session.execute(
                    update(User)
                    .where(User.id == db_user.id)
                    .values(is_subscribed=False)
                )
                await session.commit()
        await message.answer("Вы отписались от рассылки. Чтобы снова получать сообщения, напишите /start.")
        return

    # --- Keyword matching ---
    async with async_session_maker() as session:
        keyword_obj = await keyword_service.match_keyword(session, normalized)

        if keyword_obj is None:
            # No match — silently ignore or optionally send a default reply
            return

        # Send response text if configured
        if keyword_obj.response_text:
            await message.answer(keyword_obj.response_text)

        # Send file if configured
        file_sent_name = None
        if keyword_obj.file_path:
            success = await send_file_to_user(
                bot=bot,
                chat_id=message.chat.id,
                file_path=keyword_obj.file_path,
                caption=keyword_obj.file_caption,
            )
            if success:
                file_sent_name = keyword_obj.file_path

        # Log the interaction
        user_id = db_user.id if db_user else None
        await log_message(session, user_id, keyword_obj.keyword, file_sent_name)

        # Schedule follow-up if configured
        if keyword_obj.follow_up_message and keyword_obj.follow_up_delay_minutes:
            asyncio.create_task(
                schedule_follow_up(
                    bot=bot,
                    chat_id=message.chat.id,
                    message=keyword_obj.follow_up_message,
                    delay_minutes=keyword_obj.follow_up_delay_minutes,
                )
            )
            logger.info(
                f"Follow-up scheduled for {message.chat.id} "
                f"in {keyword_obj.follow_up_delay_minutes} minutes."
            )
