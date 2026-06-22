"""
Pyrogram history scanner.

Scans incoming message history of the connected user account,
matches against active keywords, and sends responses for matches
that haven't been replied to yet.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.models.database import async_session_maker
from app.models.models import MessageLog, User
from app.services.keyword_service import keyword_service
from app.services.tg_client import tg_client_service
from app.services.user_service import user_service

logger = logging.getLogger(__name__)


class HistoryScanner:
    def __init__(self):
        self._running = False
        self._progress: dict = {}

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def progress(self) -> dict:
        return dict(self._progress)

    async def scan(self, days_back: int = 7, delay_between: float = 2.0):
        """
        Scan message history for the connected Pyrogram account.

        For each private dialog:
          - Read the last `days_back` days of incoming messages
          - Match against active keywords
          - If matched and not already replied → send response + log
        """
        if self._running:
            logger.warning("Scanner already running.")
            return

        client = await tg_client_service.get_client()
        if client is None:
            raise RuntimeError("Pyrogram аккаунт не подключён. Зайди в Settings.")

        self._running = True
        self._progress = {
            "status": "running",
            "dialogs_total": 0,
            "dialogs_done": 0,
            "matches_found": 0,
            "responses_sent": 0,
            "current_dialog": "",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

            # Load all active keywords once
            async with async_session_maker() as session:
                keywords = await keyword_service.get_active_keywords(session)

            if not keywords:
                self._progress["status"] = "done_no_keywords"
                return

            # Build a set of already-logged (user_id, keyword) pairs to avoid
            # re-sending responses for messages we've already handled
            async with async_session_maker() as session:
                result = await session.execute(
                    select(MessageLog.user_id, MessageLog.keyword_matched)
                )
                already_handled = set(result.all())

            # Collect all private (non-bot) dialogs
            dialogs = []
            async for dialog in client.get_dialogs():
                chat = dialog.chat
                # Only personal chats (not groups/channels/bots)
                if chat.type.name in ("PRIVATE",) and not getattr(chat, "is_bot", False):
                    dialogs.append(chat)

            self._progress["dialogs_total"] = len(dialogs)
            logger.info(f"Scanner: {len(dialogs)} private dialogs to scan.")

            for chat in dialogs:
                self._progress["dialogs_done"] += 1
                display_name = (
                    f"{chat.first_name or ''} {chat.last_name or ''}".strip()
                    or chat.username
                    or str(chat.id)
                )
                self._progress["current_dialog"] = display_name

                # Upsert user so they exist in our DB
                async with async_session_maker() as session:
                    db_user = await user_service.upsert_user(
                        session,
                        telegram_id=chat.id,
                        username=chat.username,
                        first_name=chat.first_name,
                        last_name=chat.last_name,
                    )
                    await session.commit()
                    await session.refresh(db_user)
                    db_user_id = db_user.id

                # Scan incoming messages in this dialog
                async for msg in client.get_chat_history(chat.id, limit=200):
                    # Stop if message is older than cutoff
                    msg_date = msg.date
                    if msg_date.tzinfo is None:
                        msg_date = msg_date.replace(tzinfo=timezone.utc)
                    if msg_date < cutoff:
                        break

                    # Only process messages FROM the other person (not our own)
                    if msg.outgoing:
                        continue

                    if not msg.text:
                        continue

                    normalized = msg.text.strip().lower()

                    # Match keyword
                    matched_kw = None
                    for kw in keywords:
                        if kw.keyword == normalized or normalized.startswith(kw.keyword):
                            matched_kw = kw
                            break

                    if matched_kw is None:
                        continue

                    self._progress["matches_found"] += 1

                    # Skip if already handled
                    if (db_user_id, matched_kw.keyword) in already_handled:
                        logger.debug(f"Already handled {chat.id}/{matched_kw.keyword}, skipping.")
                        continue

                    # Send response
                    try:
                        if matched_kw.response_text:
                            await client.send_message(chat.id, matched_kw.response_text)

                        if matched_kw.file_path:
                            import os
                            FILES_DIR = os.getenv("FILES_DIR", "/data/files")
                            full_path = os.path.join(FILES_DIR, matched_kw.file_path)
                            if os.path.exists(full_path):
                                await client.send_document(
                                    chat.id, full_path,
                                    caption=matched_kw.file_caption or None,
                                )

                        # Log it
                        async with async_session_maker() as session:
                            session.add(MessageLog(
                                user_id=db_user_id,
                                keyword_matched=matched_kw.keyword,
                                file_sent=matched_kw.file_path,
                            ))
                            await session.commit()

                        already_handled.add((db_user_id, matched_kw.keyword))
                        self._progress["responses_sent"] += 1
                        logger.info(f"Responded to {chat.id} for keyword '{matched_kw.keyword}'")

                        await asyncio.sleep(delay_between)

                    except Exception as e:
                        logger.error(f"Error responding to {chat.id}: {e}")

            self._progress["status"] = "done"
            self._progress["finished_at"] = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"Scan complete: {self._progress['responses_sent']} responses sent "
                f"out of {self._progress['matches_found']} matches."
            )

        except Exception as e:
            self._progress["status"] = "error"
            self._progress["error"] = str(e)
            logger.error(f"Scanner error: {e}")
        finally:
            try:
                await client.stop()
            except Exception:
                pass
            self._running = False


history_scanner = HistoryScanner()
