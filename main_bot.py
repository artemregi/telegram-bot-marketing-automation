import asyncio
import logging
from dotenv import load_dotenv
load_dotenv()
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
import os
from app.models.database import create_tables
from app.bot.handlers import router
from app.bot.middleware import UserMiddleware
from app.services.broadcast import broadcast_service


async def main():
    logging.basicConfig(level=logging.INFO)
    await create_tables()
    bot = Bot(
        token=os.getenv("BOT_TOKEN"),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    dp = Dispatcher()
    dp.message.middleware(UserMiddleware())
    dp.include_router(router)
    broadcast_service.set_bot(bot)
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
