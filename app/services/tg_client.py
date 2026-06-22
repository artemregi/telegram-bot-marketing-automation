import logging
from typing import Optional

from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import async_session_maker
from app.models.models import Setting

logger = logging.getLogger(__name__)

KEY_API_ID       = "tg_api_id"
KEY_API_HASH     = "tg_api_hash"
KEY_PHONE        = "tg_phone"
KEY_SESSION      = "tg_session_string"
KEY_CODE_HASH    = "tg_phone_code_hash"
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
    """Pyrogram user-account client manager.

    The auth flow MUST use the same TCP connection for send_code → sign_in,
    so we keep _auth_client alive as an instance variable between the two HTTP
    requests.  Since tg_client_service is a module-level singleton this works
    fine inside a single uvicorn worker.
    """

    def __init__(self):
        self._auth_client: Optional[Client] = None

    async def get_credentials(self) -> dict:
        async with async_session_maker() as session:
            return {
                "api_id":        await _get(session, KEY_API_ID),
                "api_hash":      await _get(session, KEY_API_HASH),
                "phone":         await _get(session, KEY_PHONE),
                "session":       await _get(session, KEY_SESSION),
                "account_name":  await _get(session, KEY_ACCOUNT_NAME),
            }

    async def is_connected(self) -> bool:
        creds = await self.get_credentials()
        return bool(creds.get("session"))

    # ------------------------------------------------------------------
    # Step 1 — request SMS / Telegram code
    # ------------------------------------------------------------------
    async def send_code(self, api_id: int, api_hash: str, phone: str) -> None:
        # Tear down any leftover auth session first
        if self._auth_client:
            try:
                await self._auth_client.disconnect()
            except Exception:
                pass
            self._auth_client = None

        client = Client(
            name="auth_session",
            api_id=api_id,
            api_hash=api_hash,
            in_memory=True,
        )
        await client.connect()

        sent = await client.send_code(phone)

        # Keep client ALIVE — sign_in must reuse the same connection/DC
        self._auth_client = client

        # Persist for recovery across restarts (best-effort)
        async with async_session_maker() as session:
            await _set(session, KEY_API_ID,    str(api_id))
            await _set(session, KEY_API_HASH,  api_hash)
            await _set(session, KEY_PHONE,     phone)
            await _set(session, KEY_CODE_HASH, sent.phone_code_hash)
            await session.commit()

        logger.info(f"Code sent to {phone}")

    # ------------------------------------------------------------------
    # Step 2 — verify code (+ optional 2FA password)
    # ------------------------------------------------------------------
    async def sign_in(self, code: str, password: str = "") -> dict:
        # Re-use the kept-alive client; fall back to fresh connection if
        # the worker restarted between the two steps.
        client = self._auth_client

        if client is None:
            async with async_session_maker() as session:
                api_id       = int(await _get(session, KEY_API_ID)   or 0)
                api_hash     = await _get(session, KEY_API_HASH)      or ""
                phone_stored = await _get(session, KEY_PHONE)         or ""
                code_hash    = await _get(session, KEY_CODE_HASH)     or ""

            client = Client(
                name="auth_session",
                api_id=api_id,
                api_hash=api_hash,
                in_memory=True,
            )
            await client.connect()
        else:
            async with async_session_maker() as session:
                phone_stored = await _get(session, KEY_PHONE)     or ""
                code_hash    = await _get(session, KEY_CODE_HASH) or ""

        try:
            try:
                user = await client.sign_in(
                    phone_number=phone_stored,
                    phone_code_hash=code_hash,
                    phone_code=code.strip(),
                )
            except SessionPasswordNeeded:
                if not password:
                    raise ValueError("Требуется пароль 2FA")
                user = await client.check_password(password)

            session_string = await client.export_session_string()
            name = (
                f"{user.first_name or ''} {user.last_name or ''}".strip()
                or user.username
                or str(user.id)
            )
        finally:
            await client.disconnect()
            self._auth_client = None

        async with async_session_maker() as session:
            await _set(session, KEY_SESSION,      session_string)
            await _set(session, KEY_ACCOUNT_NAME, name)
            await _set(session, KEY_CODE_HASH,    None)
            await session.commit()

        logger.info(f"Signed in as {name}")
        return {"name": name, "id": user.id}

    # ------------------------------------------------------------------
    # Disconnect / reset
    # ------------------------------------------------------------------
    async def disconnect(self):
        if self._auth_client:
            try:
                await self._auth_client.disconnect()
            except Exception:
                pass
            self._auth_client = None

        async with async_session_maker() as session:
            await _set(session, KEY_SESSION,      None)
            await _set(session, KEY_ACCOUNT_NAME, None)
            await session.commit()

    # ------------------------------------------------------------------
    # Get a ready-to-use client for sending (uses saved session string)
    # ------------------------------------------------------------------
    async def get_client(self) -> Optional[Client]:
        creds = await self.get_credentials()
        if not creds.get("session"):
            return None
        client = Client(
            name="sender_session",
            api_id=int(creds["api_id"]),
            api_hash=creds["api_hash"],
            session_string=creds["session"],
            in_memory=True,
        )
        await client.start()
        return client


tg_client_service = TgClientService()
