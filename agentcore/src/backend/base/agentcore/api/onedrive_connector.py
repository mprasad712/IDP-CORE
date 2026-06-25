"""OneDrive connector API — OAuth flow, account linking, file list/read.

Isolated router for OneDrive-specific operations. Does NOT modify any connector-catalogue
CRUD — that remains in connector_catalogue.py.

OneDrive uses delegated OAuth (the user signs in), exactly like the Outlook connector. The
``/common`` authority is used so a single connector works for BOTH personal (consumer) and
work/school (Entra) accounts. File operations target Microsoft Graph ``/me/drive``.
"""
from __future__ import annotations

import base64
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlparse
from uuid import UUID

import httpx
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from loguru import logger
from pydantic import BaseModel

from agentcore.services.cache.redis_client import get_redis_client
from agentcore.services.deps import get_settings_service

from agentcore.api.connector_catalogue import (
    FILE_OAUTH_PROVIDERS,
    _can_access_connector,
    _decrypt_provider_config,
    _prepare_provider_config,
    _get_scope_memberships,
    _require_connector_permission,
)
from agentcore.api.utils import CurrentActiveUser, DbSession
from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue

router = APIRouter(prefix="/onedrive", tags=["OneDrive Connector"])

# ── Constants ────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# /common works for both personal and work/school accounts.
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
FILE_SCOPES = "Files.Read Files.ReadWrite User.Read offline_access"

_STATE_TTL_SECONDS = 600  # 10 minutes

# Short-lived cache of folder/file listings so repeat opens + drill-downs of the picker are instant
# (one Graph round-trip is otherwise ~0.5–1s). Keyed by (connector_id, account_email, folder_path).
_list_cache: TTLCache = TTLCache(maxsize=256, ttl=60)
_list_cache_lock = threading.Lock()


async def _store_oauth_state(state: str, data: dict) -> None:
    settings_service = get_settings_service()
    redis = get_redis_client(settings_service)
    await redis.setex(f"onedrive_oauth:{state}", _STATE_TTL_SECONDS, json.dumps(data))


async def _pop_oauth_state(state: str) -> dict | None:
    settings_service = get_settings_service()
    redis = get_redis_client(settings_service)
    key = f"onedrive_oauth:{state}"
    raw = await redis.get(key)
    if not raw:
        return None
    await redis.delete(key)
    return json.loads(raw)


# ── Pydantic models ─────────────────────────────────────────────

class OAuthStartResponse(BaseModel):
    authorize_url: str
    state: str


class ListFilesRequest(BaseModel):
    account_email: str | None = None
    folder_path: str = ""  # path within the drive (e.g. "Invoices/2024"); empty = root
    top: int = 50


class ReadFileRequest(BaseModel):
    account_email: str | None = None
    item_id: str


# ── Helpers ──────────────────────────────────────────────────────

async def _load_connector(
    connector_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> ConnectorCatalogue:
    """Load and validate a connector belongs to the user and is a OneDrive connector."""
    await _require_connector_permission(current_user, "view_connector_page")
    row = await session.get(ConnectorCatalogue, connector_id)
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
    if row.provider not in FILE_OAUTH_PROVIDERS:
        raise HTTPException(status_code=400, detail="Not a OneDrive connector")
    org_ids, dept_pairs = await _get_scope_memberships(session, current_user.id)
    if not _can_access_connector(row, current_user, org_ids, dept_pairs):
        raise HTTPException(status_code=403, detail="Connector is outside your visibility scope")
    return row


def _get_decrypted_config(row: ConnectorCatalogue) -> dict:
    return _decrypt_provider_config(row.provider, row.provider_config or {})


def _find_account(config: dict, email: str | None) -> dict | None:
    """Find a linked account by email (case-insensitive). Returns first account when email is empty."""
    accounts = config.get("linked_accounts", [])
    if not accounts:
        return None
    if not email:
        return accounts[0]
    for acct in accounts:
        if acct.get("email", "").lower() == email.lower():
            return acct
    return None


async def _refresh_token_if_needed(config: dict, acct: dict, force: bool = False) -> tuple[str, bool]:
    """Refresh the access token if expired. Returns (access_token, was_refreshed)."""
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
                "scope": FILE_SCOPES,
            },
        )

    if resp.status_code != 200:
        logger.warning("OneDrive token refresh failed: {}", resp.text[:300])
        raise HTTPException(status_code=401, detail="Token refresh failed. Re-authenticate.")

    data = resp.json()
    acct["access_token"] = data["access_token"]
    acct["refresh_token"] = data.get("refresh_token", refresh_token)
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
        logger.error("Failed to save OneDrive connector config: {}", exc)
        raise HTTPException(status_code=500, detail="Failed to save connector configuration")


# ── OAuth endpoints ──────────────────────────────────────────────

@router.get("/{connector_id}/oauth/start")
async def start_oauth(
    connector_id: UUID,
    request: Request,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> OAuthStartResponse:
    """Generate the Microsoft OAuth authorize URL for OneDrive (Files) scopes."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)

    client_id = config.get("client_id", "")
    if not client_id:
        raise HTTPException(status_code=400, detail="client_id is required in provider_config")

    # Azure AD requires "https://" or "http://localhost" — 127.0.0.1 is rejected.
    base = str(request.base_url).rstrip("/")
    base = base.replace("://127.0.0.1", "://localhost")
    redirect_uri = base + "/api/onedrive/oauth/callback"

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
        "scope": FILE_SCOPES,
        "state": state,
        "response_mode": "query",
        "prompt": "select_account",
    })
    authorize_url = f"{AUTHORIZE_URL}?{params}"

    return OAuthStartResponse(authorize_url=authorize_url, state=state)


@router.get("/oauth/callback")
async def oauth_callback(
    session: DbSession,
    code: str = Query(""),
    state: str = Query(...),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    """Exchange the auth code for tokens and link the account to the connector.

    Called by Microsoft's redirect — no JWT auth required; the *state* encodes connector/user.
    """
    state_data = await _pop_oauth_state(state) if state else None
    fe = state_data.get("frontend_origin", "") if state_data else ""

    if error:
        logger.warning("OneDrive OAuth error: {} — {}", error, error_description)
        return RedirectResponse(
            url=f"{fe}/connectors?error=onedrive_oauth_failed&detail={quote(error_description or error)}",
        )

    if not code:
        raise HTTPException(status_code=400, detail="Authorization code missing")
    if not state_data:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    connector_id = UUID(state_data["connector_id"])
    user_id = UUID(state_data["user_id"])
    redirect_uri = state_data.get("redirect_uri", "")

    row = await session.get(ConnectorCatalogue, connector_id)
    if not row or row.provider not in FILE_OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail="Connector not found")

    config = _get_decrypted_config(row)
    client_id = config.get("client_id", "")
    client_secret = config.get("client_secret", "")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "scope": FILE_SCOPES,
            },
        )

    if resp.status_code != 200:
        logger.error("OneDrive token exchange failed: {}", resp.text[:500])
        return RedirectResponse(url=f"{fe}/connectors?error=onedrive_token_exchange_failed")

    token_data = resp.json()
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)

    async with httpx.AsyncClient(timeout=10) as client:
        me_resp = await client.get(
            f"{GRAPH_BASE}/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if me_resp.status_code != 200:
        logger.error("OneDrive /me call failed: {}", me_resp.text[:300])
        return RedirectResponse(url=f"{fe}/connectors?error=onedrive_profile_fetch_failed")

    me_data = me_resp.json()
    email = (me_data.get("mail") or me_data.get("userPrincipalName") or "").lower()
    display_name = me_data.get("displayName", "")
    if not email:
        return RedirectResponse(url=f"{fe}/connectors?error=onedrive_no_email")

    # Validate the account actually has a drive (reject accounts without OneDrive provisioned).
    async with httpx.AsyncClient(timeout=10) as client:
        drive_resp = await client.get(
            f"{GRAPH_BASE}/me/drive",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if drive_resp.status_code != 200:
        try:
            err_detail = drive_resp.json().get("error", {}).get("message", drive_resp.text[:200])
        except Exception:
            err_detail = drive_resp.text[:200]
        logger.warning(f"OneDrive validation failed for {email}: {drive_resp.status_code} — {err_detail}")
        return RedirectResponse(
            url=f"{fe}/connectors?error=onedrive_no_drive&detail={quote(err_detail)}"
        )

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
    # Linking an account makes the connector usable — mark it connected.
    row.status = "connected"
    try:
        await session.commit()
    except Exception as exc:
        await session.rollback()
        logger.error("Failed to save linked OneDrive account: {}", exc)
        return RedirectResponse(url=f"{fe}/connectors?error=onedrive_save_failed")

    logger.info("OneDrive account {} linked to connector {}", email, connector_id)
    return RedirectResponse(
        url=f"{fe}/connectors?success=onedrive_account_linked&email={quote(email)}",
    )


# ── Account management ──────────────────────────────────────────

@router.get("/{connector_id}/accounts")
async def list_accounts(
    connector_id: UUID,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> list[dict]:
    """List linked OneDrive accounts for a connector (tokens masked)."""
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
    """Remove a linked account from the connector."""
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


# ── File operations (/me/drive) ──────────────────────────────────

def _drive_path_url(folder_path: str) -> str:
    """Build the Graph children URL for a OneDrive path (empty = root)."""
    folder_path = (folder_path or "").strip().strip("/")
    if folder_path:
        if ".." in folder_path or "\\" in folder_path:
            raise HTTPException(status_code=400, detail="Invalid folder_path")
        safe = quote(folder_path, safe="/")
        return f"{GRAPH_BASE}/me/drive/root:/{safe}:/children"
    return f"{GRAPH_BASE}/me/drive/root/children"


@router.post("/{connector_id}/list")
async def list_files(
    connector_id: UUID,
    req: ListFilesRequest,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """List files and folders in a OneDrive path."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)
    acct = _find_account(config, req.account_email)
    if not acct:
        raise HTTPException(status_code=404, detail="No linked OneDrive account. Link one via OAuth first.")

    # Serve from cache when warm — makes repeat opens / drill-downs of the folder picker instant.
    cache_key = (str(connector_id), acct.get("email", ""), req.folder_path, req.top)
    with _list_cache_lock:
        cached = _list_cache.get(cache_key)
    if cached is not None:
        return cached

    access_token, refreshed = await _refresh_token_if_needed(config, acct)
    if refreshed:
        await _save_updated_config(session, row, config, current_user.id)

    url = _drive_path_url(req.folder_path)
    params = {
        "$top": str(req.top),
        "$select": "id,name,size,lastModifiedDateTime,createdDateTime,webUrl,file,folder",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    if resp.status_code == 401:
        access_token, did = await _refresh_token_if_needed(config, acct, force=True)
        if did:
            await _save_updated_config(session, row, config, current_user.id)
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Graph API error {resp.status_code}: {resp.text[:300]}")

    items = []
    for item in resp.json().get("value", []):
        entry = {
            "id": item.get("id"),
            "name": item.get("name"),
            "size": item.get("size"),
            "lastModified": item.get("lastModifiedDateTime"),
            "webUrl": item.get("webUrl", ""),
            "type": "folder" if "folder" in item else "file",
        }
        if "file" in item:
            entry["mimeType"] = item["file"].get("mimeType", "")
        items.append(entry)

    result = {
        "account_email": acct.get("email", ""),
        "folder_path": req.folder_path,
        "count": len(items),
        "items": items,
    }
    with _list_cache_lock:
        _list_cache[cache_key] = result
    return result


@router.post("/{connector_id}/read")
async def read_file(
    connector_id: UUID,
    req: ReadFileRequest,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict:
    """Download a file's content (base64) and metadata by item ID."""
    row = await _load_connector(connector_id, current_user, session)
    config = _get_decrypted_config(row)
    acct = _find_account(config, req.account_email)
    if not acct:
        raise HTTPException(status_code=404, detail="No linked OneDrive account. Link one via OAuth first.")

    access_token, refreshed = await _refresh_token_if_needed(config, acct)
    if refreshed:
        await _save_updated_config(session, row, config, current_user.id)

    safe_item = quote(req.item_id, safe="")
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        meta = await client.get(f"{GRAPH_BASE}/me/drive/items/{safe_item}", headers=headers)
        if meta.status_code != 200:
            raise HTTPException(status_code=400, detail=f"Failed to fetch metadata ({meta.status_code}): {meta.text[:200]}")
        content = await client.get(
            f"{GRAPH_BASE}/me/drive/items/{safe_item}/content",
            headers=headers, timeout=60, follow_redirects=True,
        )
    if content.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Failed to download ({content.status_code}): {content.text[:200]}")

    metadata = meta.json()
    return {
        "id": metadata.get("id"),
        "name": metadata.get("name"),
        "size": metadata.get("size"),
        "mimeType": metadata.get("file", {}).get("mimeType", ""),
        "lastModified": metadata.get("lastModifiedDateTime"),
        "webUrl": metadata.get("webUrl", ""),
        "content_base64": base64.b64encode(content.content).decode("ascii"),
    }
