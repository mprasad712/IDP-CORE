"""Gmail connector API — OAuth flow, account linking, mail read.

Isolated router for Gmail-specific operations. Does NOT modify
any connector-catalogue CRUD — that remains in connector_catalogue.py.
"""
from __future__ import annotations

import base64
import json
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse
from uuid import UUID

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from agentcore.services.cache.redis_client import get_redis_client
from agentcore.services.deps import get_settings_service

from agentcore.api.connector_catalogue import (
    _can_access_connector,
    _decrypt_provider_config,
    _prepare_provider_config,
    _get_scope_memberships,
    _require_connector_permission,
)
from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue

router = APIRouter(prefix="/gmail", tags=["Gmail Connector"])

# ── Constants ────────────────────────────────────────────────────
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
MAIL_SCOPES = " ".join([
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "openid",
    "email",
    "profile",
])

_STATE_TTL_SECONDS = 600  # 10 minutes


# ── OAuth state store (Redis, TTL-based) ──────────────────────────

async def _store_oauth_state(state: str, data: dict) -> None:
    settings_service = get_settings_service()
    redis = get_redis_client(settings_service)
    await redis.setex(f"gmail_oauth:{state}", _STATE_TTL_SECONDS, json.dumps(data))


async def _pop_oauth_state(state: str) -> dict | None:
    settings_service = get_settings_service()
    redis = get_redis_client(settings_service)
    key = f"gmail_oauth:{state}"
    raw = await redis.get(key)
    if not raw:
        return None
    await redis.delete(key)
    return json.loads(raw)


# ── Pydantic models ─────────────────────────────────────────────

class OAuthStartResponse(BaseModel):
    authorize_url: str
    state: str


class ReadMailRequest(BaseModel):
    account_email: str
    limit: int = 10
    label: str = "INBOX"
    filter_sender: str | None = None
    filter_subject: str | None = None


# ── Internal helpers ──────────────────────────────────────────────

async def _load_connector(
    connector_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> ConnectorCatalogue:
    await _require_connector_permission(current_user, "view_connector_page")
    row = await session.get(ConnectorCatalogue, connector_id)
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
    if row.provider != "gmail":
        raise HTTPException(status_code=400, detail="Not a Gmail connector")
    org_ids, dept_pairs = await _get_scope_memberships(session, current_user.id)
    if not _can_access_connector(row, current_user, org_ids, dept_pairs):
        raise HTTPException(status_code=403, detail="Connector is outside your visibility scope")
    return row


def _get_decrypted_config(row: ConnectorCatalogue) -> dict:
    return _decrypt_provider_config(row.provider, row.provider_config or {})


def _find_account(config: dict, email: str) -> dict | None:
    for acct in config.get("linked_accounts", []):
        if acct.get("email", "").lower() == email.lower():
            return acct
    return None


async def _refresh_token_if_needed(config: dict, acct: dict, force: bool = False) -> tuple[str, bool]:
    """Refresh Google access token if expired. Returns (access_token, was_refreshed)."""
    access_token = acct.get("access_token", "")
    expires_at = acct.get("token_expires_at", 0)

    if not force and access_token and time.time() < (expires_at - 60):
        return access_token, False

    refresh_token = acct.get("refresh_token", "")
    if not refresh_token:
        raise HTTPException(
            status_code=401,
            detail="Token expired and no refresh token available. Re-authenticate.",
        )

    client_id = config.get("client_id", "")
    client_secret = config.get("client_secret", "")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        logger.warning("Gmail token refresh failed: {}", resp.text[:300])
        raise HTTPException(status_code=401, detail="Token refresh failed. Re-authenticate.")

    data = resp.json()
    acct["access_token"] = data["access_token"]
    # Google typically does not rotate refresh tokens; keep existing if not returned
    if data.get("refresh_token"):
        acct["refresh_token"] = data["refresh_token"]
    acct["token_expires_at"] = time.time() + data.get("expires_in", 3600)
    return data["access_token"], True


async def _save_updated_config(
    session: DbSession,
    row: ConnectorCatalogue,
    config: dict,
    current_user_id: UUID,
) -> None:
    row.provider_config = _prepare_provider_config(
        row.provider,
        config,
        connector_id=row.id,
        existing_config=row.provider_config or {},
        allow_secret_update=False,
    )
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by = current_user_id
    try:
        await session.commit()
        await session.refresh(row)
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to save Gmail connector config: {}", exc)
        raise HTTPException(status_code=500, detail="Failed to save connector configuration")


# ── Gmail message parsing ─────────────────────────────────────────

def _get_header(headers: list, name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_body(data: str) -> str:
    if not data:
        return ""
    try:
        padded = data + "=" * (4 - len(data) % 4)
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> tuple[str, str]:
    """Return (body_text, mime_type) from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data", "")
    if body_data:
        return _decode_body(body_data), mime_type

    parts = payload.get("parts", [])
    plain_body = ""
    html_body = ""
    for part in parts:
        part_mime = part.get("mimeType", "")
        part_data = part.get("body", {}).get("data", "")
        if part_mime == "text/plain" and part_data:
            plain_body = _decode_body(part_data)
        elif part_mime == "text/html" and part_data:
            html_body = _decode_body(part_data)
        elif part_mime.startswith("multipart/") and part.get("parts"):
            sub_body, sub_mime = _extract_body(part)
            if sub_body:
                if "plain" in sub_mime and not plain_body:
                    plain_body = sub_body
                elif not html_body:
                    html_body = sub_body

    if plain_body:
        return plain_body, "text/plain"
    if html_body:
        return html_body, "text/html"
    return "", mime_type


# ── OAuth endpoints ──────────────────────────────────────────────

@router.get("/{connector_id}/oauth/start")
async def start_oauth(
    connector_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> OAuthStartResponse:
    """Generate Google OAuth authorize URL for Gmail scopes."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)

    client_id = config.get("client_id", "")
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required in provider_config")

    base = str(request.base_url).rstrip("/")
    base = base.replace("://127.0.0.1", "://localhost")
    redirect_uri = base + "/api/gmail/oauth/callback"

    frontend_origin = ""
    referer = request.headers.get("referer", "")
    if referer:
        parsed = urlparse(referer)
        frontend_origin = f"{parsed.scheme}://{parsed.netloc}"

    state = secrets.token_urlsafe(32)
    await _store_oauth_state(state, {
        "connector_id": str(connector_id),
        "user_id": str(current_user.id),
        "redirect_uri": redirect_uri,
        "frontend_origin": frontend_origin,
    })

    params = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": MAIL_SCOPES,
        "state": state,
        "access_type": "offline",
        "prompt": "consent",  # Always request a refresh token
    })
    return OAuthStartResponse(authorize_url=f"{AUTHORIZE_URL}?{params}", state=state)


@router.get("/oauth/callback")
async def oauth_callback(
    session: DbSession,
    code: str = Query(""),
    state: str = Query(...),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    """Exchange authorization code for tokens and link account to connector."""
    state_data = await _pop_oauth_state(state) if state else None
    fe = state_data.get("frontend_origin", "") if state_data else ""

    if error:
        logger.warning("Gmail OAuth error: {} — {}", error, error_description)
        return RedirectResponse(
            url=f"{fe}/connectors?error=gmail_oauth_failed&detail={quote(error_description or error)}",
        )

    if not code:
        raise HTTPException(status_code=400, detail="Authorization code missing")

    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    connector_id = UUID(state_data["connector_id"])
    user_id = UUID(state_data["user_id"])
    redirect_uri = state_data.get("redirect_uri", "")

    row = await session.get(ConnectorCatalogue, connector_id)
    if not row or row.provider != "gmail":
        raise HTTPException(status_code=404, detail="Connector not found")

    config = _get_decrypted_config(row)
    client_id = config.get("client_id", "")
    client_secret = config.get("client_secret", "")

    # Exchange code for tokens
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        logger.error("Gmail token exchange failed: {}", resp.text[:500])
        return RedirectResponse(url=f"{fe}/connectors?error=gmail_token_exchange_failed")

    token_data = resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    # Fetch user profile
    async with httpx.AsyncClient(timeout=10) as client:
        profile_resp = await client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"alt": "json"},
        )

    if profile_resp.status_code != 200:
        logger.error("Gmail userinfo call failed: {}", profile_resp.text[:300])
        return RedirectResponse(url=f"{fe}/connectors?error=gmail_profile_fetch_failed")

    profile = profile_resp.json()
    email = (profile.get("email") or "").lower()
    display_name = profile.get("name", "")

    if not email:
        return RedirectResponse(url=f"{fe}/connectors?error=gmail_no_email")

    # Verify Gmail access by reading the INBOX label
    async with httpx.AsyncClient(timeout=10) as client:
        inbox_resp = await client.get(
            f"{GMAIL_BASE}/users/me/labels/INBOX",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if inbox_resp.status_code != 200:
        try:
            err_detail = inbox_resp.json().get("error", {}).get("message", inbox_resp.text[:200])
        except Exception:
            err_detail = inbox_resp.text[:200]
        logger.warning("Gmail access validation failed for {}: {} — {}", email, inbox_resp.status_code, err_detail)
        return RedirectResponse(
            url=f"{fe}/connectors?error=gmail_no_access&detail={quote(err_detail)}"
        )

    # Upsert linked account
    linked_accounts: list[dict] = config.get("linked_accounts", [])
    now_iso = datetime.now(timezone.utc).isoformat()
    new_acct = {
        "email": email,
        "display_name": display_name,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": time.time() + expires_in,
        "linked_at": now_iso,
    }

    found = False
    for i, acct in enumerate(linked_accounts):
        if acct.get("email", "").lower() == email.lower():
            linked_accounts[i] = new_acct
            found = True
            break
    if not found:
        linked_accounts.append(new_acct)

    config["linked_accounts"] = linked_accounts
    row.provider_config = _prepare_provider_config(
        row.provider,
        config,
        connector_id=row.id,
        existing_config=row.provider_config or {},
        allow_secret_update=False,
    )
    row.updated_at = datetime.now(timezone.utc)
    row.updated_by = user_id
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to save linked Gmail account: {}", exc)
        return RedirectResponse(url=f"{fe}/connectors?error=gmail_save_failed")

    logger.info("Gmail account {} linked to connector {}", email, connector_id)
    return RedirectResponse(
        url=f"{fe}/connectors?success=gmail_account_linked&email={quote(email)}",
    )


# ── Account management ──────────────────────────────────────────

@router.get("/{connector_id}/accounts")
async def list_accounts(
    connector_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> list[dict]:
    """List linked Gmail accounts for a connector (tokens masked)."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)
    return [
        {
            "email": acct.get("email", ""),
            "display_name": acct.get("display_name", ""),
            "linked_at": acct.get("linked_at", ""),
            "token_expires_at": acct.get("token_expires_at"),
        }
        for acct in config.get("linked_accounts", [])
    ]


@router.delete("/{connector_id}/accounts/{email}")
async def unlink_account(
    connector_id: UUID,
    email: str,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Remove a linked Gmail account from the connector."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)

    linked_accounts = config.get("linked_accounts", [])
    original_count = len(linked_accounts)
    linked_accounts = [a for a in linked_accounts if a.get("email", "").lower() != email.lower()]

    if len(linked_accounts) == original_count:
        raise HTTPException(status_code=404, detail=f"Account '{email}' not found")

    config["linked_accounts"] = linked_accounts
    await _save_updated_config(session, row, config, current_user.id)
    return {"message": f"Account '{email}' unlinked successfully"}


# ── Mail operations ──────────────────────────────────────────────

@router.post("/{connector_id}/read")
async def read_mail(
    connector_id: UUID,
    req: ReadMailRequest,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Read messages from a linked Gmail account."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)

    acct = _find_account(config, req.account_email)
    if not acct:
        raise HTTPException(status_code=404, detail=f"Account '{req.account_email}' not linked")

    access_token, was_refreshed = await _refresh_token_if_needed(config, acct)
    if was_refreshed:
        await _save_updated_config(session, row, config, current_user.id)

    headers = {"Authorization": f"Bearer {access_token}"}

    # Build query string for Gmail search
    q_parts: list[str] = []
    if req.filter_sender:
        q_parts.append(f"from:{req.filter_sender}")
    if req.filter_subject:
        q_parts.append(f"subject:{req.filter_subject}")

    list_params: dict = {
        "labelIds": req.label,
        "maxResults": req.limit,
    }
    if q_parts:
        list_params["q"] = " ".join(q_parts)

    # Step 1: List message IDs
    async with httpx.AsyncClient(timeout=15) as client:
        list_resp = await client.get(
            f"{GMAIL_BASE}/users/me/messages",
            headers=headers,
            params=list_params,
        )

    # Retry once with force-refresh on 401
    if list_resp.status_code == 401:
        logger.warning("read_mail: Gmail API 401, force-refreshing token")
        access_token, _ = await _refresh_token_if_needed(config, acct, force=True)
        if _:
            await _save_updated_config(session, row, config, current_user.id)
        headers = {"Authorization": f"Bearer {access_token}"}
        async with httpx.AsyncClient(timeout=15) as client:
            list_resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages",
                headers=headers,
                params=list_params,
            )

    if list_resp.status_code != 200:
        raise HTTPException(
            status_code=400,
            detail=f"Gmail API error {list_resp.status_code}: {list_resp.text[:300]}",
        )

    message_ids = [m["id"] for m in list_resp.json().get("messages", [])]

    # Step 2: Fetch each message with full payload
    messages: list[dict] = []
    async with httpx.AsyncClient(timeout=20) as client:
        for msg_id in message_ids:
            msg_resp = await client.get(
                f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "full"},
            )
            if msg_resp.status_code != 200:
                continue
            msg = msg_resp.json()
            payload = msg.get("payload", {})
            hdrs = payload.get("headers", [])
            body, body_type = _extract_body(payload)
            messages.append({
                "id": msg.get("id"),
                "threadId": msg.get("threadId"),
                "snippet": msg.get("snippet", ""),
                "subject": _get_header(hdrs, "Subject"),
                "from": _get_header(hdrs, "From"),
                "to": _get_header(hdrs, "To"),
                "date": _get_header(hdrs, "Date"),
                "body": body,
                "bodyContentType": body_type,
                "labelIds": msg.get("labelIds", []),
            })

    return {
        "account_email": req.account_email,
        "label": req.label,
        "count": len(messages),
        "messages": messages,
    }
