"""Merge all unresolved heads including IDP branch into single lineage.

Revision ID: idp_b05_merge_heads
Revises: (all current heads)
Create Date: 2026-06-06
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

revision: str = "idp_b05_merge_heads"
down_revision: Union[str, Sequence[str], None] = (
    "idp_b04_seed_templates",
    "20260317_agent_api_key",
    "84c14b0721d8",
    "ap1b2c3d4e5",
    "c2e5756285b4",
    "c9d0e1f2a3b4",
    "cl2a3b4c5d6e",
    "ds20260319002",
    "ev2b3c4d5e6f",
    "g3e1x0l9a7b2",
    "le1a2d3e4r5",
    "lf1a2b3c4d5f",
    "mcp20260319001",
    "mcp20260319002",
    "p5q6r7s8t9u0",
    "r1e2l3d4o5c6",
    "r2s3t4u5v6w7",
    "rb1a2b3c4d5",
    "s1t2u3v4w5x6",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
