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

    def aggregated_data(self) -> Data:
        chunks = self.chunks_data
        if not chunks:
            return Data(data={}, text="")

        if not isinstance(chunks, list):
            chunks = [chunks]

        strategy = self.dedup_strategy
        merged: dict = {}

        for chunk in chunks:
            chunk_data: dict = {}
            if isinstance(chunk, Data):
                chunk_data = chunk.data or {}
            elif isinstance(chunk, dict):
                chunk_data = chunk

            for key, value in chunk_data.items():
                # Skip internal pipeline metadata keys
                if key.startswith("chunk_") or key == "total_chunks" or key == "chunking_method":
                    continue

                if key not in merged:
                    merged[key] = value
                elif strategy == "keep_first":
                    pass  # already set
                elif strategy == "keep_highest_confidence":
                    existing_conf = merged.get(f"{key}_confidence", 0.0)
                    new_conf = chunk_data.get(f"{key}_confidence", 0.0)
                    try:
                        if float(new_conf) > float(existing_conf):
                            merged[key] = value
                            merged[f"{key}_confidence"] = new_conf
                    except (TypeError, ValueError):
                        pass
                elif strategy == "merge_all":
                    existing = merged[key]
                    if isinstance(existing, list):
                        existing.append(value)
                    else:
                        merged[key] = [existing, value]

        total_chunks = len(chunks)
        merged["_aggregated_from_chunks"] = total_chunks
        self.status = f"Aggregated {total_chunks} chunk(s) → {len(merged)} field(s)"
        return Data(data=merged)
