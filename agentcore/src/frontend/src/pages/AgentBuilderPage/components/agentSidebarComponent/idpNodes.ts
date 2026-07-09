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

// Registry-backed model picker (rendered by IDPModelDropdown via the `idp_model_fetch` flag).
// Stores "display | model_name | uuid"; the backend resolves the UUID (agent_config.py).
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

function output(
  name: string,
  display_name: string,
  types: string[] = ["Message"],
  // group_outputs TRUE renders each output as its OWN connector handle (what a router needs so you
  // can wire both branches); FALSE collapses them into a single handle with a dropdown selector.
  separateHandle = false,
) {
  return {
    types,
    selected: types[0],
    name,
    display_name,
    method: name,
    cache: true,
    allows_loop: false,
    group_outputs: separateHandle,
    tool_mode: true,
    hidden: false,
  };
}

// Gate a field so it only renders when the selected connector is an email connector
// (the IDPConnectorDropdown writes the chosen connector's provider into `connector_provider`).
// Both Outlook and Gmail share the same email polling/filter options, so both providers match.
// (RenderInputParameters' show_when accepts an array of accepted values.)
// Future SharePoint/OneDrive connectors won't show these email-only options.
const EMAIL_ONLY = { field: "connector_provider", value: ["outlook", "gmail"] } as const;
function emailOnly<T>(field: T): T & { show_when: typeof EMAIL_ONLY } {
  return { ...field, show_when: EMAIL_ONLY };
}

// Gate a field so it only renders when the selected connector is a SharePoint connector.
// SharePoint connectors process files from a chosen library/folder (no email filters apply).
const SHAREPOINT_ONLY = { field: "connector_provider", value: "sharepoint" } as const;
function sharepointOnly<T>(field: T): T & { show_when: typeof SHAREPOINT_ONLY } {
  return { ...field, show_when: SHAREPOINT_ONLY };
}

// Gate a field so it only renders when the selected connector is a OneDrive connector.
// OneDrive processes files from a chosen folder within the signed-in account's drive.
const ONEDRIVE_ONLY = { field: "connector_provider", value: "onedrive" } as const;
function onedriveOnly<T>(field: T): T & { show_when: typeof ONEDRIVE_ONLY } {
  return { ...field, show_when: ONEDRIVE_ONLY };
}

// ─── node definitions ─────────────────────────────────────────────────────────

export const IDP_NODES: IDPData = {
  // ── Input ──────────────────────────────────────────────────────────────────
  idp_input: {
    DocumentUpload: {
      display_name: "Document Upload",
      description:
        "Entry point for the uploaded document. The file is uploaded in the Playground (or via the API), which feeds it into this node — there is no manual upload on the canvas.",
      icon: "Upload",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: [],
      template: {},
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
      field_order: [
        "connector_name",
        // SharePoint (shown when a SharePoint connector is selected)
        "sharepoint_library",
        "sharepoint_folder",
        "sharepoint_file_types",
        // OneDrive (shown when a OneDrive connector is selected)
        "onedrive_folder",
        "onedrive_file_types",
        // Outlook / email
        "folder",
        "filter_subject",
        "filter_sender",
        "filter_has_attachments",
        "unread_only",
        "max_emails",
        "poll_interval_seconds",
        "account_email",
        "filter_body",
        "filter_to",
        "filter_cc",
        "filter_importance",
        "mark_as_read",
        "fetch_full_body",
      ],
      template: {
        connector_name: {
          _input_type: "StrInput",
          idp_connector_fetch: true,
          advanced: false,
          display_name: "Connector",
          dynamic: false,
          info: "Select a connected mail connector to pull attachments from. The email filters below apply when the published agent polls this mailbox.",
          list: false,
          name: "connector_name",
          placeholder: "Select a connector...",
          required: true,
          show: true,
          title_case: false,
          type: "str",
          value: "",
        },
        // Hidden state: the connector's provider (set by IDPConnectorDropdown on selection). Drives
        // show_when for the email-only fields so they appear only for Outlook/email connectors.
        connector_provider: {
          _input_type: "StrInput",
          advanced: false,
          display_name: "Connector Provider",
          dynamic: false,
          info: "",
          list: false,
          name: "connector_provider",
          placeholder: "",
          required: false,
          show: false,
          title_case: false,
          type: "str",
          value: "",
        },
        // SharePoint document-library options — the published agent's background monitor polls the
        // chosen library/folder and ingests new files into Processed Docs (ingest_mode=idp_pipeline).
        // sharepointOnly() => only shown when the selected connector is a SharePoint connector.
        sharepoint_library: sharepointOnly(strField(
          "sharepoint_library", "Library Name", "Shared Documents",
          "SharePoint document library to read from (e.g. 'Shared Documents').",
        )),
        sharepoint_folder: sharepointOnly(strField(
          "sharepoint_folder", "Folder Path", "",
          "Folder within the library to process (e.g. 'Invoices/2024'). Leave empty for the library root.",
        )),
        sharepoint_file_types: sharepointOnly(strField(
          "sharepoint_file_types", "File Type Filter", "pdf,png,jpg,docx",
          "Comma-separated file extensions to process. Leave empty for all supported types.",
        )),
        // OneDrive options — the published agent's background monitor polls the chosen folder in the
        // signed-in account's drive and ingests new files into Processed Docs (ingest_mode=idp_pipeline).
        onedrive_folder: onedriveOnly({
          // idp_onedrive_folder_fetch → custom renderer lists the account's folders to pick from
          // (still a combobox, so a deeper path can be typed manually).
          ...strField(
            "onedrive_folder", "Folder", "",
            "Pick a folder from your OneDrive to process. Leave empty for the drive root.",
          ),
          idp_onedrive_folder_fetch: true,
        }),
        onedrive_file_types: onedriveOnly(strField(
          "onedrive_file_types", "File Type Filter", "pdf,png,jpg,docx",
          "Comma-separated file extensions to process. Leave empty for all supported types.",
        )),
        // Email filters / polling — the published agent's background monitor reads these (subject /
        // sender / folder / poll interval, etc.). Modeled on the MiCore Outlook connector options;
        // the backend (sync_email_monitors_for_agent) reads each by these exact field names.
        // emailOnly() => only shown when the selected connector is Outlook/email.
        folder: emailOnly(dropdownField(
          "folder", "Mail Folder", ["inbox", "sentitems", "drafts", "archive"], "inbox",
          "Mailbox folder to poll for new email.",
        )),
        filter_subject: emailOnly(strField(
          "filter_subject", "Subject contains", "",
          "Only process emails whose subject contains this text.",
        )),
        filter_sender: emailOnly(strField(
          "filter_sender", "Filter by Sender", "",
          "Only process emails FROM this exact address.",
        )),
        filter_has_attachments: emailOnly(boolField(
          "filter_has_attachments", "Has Attachments Only", true,
          "Only process emails that have attachments.",
        )),
        unread_only: emailOnly(boolField(
          "unread_only", "Unread Only", false, "Only process unread emails.",
        )),
        max_emails: emailOnly(intField(
          "max_emails", "Max Emails", 10, "Maximum number of recent emails to scan per poll.",
        )),
        poll_interval_seconds: emailOnly(intField(
          "poll_interval_seconds", "Poll Interval (seconds)", 60,
          "How often the PUBLISHED agent checks for new mail (used by the background monitor).",
        )),
        account_email: emailOnly(strField(
          "account_email", "Account / Mailbox", "",
          "Linked mailbox to read from. Leave empty to use the first linked account.",
          false, true,
        )),
        filter_body: emailOnly(strField(
          "filter_body", "Body contains", "",
          "Only process emails whose body contains this text.", false, true,
        )),
        filter_to: emailOnly(strField(
          "filter_to", "Filter by Recipient (To)", "",
          "Only process emails sent TO this address.", false, true,
        )),
        filter_cc: emailOnly(strField(
          "filter_cc", "Filter by CC", "",
          "Only process emails with this address in CC.", false, true,
        )),
        filter_importance: emailOnly(dropdownField(
          "filter_importance", "Importance", ["all", "low", "normal", "high"], "all",
          "Only process emails of this importance.", true,
        )),
        mark_as_read: emailOnly(boolField(
          "mark_as_read", "Mark as Read After Processing", false,
          "Mark emails as read once processed.", true,
        )),
        fetch_full_body: emailOnly(boolField(
          "fetch_full_body", "Fetch Full Body", false,
          "Fetch the full email body (otherwise a short preview is used).", true,
        )),
      },
      outputs: [output("document", "Document", ["Message"])],
    },
  },

  // ── Pre-Processing ────────────────────────────────────────────────────────
  idp_preprocessing: {
    ScanCorrector: {
      display_name: "Scan Corrector",
      description: "Fixes scanned document images before OCR — toggle skew correction and rotation correction independently.",
      icon: "ScanLine",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: ["document", "fix_skew", "skew_threshold", "fix_rotation", "allowed_angles"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Scanned document image to correct.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        fix_skew: boolField(
          "fix_skew",
          "Fix Skew",
          true,
          "Detect and correct slight page tilt (sub-90° skew).",
        ),
        skew_threshold: floatField(
          "skew_threshold",
          "Skew Threshold (°)",
          0.5,
          "Minimum tilt angle in degrees to trigger skew correction.",
          true,
        ),
        fix_rotation: boolField(
          "fix_rotation",
          "Fix Rotation",
          true,
          "Detect and correct 90 / 180 / 270 degree page mis-rotation.",
        ),
        allowed_angles: strField(
          "allowed_angles",
          "Rotation Angles",
          "90,180,270",
          "Comma-separated angles to check for rotation correction.",
          false,
          true,
        ),
      },
      outputs: [output("corrected_document", "Corrected Document", ["Message"])],
    },

    DocumentTypeDetector: {
      display_name: "Document Type Detector",
      description: "Filters documents by extension, then detects whether the document is digital or scanned and routes to the matching output path.",
      icon: "FileScan",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 2,
      base_classes: ["Message"],
      field_order: ["document", "allowed_extensions", "skip_unmatched", "digital_label", "scanned_label", "min_text_length"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Incoming document to inspect.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        allowed_extensions: strField(
          "allowed_extensions",
          "Allowed Extensions",
          "pdf,png,jpg,jpeg,tiff,bmp,xlsx,xls,docx,doc",
          "Comma-separated list of file extensions to accept. Documents with other extensions are skipped.",
        ),
        skip_unmatched: boolField(
          "skip_unmatched",
          "Skip Unmatched Extensions",
          true,
          "When enabled, documents whose extension is not in the allowed list are silently dropped. When disabled, they pass through with a warning.",
        ),
        digital_label: strField(
          "digital_label",
          "Digital Label",
          "digital",
          "Value attached to documents that have a native text layer.",
          false,
          true,
        ),
        scanned_label: strField(
          "scanned_label",
          "Scanned Label",
          "scanned",
          "Value attached to documents that are image-only (no native text layer).",
          false,
          true,
        ),
        min_text_length: intField(
          "min_text_length",
          "Min Native Text Length",
          50,
          "Minimum character count in the native text layer to classify a document as digital.",
          true,
        ),
      },
      outputs: [
        output("digital_path", "Digital (True)", ["Message"], true),
        output("scanned_path", "Scanned (False)", ["Message"], true),
      ],
    },

    PageSelector: {
      display_name: "Page Selector",
      description: "Slices a document to a specific page range before OCR or extraction.",
      icon: "BookOpen",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 3,
      base_classes: ["Message"],
      field_order: ["document", "selection_mode", "first_n_pages", "page_range_start", "page_range_end"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Full document to slice.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        selection_mode: dropdownField(
          "selection_mode",
          "Selection Mode",
          ["first_n", "last_n", "range", "all"],
          "first_n",
          "How to select pages: first N pages, last N pages, a custom range, or all pages.",
        ),
        first_n_pages: intField(
          "first_n_pages",
          "Number of Pages",
          3,
          "Used when Selection Mode is 'first_n' or 'last_n'. Number of pages to keep.",
        ),
        page_range_start: intField(
          "page_range_start",
          "Range Start (page)",
          1,
          "Used when Selection Mode is 'range'. First page number (1-based, inclusive).",
          true,
        ),
        page_range_end: intField(
          "page_range_end",
          "Range End (page)",
          5,
          "Used when Selection Mode is 'range'. Last page number (1-based, inclusive).",
          true,
        ),
      },
      outputs: [output("selected_pages", "Selected Pages", ["Message"])],
    },

    ChunkingStrategy: {
      display_name: "Chunking Strategy",
      description: "Splits a long document into token-bounded chunks before sending to an LLM, respecting the model's context window.",
      icon: "Scissors",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 4,
      base_classes: ["Message"],
      field_order: ["document", "chunk_size_tokens", "overlap_tokens", "model_name"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document text to split into chunks.",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        chunk_size_tokens: intField(
          "chunk_size_tokens",
          "Chunk Size (tokens)",
          4096,
          "Maximum number of tokens per chunk. Keep below the target LLM's context limit.",
        ),
        overlap_tokens: intField(
          "overlap_tokens",
          "Overlap (tokens)",
          256,
          "Number of tokens repeated between adjacent chunks to preserve context across boundaries.",
        ),
        model_name: strField(
          "model_name",
          "Target LLM Model",
          "gpt-4o",
          "Model whose tokenizer is used to count tokens accurately.",
          false,
          true,
        ),
      },
      outputs: [output("chunks", "Document Chunks", ["Message"])],
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
    DocumentExtractor: {
      display_name: "AI Field Extractor",
      description: "Extracts structured fields from a document using an LLM. Choose between writing a custom prompt or selecting a saved Field Configuration.",
      icon: "BrainCircuit",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Data"],
      field_order: ["document", "llm", "config_name", "config_names", "input_mode", "unmatched_action"],
      template: {
        document: {
          _input_type: "HandleInput",
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
        llm: {
          _input_type: "HandleInput",
          advanced: false,
          display_name: "Language Model",
          dynamic: false,
          info: "Connect a Large Language Model node from the Models sidebar.",
          input_types: ["LanguageModel"],
          list: false,
          name: "llm",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
        config_name: {
          _input_type: "StrInput",
          idp_config_fetch: true,
          advanced: false,
          display_name: "Field Configuration",
          dynamic: false,
          info: "Select a single saved Field Configuration. Used when no multi-type routing is needed.",
          list: false,
          name: "config_name",
          placeholder: "Select a configuration...",
          required: false,
          show: true,
          title_case: false,
          type: "str",
          value: "",
        },
        config_names: {
          _input_type: "MultiselectInput",
          idp_config_fetch_multi: true,
          advanced: false,
          display_name: "Field Configurations (Multi-Type)",
          dynamic: false,
          info: "Select multiple Field Configurations for multi-type routing. When a Document Classifier is upstream, the config whose doc_type matches the classified type is used automatically. Documents with no matching config are marked 'skipped'.",
          list: true,
          name: "config_names",
          options: [],
          placeholder: "Select configurations...",
          required: false,
          show: true,
          title_case: false,
          type: "str",
          value: [],
        },
        input_mode: dropdownField(
          "input_mode",
          "Input Mode",
          ["auto", "text", "vision", "text_vision"],
          "auto",
          "How the model reads the document. Auto (recommended): a digital doc uses its text layer; a scanned doc uses OCR when a PaddleOCR node is on the canvas, otherwise a vision-capable model reads the page images (and it errors if there is no OCR node and the model can't see). Text: OCR/native text only. Vision: send page images to a vision model (scanned PDFs, NO OCR). Text + Vision: send both. Vision / Text + Vision require a model marked 'Supports vision' in the Model Catalogue.",
        ),
        unmatched_action: dropdownField(
          "unmatched_action",
          "On Unmatched Type",
          ["skip", "extract_anyway", "drop"],
          "skip",
          "What to do when a document's classified type matches none of the selected Field Configurations (e.g. a medical report in an invoice-only agent): skip (default) / extract anyway / drop. Requires a Document Classifier upstream.",
          true,
        ),
      },
      outputs: [output("extracted_data", "Extracted Data", ["Data"])],
    },


    // Vision extraction is NOT a separate node — it is driven by the "Input Mode" dropdown on the
    // AI Field Extractor above (auto/text/vision/text_vision). The pipeline renders page images and
    // sends them to a vision-capable model when the route is vision/text_vision (see pipeline.py).
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
        output("passed", "Passed", ["Data"], true),
        output("failed", "Failed", ["Data"], true),
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
          info: "Final results to store. Connect the AI Field Extractor here; you can also connect the Visual Element Detection 'Detected Elements' output — both are stored in the Processed Docs record.",
          input_types: ["Data"],
          list: true,
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

    WebhookOutput: {
      display_name: "Webhook Output",
      description: "POSTs the extracted data as JSON to an external API endpoint.",
      icon: "Webhook",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      is_output: true,
      priority: 1,
      base_classes: [],
      field_order: ["data", "url", "method", "headers", "include_metadata"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Extracted Data",
          dynamic: false,
          info: "Data to send as the request body (serialised to JSON).",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        url: strField("url", "Endpoint URL", "", "Full URL to POST the data to (e.g. https://api.example.com/ingest).", true),
        method: dropdownField(
          "method",
          "HTTP Method",
          ["POST", "PUT", "PATCH"],
          "POST",
          "HTTP method to use when calling the endpoint.",
          true,
        ),
        headers: promptField(
          "headers",
          "Headers (JSON)",
          '{"Content-Type": "application/json"}',
          "Additional HTTP headers as a JSON object.",
        ),
        include_metadata: boolField(
          "include_metadata",
          "Include Processing Metadata",
          false,
          "When enabled, attaches pipeline metadata (agent id, run id, timestamp) to the payload.",
          true,
        ),
      },
      outputs: [],
    },
  },

  // ── Classification ────────────────────────────────────────────────────────
  idp_classification: {
    DocumentClassifier: {
      display_name: "Document Classifier",
      description:
        "Uses an LLM to classify the document type from your Field Configurations and tags the document for downstream routing.",
      icon: "Tag",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 0,
      base_classes: ["Message"],
      field_order: ["document", "llm", "document_types", "confidence_threshold"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document to classify (e.g. OCR text blocks from PaddleOCR).",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        llm: {
          _input_type: "HandleInput",
          advanced: false,
          display_name: "Language Model",
          dynamic: false,
          info: "Connect a model from the Models sidebar. Without one, the classifier matches document types by keyword (offline).",
          input_types: ["LanguageModel"],
          list: false,
          name: "llm",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
        document_types: {
          _input_type: "MultiselectInput",
          idp_doc_type_fetch: true,
          advanced: false,
          display_name: "Document Types",
          dynamic: false,
          info: "Select the document types to classify against. Populated from your Field Configurations.",
          list: true,
          name: "document_types",
          options: [],
          placeholder: "Select types...",
          required: false,
          show: true,
          title_case: false,
          type: "str",
          value: [],
        },
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
      ],
    },

    DocumentSplitter: {
      display_name: "Document Splitter",
      description:
        "Splits a PDF that contains several documents (invoice + delivery note + T&Cs, KYC bundles) into one child document per sub-document; each is classified + extracted independently. Connect a model for smart (visual+textual) detection. Place after the Input node.",
      icon: "Scissors",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 1,
      base_classes: ["Message"],
      field_order: ["document", "llm", "use_vision"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Document",
          dynamic: false,
          info: "Document to split (connect the Document Upload or Connector Input output).",
          input_types: ["Message"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        llm: {
          _input_type: "HandleInput",
          advanced: false,
          display_name: "Language Model",
          dynamic: false,
          info: "Optional. A vision-capable model gives the most accurate boundary detection; without one, textual rules are used.",
          input_types: ["LanguageModel"],
          list: false,
          name: "llm",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
        use_vision: boolField(
          "use_vision",
          "Use Vision",
          true,
          "Send page images to the model (the visual signal) when a model is connected.",
          true,
        ),
      },
      outputs: [
        output("output_document", "Document", ["Message"]),
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
      // QR / barcodes use a built-in decoder (pyzbar) — no model needed. Signatures, checkboxes,
      // stamps, logos and handwriting are detected by a vision LLM: connect a vision-capable
      // Language Model node (or pick one via the Model dropdown).
      field_order: [
        "document", "llm",
        "detect_signatures", "detect_checkboxes", "detect_qr",
        "detect_stamps", "detect_logos",
      ],
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
        llm: {
          _input_type: "HandleInput",
          advanced: false,
          display_name: "Language Model",
          dynamic: false,
          info: "Connect a vision-capable Large Language Model node. Used to detect signatures, checkboxes, stamps, logos and handwriting from the page images. QR / barcodes use a built-in decoder and don't need a model.",
          input_types: ["LanguageModel"],
          list: false,
          name: "llm",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
        detect_signatures: boolField("detect_signatures", "Detect Signatures", true),
        detect_checkboxes: boolField("detect_checkboxes", "Detect Checkboxes", true),
        detect_qr: boolField("detect_qr", "Detect QR / Barcodes", true),
        detect_stamps: boolField("detect_stamps", "Detect Stamps", true),
        detect_logos: boolField("detect_logos", "Detect Logos", true),
      },
      outputs: [
        output("detected_elements", "Detected Elements", ["Data"]),
      ],
    },
  },

  // ── Flow Control ──────────────────────────────────────────────────────────
  idp_flow_control: {
    OutputParser: {
      display_name: "Output Parser",
      description: "Joins two branches (e.g. digital and scanned paths) back into a single flow.",
      icon: "Merge",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 1,
      base_classes: ["Message", "Data"],
      field_order: ["branch_a", "branch_b"],
      template: {
        branch_a: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Branch A",
          dynamic: false,
          info: "First branch input (e.g. true path).",
          input_types: ["Message", "Data"],
          list: false,
          name: "branch_a",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
        branch_b: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Branch B",
          dynamic: false,
          info: "Second branch input (e.g. false path).",
          input_types: ["Message", "Data"],
          list: false,
          name: "branch_b",
          placeholder: "",
          required: false,
          show: true,
          type: "other",
          value: "",
        },
      },
      outputs: [output("merged", "Merged", ["Message", "Data"])],
    },

    MultiBranchRouter: {
      display_name: "Multi-Branch Router",
      description: "Routes to up to 5 named output paths based on the value of a classification or type field.",
      icon: "Network",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 2,
      base_classes: ["Message", "Data"],
      field_order: ["document", "route_field", "route_1", "route_2", "route_3", "route_4", "route_5"],
      template: {
        document: {
          _input_type: "MessageInput",
          advanced: false,
          display_name: "Input",
          dynamic: false,
          info: "Document or data to route.",
          input_types: ["Message", "Data"],
          list: false,
          name: "document",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        route_field: strField(
          "route_field",
          "Route Field",
          "document_type",
          "Name of the field whose value determines which output path to take.",
        ),
        route_1: strField("route_1", "Route 1 Value", "invoice", "Field value that maps to output Route 1."),
        route_2: strField("route_2", "Route 2 Value", "receipt", "Field value that maps to output Route 2."),
        route_3: strField("route_3", "Route 3 Value", "contract", "Field value that maps to output Route 3.", false, true),
        route_4: strField("route_4", "Route 4 Value", "", "Field value that maps to output Route 4.", false, true),
        route_5: strField("route_5", "Route 5 Value", "", "Field value that maps to output Route 5.", false, true),
      },
      outputs: [
        output("route_1_out", "Route 1", ["Message", "Data"], true),
        output("route_2_out", "Route 2", ["Message", "Data"], true),
        output("route_3_out", "Route 3", ["Message", "Data"], true),
        output("route_4_out", "Route 4", ["Message", "Data"], true),
        output("route_5_out", "Route 5", ["Message", "Data"], true),
        output("unmatched", "Unmatched", ["Message", "Data"], true),
      ],
    },

    ApprovalGate: {
      display_name: "Approval Gate",
      description: "Reads the rule outcome and routes to auto-approved or pending-review output for the Processed Docs page.",
      icon: "ShieldCheck",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 3,
      base_classes: ["Data"],
      field_order: ["data", "approval_field", "approve_value"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Rules Result",
          dynamic: false,
          info: "Output from Rules / Conditions or Confidence Router.",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        approval_field: strField(
          "approval_field",
          "Approval Field",
          "rule_action",
          "Field name in the data that contains the approval decision.",
        ),
        approve_value: strField(
          "approve_value",
          "Auto-Approve Value",
          "auto_approve",
          "Field value that triggers the auto-approved path; anything else goes to pending review.",
        ),
      },
      outputs: [
        output("auto_approved", "Auto Approved", ["Data"], true),
        output("pending_review", "Pending Review", ["Data"], true),
      ],
    },

    ConfidenceRouter: {
      display_name: "Confidence Router",
      description: "Routes extracted data based on overall confidence score — high confidence to approval, low to review.",
      icon: "Gauge",
      documentation: "",
      beta: false,
      legacy: false,
      official: true,
      priority: 4,
      base_classes: ["Data"],
      field_order: ["data", "confidence_field", "threshold"],
      template: {
        data: {
          _input_type: "DataInput",
          advanced: false,
          display_name: "Extracted Data",
          dynamic: false,
          info: "Extracted data containing a confidence score field.",
          input_types: ["Data"],
          list: false,
          name: "data",
          placeholder: "",
          required: true,
          show: true,
          type: "other",
          value: "",
        },
        confidence_field: strField(
          "confidence_field",
          "Confidence Field",
          "confidence",
          "Name of the field that holds the overall confidence score (0–1).",
        ),
        threshold: floatField(
          "threshold",
          "Confidence Threshold",
          0.8,
          "Scores at or above this value go to High Confidence; below go to Low Confidence.",
        ),
      },
      outputs: [
        output("high_confidence", "High Confidence", ["Data"], true),
        output("low_confidence", "Low Confidence", ["Data"], true),
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
      field_order: ["data", "llm", "tolerance", "max_retries"],
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
        llm: {
          _input_type: "HandleInput",
          advanced: false,
          display_name: "Language Model",
          dynamic: false,
          info: "Connect a Language Model — REQUIRED to re-prompt and FIX a detected arithmetic mismatch. Without it, Math Reconcile can only detect the mismatch, not correct it.",
          input_types: ["LanguageModel"],
          list: false,
          name: "llm",
          placeholder: "",
          required: false,
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
