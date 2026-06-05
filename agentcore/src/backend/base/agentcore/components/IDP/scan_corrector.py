from __future__ import annotations

from agentcore.custom.custom_node.node import Node
from agentcore.io import BoolInput, FloatInput, HandleInput, MessageTextInput, Output
from agentcore.schema.message import Message


class IDPScanCorrector(Node):
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

    def corrected_document(self) -> Message:
        src = self.document
        ops = []

        if self.fix_skew:
            ops.append(f"skew>{self.skew_threshold}°")
        if self.fix_rotation:
            ops.append(f"rotation[{self.allowed_angles}]")

        self.status = f"Applied: {', '.join(ops)}" if ops else "No corrections enabled"

        if isinstance(src, Message):
            if src.data is None:
                src.data = {}
            src.data["scan_corrections"] = ops
            return src
        return Message(text=str(src), data={"scan_corrections": ops})
