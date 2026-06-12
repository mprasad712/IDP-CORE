from __future__ import annotations

from typing import Annotated
from datetime import datetime, timezone, timedelta
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm
from agentcore.services.database.models.user.crud import get_user_by_username
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, or_

import httpx
from pydantic import BaseModel, EmailStr
from jose import jwt, JWTError
import secrets
from agentcore.api.utils import DbSession
from agentcore.api.schemas import Token
from sqlmodel import select
from agentcore.services.auth.utils import (
    authenticate_user,
    create_refresh_token,
    create_user_tokens,
    get_password_hash,
    make_set_password_token,
)
from agentcore.services.database.models.user.crud import get_user_by_id
from agentcore.services.deps import get_settings_service
from agentcore.services.database.models.user.model import User
from agentcore.services.auth.permissions import get_permissions_for_role, normalize_role
from agentcore.services.cache.user_cache import UserCacheService
from agentcore.services.notifications import send_verification_email, send_admin_user_created_email


class AzureSSORequest(BaseModel):
    idToken: str

class AzureSSOResponse(Token):
    role: str
    permissions: list[str]

router = APIRouter(tags=["Login"])


def _apply_auth_cookies(response: Response, tokens: dict, auth_settings, user: User) -> None:
    persistent_cookie = bool(tokens.get("persistent_cookie", True))
    access_expires = tokens.get("access_expires_in") if persistent_cookie else None
    refresh_expires = tokens.get("refresh_expires_in") if persistent_cookie else None

    response.set_cookie(
        "refresh_token_ag",
        tokens["refresh_token"],
        httponly=auth_settings.REFRESH_HTTPONLY,
        samesite=auth_settings.REFRESH_SAME_SITE,
        secure=auth_settings.REFRESH_SECURE,
        expires=refresh_expires,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    response.set_cookie(
        "access_token_ag",
        tokens["access_token"],
        httponly=auth_settings.ACCESS_HTTPONLY,
        samesite=auth_settings.ACCESS_SAME_SITE,
        secure=auth_settings.ACCESS_SECURE,
        expires=access_expires,
        domain=auth_settings.COOKIE_DOMAIN,
    )
    response.set_cookie(
        "apikey_tkn_ag",
        str(user.store_api_key),
        httponly=auth_settings.ACCESS_HTTPONLY,
        samesite=auth_settings.ACCESS_SAME_SITE,
        secure=auth_settings.ACCESS_SECURE,
        expires=None,
        domain=auth_settings.COOKIE_DOMAIN,
    )


def _normalize_login_identity(value: str | None) -> str:
    identity = (value or "").strip()
    return identity.lower() if "@" in identity else identity


def _role_priority(role: str | None) -> int:
    normalized = normalize_role(role or "doc_submitter")
    priorities = {
        "root": 500,
        "super_admin": 400,
        "idp_auditor": 350,
        "department_admin": 300,
        "idp_configurator": 200,
        "doc_reviewer": 200,
        "doc_submitter": 100,
        "doc_approver": 150,
    }
    return priorities.get(normalized, 0)


async def _resolve_sso_identity_user(
    db: DbSession,
    *,
    normalized_email: str,
    entra_object_id: str | None,
    display_name: str | None,
) -> User | None:
    predicates = [
        func.lower(User.username) == normalized_email,
        func.lower(User.email) == normalized_email,
    ]
    if entra_object_id:
        predicates.append(User.entra_object_id == entra_object_id)

    candidates = (
        await db.exec(
            select(User).where(
                User.deleted_at.is_(None),
                or_(*predicates),
            )
        )
    ).all()
    if not candidates:
        return None

    candidates.sort(
        key=lambda user: (
            _role_priority(getattr(user, "role", None)),
            1 if getattr(user, "is_superuser", False) else 0,
            1 if getattr(user, "created_by", None) else 0,
            getattr(user, "updated_at", None) or getattr(user, "create_at", None),
        ),
        reverse=True,
    )

    canonical = candidates[0]
    now = datetime.now(timezone.utc)
    changed = False

    if not canonical.email:
        canonical.email = normalized_email
        changed = True
    if display_name and not canonical.display_name:
        canonical.display_name = display_name
        changed = True
    if entra_object_id and canonical.entra_object_id != entra_object_id:
        canonical.entra_object_id = entra_object_id
        changed = True

    for duplicate in candidates[1:]:
        if normalize_role(getattr(duplicate, "role", "doc_submitter")) != "doc_submitter":
            continue
        duplicate.is_active = False
        duplicate.deleted_at = duplicate.deleted_at or now
        duplicate.updated_at = now
        if entra_object_id and duplicate.entra_object_id == entra_object_id:
            duplicate.entra_object_id = None
        db.add(duplicate)
        changed = True

    if changed:
        canonical.updated_at = now
        db.add(canonical)
        await db.commit()
        await db.refresh(canonical)

    return canonical


@router.post("/login", response_model=AzureSSOResponse)
async def login_to_get_access_token(
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
):
    auth_settings = get_settings_service().auth_settings
    try:
        user = await authenticate_user(form_data.username, form_data.password, db)
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc

    if user:
        # Promote to root if the authenticated user's email matches PLATFORM_ROOT_EMAIL
        root_email = (
            str(auth_settings.PLATFORM_ROOT_EMAIL).strip().lower()
            if auth_settings.PLATFORM_ROOT_EMAIL
            else ""
        )
        user_email = (user.email or "").strip().lower()
        user_username = (user.username or "").strip().lower()
        if root_email and (user_email == root_email or user_username == root_email):
            if normalize_role(getattr(user, "role", "doc_submitter")) != "root":
                user.role = "root"
                user.is_superuser = True
                db.add(user)
                await db.commit()
                await db.refresh(user)
            current_role = "root"
        else:
            current_role = normalize_role(getattr(user, "role", "idp_configurator"))

        tokens = await create_user_tokens(user_id=user.id, db=db, update_last_login=True)
        _apply_auth_cookies(response, tokens, auth_settings, user)
        permissions = await get_permissions_for_role(current_role)
        from agentcore.observability.metrics_registry import record_login_attempt
        record_login_attempt("success")
        return {
            **tokens,
            "role": current_role,
            "permissions": permissions
        }
    from agentcore.observability.metrics_registry import record_login_attempt
    record_login_attempt("failure")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/azure/sso", response_model=AzureSSOResponse)
async def azure_sso_login(
    body: AzureSSORequest,
    response: Response,
    db: DbSession,
):
    auth_settings = get_settings_service().auth_settings

    # -----------------------------
    # Verify Azure token
    # -----------------------------
    jwks_url = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
    async with httpx.AsyncClient() as client:
        jwks = (await client.get(jwks_url)).json()

    try:
        payload = jwt.decode(
            body.idToken,
            jwks,
            algorithms=["RS256"],
            audience=auth_settings.AZURE_SSO_CLIENT_ID,
            issuer=f"https://login.microsoftonline.com/{auth_settings.AZURE_TENANT_ID}/v2.0",
        )
    except Exception as e:
        from agentcore.observability.metrics_registry import record_login_attempt
        record_login_attempt("failure")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Azure token",
        ) from e

    email = payload.get("preferred_username") or payload.get("email")
    entra_object_id = payload.get("oid")
    normalized_email = _normalize_login_identity(email) if email else ""
    root_email = str(auth_settings.PLATFORM_ROOT_EMAIL).strip().lower() if auth_settings.PLATFORM_ROOT_EMAIL else ""

    if not email:
        raise HTTPException(
            status_code=400,
            detail="Email not found in Azure token",
        )

    user = await _resolve_sso_identity_user(
        db,
        normalized_email=normalized_email,
        entra_object_id=entra_object_id,
        display_name=payload.get("name"),
    )

    # -----------------------------
    # Find or Create User
    # -----------------------------
    resolved_role = "doc_submitter"

    if root_email and normalized_email == root_email:
        resolved_role = "root"
        if user:
            if normalize_role(getattr(user, "role", "doc_submitter")) != "root":
                user.role = "root"
                user.is_superuser = True
                db.add(user)
                await db.commit()
                await db.refresh(user)
    elif user:
        resolved_role = normalize_role(getattr(user, "role", "doc_submitter"))
    else:
        resolved_role = "doc_submitter"

    is_new_user = False
    if not user:
        random_password = secrets.token_urlsafe(32)
        user = User(
            username=normalized_email,
            email=normalized_email,
            display_name=payload.get("name"),
            entra_object_id=entra_object_id,
            password=get_password_hash(random_password),
            role=resolved_role,
            is_superuser=resolved_role in {"root", "super_admin", "department_admin"},
            is_active=auth_settings.NEW_USER_IS_ACTIVE,
        )
        try:
            db.add(user)
            await db.commit()
            await db.refresh(user)
            is_new_user = True
        except IntegrityError:
            await db.rollback()
            existing_user = await _resolve_sso_identity_user(
                db,
                normalized_email=normalized_email,
                entra_object_id=entra_object_id,
                display_name=payload.get("name"),
            )
            if not existing_user:
                raise HTTPException(status_code=500, detail="Unable to provision SSO user.")
            user = existing_user
            resolved_role = normalize_role(getattr(user, "role", "doc_submitter"))

    # DB role always wins for registered users (except configured root email override above)
    if user and not (root_email and normalized_email == root_email):
        resolved_role = normalize_role(getattr(user, "role", "doc_submitter"))

    # Check if user account has expired
    if user and not is_new_user and user.expires_at is not None:
        from datetime import datetime as _dt, timezone as _tz

        _now = _dt.now(_tz.utc)
        _exp = user.expires_at if user.expires_at.tzinfo else user.expires_at.replace(tzinfo=_tz.utc)
        if _now >= _exp:
            user.is_active = False
            db.add(user)
            await db.commit()
            await db.refresh(user)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User account has expired",
            )

    if user and not is_new_user and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Inactive user",
        )

    permissions = await get_permissions_for_role(resolved_role)

    settings_service = get_settings_service()
    user_cache = UserCacheService(settings_service)
    user_dict = user.model_dump(mode="json", exclude={"password"})
    await user_cache.set_user(user_dict)


    tokens = await create_user_tokens(user_id=user.id, db=db, update_last_login=True)
    _apply_auth_cookies(response, tokens, auth_settings, user)
    from agentcore.observability.metrics_registry import record_login_attempt
    record_login_attempt("success")
    return {
        **tokens,
        "role": resolved_role,
        "permissions": permissions
    }

@router.post("/refresh", response_model=AzureSSOResponse)
async def refresh_token(
    request: Request,
    response: Response,
    db: DbSession,
):
    auth_settings = get_settings_service().auth_settings

    token = request.cookies.get("refresh_token_ag")

    if token:
        tokens = await create_refresh_token(token, db)
        user_id = tokens.get("user_id") 
        user = await get_user_by_id(db, user_id)
        if not user:
             raise HTTPException(status_code=404, detail="User not found")
        user_role = normalize_role(getattr(user, "role", "idp_configurator"))
        permissions = await get_permissions_for_role(user_role)
        _apply_auth_cookies(response, tokens, auth_settings, user)
        return {
            **tokens,
            "role": user_role,
            "permissions": permissions
        }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid refresh token",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("refresh_token_ag")
    response.delete_cookie("access_token_ag")
    response.delete_cookie("apikey_tkn_ag")
    return {"message": "Logout successful"}


# ── Registration ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


def _make_verification_token(user_id: str, secret_key: str) -> str:
    payload = {
        "sub": user_id,
        "type": "email_verification",
        "exp": datetime.now(timezone.utc) + timedelta(hours=24),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")


@router.post("/register", status_code=201)
async def register_user(body: RegisterRequest, request: Request, db: DbSession):
    auth_settings = get_settings_service().auth_settings
    settings = get_settings_service().settings

    username = body.username.strip()
    email = body.email.strip().lower()

    if not username or not email or not body.password:
        raise HTTPException(status_code=400, detail="Username, email and password are required.")

    existing = (
        await db.exec(
            select(User).where(
                User.deleted_at.is_(None),
                or_(
                    func.lower(User.username) == username.lower(),
                    func.lower(User.email) == email,
                ),
            )
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="A user with that username or email already exists.")

    user = User(
        username=username,
        email=email,
        display_name=username,
        password=get_password_hash(body.password),
        role="doc_submitter",
        is_active=False,
        is_superuser=False,
    )
    try:
        db.add(user)
        await db.commit()
        await db.refresh(user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="A user with that username or email already exists.")

    secret_key = auth_settings.SECRET_KEY.get_secret_value()
    token = _make_verification_token(str(user.id), secret_key)

    base_url = str(request.base_url).rstrip("/")
    verification_link = f"{base_url}/api/verify-email?token={token}"

    await send_verification_email(
        settings=settings,
        recipient_email=email,
        recipient_name=username,
        verification_link=verification_link,
    )

    return {"message": "Registration successful. Please check your email to verify your account."}


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(token: str, db: DbSession):
    auth_settings = get_settings_service().auth_settings
    secret_key = auth_settings.SECRET_KEY.get_secret_value()

    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")

    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
    except JWTError:
        return HTMLResponse(_verification_page(success=False, frontend_url=frontend_url))

    if payload.get("type") != "email_verification":
        return HTMLResponse(_verification_page(success=False, frontend_url=frontend_url))

    user_id = payload.get("sub")
    user = await get_user_by_id(db, user_id)
    if not user:
        return HTMLResponse(_verification_page(success=False, frontend_url=frontend_url))

    if not user.is_active:
        user.is_active = True
        user.updated_at = datetime.now(timezone.utc)
        db.add(user)
        await db.commit()

    return HTMLResponse(_verification_page(success=True, frontend_url=frontend_url))


def _verification_page(*, success: bool, frontend_url: str) -> str:
    if success:
        title = "Email Verified"
        heading = "Email verified successfully!"
        message = "Your account is now active. You can sign in."
        color = "#D04A02"
    else:
        title = "Verification Failed"
        heading = "Verification link is invalid or expired."
        message = "Please register again or request a new verification link."
        color = "#c0392b"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>{title}</title>
  <meta http-equiv="refresh" content="4; url={frontend_url}/login">
  <style>
    body {{ font-family: Calibri, Arial, sans-serif; display: flex; align-items: center;
           justify-content: center; min-height: 100vh; margin: 0; background: #f7f7f7; }}
    .card {{ background: #fff; border-top: 4px solid {color}; border-radius: 8px;
             padding: 40px 48px; text-align: center; box-shadow: 0 4px 24px rgba(0,0,0,.08); max-width: 420px; }}
    h1 {{ color: {color}; font-size: 1.4rem; margin-bottom: 12px; }}
    p {{ color: #555; line-height: 1.6; }}
    a {{ color: {color}; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{heading}</h1>
    <p>{message}</p>
    <p style="margin-top:20px;font-size:0.9rem;color:#999;">
      Redirecting to <a href="{frontend_url}/login">login</a> in a moment…
    </p>
  </div>
</body>
</html>"""

# ── Set password (admin-created users / password reset) ──────────────────────

class SetPasswordRequest(BaseModel):
    token: str
    password: str


@router.post("/set-password")
async def set_password(body: SetPasswordRequest, db: DbSession):
    auth_settings = get_settings_service().auth_settings
    secret_key = auth_settings.SECRET_KEY.get_secret_value()

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    # Decode without verification first to extract the user_id.
    try:
        unverified = jwt.get_unverified_claims(body.token)
    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired link. Please request a new one.")

    if unverified.get("type") != "set_password":
        raise HTTPException(status_code=400, detail="Invalid token type.")

    user_id = unverified.get("sub")
    user = await get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Re-verify using the key bound to the user's current password hash.
    # If the password was already changed the signing key will no longer match.
    signing_key = secret_key + (user.password or "")[:20]
    try:
        jwt.decode(body.token, signing_key, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(
            status_code=400,
            detail="This link has already been used or has expired. Please contact your administrator for a new one.",
        )

    user.password = get_password_hash(body.password)
    user.is_active = True
    user.updated_at = datetime.now(timezone.utc)
    db.add(user)
    await db.commit()

    return {"message": "Password set successfully. You can now sign in."}


# @router.post("/logout")
# async def logout(response: Response):
#     auth_settings = get_settings_service().auth_settings

#     cookie_params = {
#         "domain": auth_settings.COOKIE_DOMAIN,
#         "path": "/", # Ensure this matches where the cookie was set
#         "httponly": True,
#         "samesite": auth_settings.REFRESH_SAME_SITE,
#         "secure": auth_settings.REFRESH_SECURE,
#     }

#     response.delete_cookie("refresh_token_ag", **cookie_params)
#     response.delete_cookie("access_token_ag", **cookie_params)
#     response.delete_cookie("apikey_tkn_ag", **cookie_params)
    
#     return {"message": "Logout successful"}
