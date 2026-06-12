from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DropdownInput, HandleInput, IntInput, Output
from agentcore.schema.message import Message


class IDPPageSelector(Node):
    display_name = "Page Selector"
    description = "Slices a document to a specific page range before OCR or extraction."
    icon = "BookOpen"
    name = "IDPPageSelector"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
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

    outputs = [
        Output(display_name="Selected Pages", name="selected_pages", method="selected_pages"),
    ]

    def selected_pages(self) -> Message:
        src = self.document
        text = src.text if isinstance(src, Message) else str(src)

        # Split into pages by common page-break markers
        pages = [p.strip() for p in text.split("\x0c") if p.strip()]
        if not pages:
            pages = [text]

        mode = self.selection_mode
        n = max(1, self.first_n_pages)

        if mode == "first_n":
            selected = pages[:n]
        elif mode == "last_n":
            selected = pages[-n:]
        elif mode == "range":
            start = max(1, self.page_range_start) - 1
            end = max(start + 1, self.page_range_end)
            selected = pages[start:end]
        else:
            selected = pages

        self.status = f"Selected {len(selected)}/{len(pages)} page(s)"
        result_text = "\x0c".join(selected)

        if isinstance(src, Message):
            src.text = result_text
            if src.data is None:
                src.data = {}
            src.data["selected_page_count"] = len(selected)
            src.data["total_page_count"] = len(pages)
            return src
        return Message(text=result_text, data={"selected_page_count": len(selected), "total_page_count": len(pages)})
