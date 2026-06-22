import os
import logging
from typing import Optional
from fastapi import Request, HTTPException, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeSerializer, BadSignature

logger = logging.getLogger(__name__)

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
SESSION_COOKIE_NAME = "admin_session"
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme123")

_serializer = URLSafeSerializer(SECRET_KEY, salt="admin-session")


def create_session_token(username: str) -> str:
    """Create a signed session token for the given username."""
    return _serializer.dumps({"user": username})


def verify_session_token(token: str) -> Optional[str]:
    """Verify and decode a session token. Returns username or None."""
    try:
        data = _serializer.loads(token)
        return data.get("user")
    except BadSignature:
        return None
    except Exception as e:
        logger.warning(f"Session token verification error: {e}")
        return None


def check_credentials(username: str, password: str) -> bool:
    """Check admin credentials against environment variables."""
    return username == ADMIN_USERNAME and password == ADMIN_PASSWORD


def get_current_user(request: Request) -> Optional[str]:
    """Extract and verify the current user from session cookie."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token)


def require_auth(request: Request) -> str:
    """FastAPI dependency that requires authentication. Raises 401 or redirects."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"},
        )
    return user
