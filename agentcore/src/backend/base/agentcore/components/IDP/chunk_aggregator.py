from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import DataInput, DropdownInput, Output
from agentcore.schema.data import Data


class IDPChunkAggregator(Node):
    display_name = "Chunk Aggregator"
    description = "Combines per-chunk extraction results from Chunking Strategy into one unified document result."
    icon = "Combine"
    name = "IDPChunkAggregator"

    inputs = [
        DataInput(
            name="chunks_data",
            display_name="Chunk Results",
            info="Extracted data from each chunk.",
            is_list=True,
            required=True,
        ),
        DropdownInput(
            name="dedup_strategy",
            display_name="Deduplication Strategy",
            options=["keep_first", "keep_highest_confidence", "merge_all"],
            value="keep_highest_confidence",
            advanced=True,
            info="How to handle the same field appearing in multiple chunks.",
        ),
    ]

    outputs = [
        Output(display_name="Aggregated Data", name="aggregated_data", method="aggregated_data"),
    ]

    def aggregated_data(self):
        from agentcore.services.idp.graph_native.payload import carry, get_payload

        chunks = self.chunks_data
        if not chunks:
            self.status = "No chunks to aggregate."
            return Data(data={}, text="")
        if not isinstance(chunks, list):
            chunks = [chunks]

        strategy = self.dedup_strategy
        merged_headers: dict = {}
        merged_line_items: list = []
        rep = chunks[0]  # representative — carries document_id + the shared IDP payload

        for chunk in chunks:
            # Prefer the IDP working-set envelope (where the extractor puts `extracted`); fall back to
            # a bare Data/dict for back-compat with non-IDP producers.
            extracted = (get_payload(chunk).get("extracted") or {})
            if not extracted and isinstance(getattr(chunk, "data", None), dict):
                extracted = chunk.data
            headers = extracted.get("headers", {}) if isinstance(extracted, dict) else {}
            for field_name, field_data in headers.items():
                if field_name not in merged_headers:
                    merged_headers[field_name] = field_data
                elif strategy == "keep_first":
                    pass
                elif strategy == "keep_highest_confidence":
                    try:
                        new_c = float((field_data or {}).get("confidence", 0.0))
                        cur_c = float((merged_headers[field_name] or {}).get("confidence", 0.0))
                        if new_c > cur_c:
                            merged_headers[field_name] = field_data
                    except (TypeError, ValueError, AttributeError):
                        pass
                elif strategy == "merge_all":
                    merged_headers[field_name] = field_data  # last wins; line items carry the rest
            rows = extracted.get("line_items", []) if isinstance(extracted, dict) else []
            if isinstance(rows, list):
                merged_line_items.extend(rows)

        merged = {"headers": merged_headers, "line_items": merged_line_items}
        self.status = f"Aggregated {len(chunks)} chunk(s) → {len(merged_headers)} header field(s), {len(merged_line_items)} line item(s)"
        # Carry the merged extraction in the IDP payload so document_id + fields survive downstream + to the sink.
        return carry(rep, extracted=merged)
