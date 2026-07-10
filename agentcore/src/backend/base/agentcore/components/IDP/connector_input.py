"""Connector Input component for the IDP pipeline.

Pulls email attachments from a connected Outlook mail connector and outputs them as
documents for downstream OCR / extraction. When an Outlook connector is selected, the node
reveals the full email-filter + polling controls (subject / body / sender / To / CC /
importance / has-attachment / unread / folder / max / poll interval). The same fields are
read at publish time by ``TriggerService.sync_email_monitors_for_agent`` to create the
background email-monitor that auto-ingests matching emails into Processed Docs.
"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import os
import tempfile
from urllib.parse import quote

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output
from agentcore.schema.message import Message
from agentcore.logging import logger

_EMAIL_PROVIDERS = {"outlook", "gmail"}
_SHAREPOINT_PROVIDERS = {"sharepoint"}
_ONEDRIVE_PROVIDERS = {"onedrive"}
_IDP_PROVIDERS = _EMAIL_PROVIDERS | _SHAREPOINT_PROVIDERS | _ONEDRIVE_PROVIDERS
GRAPH_BASE = "https://graph.microsoft.com/v1.0"
GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"

# Map Outlook-style folder names to Gmail label IDs
_GMAIL_LABEL_MAP = {
    "inbox": "INBOX",
    "sentitems": "SENT",
    "sent": "SENT",
    "drafts": "DRAFT",
    "trash": "TRASH",
    "spam": "SPAM",
    "junk": "SPAM",
    "archive": "INBOX",  # Gmail has no archive label; fall back to INBOX
}

# Email-only inputs — hidden until the selected connector's provider is an email provider.
_EMAIL_FIELDS = [
    "account_email", "folder", "max_emails", "poll_interval_seconds",
    "filter_sender", "filter_subject", "filter_body", "filter_to", "filter_cc",
    "filter_importance", "filter_has_attachments", "unread_only", "mark_as_read", "fetch_full_body",
]

# SharePoint-only inputs — hidden until the selected connector's provider is SharePoint.
_SHAREPOINT_FIELDS = ["sharepoint_library", "sharepoint_folder", "sharepoint_file_types"]

# OneDrive-only inputs — hidden until the selected connector's provider is OneDrive.
_ONEDRIVE_FIELDS = ["onedrive_folder", "onedrive_file_types"]


def _run_async(coro):
    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=30)
    except RuntimeError:
        return asyncio.run(coro)


def _fetch_mail_connectors() -> list[str]:
    """Return 'name | provider | target | uuid' strings for connected IDP-capable connectors.

    Includes Outlook (target = linked mailboxes) and SharePoint (target = site URL) connectors.
    """
    try:
        from agentcore.services.deps import get_db_service
        db_service = get_db_service()

        async def _query():
            from sqlalchemy import select
            from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue
            from agentcore.api.connector_catalogue import _decrypt_provider_config

            async with db_service.with_session() as session:
                stmt = (
                    select(ConnectorCatalogue)
                    .where(ConnectorCatalogue.provider.in_(_IDP_PROVIDERS))
                    .where(ConnectorCatalogue.status == "connected")
                    .order_by(ConnectorCatalogue.name)
                )
                result = await session.execute(stmt)
                rows = result.scalars().all()
                items = []
                for r in rows:
                    config = _decrypt_provider_config(r.provider, r.provider_config or {})
                    if r.provider in _SHAREPOINT_PROVIDERS:
                        target = config.get("site_url", "no site configured")
                    elif r.provider in _ONEDRIVE_PROVIDERS:
                        accounts = config.get("linked_accounts", [])
                        target = ", ".join(a.get("email", "") for a in accounts) or "no account linked"
                    else:
                        accounts = config.get("linked_accounts", [])
                        target = ", ".join(a.get("email", "") for a in accounts) or "no mailbox linked"
                    items.append(f"{r.name} | {r.provider} | {target} | {r.id}")
                return items

        return _run_async(_query())
    except Exception as e:
        logger.warning(f"Could not fetch IDP connectors: {e}")
        return []


def _parse_connector_id(value: str) -> str | None:
    """Extract UUID from 'name | provider | emails | uuid' string."""
    if not value:
        return None
    parts = [p.strip() for p in value.split("|")]
    return parts[-1] if len(parts) >= 4 else value.strip() or None


def _parse_connector_provider(value: str) -> str:
    """Extract provider from 'name | provider | emails | uuid' string (lower-cased)."""
    if not value:
        return ""
    parts = [p.strip() for p in value.split("|")]
    return parts[1].lower() if len(parts) >= 4 else ""


def _resolve_connector_by_name(name: str, provider: str | None = None) -> tuple[str, str] | None:
    """Resolve a plain connector NAME (as saved by the canvas ``IDPConnectorDropdown``) to
    ``(connector_id, provider)`` from the catalogue.

    The builder canvas saves the chosen connector's plain name in ``connector_name`` (and its
    provider in ``connector_provider``), whereas the backend palette dropdown saves the full
    "name | provider | target | uuid" token in ``connector``. This bridges the canvas save shape to
    the UUID the config loaders need. Returns ``None`` when no connected IDP connector matches.

    When ``provider`` is given it further narrows the match (connector names are unique only within an
    org/dept scope, so a provider hint disambiguates). Matches are ordered deterministically and a
    warning is logged if more than one row matches, so the choice is stable rather than
    insertion-order dependent.
    """
    if not name:
        return None
    try:
        from agentcore.services.deps import get_db_service
        db_service = get_db_service()

        async def _query():
            from sqlalchemy import select
            from agentcore.services.database.models.connector_catalogue.model import ConnectorCatalogue

            async with db_service.with_session() as session:
                stmt = (
                    select(ConnectorCatalogue)
                    .where(ConnectorCatalogue.name == name)
                    .where(ConnectorCatalogue.provider.in_(_IDP_PROVIDERS))
                    .where(ConnectorCatalogue.status == "connected")
                    .order_by(ConnectorCatalogue.name, ConnectorCatalogue.id)
                )
                if provider:
                    stmt = stmt.where(ConnectorCatalogue.provider == provider)
                result = await session.execute(stmt)
                rows = result.scalars().all()
                if not rows:
                    return None
                if len(rows) > 1:
                    logger.warning(
                        f"Connector name '{name}'"
                        + (f" (provider '{provider}')" if provider else "")
                        + f" matched {len(rows)} connected connectors; using the first by (name, id). "
                        "Use unique connector names to avoid ambiguity."
                    )
                row = rows[0]
                return (str(row.id), (row.provider or "").lower())

        return _run_async(_query())
    except Exception as e:
        logger.warning(f"Could not resolve connector by name '{name}': {e}")
        return None


def _native_text_from_bytes(file_bytes: bytes, filename: str) -> tuple[str, list[dict]]:
    """Extract the native text layer from a fetched attachment — the SAME thing the Document Upload
    node does — so a DIGITAL document classifies/extracts with no OCR node on the canvas.

    Returns ``(text, tokens)``; ``("", [])`` for a scanned / image-only file (no text layer). In that
    case a downstream PaddleOCR node supplies the text (the temp ``file_path`` stays in the message
    ``data``). Never returns the file path — feeding a path as "document text" is exactly what made the
    text-based classifier label emailed invoices 'unknown'.
    """
    file_type = (os.path.splitext(filename or "")[1].lstrip(".") or "pdf").lower()
    try:
        from agentcore.services.idp.text_layer import extract_native_text
        _, tokens = extract_native_text(file_bytes, file_type)
        text = " ".join(str(t.get("text", "")) for t in tokens)
        return text, tokens
    except Exception as e:  # scanned / unsupported — an OCR node supplies text downstream
        logger.debug(f"[ConnectorInput] no native text for '{filename}' ({e}); an OCR node will supply it")
        return "", []


def _addr_list(recipients) -> list[str]:
    """Flatten a Graph recipients array → list of email addresses."""
    out = []
    for r in recipients or []:
        a = ((r or {}).get("emailAddress") or {}).get("address", "")
        if a:
            out.append(a)
    return out


def _fetch_message_file_attachments_sync(access_token: str, message_id: str) -> list[dict] | None:
    """Sync: list a message's downloadable FILE attachments — following Graph ``@odata.nextLink``
    pagination AND fetching large attachments (no inline ``contentBytes``, ~>3 MB) via ``/$value``.

    Mirrors ``TriggerService._fetch_attachments_raw`` for the manual-pull / playground preview path
    (reuses ``_is_graph_url`` + ``_MAX_ATTACHMENT_PAGES`` so the SSRF guard and page cap stay in one
    place). Returns ``[{"name": str, "data": bytes}, ...]`` (possibly empty), or ``None`` when the
    INITIAL attachments-list request fails so the caller can fall through to the next message. Preview
    is best-effort: a forged non-Graph nextLink stops paging, a failed ``/$value`` skips that one — no
    raise, no infinite retry (unlike the production ingest path).
    """
    import base64 as _b64
    import httpx
    from urllib.parse import quote as _quote

    from agentcore.services.trigger.service import _MAX_ATTACHMENT_PAGES, _is_graph_url

    safe_id = _quote(message_id, safe="")
    headers = {"Authorization": f"Bearer {access_token}"}
    url: str | None = f"{GRAPH_BASE}/me/messages/{safe_id}/attachments"
    out: list[dict] = []
    pages = 0
    first = True
    while url and pages < _MAX_ATTACHMENT_PAGES:
        if not _is_graph_url(url):
            logger.warning(f"[ConnectorInput] refusing non-Graph attachments URL for {message_id[:20]}...")
            break  # best-effort: stop paging, keep what we have
        try:
            resp = httpx.get(url, headers=headers, timeout=30)
        except Exception as e:
            logger.warning(f"[ConnectorInput] attachments fetch error for {message_id[:20]}...: {e}")
            return None if first else out
        if resp.status_code != 200:
            logger.warning(f"[ConnectorInput] attachments HTTP {resp.status_code} for {message_id[:20]}...")
            return None if first else out
        try:
            body = resp.json()
        except Exception as e:
            logger.warning(f"[ConnectorInput] bad attachments JSON for {message_id[:20]}...: {e}")
            return None if first else out
        if not isinstance(body, dict):
            logger.warning(f"[ConnectorInput] unexpected attachments payload for {message_id[:20]}...")
            return None if first else out
        first = False
        for att in body.get("value", []) or []:
            if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            name = att.get("name", "attachment")
            cb = att.get("contentBytes", "")
            if cb:
                try:
                    data = _b64.b64decode(cb)
                except Exception:
                    logger.warning(f"[ConnectorInput] could not decode attachment '{name}'; skipping")
                    continue
            else:
                # Large attachment (no inline bytes) → fetch raw via /$value.
                att_id = att.get("id", "")
                if not att_id:
                    continue
                val_url = f"{GRAPH_BASE}/me/messages/{safe_id}/attachments/{_quote(att_id, safe='')}/$value"
                try:
                    vresp = httpx.get(val_url, headers=headers, timeout=60)
                except Exception as e:
                    logger.warning(f"[ConnectorInput] $value error for '{name}': {e}; skipping")
                    continue
                if vresp.status_code != 200:
                    logger.warning(f"[ConnectorInput] $value HTTP {vresp.status_code} for '{name}'; skipping")
                    continue
                data = vresp.content
            if data:
                out.append({"name": name, "data": data})
        url = body.get("@odata.nextLink")
        pages += 1
    return out


class IDPConnectorInput(Node):
    """Pull email attachments from a connected mail connector for IDP processing."""

    display_name = "Connector Input"
    description = "Pulls attachments from a connected source such as an Outlook mail connector."
    icon = "Plug"
    name = "IDPConnectorInput"

    inputs = [
        DropdownInput(
            name="connector",
            display_name="Connector",
            info=(
                "Select a connector from the Connectors Catalogue. For Outlook, link a mailbox via "
                "OAuth and the email filters appear. For SharePoint, choose the document library and "
                "folder below and the published agent ingests new files from that folder."
            ),
            options=[],
            value="",
            refresh_button=True,
            real_time_refresh=True,
            required=True,
        ),
        # SharePoint-only fields — revealed when a SharePoint connector is selected.
        MessageTextInput(
            name="sharepoint_library", display_name="Library Name", show=False, value="Shared Documents",
            info="SharePoint document library to read from (e.g. 'Shared Documents').",
        ),
        MessageTextInput(
            name="sharepoint_folder", display_name="Folder Path", show=False, value="",
            info="Folder within the library to process (e.g. 'Invoices/2024'). Leave empty for the library root.",
        ),
        MessageTextInput(
            name="sharepoint_file_types", display_name="File Type Filter", show=False, value="pdf,png,jpg,docx",
            info="Comma-separated file extensions to process. Leave empty for all supported types.",
        ),
        # OneDrive-only fields — revealed when a OneDrive connector is selected.
        MessageTextInput(
            name="onedrive_folder", display_name="Folder Path", show=False, value="",
            info="Folder within OneDrive to process (e.g. 'Documents/Invoices'). Leave empty for the drive root.",
        ),
        MessageTextInput(
            name="onedrive_file_types", display_name="File Type Filter", show=False, value="pdf,png,jpg,docx",
            info="Comma-separated file extensions to process. Leave empty for all supported types.",
        ),
        MessageTextInput(
            name="account_email", display_name="Account / Mailbox", show=False, value="",
            info="Linked mailbox to read from. Leave empty to use the first linked account.",
        ),
        MessageTextInput(
            name="folder", display_name="Mail Folder", show=False, value="inbox",
            info="Folder to scan: inbox, sentitems, drafts, archive, or a custom folder name.",
        ),
        IntInput(
            name="max_emails", display_name="Max Emails", show=False, value=10,
            info="Maximum number of recent emails to scan per poll.",
        ),
        IntInput(
            name="poll_interval_seconds", display_name="Poll Interval (seconds)", show=False, value=60,
            info="How often the PUBLISHED agent checks for new mail (used by the background monitor).",
        ),
        MessageTextInput(
            name="filter_sender", display_name="Filter by Sender", show=False, value="",
            info="Only process emails FROM this exact address.",
        ),
        MessageTextInput(
            name="filter_subject", display_name="Subject contains", show=False, value="",
            info="Only process emails whose subject contains this text.",
        ),
        MessageTextInput(
            name="filter_body", display_name="Body contains", show=False, value="",
            info="Only process emails whose body contains this text.",
        ),
        MessageTextInput(
            name="filter_to", display_name="Filter by Recipient (To)", show=False, value="",
            info="Only process emails sent TO this address.",
        ),
        MessageTextInput(
            name="filter_cc", display_name="Filter by CC", show=False, value="",
            info="Only process emails with this address in CC.",
        ),
        DropdownInput(
            name="filter_importance", display_name="Importance", show=False,
            options=["all", "low", "normal", "high"], value="all",
            info="Only process emails of this importance.",
        ),
        BoolInput(
            name="filter_has_attachments", display_name="Has Attachments Only", show=False, value=True,
            info="Only process emails that have attachments.",
        ),
        BoolInput(
            name="unread_only", display_name="Unread Only", show=False, value=False,
            info="Only process unread emails.",
        ),
        BoolInput(
            name="mark_as_read", display_name="Mark as Read After Processing", show=False, value=False,
            info="Mark emails as read once processed (applies to background polling).",
        ),
        BoolInput(
            name="fetch_full_body", display_name="Fetch Full Body", show=False, value=False,
            info="Fetch the full email body (otherwise only a short preview is used).",
        ),
    ]

    outputs = [
        Output(
            display_name="Document",
            name="document",
            method="get_document",
            output_types=["Message"],
        ),
    ]

    def update_build_config(self, build_config: dict, field_value: str, field_name: str | None = None) -> dict:
        """Refresh the connector dropdown + reveal the email filters when an Outlook connector is picked."""
        if field_name in (None, "connector"):
            try:
                options = _fetch_mail_connectors()
                build_config["connector"]["options"] = options
                if not options:
                    build_config["connector"]["info"] = (
                        "No connected connectors found. Go to Connectors → Add Connector → "
                        "Microsoft Outlook or Google Gmail (link a mailbox via OAuth), "
                        "or SharePoint / OneDrive (set site/library), connect it, then refresh this field."
                    )
                current = build_config["connector"].get("value", "")
                if options and current not in options and not current:
                    build_config["connector"]["value"] = options[0]
            except Exception as e:
                logger.warning(f"Error refreshing connector options: {e}")
                build_config["connector"]["options"] = []

            # Provider-aware visibility: show the email block only for an email provider (outlook),
            # and the SharePoint block only for a SharePoint connector.
            selected = build_config.get("connector", {}).get("value", "") or ""
            if field_name == "connector" and isinstance(field_value, str) and field_value:
                selected = field_value
            provider = _parse_connector_provider(selected)
            is_email = provider in _EMAIL_PROVIDERS
            is_sharepoint = provider in _SHAREPOINT_PROVIDERS
            is_onedrive = provider in _ONEDRIVE_PROVIDERS
            for fld in _EMAIL_FIELDS:
                if fld in build_config:
                    build_config[fld]["show"] = is_email
            for fld in _SHAREPOINT_FIELDS:
                if fld in build_config:
                    build_config[fld]["show"] = is_sharepoint
            for fld in _ONEDRIVE_FIELDS:
                if fld in build_config:
                    build_config[fld]["show"] = is_onedrive
        return build_config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_selected_connector(self) -> tuple[str | None, str]:
        """Resolve the chosen connector to ``(connector_id, provider)`` across both save shapes.

        - Backend palette dropdown: ``connector`` = "name | provider | target | uuid" — taken as a
          token only when it has ≥4 pipe segments AND the last is a real UUID (so a display name that
          happens to contain ``|`` is not mis-parsed). The uuid + provider come straight from the
          token, no DB lookup (unchanged legacy behaviour).
        - Canvas ``IDPConnectorDropdown``: ``connector`` is empty; the plain name is in
          ``connector_name`` and the provider hint in ``connector_provider``. The name is resolved to
          a uuid via the catalogue so the manual-pull / preview path works from the canvas. The
          catalogue's provider is authoritative; the hint only disambiguates and is a fallback.

        An unresolvable plain name yields ``(None, provider)`` — it is NOT passed downstream as an id
        (that would reach ``UUID()`` and log a noisy error) — so the caller raises a clean error.

        Memoized per component instance: selection can't change mid-run, so both the provider-dispatch
        resolve in ``get_document`` and the sub-method's resolve reuse the SAME connector row.

        Uses ``getattr(..., "")`` because ``connector_name`` / ``connector_provider`` only exist on the
        component when the canvas template carries them (``connector_provider`` is a hidden field the
        run engine may skip).
        """
        cached = getattr(self, "_resolved_connector_cache", None)
        if cached is not None:
            return cached

        from uuid import UUID

        def _is_uuid(s: str) -> bool:
            try:
                UUID(str(s))
            except (ValueError, AttributeError, TypeError):
                return False
            return True

        token = (getattr(self, "connector", "") or "").strip()
        parts = [p.strip() for p in token.split("|")] if token else []
        if len(parts) >= 4 and _is_uuid(parts[-1]):
            result: tuple[str | None, str] = (_parse_connector_id(token), _parse_connector_provider(token))
            self._resolved_connector_cache = result
            return result

        # Canvas shape (or a bare name/id typed into the backend field): resolve the plain name.
        name = token or (getattr(self, "connector_name", "") or "").strip()
        provider_hint = (getattr(self, "connector_provider", "") or "").strip().lower()
        if not name:
            result = (None, provider_hint)
        else:
            resolved = _resolve_connector_by_name(name, provider_hint or None)
            if resolved:
                cid, prov = resolved
                # DB provider is authoritative (the hint only disambiguates the lookup). A stale hint
                # can't win — it would have filtered the row out and we'd not be here.
                result = (cid, prov)
            elif _is_uuid(name):
                # Defensive: a bare UUID was stored directly — use it (provider from the hint).
                result = (name, provider_hint)
            else:
                # Unresolvable plain name (deleted / typo'd) — no id; caller raises a clean error.
                result = (None, provider_hint)
        self._resolved_connector_cache = result
        return result

    def _get_config(self) -> dict:
        from agentcore.components.tools.outlook_mail import _get_outlook_config
        connector_id, _ = self._resolve_selected_connector()
        if not connector_id:
            raise ValueError(
                "No connector selected, or the selected connector is no longer available. "
                "Pick one from the Connector dropdown."
            )
        config = _get_outlook_config(connector_id)
        if config is None:
            raise ValueError(f"Connector not found (id={connector_id}). It may have been deleted.")
        config["_connector_id"] = connector_id
        return config

    def _get_account(self, config: dict) -> dict:
        accounts = config.get("linked_accounts", [])
        if not accounts:
            raise ValueError("No linked mailboxes. Link a mailbox via OAuth on the Connectors page.")
        email_filter = (self.account_email or "").strip().lower()
        if email_filter:
            acct = next((a for a in accounts if a.get("email", "").lower() == email_filter), None)
            if acct is None:
                avail = ", ".join(a.get("email", "") for a in accounts)
                raise ValueError(f"Account '{email_filter}' not found. Available: {avail}")
            return acct
        return accounts[0]

    def _get_token(self, config: dict, acct: dict) -> str:
        from agentcore.components.tools.outlook_mail import _refresh_token_sync
        return _refresh_token_sync(config, acct)

    def _build_filters(self) -> tuple[list[str], dict]:
        """Return (odata_filter_list, criteria_dict) from the node's filter fields."""
        crit = {
            "sender": (self.filter_sender or "").strip().lower(),
            "subject": (self.filter_subject or "").strip().lower(),
            "body": (self.filter_body or "").strip().lower(),
            "to": (self.filter_to or "").strip().lower(),
            "cc": (self.filter_cc or "").strip().lower(),
            "importance": (self.filter_importance or "all").strip().lower(),
            "has_attachments": bool(self.filter_has_attachments),
            "unread_only": bool(self.unread_only),
        }

        def esc(s: str) -> str:
            return s.replace("'", "''")

        odata: list[str] = []
        if crit["has_attachments"]:
            odata.append("hasAttachments eq true")
        if crit["unread_only"]:
            odata.append("isRead eq false")
        if crit["sender"]:
            odata.append(f"from/emailAddress/address eq '{esc(crit['sender'])}'")
        if crit["subject"]:
            odata.append(f"contains(subject, '{esc(crit['subject'])}')")
        if crit["body"]:
            odata.append(f"contains(body/content, '{esc(crit['body'])}')")
        if crit["to"]:
            odata.append(f"toRecipients/any(r:r/emailAddress/address eq '{esc(crit['to'])}')")
        if crit["cc"]:
            odata.append(f"ccRecipients/any(c:c/emailAddress/address eq '{esc(crit['cc'])}')")
        if crit["importance"] and crit["importance"] != "all":
            odata.append(f"importance eq '{esc(crit['importance'])}'")
        return odata, crit

    @staticmethod
    def _matches(msg: dict, crit: dict) -> bool:
        """Client-side equivalent of the OData filters (for the fallback path)."""
        frm = (((msg.get("from") or {}).get("emailAddress")) or {}).get("address", "").lower()
        subj = (msg.get("subject") or "").lower()
        body_text = ((msg.get("body") or {}).get("content") or msg.get("bodyPreview") or "").lower()
        tos = [a.lower() for a in _addr_list(msg.get("toRecipients"))]
        ccs = [a.lower() for a in _addr_list(msg.get("ccRecipients"))]
        imp = (msg.get("importance") or "").lower()
        if crit["has_attachments"] and not msg.get("hasAttachments"):
            return False
        if crit["unread_only"] and msg.get("isRead"):
            return False
        if crit["sender"] and crit["sender"] != frm:
            return False
        if crit["subject"] and crit["subject"] not in subj:
            return False
        if crit["body"] and crit["body"] not in body_text:
            return False
        if crit["to"] and crit["to"] not in tos:
            return False
        if crit["cc"] and crit["cc"] not in ccs:
            return False
        if crit["importance"] != "all" and imp != crit["importance"]:
            return False
        return True

    # ------------------------------------------------------------------
    # SharePoint single pull (manual / playground preview)
    # ------------------------------------------------------------------

    def _get_sharepoint_document(self) -> Message:
        """Download the first supported file from the selected SharePoint library/folder.

        Mirrors the email preview: returns one file as a Message carrying its temp ``file_path`` so
        the downstream IDP pipeline can process it. The published agent ingests the whole folder via
        the background SharePoint monitor (see TriggerService.sync_sharepoint_idp_monitors_for_agent).
        """
        from urllib.parse import quote
        from agentcore.components.tools.sharepoint_document import (
            GRAPH_BASE as SP_GRAPH_BASE,
            _acquire_token_sync,
            _get_sharepoint_config,
            _graph_get_sync,
            _request_sync,
            _resolve_drive_id_sync,
            _resolve_site_id_sync,
        )

        connector_id, _ = self._resolve_selected_connector()
        if not connector_id:
            self.status = "Error: no connector selected"
            return Message(text="No SharePoint connector selected. Pick one from the Connector dropdown.")

        config = _get_sharepoint_config(connector_id)
        if config is None:
            self.status = "Error: connector not found"
            return Message(text=f"SharePoint connector not found (id={connector_id}). It may have been deleted.")

        library = (self.sharepoint_library or "").strip() or "Shared Documents"
        folder = (self.sharepoint_folder or "").strip().strip("/")
        types_raw = (self.sharepoint_file_types or "").strip()
        norm_types = {t.strip().lower().lstrip(".") for t in types_raw.split(",") if t.strip()}

        try:
            access_token = _acquire_token_sync(config)
            site_id = _resolve_site_id_sync(config, access_token)
            drive_id = _resolve_drive_id_sync(site_id, library, access_token, config=config)
        except Exception as e:
            self.status = f"Error: {e}"
            return Message(text=f"Failed to connect to SharePoint: {e}")

        if folder:
            safe_path = quote(folder, safe="/")
            list_url = f"{SP_GRAPH_BASE}/drives/{drive_id}/root:/{safe_path}:/children"
        else:
            list_url = f"{SP_GRAPH_BASE}/drives/{drive_id}/root/children"

        try:
            resp = _graph_get_sync(
                list_url, access_token, params={"$top": "100", "$select": "id,name,size,file,folder"}, config=config
            )
        except Exception as e:
            self.status = f"Network error: {e}"
            return Message(text=f"Network error listing SharePoint folder: {e}")
        if resp.status_code != 200:
            self.status = f"Graph API error {resp.status_code}"
            return Message(text=f"Graph API error {resp.status_code}: {resp.text[:200]}")

        files = [i for i in resp.json().get("value", []) if "file" in i]
        if norm_types:
            files = [
                f for f in files
                if (f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "") in norm_types
            ]
        if not files:
            self.status = "No matching files found"
            return Message(text="No files matching the filter were found in the selected SharePoint folder.")

        item = files[0]
        item_id = item.get("id", "")
        filename = item.get("name", "document")
        content_url = f"{SP_GRAPH_BASE}/drives/{drive_id}/items/{quote(item_id, safe='')}/content"
        try:
            dl = _request_sync(
                "GET", content_url, access_token=access_token, config=config,
                headers={"Authorization": f"Bearer {access_token}"}, timeout=60,
            )
        except Exception as e:
            self.status = f"Download error: {e}"
            return Message(text=f"Failed to download '{filename}': {e}")
        if dl.status_code != 200:
            self.status = f"Download error {dl.status_code}"
            return Message(text=f"Failed to download '{filename}' ({dl.status_code}): {dl.text[:200]}")

        suffix = os.path.splitext(filename)[1] or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="idp_sp_") as f:
            f.write(dl.content)
            tmp_path = f.name

        self.status = f"Fetched '{filename}' from SharePoint ({library}/{folder or 'root'})"
        logger.info(f"[ConnectorInput] Downloaded SharePoint file {filename} → {tmp_path}")
        _text, _tokens = _native_text_from_bytes(dl.content, filename)
        return Message(
            text=_text,
            data={
                "file_path": tmp_path,
                "filename": filename,
                "tokens": _tokens,
                "source": "sharepoint_connector",
                "library": library,
                "folder": folder,
                "item_id": item_id,
            },
        )

    # ------------------------------------------------------------------
    # OneDrive single pull (manual / playground preview)
    # ------------------------------------------------------------------

    def _onedrive_token(self, config: dict, acct: dict) -> str:
        """Return a valid OneDrive access token, refreshing via /common when expired."""
        import time as _time
        import httpx

        access_token = acct.get("access_token", "")
        expires_at = acct.get("token_expires_at", 0)
        if access_token and _time.time() < (expires_at - 60):
            return access_token

        refresh_token = acct.get("refresh_token", "")
        if not refresh_token:
            raise ValueError("OneDrive token expired and no refresh token. Re-link the account.")
        resp = httpx.post(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data={
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
                "scope": "Files.Read Files.ReadWrite User.Read offline_access",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ValueError(f"OneDrive token refresh failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        return data["access_token"]

    def _get_onedrive_document(self) -> Message:
        """Download the first supported file from the selected OneDrive folder (preview pull)."""
        import httpx
        from urllib.parse import quote as _quote
        from agentcore.components.tools.outlook_mail import _get_outlook_config

        connector_id, _ = self._resolve_selected_connector()
        if not connector_id:
            self.status = "Error: no connector selected"
            return Message(text="No OneDrive connector selected. Pick one from the Connector dropdown.")

        config = _get_outlook_config(connector_id)  # provider-agnostic loader (decrypts by provider)
        if config is None:
            self.status = "Error: connector not found"
            return Message(text=f"OneDrive connector not found (id={connector_id}). It may have been deleted.")

        accounts = config.get("linked_accounts", [])
        if not accounts:
            self.status = "Error: no linked account"
            return Message(text="No linked OneDrive account. Link one via OAuth on the Connectors page.")
        acct = accounts[0]

        try:
            access_token = self._onedrive_token(config, acct)
        except Exception as e:
            self.status = f"Error: {e}"
            return Message(text=str(e))

        folder = (self.onedrive_folder or "").strip().strip("/")
        types_raw = (self.onedrive_file_types or "").strip()
        norm_types = {t.strip().lower().lstrip(".") for t in types_raw.split(",") if t.strip()}

        if folder:
            safe = _quote(folder, safe="/")
            url = f"{GRAPH_BASE}/me/drive/root:/{safe}:/children"
        else:
            url = f"{GRAPH_BASE}/me/drive/root/children"

        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            resp = httpx.get(
                url, headers=headers,
                params={"$top": "100", "$select": "id,name,size,file,folder"}, timeout=20,
            )
        except Exception as e:
            self.status = f"Network error: {e}"
            return Message(text=f"Network error listing OneDrive folder: {e}")
        if resp.status_code != 200:
            self.status = f"Graph API error {resp.status_code}"
            return Message(text=f"Graph API error {resp.status_code}: {resp.text[:200]}")

        files = [i for i in resp.json().get("value", []) if "file" in i]
        if norm_types:
            files = [
                f for f in files
                if (f["name"].rsplit(".", 1)[-1].lower() if "." in f["name"] else "") in norm_types
            ]
        if not files:
            self.status = "No matching files found"
            return Message(text="No files matching the filter were found in the selected OneDrive folder.")

        item = files[0]
        item_id = item.get("id", "")
        filename = item.get("name", "document")
        try:
            dl = httpx.get(
                f"{GRAPH_BASE}/me/drive/items/{_quote(item_id, safe='')}/content",
                headers=headers, timeout=60, follow_redirects=True,
            )
        except Exception as e:
            self.status = f"Download error: {e}"
            return Message(text=f"Failed to download '{filename}': {e}")
        if dl.status_code != 200:
            self.status = f"Download error {dl.status_code}"
            return Message(text=f"Failed to download '{filename}' ({dl.status_code}): {dl.text[:200]}")

        suffix = os.path.splitext(filename)[1] or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="idp_od_") as f:
            f.write(dl.content)
            tmp_path = f.name

        self.status = f"Fetched '{filename}' from OneDrive ({folder or 'root'})"
        logger.info(f"[ConnectorInput] Downloaded OneDrive file {filename} → {tmp_path}")
        _text, _tokens = _native_text_from_bytes(dl.content, filename)
        return Message(
            text=_text,
            data={
                "file_path": tmp_path,
                "filename": filename,
                "tokens": _tokens,
                "source": "onedrive_connector",
                "folder": folder,
                "item_id": item_id,
            },
        )

    # ------------------------------------------------------------------
    # Gmail single pull (manual / playground preview)
    # ------------------------------------------------------------------

    def _gmail_token(self, config: dict, acct: dict) -> str:
        """Return a valid Gmail access token, refreshing via Google when expired."""
        import time as _time
        import httpx

        access_token = acct.get("access_token", "")
        expires_at = acct.get("token_expires_at", 0)
        if access_token and _time.time() < (expires_at - 60):
            return access_token

        refresh_token = acct.get("refresh_token", "")
        if not refresh_token:
            raise ValueError("Gmail token expired and no refresh token. Re-link the account.")
        resp = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": config.get("client_id", ""),
                "client_secret": config.get("client_secret", ""),
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            raise ValueError(f"Gmail token refresh failed ({resp.status_code}): {resp.text[:200]}")
        return resp.json()["access_token"]

    def _get_gmail_document(self) -> Message:
        """Fetch the first attachment from a matching Gmail message."""
        import base64 as _b64
        import httpx
        from agentcore.components.tools.outlook_mail import _get_outlook_config

        connector_id, _ = self._resolve_selected_connector()
        if not connector_id:
            self.status = "Error: no connector selected"
            return Message(text="No Gmail connector selected.")

        config = _get_outlook_config(connector_id)
        if config is None:
            self.status = "Error: connector not found"
            return Message(text=f"Gmail connector not found (id={connector_id}).")

        accounts = config.get("linked_accounts", [])
        if not accounts:
            self.status = "Error: no linked account"
            return Message(text="No linked Gmail account. Link one via OAuth on the Connectors page.")

        email_filter = (self.account_email or "").strip().lower()
        acct = next(
            (a for a in accounts if a.get("email", "").lower() == email_filter),
            accounts[0],
        ) if email_filter else accounts[0]

        try:
            access_token = self._gmail_token(config, acct)
        except Exception as e:
            self.status = f"Error: {e}"
            return Message(text=str(e))

        # Map folder name → Gmail label
        folder_raw = (self.folder or "inbox").strip().lower()
        label_id = _GMAIL_LABEL_MAP.get(folder_raw, folder_raw.upper())

        # Build Gmail search query from filters
        q_parts: list[str] = []
        if (self.filter_sender or "").strip():
            q_parts.append(f"from:{self.filter_sender.strip()}")
        if (self.filter_subject or "").strip():
            q_parts.append(f"subject:{self.filter_subject.strip()}")
        if (self.filter_body or "").strip():
            q_parts.append(self.filter_body.strip())
        if getattr(self, "filter_has_attachments", True):
            q_parts.append("has:attachment")
        if getattr(self, "unread_only", False):
            q_parts.append("is:unread")

        max_emails = max(1, int(self.max_emails or 10))
        list_params: dict = {"labelIds": label_id, "maxResults": max_emails}
        if q_parts:
            list_params["q"] = " ".join(q_parts)

        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            list_resp = httpx.get(f"{GMAIL_BASE}/users/me/messages", headers=headers, params=list_params, timeout=20)
        except Exception as e:
            self.status = f"Network error: {e}"
            return Message(text=f"Network error fetching Gmail messages: {e}")

        if list_resp.status_code != 200:
            self.status = f"Gmail API error {list_resp.status_code}"
            return Message(text=f"Gmail API error {list_resp.status_code}: {list_resp.text[:200]}")

        message_ids = [m["id"] for m in list_resp.json().get("messages", [])]
        if not message_ids:
            self.status = "No emails matching the filters found"
            return Message(text="No Gmail messages matching the filters were found.")

        for msg_id in message_ids:
            try:
                msg_resp = httpx.get(
                    f"{GMAIL_BASE}/users/me/messages/{msg_id}",
                    headers=headers,
                    params={"format": "full"},
                    timeout=20,
                )
            except Exception:
                continue
            if msg_resp.status_code != 200:
                continue

            msg = msg_resp.json()
            payload = msg.get("payload", {})
            hdrs = payload.get("headers", [])
            subject = next((h["value"] for h in hdrs if h.get("name", "").lower() == "subject"), "(no subject)")
            from_addr = next((h["value"] for h in hdrs if h.get("name", "").lower() == "from"), "")

            # Find attachment parts (filename set + attachmentId present)
            def _find_attachments(p: dict) -> list[dict]:
                results = []
                if p.get("filename") and p.get("body", {}).get("attachmentId"):
                    results.append(p)
                for part in p.get("parts", []):
                    results.extend(_find_attachments(part))
                return results

            att_parts = _find_attachments(payload)
            for att_part in att_parts:
                att_id = att_part["body"]["attachmentId"]
                filename = att_part.get("filename", "attachment")
                try:
                    att_resp = httpx.get(
                        f"{GMAIL_BASE}/users/me/messages/{msg_id}/attachments/{att_id}",
                        headers=headers,
                        timeout=30,
                    )
                except Exception:
                    continue
                if att_resp.status_code != 200:
                    continue

                data_b64 = att_resp.json().get("data", "")
                if not data_b64:
                    continue

                # Gmail uses base64url; pad and decode
                padded = data_b64 + "=" * (4 - len(data_b64) % 4)
                content_bytes = _b64.urlsafe_b64decode(padded)

                suffix = os.path.splitext(filename)[1] or ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="idp_gmail_") as f:
                    f.write(content_bytes)
                    tmp_path = f.name

                self.status = f"Fetched attachment '{filename}' from '{subject}' ({from_addr})"
                logger.info(f"[ConnectorInput] Downloaded Gmail attachment {filename} → {tmp_path}")
                _text, _tokens = _native_text_from_bytes(content_bytes, filename)
                return Message(
                    text=_text,
                    data={
                        "file_path": tmp_path,
                        "filename": filename,
                        "tokens": _tokens,
                        "source": "gmail_connector",
                        "subject": subject,
                        "from": from_addr,
                        "message_id": msg_id,
                    },
                )

        self.status = "No downloadable attachments found"
        return Message(text="No downloadable file attachments found in matching Gmail messages.")

    # ------------------------------------------------------------------
    # Main output method
    # ------------------------------------------------------------------

    async def get_document(self) -> Message:
        """Yield the document for this run.

        When the pipeline injected a ``document_id`` (an already-ingested doc — e.g. an email the
        monitor fetched + saved to storage, or any re-run), load THAT document from storage — exactly
        like the Document Upload node — so the emitted Message carries the IDP working-set
        (``document_id`` / ``agent_scope`` / ``file_name`` — what every downstream node's ``load_bytes``
        reads) plus the native text. Re-pulling from the connector here would instead emit only a temp
        ``file_path`` in ``.data`` (never the ``additional_kwargs`` working-set), so downstream nodes
        would see an empty ``idp`` payload and could not load the file — and it would fail outright when
        the node's connector field is empty.

        With no ``document_id`` (manual / playground preview) it fetches straight from the connector.
        """
        import asyncio

        doc_id = str(getattr(self, "document_id", "") or "").strip()
        if doc_id:
            return await self._document_from_ingested(doc_id)
        # Manual/preview pull: the connector fetch is sync (blocking httpx) — run it off the event loop.
        return await asyncio.to_thread(self._fetch_from_connector)

    async def _document_from_ingested(self, raw: str) -> Message:
        """Load an already-ingested document from storage and emit it via ``new_message`` — mirrors the
        Document Upload node so the connector/email pipeline path produces a downstream-loadable IDP
        Message (native text for a digital doc; a scanned doc yields empty text and is read by a vision
        model or a PaddleOCR node). This is what makes the published connector/email agent extract."""
        import asyncio
        from uuid import UUID as _UUID

        from sqlmodel import select

        from agentcore.services.database.models.idp.documents import IdpDocument, IdpProcessingJob
        from agentcore.services.deps import get_storage_service, session_scope
        from agentcore.services.idp import text_layer
        from agentcore.services.idp.graph_native.payload import new_message
        from agentcore.services.idp.storage_scope import scope_of

        doc_id = _UUID(raw)
        async with session_scope() as session:
            doc = await session.get(IdpDocument, doc_id)
            if doc is None:
                raise ValueError(f"IDP document {raw} not found.")
            job = (
                await session.exec(
                    select(IdpProcessingJob)
                    .where(IdpProcessingJob.document_id == doc_id)
                    .order_by(IdpProcessingJob.created_at.desc())
                )
            ).first()
            # Read the directory the doc was ACTUALLY written to from file_path (never rebuild from
            # agent_id — pre-rename docs live under the old folder). See storage_scope.scope_of.
            file_path = doc.file_path or ""
            agent_scope = scope_of(file_path)
            file_name = file_path.split("/", 1)[1] if "/" in file_path else file_path
            file_type = (doc.file_type or "").lower().lstrip(".")
            job_id = str(job.id) if job else None
            idp_agent_id = str(doc.agent_id)
            orig_name = doc.original_filename or file_name

        file_bytes = await get_storage_service().get_file(agent_scope, file_name)
        # PDF parse / page classification is CPU-bound — keep it off the event loop (mirrors Upload).
        overall_kind, page_status = await asyncio.to_thread(
            text_layer.classify_document, file_bytes, file_type, min_text_length=50
        )
        text, tokens = await asyncio.to_thread(_native_text_from_bytes, file_bytes, orig_name)

        self.status = f"{overall_kind} · {len(page_status)} page(s) (connector ingest)"
        return new_message(
            text=text,
            document_id=raw,
            job_id=job_id,
            agent_id=idp_agent_id,
            agent_scope=agent_scope,
            file_name=file_name,
            file_type=file_type,
            original_filename=orig_name,
            overall_kind=overall_kind,
            page_status=page_status,
            tokens=tokens,
            source="mail_connector",
        )

    def _fetch_from_connector(self) -> Message:
        """Fetch a document straight from the connector (manual / playground preview — no ingested
        ``document_id``). Routes to SharePoint, OneDrive, Gmail, or Outlook by the selected provider.
        Runs synchronously (blocking httpx); ``get_document`` calls it via ``asyncio.to_thread``.
        """
        import httpx

        _, provider = self._resolve_selected_connector()
        if provider in _SHAREPOINT_PROVIDERS:
            return self._get_sharepoint_document()
        if provider in _ONEDRIVE_PROVIDERS:
            return self._get_onedrive_document()
        if provider == "gmail":
            return self._get_gmail_document()

        try:
            config = self._get_config()
            acct = self._get_account(config)
            access_token = self._get_token(config, acct)
        except Exception as e:
            self.status = f"Error: {e}"
            return Message(text=f"Connector Input error: {e}")

        folder = (self.folder or "inbox").strip() or "inbox"
        max_emails = max(1, int(self.max_emails or 10))
        fetch_full_body = bool(self.fetch_full_body)
        odata, crit = self._build_filters()

        select = (
            "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "hasAttachments,importance,isRead,bodyPreview"
        )
        if fetch_full_body or crit["body"]:
            select += ",body"
        params: dict[str, str] = {"$top": str(max_emails), "$select": select, "$orderby": "receivedDateTime desc"}
        if odata:
            params["$filter"] = " and ".join(odata)

        url = f"{GRAPH_BASE}/me/mailFolders/{folder}/messages"

        def _get(p):
            return httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=p, timeout=20)

        try:
            resp = _get(params)
        except Exception as e:
            self.status = f"Network error: {e}"
            return Message(text=f"Network error fetching mail: {e}")

        if resp.status_code == 401:
            try:
                from agentcore.components.tools.outlook_mail import _refresh_token_sync
                access_token = _refresh_token_sync(config, acct, force=True)
                resp = _get(params)
            except Exception as e:
                self.status = f"Auth error: {e}"
                return Message(text=f"Authentication failed: {e}. Re-link the mailbox on the Connectors page.")

        # Graph rejected the OData filter (common for any(...)/contains on consumer accounts) →
        # refetch without $filter and filter client-side below.
        if resp.status_code in (400, 501) and odata:
            try:
                resp = _get({"$top": str(max_emails * 3), "$select": select, "$orderby": "receivedDateTime desc"})
            except Exception as e:
                self.status = f"Network error on retry: {e}"
                return Message(text=str(e))

        if resp.status_code != 200:
            self.status = f"Graph API error {resp.status_code}"
            return Message(text=f"Graph API error {resp.status_code}: {resp.text[:200]}")

        # Always apply the criteria client-side (a no-op when OData already filtered).
        messages = [m for m in resp.json().get("value", []) if self._matches(m, crit)][:max_emails]
        if not messages:
            self.status = "No emails matching the filters found"
            return Message(text="No emails matching the filters were found.")

        for msg in messages:
            msg_id = msg["id"]
            subject = msg.get("subject", "(no subject)")
            from_addr = (((msg.get("from") or {}).get("emailAddress")) or {}).get("address", "")

            # Follows attachment pagination + fetches large (>3 MB) attachments via /$value, so the
            # preview surfaces big files instead of "No downloadable attachments". Single-doc preview:
            # we still return the FIRST downloadable attachment of the first matching email.
            atts = _fetch_message_file_attachments_sync(access_token, msg_id)
            if not atts:
                continue  # None (list fetch failed) or [] (no downloadable files) → try next message

            att = atts[0]
            filename = att["name"]
            content_bytes = att["data"]
            suffix = os.path.splitext(filename)[1] or ".bin"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="idp_conn_") as f:
                f.write(content_bytes)
                tmp_path = f.name

            self.status = f"Fetched attachment '{filename}' from '{subject}' ({from_addr})"
            logger.info(f"[ConnectorInput] Downloaded {filename} → {tmp_path}")
            _text, _tokens = _native_text_from_bytes(content_bytes, filename)
            return Message(
                text=_text,
                data={
                    "file_path": tmp_path,
                    "filename": filename,
                    "tokens": _tokens,
                    "source": "mail_connector",
                    "subject": subject,
                    "from": from_addr,
                    "to": _addr_list(msg.get("toRecipients")),
                    "cc": _addr_list(msg.get("ccRecipients")),
                    "message_id": msg_id,
                    "received": msg.get("receivedDateTime", ""),
                },
            )

        self.status = "No downloadable attachments found"
        return Message(text="No downloadable file attachments found in matching emails.")
