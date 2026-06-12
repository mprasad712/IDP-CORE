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
import IconComponent from "../../../../../../components/common/genericIconComponent";
import { ICON_STROKE_WIDTH } from "../../../../../../constants/constants";
import { cn } from "../../../../../../utils/utils";

const IDP_DOCUMENT_TYPES = new Set(["DocumentUpload", "ConnectorInput"]);
const IDP_ACCEPT = ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.xlsx,.xls,.docx,.doc";

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

  const isIDPAgent = nodes.some(
    (n: any) => n.data?.type && IDP_DOCUMENT_TYPES.has(n.data.type),
  );

  const [file, setFile] = useState<File | null>(null);
  const [state, setState] = useState<ProcessingState>("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [result, setResult] = useState<any>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const { run: uploadAndProcess } = useUploadAndProcess();

  const stopPolling = () => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  const pollForResult = async (documentId: string) => {
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
      ];
      if (terminal.includes(doc.status)) {
        if (doc.status === "failed") {
          const msg = doc.error_message || "Document processing failed";
          setState("error");
          setErrorMsg(msg);
          setErrorData({ title: "Processing failed", list: [msg] });
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

      {/* JSON result */}
      {state === "done" && result && (
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Extracted Data
            </span>
            <Button variant="ghost" size="icon" className="h-6 w-6" onClick={reset}>
              <IconComponent
                name="X"
                className="h-3.5 w-3.5"
                strokeWidth={ICON_STROKE_WIDTH}
              />
            </Button>
          </div>
          <pre className="max-h-64 overflow-auto rounded-lg border border-border bg-muted/40 p-3 text-xs text-foreground custom-scroll">
            {JSON.stringify(
              {
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
              },
              null,
              2,
            )}
          </pre>
          <Button variant="outline" className="w-full text-sm" onClick={reset}>
            Process Another Document
          </Button>
        </div>
      )}
    </div>
  );
};

export default NoInputView;
