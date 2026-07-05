import type React from "react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import Loading from "@/components/ui/loading";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import { useUploadAndProcess } from "@/controllers/API/queries/idp/use-upload-and-process";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import useAgentStore from "@/stores/agentStore";
import useAlertStore from "@/stores/alertStore";
import { useIdpResultStore } from "@/stores/idpResultStore";
import IconComponent from "../../../../../../components/common/genericIconComponent";
import { ICON_STROKE_WIDTH } from "../../../../../../constants/constants";
import { cn } from "../../../../../../utils/utils";

const IDP_DOCUMENT_TYPES = new Set(["DocumentUpload", "ConnectorInput"]);
const IDP_ACCEPT = ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.xlsx,.xls,.docx,.doc";

// The element types the Visual Element Detection node can produce. We always emit ALL of
// these as keys so the output shows signature/stamp/logo even when the model found none
// (empty array) — not just the types that happened to be detected.
const DETECTION_KEYS = [
  "signature",
  "checkbox",
  "qr",
  "barcode",
  "stamp",
  "logo",
] as const;

/** Checkbox decoded_value is a JSON string {label, state}; legacy rows were plain "checked"/"unchecked". */
function parseCheckbox(decoded: any): { label: string | null; state: string } {
  try {
    const o = typeof decoded === "string" ? JSON.parse(decoded) : decoded;
    if (o && typeof o === "object" && ("state" in o || "label" in o)) {
      return { label: o.label ?? null, state: o.state ?? "unchecked" };
    }
  } catch {
    /* legacy plain-string value */
  }
  return {
    label: null,
    state: String(decoded ?? "").toLowerCase() === "checked" ? "checked" : "unchecked",
  };
}

/** Group a flat detected-elements list into { <type>: [...] } with every DETECTION_KEYS key present. */
function groupDetected(elements: any[]): Record<string, any[]> {
  const grouped: Record<string, any[]> = {};
  for (const k of DETECTION_KEYS) grouped[k] = [];
  for (const e of elements || []) {
    const key = e.element_type;
    // Handwriting detection was removed — ignore any legacy "annotation" rows.
    if (key === "annotation" || key === "handwriting") continue;
    if (key === "checkbox") {
      const { label, state } = parseCheckbox(e.decoded_value);
      (grouped[key] = grouped[key] || []).push({
        page: e.page_number,
        label,
        state,
        confidence: e.confidence,
      });
    } else {
      (grouped[key] = grouped[key] || []).push({
        page: e.page_number,
        value: e.decoded_value,
        confidence: e.confidence,
      });
    }
  }
  return grouped;
}

/** Shape the polled processed-doc into the display object shown in the main chat area. */
function buildIdpDisplay(result: any) {
  return {
    status: result.status,
    document_type: result.predicted_type,
    confidence: result.overall_confidence,
    headers: result.headers?.map((h: any) => ({
      field: h.field_name,
      value: h.extracted_value,
      confidence: h.confidence_score,
    })),
    line_items: result.line_items?.map((li: any) => ({
      row: li.row_index,
      column: li.column_name,
      value: li.extracted_value,
      confidence: li.confidence_score,
    })),
    // Grouped by type with every detectable type as a key (empty when none found).
    // Only present when a Visual Element Detection node ran for this document.
    ...(result.detected_elements?.length
      ? { detected_elements: groupDetected(result.detected_elements) }
      : {}),
  };
}

interface NoInputViewProps {
  isBuilding: boolean;
  sendMessage: (args: { repeat: number }) => Promise<void>;
  stopBuilding: () => void;
}

type ProcessingState = "idle" | "uploading" | "processing" | "done" | "error";

const NoInputView: React.FC<NoInputViewProps> = ({
  isBuilding,
  sendMessage,
  stopBuilding,
}) => {
  const nodes = useAgentStore((state) => state.nodes);
  const currentAgentId = useAgentsManagerStore((state) => state.currentAgentId);
  const setErrorData = useAlertStore((state) => state.setErrorData);
  const setIdpResult = useIdpResultStore((state) => state.setResult);
  const clearIdpResult = useIdpResultStore((state) => state.clearResult);

  const isIDPAgent = nodes.some(
    (n: any) => n.data?.type && IDP_DOCUMENT_TYPES.has(n.data.type),
  );

  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<ProcessingState>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [result, setResult] = useState<any>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { run: uploadAndProcess } = useUploadAndProcess();

  const stopPolling = () => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  // Publish the extracted result to the shared store so the MAIN chat area renders it
  // (instead of showing it docked at the bottom of the input). Clear it on unmount.
  useEffect(() => {
    if (state === "done" && result) {
      setIdpResult(buildIdpDisplay(result));
    }
  }, [state, result, setIdpResult]);
  useEffect(() => () => clearIdpResult(), [clearIdpResult]);

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCountRef = useRef(0);
  const MAX_POLLS = 240; // 240 × 2500 ms = 10 minutes

  const pollForResult = async (documentId: string) => {
    pollCountRef.current += 1;
    if (pollCountRef.current > MAX_POLLS) {
      setState("error");
      setErrorMsg("Processing timed out — check the backend logs for details");
      setErrorData({ title: "Processing timed out", list: ["The document has been processing for over 10 minutes. Check the backend logs."] });
      return;
    }
    try {
      const res = await api.get(
        `${getURL("IDP_PROCESSED_DOCS")}/${documentId}`,
      );
      const doc = res.data;
      const terminal = [
        "extracted",
        "pending_review",
        "auto_approved",
        "reviewed",
        "failed",
        "skipped",
        "split",
      ];
      if (terminal.includes(doc.status)) {
        if (doc.status === "failed") {
          const msg = doc.error_message || "Document processing failed";
          setState("error");
          setErrorMsg(msg);
          setErrorData({ title: "Processing failed", list: [msg] });
        } else if (doc.status === "skipped") {
          setState("error");
          setErrorMsg("Document type not in selected types — document was skipped");
          setErrorData({ title: "Document skipped", list: ["The document type was not in the configured list and was skipped."] });
        } else {
          setResult(doc);
          setState("done");
        }
        return;
      }
      pollRef.current = setTimeout(() => pollForResult(documentId), 2500);
    } catch (e: any) {
      setState("error");
      setErrorMsg(e?.message || "Failed to retrieve result");
    }
  };

  const handleRun = async () => {
    if (!file) return;
    try {
      setState("uploading");
      setErrorMsg("");
      pollCountRef.current = 0;
      const processResult = await uploadAndProcess(currentAgentId, file);
      setState("processing");
      pollForResult(String(processResult.document_id));
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Upload failed";
      setState("error");
      setErrorMsg(msg);
      setErrorData({ title: "Upload failed", list: [msg] });
    }
  };

  const reset = () => {
    stopPolling();
    setState("idle");
    setFile(null);
    setResult(null);
    setErrorMsg("");
    clearIdpResult();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) {
      setFile(f);
      e.target.value = "";
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) setFile(f);
  };

  // ── Non-IDP: original Run agent button ───────────────────────────────────
  if (!isIDPAgent) {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center">
        <div className="flex w-full flex-col items-center justify-center gap-3 rounded-md border border-input bg-muted p-2 py-4">
          {!isBuilding ? (
            <Button
              data-testid="button-send"
              className="font-semibold"
              onClick={async () => {
                await sendMessage({ repeat: 1 });
              }}
            >
              Run agent
            </Button>
          ) : (
            <Button
              onClick={stopBuilding}
              data-testid="button-stop"
              unstyled
              className="form-modal-send-button cursor-pointer bg-muted text-foreground hover:bg-secondary-hover dark:hover:bg-input"
            >
              <div className="flex items-center gap-2 rounded-md text-sm font-medium">
                Stop
                <Loading className="h-4 w-4" />
              </div>
            </Button>
          )}
        </div>
      </div>
    );
  }

  // ── IDP: document upload + JSON result ───────────────────────────────────
  return (
    <div className="flex w-full flex-col gap-3">
      {/* Upload area — hidden while processing or showing result */}
      {(state === "idle" || state === "error") && (
        <>
          <div
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-border bg-muted/30 px-4 py-6 text-center transition-colors hover:border-primary/50 hover:bg-muted/50",
              isDragging && "border-primary bg-primary/5",
            )}
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={handleDrop}
          >
            <IconComponent
              name="Upload"
              className="h-6 w-6 text-muted-foreground"
              strokeWidth={ICON_STROKE_WIDTH}
            />
            {file ? (
              <div className="flex items-center gap-2">
                <IconComponent
                  name="File"
                  className="h-4 w-4 text-primary"
                  strokeWidth={ICON_STROKE_WIDTH}
                />
                <span className="max-w-[220px] truncate text-sm font-medium text-foreground">
                  {file.name}
                </span>
                <button
                  className="text-muted-foreground hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    setFile(null);
                  }}
                >
                  <IconComponent
                    name="X"
                    className="h-3.5 w-3.5"
                    strokeWidth={ICON_STROKE_WIDTH}
                  />
                </button>
              </div>
            ) : (
              <>
                <p className="text-sm font-medium text-foreground">
                  Drop a document here or click to browse
                </p>
                <p className="text-xs text-muted-foreground">
                  PDF, PNG, JPG, TIFF, Excel, Word
                </p>
              </>
            )}
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={IDP_ACCEPT}
            className="hidden"
            onChange={handleFileChange}
          />
          {state === "error" && (
            <p className="text-center text-xs text-destructive">{errorMsg}</p>
          )}
          <Button
            className="w-full font-semibold"
            disabled={!file}
            onClick={handleRun}
          >
            <IconComponent
              name="Play"
              className="mr-1.5 h-4 w-4"
              strokeWidth={2}
            />
            Process Document
          </Button>
        </>
      )}

      {/* In-progress state */}
      {(state === "uploading" || state === "processing") && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-6">
          <Loading className="h-6 w-6 text-primary" />
          <p className="text-sm font-medium text-foreground">
            {state === "uploading" ? "Uploading document…" : "Processing document…"}
          </p>
          {file && (
            <p className="max-w-[240px] truncate text-xs text-muted-foreground">
              {file.name}
            </p>
          )}
        </div>
      )}

      {/* Done — the extracted result is rendered in the MAIN chat area (idpResultStore). */}
      {state === "done" && result && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-5">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <IconComponent
              name="CircleCheck"
              className="h-5 w-5 text-emerald-500"
              strokeWidth={ICON_STROKE_WIDTH}
            />
            Document processed
          </div>
          <p className="text-xs text-muted-foreground">
            Extracted data is shown above.
          </p>
          <Button variant="outline" className="w-full text-sm" onClick={reset}>
            Process Another Document
          </Button>
        </div>
      )}
    </div>
  );
};

export default NoInputView;
