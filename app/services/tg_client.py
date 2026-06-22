import logging
from typing import Optional

from pyrogram import Client
from pyrogram.errors import (
    SessionPasswordNeeded, PhoneCodeExpired,
    PhoneCodeInvalid, PhoneNumberInvalid,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session_maker
from app.models.models import Setting

logger = logging.getLogger(__name__)

# Keys used in the settings table
KEY_API_ID = "tg_api_id"
KEY_API_HASH = "tg_api_hash"
KEY_PHONE = "tg_phone"
KEY_SESSION = "tg_session_string"
KEY_PHONE_CODE_HASH = "tg_phone_code_hash"
KEY_ACCOUNT_NAME = "tg_account_name"


async def _get(session: AsyncSession, key: str) -> Optional[str]:
    r = await session.execute(select(Setting).where(Setting.key == key))
    s = r.scalar_one_or_none()
    return s.value if s else None


async def _set(session: AsyncSession, key: str, value: Optional[str]):
    r = await session.execute(select(Setting).where(Setting.key == key))
    s = r.scalar_one_or_none()
    if s is None:
        session.add(Setting(key=key, value=value))
    else:
        s.value = value
    await session.flush()


class TgClientService:
    """Manages a Pyrogram user-account client for broadcasts."""

    async def get_credentials(self) -> dict:
        async with async_session_maker() as session:
            return {
                "api_id": await _get(session, KEY_API_ID),
                "api_hash": await _get(session, KEY_API_HASH),
                "phone": await _get(session, KEY_PHONE),
                "session": await _get(session, KEY_SESSION),
                "account_name": await _get(session, KEY_ACCOUNT_NAME),
            }

    async def is_connected(self) -> bool:
        creds = await self.get_credentials()
        return bool(creds.get("session"))

    async def send_code(self, api_id: int, api_hash: str, phone: str) -> str:
        """Request SMS code. Returns phone_code_hash."""
        client = Client(
            name="mem",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
        )
        await client.connect()
        try:
            sent = await client.send_code(phone)
        finally:
            await client.disconnect()

        # Persist credentials and temporary hash
        async with async_session_maker() as session:
            await _set(session, KEY_API_ID, str(api_id))
            await _set(session, KEY_API_HASH, api_hash)
            await _set(session, KEY_PHONE, phone)
            await _set(session, KEY_PHONE_CODE_HASH, sent.phone_code_hash)
            await session.commit()

        return sent.phone_code_hash

    async def sign_in(self, code: str, password: str = "") -> dict:
        """Verify code (and optional 2FA password). Returns account info."""
        async with async_session_maker() as session:
            api_id = int(await _get(session, KEY_API_ID) or 0)
            api_hash = await _get(session, KEY_API_HASH) or ""
            phone = await _get(session, KEY_PHONE) or ""
            phone_code_hash = await _get(session, KEY_PHONE_CODE_HASH) or ""

        client = Client(
            name="mem",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
        )
        await client.connect()
        try:
            try:
                user = await client.sign_in(
                    phone_number=phone,
                    phone_code_hash=phone_code_hash,
                    phone_code=code.strip(),
                )
            except SessionPasswordNeeded:
                if not password:
                    raise ValueError("2FA пароль обязателен")
                user = await client.check_password(password)

            session_string = await client.export_session_string()
            name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or str(user.id)
        finally:
            await client.disconnect()

        async with async_session_maker() as session:
            await _set(session, KEY_SESSION, session_string)
            await _set(session, KEY_ACCOUNT_NAME, name)
            await _set(session, KEY_PHONE_CODE_HASH, None)
            await session.commit()

        return {"name": name, "id": user.id}

    async def disconnect(self):
        """Remove stored session."""
        async with async_session_maker() as session:
            await _set(session, KEY_SESSION, None)
            await _set(session, KEY_ACCOUNT_NAME, None)
            await session.commit()

    async def get_client(self) -> Optional[Client]:
        """Return a started Pyrogram client if session exists."""
        creds = await self.get_credentials()
        if not creds.get("session"):
            return None
        client = Client(
            name="mem",
            api_id=int(creds["api_id"]),
            api_hash=creds["api_hash"],
            session_string=creds["session"],
            in_memory=True,
        )
        await client.start()
        return client


tg_client_service = TgClientService()
