import asyncio
import logging
import random
from datetime import datetime, timezone, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from sqlalchemy import select, update

from app.models.database import async_session_maker
from app.models.models import Broadcast, User
from app.services.tg_client import tg_client_service

logger = logging.getLogger(__name__)

# Zero-width space for message randomization (avoids duplicate detection)
ZERO_WIDTH_SPACE = "\u200b"


class BroadcastService:
    def __init__(self):
        self._bot: Optional[Bot] = None
        self._running_tasks: dict[int, asyncio.Task] = {}

    def set_bot(self, bot: Bot):
        self._bot = bot

    async def create_broadcast(self, message_text: str, target_tag: Optional[str] = None) -> Broadcast:
        """Create a new broadcast record in draft status."""
        async with async_session_maker() as session:
            broadcast = Broadcast(
                message_text=message_text,
                target_tag=target_tag,
                status="draft",
                sent_count=0,
            )
            session.add(broadcast)
            await session.commit()
            await session.refresh(broadcast)
            return broadcast

    async def start_broadcast(self, broadcast_id: int) -> bool:
        """Start a broadcast as a background asyncio task."""
        if self._bot is None:
            logger.error("Bot not set on BroadcastService. Cannot start broadcast.")
            return False

        if broadcast_id in self._running_tasks:
            task = self._running_tasks[broadcast_id]
            if not task.done():
                logger.warning(f"Broadcast {broadcast_id} is already running.")
                return False

        task = asyncio.create_task(self.run_broadcast(broadcast_id, self._bot))
        self._running_tasks[broadcast_id] = task
        return True

    async def run_broadcast(self, broadcast_id: int, bot: Bot):
        """
        Main broadcast loop with anti-ban measures:
        - Random delay 30-90s between messages
        - Max 40 messages per hour
        - Zero-width space randomization
        - Auto-unsubscribe on blocked users
        """
        logger.info(f"Starting broadcast id={broadcast_id}")

        async with async_session_maker() as session:
            result = await session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
            broadcast = result.scalar_one_or_none()
            if broadcast is None:
                logger.error(f"Broadcast {broadcast_id} not found.")
                return

            # Mark as running
            broadcast.status = "running"
            broadcast.started_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(broadcast)

            message_text = broadcast.message_text
            target_tag = broadcast.target_tag

        # Get target users
        async with async_session_maker() as session:
            stmt = select(User).where(User.is_subscribed == True)
            if target_tag:
                # Filter users that have the tag in their tags JSON array
                # Using JSONB contains operator via text cast approach
                from sqlalchemy import func, cast
                from sqlalchemy.dialects.postgresql import JSONB
                stmt = stmt.where(
                    User.tags.op("@>")(cast([target_tag], JSONB))
                )
            result = await session.execute(stmt)
            users = result.scalars().all()
            user_ids = [(u.id, u.telegram_id) for u in users]

        logger.info(f"Broadcast {broadcast_id}: targeting {len(user_ids)} users.")

        # Try to get Pyrogram user-account client; fall back to bot
        pyro_client = None
        try:
            pyro_client = await tg_client_service.get_client()
            if pyro_client:
                logger.info(f"Broadcast {broadcast_id}: using Pyrogram user account.")
            else:
                logger.info(f"Broadcast {broadcast_id}: using bot (no Pyrogram session).")
        except Exception as e:
            logger.warning(f"Could not start Pyrogram client: {e}. Falling back to bot.")
            pyro_client = None

        sent_count = 0
        sent_this_hour = 0
        hour_start = datetime.now(timezone.utc)
        MAX_PER_HOUR = 40

        try:
            for user_id, telegram_id in user_ids:
                # Check hourly rate limit
                now = datetime.now(timezone.utc)
                if (now - hour_start) >= timedelta(hours=1):
                    sent_this_hour = 0
                    hour_start = now

                if sent_this_hour >= MAX_PER_HOUR:
                    wait_seconds = 3600 - (now - hour_start).total_seconds()
                    if wait_seconds > 0:
                        logger.info(f"Broadcast {broadcast_id}: hourly limit. Waiting {wait_seconds:.0f}s...")
                        await asyncio.sleep(wait_seconds)
                    sent_this_hour = 0
                    hour_start = datetime.now(timezone.utc)

                # Randomize message
                randomized_message = message_text + ZERO_WIDTH_SPACE * random.randint(1, 5)

                try:
                    if pyro_client:
                        await pyro_client.send_message(chat_id=telegram_id, text=randomized_message)
                    else:
                        await bot.send_message(chat_id=telegram_id, text=randomized_message)

                    sent_count += 1
                    sent_this_hour += 1
                    logger.debug(f"Broadcast {broadcast_id}: sent to {telegram_id}")

                    if sent_count % 10 == 0:
                        async with async_session_maker() as session:
                            await session.execute(
                                update(Broadcast)
                                .where(Broadcast.id == broadcast_id)
                                .values(sent_count=sent_count)
                            )
                            await session.commit()

                except TelegramForbiddenError:
                    logger.info(f"User {telegram_id} blocked — unsubscribing.")
                    async with async_session_maker() as session:
                        await session.execute(
                            update(User).where(User.id == user_id).values(is_subscribed=False)
                        )
                        await session.commit()

                except TelegramBadRequest as e:
                    if "bot was blocked" in str(e).lower() or "user is deactivated" in str(e).lower():
                        logger.info(f"User {telegram_id} blocked/deactivated — unsubscribing.")
                        async with async_session_maker() as session:
                            await session.execute(
                                update(User).where(User.id == user_id).values(is_subscribed=False)
                            )
                            await session.commit()
                    else:
                        logger.warning(f"TelegramBadRequest for {telegram_id}: {e}")

                except Exception as e:
                    if "USER_PRIVACY_RESTRICTED" in str(e):
                        logger.info(f"User {telegram_id} has privacy restrictions — skipping.")
                    elif "INPUT_USER_DEACTIVATED" in str(e) or "USER_DEACTIVATED" in str(e):
                        logger.info(f"User {telegram_id} deactivated — unsubscribing.")
                        async with async_session_maker() as session:
                            await session.execute(
                                update(User).where(User.id == user_id).values(is_subscribed=False)
                            )
                            await session.commit()
                    else:
                        logger.error(f"Error sending to {telegram_id}: {e}")

                delay = random.randint(30, 90)
                await asyncio.sleep(delay)

        finally:
            if pyro_client:
                try:
                    await pyro_client.stop()
                except Exception:
                    pass

        # Mark broadcast as done
        async with async_session_maker() as session:
            await session.execute(
                update(Broadcast)
                .where(Broadcast.id == broadcast_id)
                .values(
                    status="done",
                    sent_count=sent_count,
                    finished_at=datetime.now(timezone.utc),
                )
            )
            await session.commit()

        logger.info(f"Broadcast {broadcast_id} completed. Sent: {sent_count}/{len(user_ids)}.")

    async def get_running_broadcasts(self) -> list[dict]:
        """Return info about all running broadcasts."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(Broadcast)
                .where(Broadcast.status.in_(["running", "draft"]))
                .order_by(Broadcast.created_at.desc())
            )
            broadcasts = result.scalars().all()
            return [
                {
                    "id": b.id,
                    "status": b.status,
                    "sent_count": b.sent_count,
                    "target_tag": b.target_tag,
                    "started_at": b.started_at.isoformat() if b.started_at else None,
                    "message_preview": b.message_text[:80] + "..." if len(b.message_text) > 80 else b.message_text,
                }
                for b in broadcasts
            ]

    async def get_recent_broadcasts(self, limit: int = 10) -> list[dict]:
        """Return recent broadcasts for status display."""
        async with async_session_maker() as session:
            result = await session.execute(
                select(Broadcast)
                .order_by(Broadcast.created_at.desc())
                .limit(limit)
            )
            broadcasts = result.scalars().all()
            return [
                {
                    "id": b.id,
                    "status": b.status,
                    "sent_count": b.sent_count,
                    "target_tag": b.target_tag or "all",
                    "created_at": b.created_at.isoformat() if b.created_at else None,
                    "started_at": b.started_at.isoformat() if b.started_at else None,
                    "finished_at": b.finished_at.isoformat() if b.finished_at else None,
                    "message_preview": b.message_text[:80] + "..." if len(b.message_text) > 80 else b.message_text,
                }
                for b in broadcasts
            ]


broadcast_service = BroadcastService()
