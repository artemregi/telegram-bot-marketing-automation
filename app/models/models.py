from datetime import datetime
from typing import Optional, List
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer,
    String, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB
from app.models.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    tags: Mapped[List[str]] = mapped_column(JSONB, default=list, server_default="[]", nullable=False)
    is_subscribed: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)

    logs: Mapped[List["MessageLog"]] = relationship("MessageLog", back_populates="user", lazy="select")

    def __repr__(self) -> str:
        return f"<User id={self.id} telegram_id={self.telegram_id} username={self.username}>"


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(500), unique=True, nullable=False, index=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    file_caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    follow_up_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    follow_up_delay_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<Keyword id={self.id} keyword={self.keyword}>"


class MessageLog(Base):
    __tablename__ = "messages_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    keyword_matched: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_sent: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[Optional["User"]] = relationship("User", back_populates="logs")

    def __repr__(self) -> str:
        return f"<MessageLog id={self.id} user_id={self.user_id} keyword={self.keyword_matched}>"


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_tag: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", server_default="draft", nullable=False)
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Broadcast id={self.id} status={self.status} sent={self.sent_count}>"
