from __future__ import annotations

import re

from agentcore.custom.custom_node.node import Node
from agentcore.io import DropdownInput, HandleInput, IntInput, MessageTextInput, Output
from agentcore.schema.message import Message


class IDPChunkingStrategy(Node):
    display_name = "Chunking Strategy"
    description = (
        "Splits a long document into token-bounded chunks before sending to an LLM, "
        "respecting the model's context window."
    )
    icon = "Scissors"
    name = "IDPChunkingStrategy"

    inputs = [
        HandleInput(
            name="document",
            display_name="Document",
            input_types=["Message"],
            required=True,
        ),
        IntInput(
            name="chunk_size_tokens",
            display_name="Chunk Size (tokens)",
            value=4096,
            info="Maximum tokens per chunk.",
        ),
        IntInput(
            name="overlap_tokens",
            display_name="Overlap (tokens)",
            value=256,
            info="Tokens repeated between adjacent chunks to preserve boundary context.",
        ),
        DropdownInput(
            name="chunking_method",
            display_name="Chunking Method",
            options=["fixed_token", "semantic", "section_based"],
            value="fixed_token",
            info="fixed_token: hard token split; semantic: split at paragraph/sentence; section_based: split at headings.",
        ),
        MessageTextInput(
            name="model_name",
            display_name="Target LLM Model",
            value="gpt-4o",
            advanced=True,
            info="Model whose tokenizer is used for accurate token counting.",
        ),
    ]

    outputs = [
        Output(display_name="Document Chunks", name="chunks", method="chunks", output_types=["Message"], is_list=True),
    ]

    # ── tokenisation ──────────────────────────────────────────────────────────

    def _count_tokens(self, text: str) -> int:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.model_name)
            return len(enc.encode(text))
        except Exception:
            # Fallback: rough estimate (1 token ≈ 4 chars)
            return max(1, len(text) // 4)

    def _encode(self, text: str) -> list[int]:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.model_name)
            return enc.encode(text)
        except Exception:
            # Fallback: treat each word as one token
            return text.split()  # type: ignore[return-value]

    def _decode(self, tokens) -> str:
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(self.model_name)
            return enc.decode(tokens)
        except Exception:
            return " ".join(tokens)  # type: ignore[arg-type]

    # ── splitting strategies ──────────────────────────────────────────────────

    def _split_fixed_token(self, text: str) -> list[str]:
        tokens = self._encode(text)
        size = max(1, self.chunk_size_tokens)
        overlap = min(self.overlap_tokens, size - 1)
        step = size - overlap
        chunks = []
        for i in range(0, len(tokens), step):
            chunk_tokens = tokens[i: i + size]
            chunk_text = self._decode(chunk_tokens)
            if chunk_text.strip():
                chunks.append(chunk_text)
        return chunks

    def _split_semantic(self, text: str) -> list[str]:
        # Split on double newlines (paragraphs) then greedily fill chunks up to token limit
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        return self._pack_units(paragraphs)

    def _split_section_based(self, text: str) -> list[str]:
        # Split on markdown-style headings or lines that look like section headers
        sections = re.split(r"(?m)^(?=#{1,4}\s|\n[A-Z][A-Z\s]{4,}\n)", text)
        sections = [s.strip() for s in sections if s.strip()]
        if len(sections) <= 1:
            return self._split_semantic(text)
        return self._pack_units(sections)

    def _pack_units(self, units: list[str]) -> list[str]:
        size = max(1, self.chunk_size_tokens)
        overlap = min(self.overlap_tokens, size // 2)
        chunks: list[str] = []
        current_tokens: list = []

        for unit in units:
            unit_tokens = self._encode(unit)
            if len(current_tokens) + len(unit_tokens) > size:
                if current_tokens:
                    chunks.append(self._decode(current_tokens))
                # Start next chunk with overlap from previous
                current_tokens = current_tokens[-overlap:] + unit_tokens if overlap else unit_tokens
            else:
                current_tokens.extend(unit_tokens)

        if current_tokens:
            chunks.append(self._decode(current_tokens))
        return chunks

    # ── main method ───────────────────────────────────────────────────────────

    def chunks(self) -> list[Message]:
        src = self.document
        text = src.text if isinstance(src, Message) else str(src)
        base_data = src.data if isinstance(src, Message) and src.data else {}

        method = self.chunking_method
        if method == "semantic":
            chunk_texts = self._split_semantic(text)
        elif method == "section_based":
            chunk_texts = self._split_section_based(text)
        else:
            chunk_texts = self._split_fixed_token(text)

        total = len(chunk_texts)
        self.status = f"Produced {total} chunk(s) via {method}"

        return [
            Message(
                text=chunk_text,
                data={**base_data, "chunk_index": idx, "total_chunks": total, "chunking_method": method},
            )
            for idx, chunk_text in enumerate(chunk_texts)
        ]
