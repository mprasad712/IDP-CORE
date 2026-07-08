from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentcore.components._importing import import_mod

if TYPE_CHECKING:
    from agentcore.components.IDP.connector_input import IDPConnectorInput
    from agentcore.components.IDP.document_upload import IDPDocumentUpload
    from agentcore.components.IDP.processed_docs_output import IDPProcessedDocsOutput
    from agentcore.components.IDP.paddle_ocr import IDPPaddleOCR
    from agentcore.components.IDP.visual_element_detection import IDPVisualElementDetection
    from agentcore.components.IDP.math_reconcile import IDPMathReconcile
    from agentcore.components.IDP.approval_gate import IDPApprovalGate
    from agentcore.components.IDP.chunking_strategy import IDPChunkingStrategy
    from agentcore.components.IDP.condition_node import IDPConditionNode
    from agentcore.components.IDP.confidence_router import IDPConfidenceRouter
    from agentcore.components.IDP.document_classifier import IDPDocumentClassifier
    from agentcore.components.IDP.document_type_detector import IDPDocumentTypeDetector
    from agentcore.components.IDP.llm_extractor import IDPLLMExtractor
    from agentcore.components.IDP.merge_node import IDPMergeNode
    from agentcore.components.IDP.multi_branch_router import IDPMultiBranchRouter
    from agentcore.components.IDP.output_parser import IDPOutputParser
    from agentcore.components.IDP.page_selector import IDPPageSelector
    from agentcore.components.IDP.rules_conditions import IDPRulesConditions
    from agentcore.components.IDP.scan_corrector import IDPScanCorrector
    from agentcore.components.IDP.webhook_output import IDPWebhookOutput

_dynamic_imports = {
    "IDPConnectorInput": "connector_input",
    "IDPDocumentUpload": "document_upload",
    "IDPProcessedDocsOutput": "processed_docs_output",
    "IDPPaddleOCR": "paddle_ocr",
    "IDPVisualElementDetection": "visual_element_detection",
    "IDPMathReconcile": "math_reconcile",
    "IDPApprovalGate": "approval_gate",
    "IDPChunkingStrategy": "chunking_strategy",
    "IDPConditionNode": "condition_node",
    "IDPConfidenceRouter": "confidence_router",
    "IDPDocumentClassifier": "document_classifier",
    "IDPDocumentTypeDetector": "document_type_detector",
    "IDPLLMExtractor": "llm_extractor",
    "IDPMergeNode": "merge_node",
    "IDPMultiBranchRouter": "multi_branch_router",
    "IDPOutputParser": "output_parser",
    "IDPPageSelector": "page_selector",
    "IDPRulesConditions": "rules_conditions",
    "IDPScanCorrector": "scan_corrector",
    "IDPWebhookOutput": "webhook_output",
}

__all__ = list(_dynamic_imports.keys())


def __getattr__(attr_name: str) -> Any:
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    return list(__all__)
