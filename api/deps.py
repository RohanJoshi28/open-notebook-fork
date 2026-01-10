import os
import jwt

from fastapi import Depends, HTTPException, Request


def get_current_user_id(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        # In dev/test mode when AUTH_JWT_SECRET is not set, allow anonymous access
        if not os.environ.get("AUTH_JWT_SECRET"):
            user_id = "user:dev"
            request.state.user_id = user_id
        else:
            # Fallback: decode token directly if middleware didn't run (e.g., secret set after startup)
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header.split(" ", 1)[1]
                try:
                    payload = jwt.decode(token, os.environ["AUTH_JWT_SECRET"], algorithms=["HS256"])
                    user_id = payload.get("sub")
                    request.state.user_id = user_id
                    request.state.user_email = payload.get("email")
                except Exception:
                    pass
            if not user_id:
                raise HTTPException(status_code=401, detail="Unauthorized")
    return user_id


def get_current_user_email(request: Request) -> str:
    email = getattr(request.state, "user_email", None)
    if not email:
        if not os.environ.get("AUTH_JWT_SECRET"):
            email = "dev@force10partners.com"
            request.state.user_email = email
        else:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.lower().startswith("bearer "):
                token = auth_header.split(" ", 1)[1]
                try:
                    payload = jwt.decode(token, os.environ["AUTH_JWT_SECRET"], algorithms=["HS256"])
                    email = payload.get("email")
                    request.state.user_id = request.state.user_id or payload.get("sub")
                    request.state.user_email = email
                except Exception:
                    pass
            if not email:
                raise HTTPException(status_code=401, detail="Unauthorized")
    return email


ADMIN_EMAILS = {"rjoshi@force10partners.com"}


def require_admin(request: Request):
    # If auth is disabled (no secret), treat caller as admin for local/dev usage
    if not os.environ.get("AUTH_JWT_SECRET"):
        return "dev@force10partners.com"

    email = get_current_user_email(request)
    if email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")
    return email
