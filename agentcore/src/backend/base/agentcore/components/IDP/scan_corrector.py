from __future__ import annotations

import os

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, FloatInput, HandleInput, MessageTextInput, Output
from agentcore.schema.message import Message

# Formats the de-skew / rotation correction can actually process (raster pages). Office docs have no
# scanned-image page model, so they pass through untouched.
_IMAGE_EXT = {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}
_CORRECTABLE = {"pdf"} | _IMAGE_EXT


class IDPScanCorrector(Node):
    """Fixes scanned document images before OCR/extraction — de-skews slight page tilt and snaps 90/180/270
    mis-rotation, then saves the corrected file and routes the flow through it so downstream nodes only ever
    see the straightened pages. Real work: wraps ``services/idp/pre_processing`` (OpenCV)."""

    display_name = "Scan Corrector"
    description = "Fixes scanned document images before OCR — toggle skew correction and rotation correction independently."
    icon = "ScanLine"
    name = "IDPScanCorrector"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
        BoolInput(name="fix_skew", display_name="Fix Skew", value=True,
                  info="Detect and correct slight page tilt (sub-90° skew)."),
        FloatInput(name="skew_threshold", display_name="Skew Threshold (°)", value=0.5, advanced=True,
                   info="Minimum tilt angle in degrees to trigger skew correction."),
        BoolInput(name="fix_rotation", display_name="Fix Rotation", value=True,
                  info="Detect and correct 90 / 180 / 270 degree page mis-rotation."),
        MessageTextInput(name="allowed_angles", display_name="Rotation Angles", value="90,180,270", advanced=True,
                         info="Comma-separated angles to check for rotation correction."),
    ]

    outputs = [
        Output(display_name="Corrected Document", name="corrected_document", method="corrected_document"),
    ]

    async def corrected_document(self) -> Message:
        from agentcore.services.deps import get_storage_service
        from agentcore.services.idp.graph_native.payload import carry, effective_payload, load_bytes
        from agentcore.services.idp.pre_processing import (
            detect_and_correct_rotation,
            detect_and_correct_skew,
        )

        src = self.document
        payload = effective_payload(self, src)
        if not payload.get("document_id"):
            self.status = "no document in the flow — passthrough"
            return carry(src)

        if not (self.fix_skew or self.fix_rotation):
            self.status = "No corrections enabled — passthrough"
            return carry(src)

        file_type = str(payload.get("file_type") or "pdf").lower().lstrip(".")
        if file_type not in _CORRECTABLE:
            self.status = f"scan correction not applicable to .{file_type} — passthrough"
            logger.info(f"[ScanCorrector] {payload.get('document_id')}: {self.status}")
            return carry(src)

        try:
            file_bytes = await load_bytes(payload)
        except Exception as e:  # noqa: BLE001
            self.status = f"could not load document ({e}) — passthrough"
            return carry(src)

        # ── real correction: de-skew then snap rotation on the page images ──
        notes = []
        try:
            if self.fix_skew:
                file_bytes, angle = await detect_and_correct_skew(
                    file_bytes, file_type, float(self.skew_threshold or 0.1)
                )
                notes.append(f"skew {angle:+.2f}°")
            if self.fix_rotation:
                allowed = [int(a) for a in str(self.allowed_angles or "").replace(" ", "").split(",") if a.isdigit()]
                file_bytes, rot = await detect_and_correct_rotation(file_bytes, file_type, allowed or None)
                notes.append(f"rotation {rot}°")
        except Exception as e:  # noqa: BLE001 — correction is best-effort; never fail the document
            self.status = f"correction skipped ({e}) — passthrough"
            logger.warning(f"[ScanCorrector] {payload.get('document_id')}: {self.status}")
            return carry(src)

        # Persist the corrected file in its OWN sub-directory (separate from uploads + sliced), so it is
        # verifiable on disk. Configurable via IDP_CORRECTED_DIR (default "corrected").
        agent_scope = str(payload.get("agent_scope", "") or "")
        base = str(payload.get("file_name") or "document").rsplit(".", 1)[0].lstrip("/").split("/")[-1]
        corr_dir = (os.getenv("IDP_CORRECTED_DIR", "corrected") or "corrected").strip("/")
        out_ext = file_type if file_type in _IMAGE_EXT else "pdf"
        new_name = f"{corr_dir}/corrected_{base}.{out_ext}" if corr_dir else f"corrected_{base}.{out_ext}"
        try:
            await get_storage_service().save_file(agent_id=agent_scope, file_name=new_name, data=file_bytes)
        except Exception as e:  # noqa: BLE001
            self.status = f"could not save corrected file ({e}) — passthrough"
            logger.warning(f"[ScanCorrector] {payload.get('document_id')}: {self.status}")
            return carry(src)

        # Preserve any text an upstream OCR node already produced. Scan Corrector is designed to run
        # BEFORE OCR, but if it's wired AFTER PaddleOCR the source already carries real OCR text — and
        # re-deriving from the rasterized corrected file returns EMPTY and would WIPE it, breaking every
        # downstream text node (Chunking → extractor → 0 fields). So keep upstream text when present;
        # only re-derive a native text layer when the source has none (Scan Corrector ran first).
        upstream_text = (src.text if isinstance(src, Message) else "") or ""
        new_text = upstream_text
        if not upstream_text.strip():
            try:
                from agentcore.services.idp import text_layer

                _, tokens = text_layer.extract_native_text(file_bytes, out_ext)
                new_text = " ".join(str(t.get("text", "")) for t in tokens)
            except Exception:  # noqa: BLE001
                pass

        self.status = f"Corrected ({', '.join(notes)}) → {new_name}"
        logger.info(f"[ScanCorrector] {payload.get('document_id')}: {self.status}")
        # Repoint the flow at the corrected file so OCR / the extractor see the straightened pages.
        return carry(src, text=new_text, file_name=new_name, file_type=out_ext, scan_corrections=notes)
