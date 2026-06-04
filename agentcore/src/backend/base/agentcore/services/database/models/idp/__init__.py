"""IDP (Intelligent Document Processing) tables — all new, additive ``idp_*`` tables."""

from agentcore.services.database.models.idp.config import (
    IdpAgent,
    IdpAgentRule,
    IdpBulkProcessingBatch,
    IdpDocumentBatch,
    IdpFieldConfigHeader,
    IdpFieldConfigLineItem,
    IdpFieldConfiguration,
    IdpReviewSession,
)
from agentcore.services.database.models.idp.documents import (
    IdpDetectedElement,
    IdpDocument,
    IdpDocumentClassification,
    IdpDocumentSection,
    IdpEntityLink,
    IdpExtractedHeader,
    IdpExtractedLineItem,
    IdpProcessingJob,
)

__all__ = [
    # surface (config / agent / rules / review / batches)
    "IdpFieldConfiguration",
    "IdpFieldConfigHeader",
    "IdpFieldConfigLineItem",
    "IdpAgent",
    "IdpAgentRule",
    "IdpReviewSession",
    "IdpBulkProcessingBatch",
    "IdpDocumentBatch",
    # engine (documents / jobs / extracted / differentiators)
    "IdpDocument",
    "IdpProcessingJob",
    "IdpExtractedHeader",
    "IdpExtractedLineItem",
    "IdpDocumentClassification",
    "IdpDetectedElement",
    "IdpDocumentSection",
    "IdpEntityLink",
]
