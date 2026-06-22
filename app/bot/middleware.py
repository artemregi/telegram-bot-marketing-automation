from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from app.models.database import async_session_maker
from app.services.user_service import user_service


class UserMiddleware(BaseMiddleware):
    """
    Middleware that upserts user record on every message event.
    Stores the user object in the data dict for handlers to use.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user:
            tg_user = event.from_user
            async with async_session_maker() as session:
                db_user = await user_service.upsert_user(
                    session=session,
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    last_name=tg_user.last_name,
                )
                await session.commit()
                data["db_user"] = db_user
        return await handler(event, data)
