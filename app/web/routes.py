import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import FastAPI, Request, Form, Depends, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, and_

from app.models.database import async_session_maker, create_tables
from app.models.models import User, Keyword, MessageLog, Broadcast
from app.services.broadcast import broadcast_service
from app.services.keyword_service import keyword_service
from app.services.user_service import user_service
from app.services.tg_client import tg_client_service
from app.web.auth import (
    check_credentials, create_session_token, get_current_user,
    SESSION_COOKIE_NAME
)

logger = logging.getLogger(__name__)

FILES_DIR = os.getenv("FILES_DIR", "/data/files")
os.makedirs(FILES_DIR, exist_ok=True)

# --- App Setup ---
app = FastAPI(title="Bot Admin Panel")

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.on_event("startup")
async def on_startup():
    # Run DB init in background so uvicorn starts accepting requests immediately.
    # The healthcheck at /login will respond while tables are being created.
    import asyncio
    asyncio.create_task(create_tables())


# --- Auth helpers ---

def get_flash(request: Request) -> Optional[dict]:
    """Extract flash message from query params."""
    msg = request.query_params.get("msg")
    msg_type = request.query_params.get("msg_type", "info")
    if msg:
        return {"message": msg, "type": msg_type}
    return None


def auth_redirect():
    return RedirectResponse(url="/login", status_code=303)


def require_login(request: Request) -> Optional[str]:
    user = get_current_user(request)
    return user


# --- Auth routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "flash": get_flash(request)})


@app.post("/login")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if check_credentials(username, password):
        token = create_session_token(username)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            httponly=True,
            max_age=86400 * 7,  # 7 days
            samesite="lax",
        )
        return response
    return RedirectResponse(
        url="/login?msg=Неверный+логин+или+пароль&msg_type=danger",
        status_code=303
    )


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login?msg=Вы+вышли+из+системы&msg_type=success", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# --- Dashboard ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        total_users_result = await session.execute(select(func.count()).select_from(User))
        total_users = total_users_result.scalar()

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        users_today_result = await session.execute(
            select(func.count()).select_from(User).where(User.created_at >= today_start)
        )
        users_today = users_today_result.scalar()

        active_kw_result = await session.execute(
            select(func.count()).select_from(Keyword).where(Keyword.is_active == True)
        )
        active_keywords = active_kw_result.scalar()

        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        broadcasts_week_result = await session.execute(
            select(func.count()).select_from(Broadcast).where(Broadcast.created_at >= week_start)
        )
        broadcasts_week = broadcasts_week_result.scalar()

    stats = {
        "total_users": total_users,
        "users_today": users_today,
        "active_keywords": active_keywords,
        "broadcasts_week": broadcasts_week,
    }

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "stats": stats,
        "flash": get_flash(request),
        "admin_user": user,
    })


# --- Keywords ---

@app.get("/keywords", response_class=HTMLResponse)
async def keywords_list(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        keywords = await keyword_service.get_all_keywords(session)

    return templates.TemplateResponse("keywords.html", {
        "request": request,
        "keywords": keywords,
        "flash": get_flash(request),
        "admin_user": user,
    })


@app.get("/keywords/new", response_class=HTMLResponse)
async def keyword_new_form(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    return templates.TemplateResponse("keyword_form.html", {
        "request": request,
        "keyword": None,
        "flash": get_flash(request),
        "admin_user": user,
    })


@app.post("/keywords")
async def keyword_create(
    request: Request,
    keyword: str = Form(...),
    response_text: str = Form(""),
    file_caption: str = Form(""),
    follow_up_message: str = Form(""),
    follow_up_delay_minutes: str = Form(""),
    is_active: Optional[str] = Form(None),
    file_path: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    try:
        async with async_session_maker() as session:
            await keyword_service.create_keyword(session, {
                "keyword": keyword,
                "response_text": response_text,
                "file_caption": file_caption,
                "file_path": file_path,
                "follow_up_message": follow_up_message,
                "follow_up_delay_minutes": int(follow_up_delay_minutes) if follow_up_delay_minutes else None,
                "is_active": is_active == "on" or is_active == "true" or is_active == "1",
            })
            await session.commit()
        return RedirectResponse(url="/keywords?msg=Ключевое+слово+создано&msg_type=success", status_code=303)
    except Exception as e:
        logger.error(f"Error creating keyword: {e}")
        return RedirectResponse(url=f"/keywords/new?msg={str(e)}&msg_type=danger", status_code=303)


@app.get("/keywords/{keyword_id}/edit", response_class=HTMLResponse)
async def keyword_edit_form(request: Request, keyword_id: int):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        kw = await keyword_service.get_keyword_by_id(session, keyword_id)

    if kw is None:
        return RedirectResponse(url="/keywords?msg=Ключевое+слово+не+найдено&msg_type=danger", status_code=303)

    return templates.TemplateResponse("keyword_form.html", {
        "request": request,
        "keyword": kw,
        "flash": get_flash(request),
        "admin_user": user,
    })


@app.post("/keywords/{keyword_id}/edit")
async def keyword_update(
    request: Request,
    keyword_id: int,
    keyword: str = Form(...),
    response_text: str = Form(""),
    file_caption: str = Form(""),
    follow_up_message: str = Form(""),
    follow_up_delay_minutes: str = Form(""),
    is_active: Optional[str] = Form(None),
    file_path: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    try:
        async with async_session_maker() as session:
            await keyword_service.update_keyword(session, keyword_id, {
                "keyword": keyword,
                "response_text": response_text,
                "file_caption": file_caption,
                "file_path": file_path,
                "follow_up_message": follow_up_message,
                "follow_up_delay_minutes": int(follow_up_delay_minutes) if follow_up_delay_minutes else None,
                "is_active": is_active == "on" or is_active == "true" or is_active == "1",
            })
            await session.commit()
        return RedirectResponse(url="/keywords?msg=Ключевое+слово+обновлено&msg_type=success", status_code=303)
    except Exception as e:
        logger.error(f"Error updating keyword {keyword_id}: {e}")
        return RedirectResponse(url=f"/keywords/{keyword_id}/edit?msg={str(e)}&msg_type=danger", status_code=303)


@app.post("/keywords/{keyword_id}/delete")
async def keyword_delete(request: Request, keyword_id: int):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        deleted = await keyword_service.delete_keyword(session, keyword_id)
        await session.commit()

    if deleted:
        return RedirectResponse(url="/keywords?msg=Ключевое+слово+удалено&msg_type=success", status_code=303)
    return RedirectResponse(url="/keywords?msg=Ключевое+слово+не+найдено&msg_type=danger", status_code=303)


@app.post("/keywords/{keyword_id}/toggle")
async def keyword_toggle(request: Request, keyword_id: int):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        await keyword_service.toggle_keyword(session, keyword_id)
        await session.commit()

    return RedirectResponse(url="/keywords?msg=Статус+обновлён&msg_type=success", status_code=303)


# --- Users ---

@app.get("/users", response_class=HTMLResponse)
async def users_list(request: Request, search: str = ""):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        users = await user_service.get_users(session, search=search or None)
        all_tags = await user_service.get_all_tags(session)

    pyro_connected = await tg_client_service.is_connected()

    return templates.TemplateResponse("users.html", {
        "request": request,
        "users": users,
        "search": search,
        "all_tags": all_tags,
        "pyro_connected": pyro_connected,
        "flash": get_flash(request),
        "admin_user": user,
    })


@app.post("/users/send-message")
async def user_send_message(
    request: Request,
    user_id: int = Form(...),
    text: str = Form(...),
):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        u = result.scalar_one_or_none()

    if not u:
        return RedirectResponse(url="/users?msg=Пользователь+не+найден&msg_type=danger", status_code=303)

    telegram_id = u.telegram_id
    error_msg = None

    # Try Pyrogram first, fall back to bot
    pyro_client = None
    try:
        pyro_client = await tg_client_service.get_client()
    except Exception as e:
        logger.warning(f"Pyrogram unavailable: {e}")

    try:
        if pyro_client:
            await pyro_client.send_message(chat_id=telegram_id, text=text)
            await pyro_client.stop()
        elif broadcast_service._bot:
            await broadcast_service._bot.send_message(chat_id=telegram_id, text=text)
        else:
            error_msg = "Ни Pyrogram, ни бот не настроены"
    except Exception as e:
        if pyro_client:
            try:
                await pyro_client.stop()
            except Exception:
                pass
        error_msg = str(e)[:120]

    if error_msg:
        return RedirectResponse(
            url=f"/users?msg=Ошибка+отправки:+{error_msg}&msg_type=danger",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/users?msg=Сообщение+отправлено&msg_type=success",
        status_code=303,
    )


@app.post("/users/add")
async def user_add_manual(
    request: Request,
    telegram_id: int = Form(...),
    username: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    async with async_session_maker() as session:
        existing = await user_service.get_user_by_telegram_id(session, telegram_id)
        if existing:
            return RedirectResponse(
                url="/users?msg=Пользователь+с+таким+Telegram+ID+уже+существует&msg_type=warning",
                status_code=303,
            )
        await user_service.upsert_user(
            session,
            telegram_id=telegram_id,
            username=username.strip() or None,
            first_name=first_name.strip() or None,
            last_name=last_name.strip() or None,
        )
        await session.commit()

    return RedirectResponse(url="/users?msg=Пользователь+добавлен&msg_type=success", status_code=303)


@app.post("/users/{user_id}/subscribe")
async def user_toggle_subscribe(request: Request, user_id: int):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        u = result.scalar_one_or_none()
        if u:
            u.is_subscribed = not u.is_subscribed
            await session.commit()

    return RedirectResponse(url="/users?msg=Статус+подписки+обновлён&msg_type=success", status_code=303)


@app.post("/users/{user_id}/tag")
async def user_add_tag(request: Request, user_id: int, tag: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        await user_service.add_tag(session, user_id, tag)
        await session.commit()

    return RedirectResponse(url="/users?msg=Тег+добавлен&msg_type=success", status_code=303)


@app.post("/users/{user_id}/untag")
async def user_remove_tag(request: Request, user_id: int, tag: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        await user_service.remove_tag(session, user_id, tag)
        await session.commit()

    return RedirectResponse(url="/users?msg=Тег+удалён&msg_type=success", status_code=303)


@app.get("/users/export.csv")
async def users_export_csv(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        csv_content = await user_service.export_csv(session)

    return StreamingResponse(
        iter([csv_content.encode("utf-8")]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=users.csv"},
    )


# --- Broadcast ---

@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        all_tags = await user_service.get_all_tags(session)
        subscribed_count_result = await session.execute(
            select(func.count()).select_from(User).where(User.is_subscribed == True)
        )
        subscribed_count = subscribed_count_result.scalar()

        recent = await broadcast_service.get_recent_broadcasts(10)

    return templates.TemplateResponse("broadcast.html", {
        "request": request,
        "all_tags": all_tags,
        "subscribed_count": subscribed_count,
        "recent_broadcasts": recent,
        "flash": get_flash(request),
        "admin_user": user,
    })


@app.post("/broadcast/start")
async def broadcast_start(
    request: Request,
    message_text: str = Form(...),
    target_tag: str = Form(""),
):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    if not message_text.strip():
        return RedirectResponse(url="/broadcast?msg=Сообщение+не+может+быть+пустым&msg_type=danger", status_code=303)

    broadcast = await broadcast_service.create_broadcast(
        message_text=message_text.strip(),
        target_tag=target_tag.strip() or None,
    )
    started = await broadcast_service.start_broadcast(broadcast.id)

    if started:
        return RedirectResponse(
            url="/broadcast?msg=Рассылка+запущена&msg_type=success",
            status_code=303
        )
    return RedirectResponse(
        url="/broadcast?msg=Ошибка+при+запуске+рассылки&msg_type=danger",
        status_code=303
    )


@app.get("/api/broadcast/status")
async def broadcast_status_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    data = await broadcast_service.get_recent_broadcasts(10)
    return JSONResponse(content={"broadcasts": data})


# --- Logs ---

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    async with async_session_maker() as session:
        result = await session.execute(
            select(MessageLog, User)
            .outerjoin(User, MessageLog.user_id == User.id)
            .order_by(MessageLog.timestamp.desc())
            .limit(200)
        )
        rows = result.all()
        logs = [
            {
                "id": log.id,
                "telegram_id": u.telegram_id if u else None,
                "username": u.username if u else None,
                "keyword_matched": log.keyword_matched,
                "file_sent": log.file_sent,
                "timestamp": log.timestamp,
            }
            for log, u in rows
        ]

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "logs": logs,
        "flash": get_flash(request),
        "admin_user": user,
    })


# --- File Upload & Serve ---

@app.post("/upload-file")
async def upload_file(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    # Generate unique filename preserving extension
    ext = Path(file.filename).suffix.lower()
    unique_name = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(FILES_DIR, unique_name)

    os.makedirs(FILES_DIR, exist_ok=True)
    async with aiofiles.open(save_path, "wb") as out_file:
        content = await file.read()
        await out_file.write(content)

    logger.info(f"File uploaded: {unique_name} ({len(content)} bytes)")
    return JSONResponse(content={"filename": unique_name, "original_name": file.filename})


@app.get("/files/{filename}")
async def serve_file(request: Request, filename: str):
    user = get_current_user(request)
    if not user:
        return auth_redirect()

    file_path = os.path.join(FILES_DIR, filename)
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    # Security: prevent path traversal
    abs_files_dir = os.path.abspath(FILES_DIR)
    abs_file_path = os.path.abspath(file_path)
    if not abs_file_path.startswith(abs_files_dir):
        raise HTTPException(status_code=403, detail="Forbidden")

    async def file_iterator():
        async with aiofiles.open(abs_file_path, "rb") as f:
            while chunk := await f.read(65536):
                yield chunk

    return StreamingResponse(
        file_iterator(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# --- Settings (Telegram account) ---

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    creds = await tg_client_service.get_credentials()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "creds": creds,
        "connected": bool(creds.get("session")),
        "awaiting_code": bool(creds.get("api_id") and not creds.get("session")),
        "flash": get_flash(request),
        "admin_user": admin,
    })


@app.post("/settings/send-code")
async def settings_send_code(
    request: Request,
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    try:
        await tg_client_service.send_code(api_id, api_hash, phone.strip())
        return RedirectResponse(
            url="/settings?msg=SMS+код+отправлен+на+номер+" + phone.strip() + "&msg_type=success",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"send_code error: {e}")
        return RedirectResponse(
            url=f"/settings?msg=Ошибка:+{str(e)[:80]}&msg_type=danger",
            status_code=303,
        )


@app.post("/settings/verify-code")
async def settings_verify_code(
    request: Request,
    code: str = Form(...),
    password: str = Form(""),
):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    try:
        info = await tg_client_service.sign_in(code, password)
        return RedirectResponse(
            url=f"/settings?msg=Аккаунт+подключён:+{info['name']}&msg_type=success",
            status_code=303,
        )
    except ValueError as e:
        return RedirectResponse(
            url=f"/settings?msg={str(e)}&msg_type=warning",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"sign_in error: {e}")
        return RedirectResponse(
            url=f"/settings?msg=Ошибка+кода:+{str(e)[:80]}&msg_type=danger",
            status_code=303,
        )


@app.post("/settings/disconnect")
async def settings_disconnect(request: Request):
    admin = get_current_user(request)
    if not admin:
        return auth_redirect()

    await tg_client_service.disconnect()
    return RedirectResponse(
        url="/settings?msg=Аккаунт+отключён&msg_type=info",
        status_code=303,
    )
