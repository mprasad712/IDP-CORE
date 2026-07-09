from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Any

from loguru import logger

from agentcore.custom.custom_node.node import Node
from agentcore.io import DropdownInput, HandleInput, IntInput, Output
from agentcore.schema.message import Message

# Formats with a real page model (or convertible to one). Single images are 1 page; Office docs are
# rendered to PDF by LibreOffice so they gain fixed pages.
_IMAGE_EXT = {"png", "jpg", "jpeg", "bmp", "tiff", "tif", "webp"}
_OFFICE_EXT = {"docx", "doc", "xlsx", "xls", "pptx", "ppt", "odt", "rtf"}


def _find_soffice() -> str | None:
    """Locate LibreOffice's headless binary, if installed."""
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for cand in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if os.path.exists(cand):
            return cand
    return None


def _libreoffice_to_pdf(file_bytes: bytes, ext: str) -> bytes | None:
    """Render an Office document to PDF via headless LibreOffice. Returns None if LibreOffice is not
    installed or the conversion fails (caller then passes the document through unsliced).

    Uses an isolated per-conversion user profile so concurrent conversions don't deadlock on the
    shared profile lock, plus a hard timeout so a hung soffice can't wedge the run."""
    soffice = _find_soffice()
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, f"input.{ext}")
        with open(src, "wb") as fh:
            fh.write(file_bytes)
        prof_uri = "file:///" + os.path.join(tmp, "profile").replace(os.sep, "/")
        cmd = [
            soffice, "--headless", "--norestore", "--nolockcheck",
            f"-env:UserInstallation={prof_uri}",
            "--convert-to", "pdf", "--outdir", tmp, src,
        ]
        try:
            subprocess.run(cmd, timeout=180, capture_output=True, check=True)
        except Exception as e:  # noqa: BLE001 — any failure → fall back to pass-through
            logger.warning(f"[PageSelector] LibreOffice conversion failed for .{ext}: {e}")
            return None
        out = os.path.join(tmp, "input.pdf")
        if os.path.exists(out):
            with open(out, "rb") as fh:
                return fh.read()
    return None


class IDPPageSelector(Node):
    """Slices a document to a specific page range BEFORE OCR/extraction — by slicing the actual pages,
    not the text. Normalizes the document to PDF (PDF as-is; images/TIFF via PyMuPDF; Office docs via
    LibreOffice), keeps only the selected pages, saves the sliced PDF, and routes the flow through it
    so downstream nodes only ever process those pages."""

    display_name = "Page Selector"
    description = "Slices a document to a specific page range before OCR or extraction."
    icon = "BookOpen"
    name = "IDPPageSelector"

    inputs = [
        HandleInput(name="document", display_name="Document", input_types=["Message"], required=True),
        DropdownInput(
            name="selection_mode",
            display_name="Selection Mode",
            options=["all", "first_n", "last_n", "range"],
            value="all",
            info="How to select pages. Defaults to all pages so documents are never silently truncated.",
        ),
        IntInput(name="first_n_pages", display_name="Number of Pages", value=3,
                 info="Pages to keep when mode is first_n or last_n."),
        IntInput(name="page_range_start", display_name="Range Start (page)", value=1, advanced=True),
        IntInput(name="page_range_end", display_name="Range End (page)", value=5, advanced=True),
    ]

    outputs = [Output(display_name="Selected Pages", name="selected_pages", method="selected_pages")]

    # ── page-picking ──────────────────────────────────────────────────────────
    def _pick_pages(self, total: int) -> list[int]:
        """0-based page indices to keep, per the selected mode."""
        mode = str(self.selection_mode or "all")
        n = max(1, int(self.first_n_pages or 1))
        if mode == "first_n":
            return list(range(0, min(n, total)))
        if mode == "last_n":
            return list(range(max(0, total - n), total))
        if mode == "range":
            start = max(1, int(self.page_range_start or 1)) - 1
            end = min(total, max(start + 1, int(self.page_range_end or total)))
            return list(range(start, end))
        return list(range(total))  # all

    # ── normalize any supported format to PDF bytes ───────────────────────────
    def _to_pdf(self, file_bytes: bytes, file_type: str) -> tuple[bytes | None, str]:
        """Return (pdf_bytes, note). pdf_bytes is None when the document can't be paginated (e.g. an
        Office doc with no LibreOffice installed) — the caller then passes it through unsliced."""
        import fitz

        ft = (file_type or "").lower().lstrip(".")
        if ft == "pdf":
            return file_bytes, ""
        if ft in _IMAGE_EXT:
            # PyMuPDF opens images (including multi-page TIFF) — convert to a PDF (1 page per frame).
            try:
                doc = fitz.open(stream=file_bytes, filetype=ft if ft != "jpg" else "jpeg")
                pdf = doc.convert_to_pdf()
                doc.close()
                return pdf, ""
            except Exception as e:  # noqa: BLE001
                return None, f"could not open image (.{ft}): {e}"
        if ft in _OFFICE_EXT:
            pdf = _libreoffice_to_pdf(file_bytes, ft)
            if pdf is None:
                return None, "page selection needs LibreOffice for Office documents — passed through unsliced"
            return pdf, ""
        return None, f"page selection not applicable to .{ft} — passed through"

    # ── slice a PDF to the selected pages ─────────────────────────────────────
    def _slice_pdf(self, pdf_bytes: bytes) -> tuple[bytes, list[int], int]:
        import fitz

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total = doc.page_count
        selected = self._pick_pages(total)
        new = fitz.open()
        for i in selected:
            new.insert_pdf(doc, from_page=i, to_page=i)
        out = new.tobytes()
        new.close()
        doc.close()
        return out, selected, total

    # ── output ────────────────────────────────────────────────────────────────
    async def selected_pages(self) -> Message:
        from agentcore.services.deps import get_storage_service
        from agentcore.services.idp.graph_native.payload import carry, effective_payload, load_bytes

        src = self.document
        payload = effective_payload(self, src)
        if not payload.get("document_id"):
            self.status = "no document in the flow — passthrough"
            return carry(src)

        file_type = str(payload.get("file_type") or "pdf").lower().lstrip(".")
        try:
            file_bytes = await load_bytes(payload)
        except Exception as e:  # noqa: BLE001
            self.status = f"could not load document ({e}) — passthrough"
            return carry(src)

        # 1. Normalize to PDF (blocking work → thread).
        pdf_bytes, note = await asyncio.to_thread(self._to_pdf, file_bytes, file_type)
        if pdf_bytes is None:
            # Can't paginate this format here → pass the ORIGINAL document through untouched so the
            # rest of the flow still runs (e.g. Office text extraction / single image).
            self.status = note or "page selection not applicable — passthrough"
            logger.info(f"[PageSelector] {payload.get('document_id')}: {self.status}")
            return carry(src)

        # 2. Slice to the selected pages.
        sliced_bytes, selected, total = await asyncio.to_thread(self._slice_pdf, pdf_bytes)
        if not selected:
            self.status = f"no pages in the selected range (of {total}) — passthrough"
            return carry(src)
        if len(selected) == total and file_type == "pdf":
            # Nothing actually removed and it was already a PDF — no need to write a new file.
            self.status = f"Selected {total}/{total} page(s) (all) — no slicing needed"
            return carry(src, selected_page_count=total, total_page_count=total)

        # 3. Persist the sliced PDF in its OWN sub-directory (separate from the uploaded originals).
        #    Local storage lays files out as <data_dir>/<agent_scope>/<file_name> and creates parent
        #    dirs, so a "sliced/…" file name puts sliced PDFs under <agent_scope>/sliced/ while uploads
        #    stay at <agent_scope>/. The sub-dir is configurable via IDP_SLICED_DIR (default "sliced").
        agent_scope = str(payload.get("agent_scope", "") or "")
        base = str(payload.get("file_name") or "document").rsplit(".", 1)[0].lstrip("/").split("/")[-1]
        sliced_dir = (os.getenv("IDP_SLICED_DIR", "sliced") or "sliced").strip("/")
        new_name = f"{sliced_dir}/selected_{base}.pdf" if sliced_dir else f"selected_{base}.pdf"
        try:
            await get_storage_service().save_file(agent_id=agent_scope, file_name=new_name, data=sliced_bytes)
        except Exception as e:  # noqa: BLE001 — if we can't persist, keep the original in the flow
            self.status = f"could not save sliced file ({e}) — passthrough"
            logger.warning(f"[PageSelector] {payload.get('document_id')}: {self.status}")
            return carry(src)

        # 4. Re-derive native text from the sliced PDF so the text path stays consistent with the file.
        new_text = ""
        try:
            from agentcore.services.idp import text_layer
            # CPU-bound PDF parsing — off the event loop
            _, tokens = await asyncio.to_thread(text_layer.extract_native_text, sliced_bytes, "pdf")
            new_text = " ".join(str(t.get("text", "")) for t in tokens)
        except Exception:  # noqa: BLE001
            pass

        self.status = f"Selected {len(selected)}/{total} page(s) → {new_name}"
        logger.info(f"[PageSelector] {payload.get('document_id')}: {self.status}")
        # Repoint the flow at the sliced PDF: downstream load_bytes() now reads the sliced file, so OCR
        # and the vision extractor only ever see the selected pages.
        return carry(
            src,
            text=new_text,
            file_name=new_name,
            file_type="pdf",
            selected_page_count=len(selected),
            total_page_count=total,
        )
