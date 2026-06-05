from __future__ import annotations

import json

import httpx
from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, DataInput, DropdownInput, MessageTextInput, Output
from agentcore.schema.data import Data


class IDPWebhookOutput(Node):
    display_name = "Webhook Output"
    description = "POSTs the extracted data as JSON to an external API endpoint."
    icon = "Webhook"
    name = "IDPWebhookOutput"

    inputs = [
        DataInput(
            name="data",
            display_name="Extracted Data",
            required=True,
        ),
        MessageTextInput(name="url", display_name="Endpoint URL", required=True,
                         info="Full URL to send the data to."),
        DropdownInput(
            name="method",
            display_name="HTTP Method",
            options=["POST", "PUT", "PATCH"],
            value="POST",
            advanced=True,
        ),
        MessageTextInput(
            name="headers",
            display_name="Headers (JSON)",
            value='{"Content-Type": "application/json"}',
            advanced=True,
        ),
        BoolInput(
            name="include_metadata",
            display_name="Include Processing Metadata",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(display_name="Response", name="response", method="send"),
    ]

    def send(self) -> Data:
        url = (self.url or "").strip()
        if not url:
            self.status = "Error: no URL configured"
            return Data(data={"error": "no_url"})

        try:
            headers = json.loads(self.headers or "{}")
        except json.JSONDecodeError:
            headers = {"Content-Type": "application/json"}

        payload: dict = {}
        if isinstance(self.data, Data):
            payload = dict(self.data.data or {})
            if self.include_metadata:
                payload["_pipeline_metadata"] = {
                    "agent_id": getattr(self, "_graph_id", None),
                    "run_id": getattr(self, "_run_id", None),
                }
        elif isinstance(self.data, dict):
            payload = dict(self.data)

        method = (self.method or "POST").upper()
        try:
            resp = httpx.request(method, url, json=payload, headers=headers, timeout=30)
            self.status = f"HTTP {resp.status_code}"
            logger.info(f"[WebhookOutput] {method} {url} → {resp.status_code}")
            return Data(data={"status_code": resp.status_code, "response_text": resp.text[:500]})
        except httpx.RequestError as exc:
            self.status = f"Request error: {exc}"
            logger.error(f"[WebhookOutput] {exc}")
            return Data(data={"error": str(exc)})
