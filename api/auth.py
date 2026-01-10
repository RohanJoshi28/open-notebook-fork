import os
import time
from typing import Optional, Tuple

import jwt
from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from open_notebook.domain.user import User


class JWTAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce JWT Bearer authentication.
    Expects Authorization: Bearer <token> where token is signed with AUTH_JWT_SECRET.
    """

    def __init__(self, app, excluded_paths: Optional[list] = None):
        super().__init__(app)
        self.excluded_paths = excluded_paths or [
            "/",
            "/health",
            "/docs",
            "/openapi.json",
            "/redoc",
            "/api/config",
        ]
        # Prefixes to bypass auth (all auth endpoints)
        self.excluded_prefixes = ["/api/auth/"]
        self.jwt_secret = os.environ.get("AUTH_JWT_SECRET")
        if not self.jwt_secret:
            logger.warning("AUTH_JWT_SECRET not set; authentication will be disabled")

    async def dispatch(self, request: Request, call_next):
        # Disable auth if secret not configured (dev fallback)
        if not self.jwt_secret:
            return await call_next(request)

        # Skip excluded paths/prefixes and OPTIONS
        if (
            request.method == "OPTIONS"
            or request.url.path in self.excluded_paths
            or any(request.url.path.startswith(prefix) for prefix in self.excluded_prefixes)
        ):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            user_id = payload.get("sub")
            if not user_id:
                raise ValueError("Missing sub in token")

            # Attach user info to request state for downstream use
            request.state.user_id = user_id
            request.state.user_email = payload.get("email")
            request.state.user_name = payload.get("name")
        except Exception as exc:  # broad catch to return 401
            logger.warning(f"JWT validation failed: {exc}")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)


def _google_client_id() -> str:
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    if not client_id:
        raise RuntimeError("GOOGLE_CLIENT_ID is not configured")
    return client_id


def verify_google_id_token(raw_id_token: str) -> dict:
    """
    Verify Google ID token and return decoded claims.
    """
    try:
        claims = id_token.verify_oauth2_token(
            raw_id_token, google_requests.Request(), _google_client_id()
        )
        if claims.get("iss") not in ("https://accounts.google.com", "accounts.google.com"):
            raise ValueError("Invalid issuer")
        return claims
    except Exception as exc:
        logger.error(f"Failed to verify Google ID token: {exc}")
        raise HTTPException(status_code=401, detail="Invalid Google ID token")


def issue_app_jwt(user: User, expires_in: int = 60 * 60 * 24 * 7) -> str:
    """
    Issue application JWT for the given user.
    """
    secret = os.environ.get("AUTH_JWT_SECRET")
    if not secret:
        raise RuntimeError("AUTH_JWT_SECRET is not configured")
    now = int(time.time())
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


async def get_or_create_user_from_google_claims(claims: dict) -> User:
    """
    Find or create a user record from Google claims.
    """
    from open_notebook.database.repository import repo_query

    email = claims.get("email")
    sub = claims.get("sub")
    name = claims.get("name")
    picture = claims.get("picture")

    if not email or not sub:
        raise HTTPException(status_code=400, detail="Google token missing email or sub")

    # Look up by sub
    existing = await repo_query("SELECT * FROM user WHERE sub = $sub LIMIT 1", {"sub": sub})
    if existing:
        return User(**existing[0])

    # Else look up by email (handles previously created users)
    existing_email = await repo_query("SELECT * FROM user WHERE email = $email LIMIT 1", {"email": email})
    if existing_email:
        user = User(**existing_email[0])
        user.sub = sub
        user.name = name
        user.picture = picture
        await user.save()
        return user

    # Create new user
    user = User(email=email, sub=sub, name=name, picture=picture)
    await user.save()
    return user


def assert_allowed_domain(email: str):
    allowed_domain = os.environ.get("GOOGLE_ALLOWED_DOMAIN")
    if not allowed_domain:
        return
    if not email.lower().endswith(f"@{allowed_domain.lower()}"):
        raise HTTPException(status_code=403, detail="Email domain not allowed")
