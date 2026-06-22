import csv
import io
import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select, update, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import User

logger = logging.getLogger(__name__)


class UserService:

    async def upsert_user(
        self,
        session: AsyncSession,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
        last_name: Optional[str],
    ) -> User:
        """Create or update a user record. Returns the user object."""
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()

        now = datetime.now(timezone.utc)

        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                created_at=now,
                last_active_at=now,
                tags=[],
                is_subscribed=True,
            )
            session.add(user)
            await session.flush()
            logger.info(f"New user created: telegram_id={telegram_id}")
        else:
            user.username = username
            user.first_name = first_name
            user.last_name = last_name
            user.last_active_at = now
            logger.debug(f"User updated: telegram_id={telegram_id}")

        return user

    async def get_user_by_telegram_id(self, session: AsyncSession, telegram_id: int) -> Optional[User]:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def get_users(self, session: AsyncSession, search: Optional[str] = None) -> List[User]:
        """Get all users, optionally filtered by search term."""
        stmt = select(User).order_by(User.created_at.desc())
        if search:
            search_term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    User.username.ilike(search_term),
                    User.first_name.ilike(search_term),
                    User.last_name.ilike(search_term),
                    cast(User.telegram_id, String).ilike(search_term),
                )
            )
        result = await session.execute(stmt)
        return result.scalars().all()

    async def add_tag(self, session: AsyncSession, user_id: int, tag: str) -> Optional[User]:
        """Add a tag to a user. Tags are stored as a JSON array."""
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            return None
        tag = tag.strip().lower()
        current_tags = list(user.tags or [])
        if tag not in current_tags:
            current_tags.append(tag)
            user.tags = current_tags
            await session.flush()
        return user

    async def remove_tag(self, session: AsyncSession, user_id: int, tag: str) -> Optional[User]:
        """Remove a tag from a user."""
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            return None
        tag = tag.strip().lower()
        current_tags = list(user.tags or [])
        if tag in current_tags:
            current_tags.remove(tag)
            user.tags = current_tags
            await session.flush()
        return user

    async def get_all_tags(self, session: AsyncSession) -> List[str]:
        """Get all unique tags across all users."""
        result = await session.execute(select(User.tags))
        all_tag_lists = result.scalars().all()
        unique_tags = set()
        for tag_list in all_tag_lists:
            if tag_list:
                unique_tags.update(tag_list)
        return sorted(list(unique_tags))

    async def export_csv(self, session: AsyncSession) -> str:
        """Export all users to a CSV string."""
        users = await self.get_users(session)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "telegram_id", "username", "first_name", "last_name",
            "created_at", "last_active_at", "tags", "is_subscribed"
        ])
        for user in users:
            writer.writerow([
                user.id,
                user.telegram_id,
                user.username or "",
                user.first_name or "",
                user.last_name or "",
                user.created_at.isoformat() if user.created_at else "",
                user.last_active_at.isoformat() if user.last_active_at else "",
                ",".join(user.tags or []),
                user.is_subscribed,
            ])
        return output.getvalue()


user_service = UserService()
