"""
Authentication router for Open Notebook API.
Provides endpoints for Google login and status.
"""

import os
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from loguru import logger

from api.auth import (
    assert_allowed_domain,
    get_or_create_user_from_google_claims,
    issue_app_jwt,
    verify_google_id_token,
)
from open_notebook.domain.google_credential import GoogleCredential
from open_notebook.domain.user import User
from open_notebook.utils.google_drive import DRIVE_SCOPES

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def get_auth_status():
    """
    Auth is enabled when AUTH_JWT_SECRET is set.
    """
    auth_enabled = bool(os.environ.get("AUTH_JWT_SECRET"))
    return {
        "auth_enabled": auth_enabled,
        "message": "Authentication is required" if auth_enabled else "Authentication is disabled",
    }


@router.post("/login/google")
async def login_with_google(id_token: str = Body(..., embed=True)):
    """
    Accept a Google ID token from the client, verify domain, create user, and return app JWT.
    Note: This flow does NOT issue Drive refresh tokens; prefer the code flow for Drive imports.
    """
    claims = verify_google_id_token(id_token)
    email = claims.get("email")
    assert_allowed_domain(email)
    user = await get_or_create_user_from_google_claims(claims)
    token = issue_app_jwt(user)
    logger.info(f"User {email} logged in via Google")
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "picture": user.picture,
        },
    }


@router.post("/login/google-code")
async def login_with_google_code(
    code: str = Body(..., embed=True),
    redirect_uri: str = Body(..., embed=True),
):
    """
    Fallback OAuth Code flow: exchange `code` for tokens server-side, verify id_token,
    enforce allowed domain, upsert user, and issue app JWT.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="Google client credentials not configured")

    token_url = "https://oauth2.googleapis.com/token"
    # Log minimal debug info about the incoming code (length only, no value)
    logger.info(
        "google-code: received auth code len=%s redirect_uri=%s client_id_suffix=%s",
        len(code) if code else 0,
        redirect_uri,
        client_id[-6:] if client_id else None,
    )

    payload = {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    token_data = None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(token_url, data=payload)
        if resp.status_code != 200:
            body_text = resp.text
            logger.error(
                f"Google token exchange failed: status={resp.status_code} "
                f"redirect_uri={redirect_uri} body={body_text}"
            )
            try:
                err_json = resp.json()
                err_detail = f"{err_json.get('error')}: {err_json.get('error_description')}"
            except Exception:
                err_detail = body_text
            raise HTTPException(status_code=401, detail=f"Google token exchange failed: {err_detail}")
        token_data = resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Google token exchange failed: {exc}")
        raise HTTPException(status_code=401, detail="Google token exchange failed")

    raw_id_token = token_data.get("id_token") if token_data else None
    if not raw_id_token:
        raise HTTPException(status_code=401, detail="No id_token returned from Google")

    claims = verify_google_id_token(raw_id_token)
    email = claims.get("email")
    assert_allowed_domain(email)
    user = await get_or_create_user_from_google_claims(claims)

    # Validate required Drive scopes
    scope_str = token_data.get("scope") if token_data else ""
    scopes_returned = scope_str.split() if isinstance(scope_str, str) else []
    logger.debug(
        "google-code: token exchange success scopes=%s has_refresh=%s has_access=%s expires_in=%s token_type=%s",
        scopes_returned,
        bool(token_data.get("refresh_token")),
        bool(token_data.get("access_token")),
        token_data.get("expires_in"),
        token_data.get("token_type"),
    )
    missing = [s for s in DRIVE_SCOPES if s not in scopes_returned]
    if missing:
        logger.error(f"Login missing Drive scopes; received={scopes_returned}")
        raise HTTPException(
            status_code=403,
            detail="Drive access not granted. Please re-login and allow Drive access.",
        )

    refresh_token = token_data.get("refresh_token")
    access_token = token_data.get("access_token")
    expires_in = token_data.get("expires_in", 3600)
    token_type = token_data.get("token_type", "Bearer")

    # Persist credentials (idempotent upsert keyed by user, without embedding user id into record id)
    try:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in) - 60)
        expires_at_str = expires_at.isoformat()
        from open_notebook.database.repository import repo_query, ensure_record_id

        if not refresh_token:
            raise HTTPException(
                status_code=403,
                detail="Google did not return a refresh token. Re-run login with consent to enable Drive imports.",
            )

        cred_data = {
            "user": ensure_record_id(user.id),
            "refresh_token": refresh_token,
            "access_token": access_token,
            "expires_at": expires_at_str,
            "scope": scope_str,
            "token_type": token_type,
            "updated": datetime.now(timezone.utc),
        }
        logger.debug(
            "google-code: upsert-by-user user=%s expires_at=%s scope_len=%s",
            user.id,
            expires_at_str,
            len(scope_str.split()) if isinstance(scope_str, str) else 0,
        )

        # Try update by unique user; if nothing updated, create new
        update_result = await repo_query(
            """
            UPDATE google_credential SET refresh_token = $refresh_token,
                                         access_token = $access_token,
                                         expires_at = $expires_at,
                                         scope = $scope,
                                         token_type = $token_type,
                                         updated = time::now()
            WHERE user = $user
            RETURN id;
            """,
            cred_data,
        )
        if not update_result:
            create_data = cred_data.copy()
            create_data["created"] = datetime.now(timezone.utc)
            create_result = await repo_query(
                "CREATE google_credential CONTENT $data RETURN id;",
                {"data": create_data},
            )
            logger.info("google-code: created credential id=%s", create_result[0].get("id") if create_result else None)
        else:
            logger.info("google-code: updated credential id=%s", update_result[0].get("id"))
    except Exception as exc:
        logger.exception(f"Failed to persist Drive credentials: {exc}")
        try:
            logger.error(
                "credential debug: user=%s access_present=%s refresh_present=%s expires_at=%s scope=%s",
                user.id,
                bool(access_token),
                bool(refresh_token),
                expires_at_str if 'expires_at_str' in locals() else None,
                scope_str,
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Failed to store Google credentials")

    token = issue_app_jwt(user)
    logger.info(f"User {email} logged in via Google (code flow)")
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "picture": user.picture,
        },
        "drive_scope": True,
    }


@router.post("/login/dev")
async def dev_login(request: Request, email: str = Body("rjoshi@force10partners.com", embed=True)):
    """
    Dev-only local login without Google. Guarded by env ALLOW_LOCAL_DEV_LOGIN=1 and localhost origin.
    Issues a JWT for the given email (default rjoshi@force10partners.com) and creates the user if needed.
    """
    if os.environ.get("ALLOW_LOCAL_DEV_LOGIN") != "1":
        raise HTTPException(status_code=403, detail="Dev login disabled")

    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Dev login only allowed from localhost")

    if not email:
        raise HTTPException(status_code=400, detail="Email is required")

    # Enforce allowed domain if configured
    assert_allowed_domain(email)

    from open_notebook.database.repository import repo_query

    existing = await repo_query("SELECT * FROM user WHERE email = $email LIMIT 1", {"email": email})
    if existing:
        user = User(**existing[0])
    else:
        user = User(email=email, sub=f"dev-{email}", name=email.split("@")[0], picture=None)
        await user.save()

    token = issue_app_jwt(user)
    logger.info(f"[DEV LOGIN] Issued token for {email} from {client_host}")
    return {
        "token": token,
        "user": {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "picture": user.picture,
        },
    }
