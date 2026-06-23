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

_EMAIL_PROVIDERS = {"outlook"}
_SHAREPOINT_PROVIDERS = {"sharepoint"}
_IDP_PROVIDERS = _EMAIL_PROVIDERS | _SHAREPOINT_PROVIDERS
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Email-only inputs — hidden until the selected connector's provider is an email provider.
_EMAIL_FIELDS = [
    "account_email", "folder", "max_emails", "poll_interval_seconds",
    "filter_sender", "filter_subject", "filter_body", "filter_to", "filter_cc",
    "filter_importance", "filter_has_attachments", "unread_only", "mark_as_read", "fetch_full_body",
]

# SharePoint-only inputs — hidden until the selected connector's provider is SharePoint.
_SHAREPOINT_FIELDS = ["sharepoint_library", "sharepoint_folder", "sharepoint_file_types"]


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


def _addr_list(recipients) -> list[str]:
    """Flatten a Graph recipients array → list of email addresses."""
    out = []
    for r in recipients or []:
        a = ((r or {}).get("emailAddress") or {}).get("address", "")
        if a:
            out.append(a)
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
                        "Microsoft Outlook (link a mailbox via OAuth) or SharePoint (set site/library), "
                        "connect it, then refresh this field."
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
            for fld in _EMAIL_FIELDS:
                if fld in build_config:
                    build_config[fld]["show"] = is_email
            for fld in _SHAREPOINT_FIELDS:
                if fld in build_config:
                    build_config[fld]["show"] = is_sharepoint
        return build_config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_config(self) -> dict:
        from agentcore.components.tools.outlook_mail import _get_outlook_config
        connector_id = _parse_connector_id(self.connector)
        if not connector_id:
            raise ValueError("No connector selected. Pick one from the Connector dropdown.")
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

        connector_id = _parse_connector_id(self.connector)
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
        return Message(
            text=tmp_path,
            data={
                "file_path": tmp_path,
                "filename": filename,
                "source": "sharepoint_connector",
                "library": library,
                "folder": folder,
                "item_id": item_id,
            },
        )

    # ------------------------------------------------------------------
    # Main output method (manual / playground single pull)
    # ------------------------------------------------------------------

    def get_document(self) -> Message:
        """Fetch a document for preview (manual pull).

        Routes to SharePoint folder reading when a SharePoint connector is selected, otherwise
        pulls the most recent matching email's first attachment from an Outlook connector.
        """
        import httpx

        if _parse_connector_provider(self.connector or "") in _SHAREPOINT_PROVIDERS:
            return self._get_sharepoint_document()

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

            att_url = f"{GRAPH_BASE}/me/messages/{quote(msg_id, safe='')}/attachments"
            try:
                att_resp = httpx.get(att_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
                if att_resp.status_code != 200:
                    continue
                attachments = att_resp.json().get("value", [])
            except Exception:
                continue

            for att in attachments:
                if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                    continue
                content_b64 = att.get("contentBytes", "")
                if not content_b64:
                    continue

                filename = att.get("name", "attachment")
                content_bytes = base64.b64decode(content_b64)
                suffix = os.path.splitext(filename)[1] or ".bin"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="idp_conn_") as f:
                    f.write(content_bytes)
                    tmp_path = f.name

                self.status = f"Fetched attachment '{filename}' from '{subject}' ({from_addr})"
                logger.info(f"[ConnectorInput] Downloaded {filename} → {tmp_path}")
                return Message(
                    text=tmp_path,
                    data={
                        "file_path": tmp_path,
                        "filename": filename,
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
