import logging
from typing import Dict, Any, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import Keyword

logger = logging.getLogger(__name__)


class KeywordService:

    async def get_all_keywords(self, session: AsyncSession) -> List[Keyword]:
        """Return all keywords ordered by creation date."""
        result = await session.execute(select(Keyword).order_by(Keyword.created_at.desc()))
        return result.scalars().all()

    async def get_active_keywords(self, session: AsyncSession) -> List[Keyword]:
        """Return only active keywords."""
        result = await session.execute(
            select(Keyword).where(Keyword.is_active == True).order_by(Keyword.keyword)
        )
        return result.scalars().all()

    async def match_keyword(self, session: AsyncSession, text: str) -> Optional[Keyword]:
        """
        Normalize text and find matching active keyword.
        Performs exact match on the normalized (lowercase, stripped) text.
        """
        normalized = text.strip().lower()
        result = await session.execute(
            select(Keyword).where(
                Keyword.keyword == normalized,
                Keyword.is_active == True,
            )
        )
        return result.scalar_one_or_none()

    async def get_keyword_by_id(self, session: AsyncSession, keyword_id: int) -> Optional[Keyword]:
        result = await session.execute(select(Keyword).where(Keyword.id == keyword_id))
        return result.scalar_one_or_none()

    async def create_keyword(self, session: AsyncSession, data: Dict[str, Any]) -> Keyword:
        """Create a new keyword. Keyword text is stored lowercase."""
        keyword_text = data.get("keyword", "").strip().lower()
        if not keyword_text:
            raise ValueError("Keyword text cannot be empty.")

        keyword = Keyword(
            keyword=keyword_text,
            file_path=data.get("file_path") or None,
            file_caption=data.get("file_caption") or None,
            response_text=data.get("response_text") or None,
            is_active=data.get("is_active", True),
            follow_up_message=data.get("follow_up_message") or None,
            follow_up_delay_minutes=data.get("follow_up_delay_minutes") or None,
        )
        session.add(keyword)
        await session.flush()
        logger.info(f"Keyword created: '{keyword_text}'")
        return keyword

    async def update_keyword(self, session: AsyncSession, keyword_id: int, data: Dict[str, Any]) -> Optional[Keyword]:
        """Update an existing keyword by ID."""
        keyword = await self.get_keyword_by_id(session, keyword_id)
        if keyword is None:
            return None

        if "keyword" in data and data["keyword"]:
            keyword.keyword = data["keyword"].strip().lower()
        if "file_path" in data:
            keyword.file_path = data["file_path"] or None
        if "file_caption" in data:
            keyword.file_caption = data["file_caption"] or None
        if "response_text" in data:
            keyword.response_text = data["response_text"] or None
        if "is_active" in data:
            keyword.is_active = bool(data["is_active"])
        if "follow_up_message" in data:
            keyword.follow_up_message = data["follow_up_message"] or None
        if "follow_up_delay_minutes" in data:
            val = data["follow_up_delay_minutes"]
            keyword.follow_up_delay_minutes = int(val) if val else None

        await session.flush()
        logger.info(f"Keyword updated: id={keyword_id}")
        return keyword

    async def delete_keyword(self, session: AsyncSession, keyword_id: int) -> bool:
        """Delete a keyword by ID. Returns True if deleted."""
        keyword = await self.get_keyword_by_id(session, keyword_id)
        if keyword is None:
            return False
        await session.delete(keyword)
        await session.flush()
        logger.info(f"Keyword deleted: id={keyword_id}")
        return True

    async def toggle_keyword(self, session: AsyncSession, keyword_id: int) -> Optional[Keyword]:
        """Flip the is_active status of a keyword."""
        keyword = await self.get_keyword_by_id(session, keyword_id)
        if keyword is None:
            return None
        keyword.is_active = not keyword.is_active
        await session.flush()
        logger.info(f"Keyword toggled: id={keyword_id} is_active={keyword.is_active}")
        return keyword


keyword_service = KeywordService()
