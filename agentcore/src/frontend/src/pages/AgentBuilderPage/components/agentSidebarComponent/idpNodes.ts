import type { APIClassType } from "@/types/api";

type IDPNodeEntry = { [key: string]: APIClassType };
type IDPData = { [category: string]: IDPNodeEntry };

// ─── field helpers ────────────────────────────────────────────────────────────

function strField(name: string, display_name: string, value = "", info = "", required = false, advanced = false) {
  return {
    _input_type: "StrInput",
    advanced,
    display_name,
    dynamic: false,
    info,
    list: false,
    name,
    placeholder: "",
    required,
    show: true,
    title_case: false,
    type: "str",
    value,
  };
}

function boolField(name: string, display_name: string, value = false, info = "", advanced = false) {
  return {
    _input_type: "BoolInput",
    advanced,
    display_name,
    dynamic: false,
    info,
    list: false,
    name,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "bool",
    value,
  };
}

function intField(name: string, display_name: string, value = 1, info = "", advanced = false) {
  return {
    _input_type: "IntInput",
    advanced,
    display_name,
    dynamic: false,
    info,
    list: false,
    name,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "int",
    value,
  };
}

function floatField(name: string, display_name: string, value = 0.5, info = "", advanced = false) {
  return {
    _input_type: "FloatInput",
    advanced,
    display_name,
    dynamic: false,
    info,
    list: false,
    name,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "float",
    value,
  };
}

function fileField(name: string, display_name: string, fileTypes: string[], info = "") {
  return {
    _input_type: "FileInput",
    advanced: false,
    display_name,
    dynamic: false,
    fileTypes,
    info,
    list: true,
    name,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "file",
    value: "",
  };
}

function dropdownField(name: string, display_name: string, options: string[], value: string, info = "", advanced = false) {
  return {
    _input_type: "DropdownInput",
    advanced,
    display_name,
    dynamic: false,
    info,
    list: false,
    name,
    options,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "str",
    value,
  };
}

function promptField(name: string, display_name: string, value = "", info = "") {
  return {
    _input_type: "MessageTextInput",
    advanced: false,
    display_name,
    dynamic: false,
    info,
    list: false,
    multiline: true,
    name,
    placeholder: "",
    required: false,
    show: true,
    title_case: false,
    type: "str",
    value,
  };
}

// ─── output helpers ───────────────────────────────────────────────────────────

function output(name: string, display_name: string, types: string[] = ["Message"]) {
  return {
    types,
    selected: types[0],
    name,
    display_name,
    method: name,
    cache: true,
    allows_loop: false,
    group_outputs: false,
    tool_mode: true,
    hidden: false,
  };
}

// ─── node definitions ─────────────────────────────────────────────────────────

export const IDP_NODES: IDPData = {
  // ── Input ──────────────────────────────────────────────────────────────────
  idp_input: {
    DocumentUpload: {
      display_name: "Document Upload",
      description: "Manual upload of single or multiple documents (PDF, image, Excel, Word).",
      icon: "Upload",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: ["files", "allow_multiple"],
      template: {
        files: fileField(
          "files",
          "Documents",
          ["pdf", "png", "jpg", "jpeg", "tiff", "bmp", "xlsx", "xls", "docx", "doc"],
          "Upload one or more documents to process.",
        ),
        allow_multiple: boolField(
          "allow_multiple",
          "Allow Multiple Files",
          true,
          "When enabled, multiple files can be uploaded at once.",
          true,
        ),
      },
      outputs: [output("document", "Document", ["Message"])],
    },

    ConnectorInput: {
      display_name: "Connector Input",
      description: "Pulls attachments from a connected source such as a mail connector.",
      icon: "Plug",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 1,
      base_classes: ["Message"],
      field_order: ["connector_name", "attachment_filter"],
      template: {
        connector_name: strField(
          "connector_name",
          "Connector",
          "",
          "Name of the configured connector (e.g. mail connector) to pull attachments from.",
          true,
        ),
        attachment_filter: strField(
          "attachment_filter",
          "File Type Filter",
          "pdf,png,jpg,docx",
          "Comma-separated list of allowed attachment extensions.",
          false,
          true,
        ),
      },
      outputs: [output("document", "Document", ["Message"])],
    },
  },

  // ── Pre-Processing ────────────────────────────────────────────────────────
  idp_preprocessing: {
    SkewCorrection: {
      display_name: "Skew Correction",
      description: "Detects and corrects skewed scanned pages before OCR.",
      icon: "RotateCcw",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: ["document", "threshold"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document to correct.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        threshold: floatField(
          "threshold",
          "Skew Threshold (°)",
          0.5,
          "Minimum skew angle (degrees) to trigger correction.",
          true,
        ),
      },
      outputs: [output("corrected_document", "Corrected Document", ["Message"])],
    },

    RotationCorrection: {
      display_name: "Rotation Correction",
      description: "Detects and corrects 90 / 180 / 270 degree page rotation.",
      icon: "RefreshCw",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 1,
      base_classes: ["Message"],
      field_order: ["document", "allowed_angles"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document to correct.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        allowed_angles: strField(
          "allowed_angles",
          "Allowed Angles",
          "90,180,270",
          "Comma-separated rotation angles to detect and correct.",
          false,
          true,
        ),
      },
      outputs: [output("corrected_document", "Corrected Document", ["Message"])],
    },
  },

  // ── OCR ───────────────────────────────────────────────────────────────────
  idp_ocr: {
    PaddleOCR: {
      display_name: "PaddleOCR",
      description: "OCR engine optimised for scanned documents; produces structured text blocks.",
      icon: "ScanText",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: ["document", "language", "use_gpu", "confidence_threshold"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Pre-processed document image to run OCR on.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        language: dropdownField(
          "language",
          "Language",
          ["en", "ch", "fr", "de", "es", "ar", "ja", "ko"],
          "en",
          "Primary language of the document text.",
        ),
        use_gpu: boolField("use_gpu", "Use GPU", false, "Enable GPU acceleration for OCR.", true),
        confidence_threshold: floatField(
          "confidence_threshold",
          "Confidence Threshold",
          0.7,
          "Minimum OCR confidence score to include a text block.",
          true,
        ),
      },
      outputs: [output("text_blocks", "Text Blocks", ["Message"])],
    },
  },

  // ── Extraction ────────────────────────────────────────────────────────────
  idp_extraction: {
    DynamicPrompting: {
      display_name: "Dynamic Prompting",
      description: "User writes a freeform prompt describing the fields to extract from the document.",
      icon: "MessageSquare",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Data"],
      field_order: ["document", "prompt", "model_name"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "OCR Text / Document",
          dynamic: false,
          info: "Text blocks from OCR or raw document.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        prompt: promptField(
          "prompt",
          "Extraction Prompt",
          "",
          "Describe the fields to extract. Example: 'Extract invoice number, date, vendor name, and total amount.'",
        ),
        model_name: strField("model_name", "LLM Model", "gpt-4o", "Model used for extraction.", false, true),
      },
      outputs: [output("extracted_data", "Extracted Data", ["Data"])],
    },

    NamedConfig: {
      display_name: "Named Config",
      description: "Selects a saved Field Configuration schema to drive structured extraction.",
      icon: "ClipboardList",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 1,
      base_classes: ["Data"],
      field_order: ["document", "config_name"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "OCR Text / Document",
          dynamic: false,
          info: "Text blocks from OCR or raw document.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        config_name: strField(
          "config_name",
          "Field Configuration",
          "",
          "Name of the saved Field Configuration schema to use.",
          true,
        ),
      },
      outputs: [output("extracted_data", "Extracted Data", ["Data"])],
    },

    Multimodal: {
      display_name: "Multimodal",
      description: "Vision LLM reads the document image directly without requiring an OCR step.",
      icon: "Layers",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 2,
      base_classes: ["Data"],
      field_order: ["document", "model_name", "prompt"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document Image",
          dynamic: false,
          info: "Raw document image (PDF, PNG, JPG) passed directly to the vision model.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        model_name: strField("model_name", "Vision LLM Model", "gpt-4o", "Vision-capable model for extraction.", false, true),
        prompt: promptField(
          "prompt",
          "Extraction Instruction",
          "",
          "Instruction sent to the vision model describing what to extract.",
        ),
      },
      outputs: [output("extracted_data", "Extracted Data", ["Data"])],
    },
  },

  // ── Rules ─────────────────────────────────────────────────────────────────
  idp_rules: {
    RulesConditions: {
      display_name: "Rules / Conditions",
      description: "Customisable rule builder with multiple condition rows and AND / OR logic.",
      icon: "GitBranch",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Data"],
      field_order: ["data", "logic_operator", "conditions"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Extracted Data",
          dynamic: false,
          info: "Extracted data to evaluate rules against.",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        logic_operator: dropdownField(
          "logic_operator",
          "Logic Operator",
          ["AND", "OR"],
          "AND",
          "Combine all condition rows with AND or OR logic.",
        ),
        conditions: promptField(
          "conditions",
          "Conditions (JSON)",
          "[]",
          'Array of condition objects, e.g. [{"field":"total","op":"gt","value":0}]',
        ),
      },
      outputs: [
        output("passed", "Passed", ["Data"]),
        output("failed", "Failed", ["Data"]),
      ],
    },
  },

  // ── Output ────────────────────────────────────────────────────────────────
  idp_output: {
    ProcessedDocsOutput: {
      display_name: "Processed Docs Output",
      description: "Terminal node that sends extracted results to the global Processed Docs page.",
      icon: "FileCheck",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      is_output: true,
      priority: 0,
      base_classes: [],
      field_order: ["data", "label"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Extracted Data",
          dynamic: false,
          info: "Final extracted (and optionally validated) data to store.",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        label: strField("label", "Output Label", "", "Optional label shown in the Processed Docs page.", false, true),
      },
      outputs: [],
    },
  },

  // ── Classification ────────────────────────────────────────────────────────
  idp_classification: {
    DocumentClassifier: {
      display_name: "Document Classifier",
      description: "Auto-detects document type with a confidence score.",
      icon: "Tag",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message", "Data"],
      field_order: ["document", "document_types", "confidence_threshold"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document to classify.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        document_types: strField(
          "document_types",
          "Expected Document Types",
          "invoice,receipt,contract,form,report",
          "Comma-separated list of document types the classifier should recognise.",
        ),
        confidence_threshold: floatField(
          "confidence_threshold",
          "Min Confidence Score",
          0.75,
          "Predictions below this threshold are marked as 'unknown'.",
          true,
        ),
      },
      outputs: [
        output("classified_document", "Classified Document", ["Message"]),
        output("document_type", "Document Type", ["Data"]),
      ],
    },
  },

  // ── Detection ─────────────────────────────────────────────────────────────
  idp_detection: {
    VisualElementDetection: {
      display_name: "Visual Element Detection",
      description: "Detects signatures, stamps, checkboxes, QR codes, logos, and handwritten annotations.",
      icon: "Crosshair",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message", "Data"],
      field_order: ["document", "detect_signatures", "detect_stamps", "detect_checkboxes", "detect_qr", "detect_logos", "detect_handwriting"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document image to analyse for visual elements.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        detect_signatures: boolField("detect_signatures", "Detect Signatures", true),
        detect_stamps: boolField("detect_stamps", "Detect Stamps / Seals", true),
        detect_checkboxes: boolField("detect_checkboxes", "Detect Checkboxes", true),
        detect_qr: boolField("detect_qr", "Detect QR / Barcodes", true),
        detect_logos: boolField("detect_logos", "Detect Logos", false, "", true),
        detect_handwriting: boolField("detect_handwriting", "Detect Handwriting", false, "", true),
      },
      outputs: [
        output("document_with_annotations", "Document + Annotations", ["Message"]),
        output("detected_elements", "Detected Elements", ["Data"]),
      ],
    },
  },

  // ── Validation ────────────────────────────────────────────────────────────
  idp_validation: {
    MathReconcile: {
      display_name: "Math Reconcile",
      description: "Validates extraction arithmetic and re-prompts the LLM when a mismatch is detected.",
      icon: "Calculator",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Data"],
      field_order: ["data", "tolerance", "max_retries"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Extracted Data",
          dynamic: false,
          info: "Extracted structured data containing numeric fields to reconcile.",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        tolerance: floatField(
          "tolerance",
          "Arithmetic Tolerance",
          0.01,
          "Maximum allowed absolute difference between computed and extracted totals.",
          true,
        ),
        max_retries: intField(
          "max_retries",
          "Max Re-prompt Retries",
          2,
          "Number of times to re-prompt the LLM before marking reconciliation as failed.",
          true,
        ),
      },
      outputs: [
        output("validated_data", "Validated Data", ["Data"]),
        output("reconciliation_report", "Reconciliation Report", ["Data"]),
      ],
    },
  },
};
