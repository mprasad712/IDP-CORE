import type React from "react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import Loading from "@/components/ui/loading";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import {
  usePostCancelDocument,
  useUploadAndProcess,
} from "@/controllers/API/queries/idp/use-upload-and-process";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import useAgentStore from "@/stores/agentStore";
import useAlertStore from "@/stores/alertStore";
import { useIdpResultStore } from "@/stores/idpResultStore";
import { useIdpRunStore } from "@/stores/idpRunStore";
import {
  type IdpSession,
  useIdpSessionStore,
} from "@/stores/idpSessionStore";
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

// Child statuses that mean "this child run is finished" / "finished with extraction output".
const CHILD_TERMINAL = [
  "pending_review",
  "auto_approved",
  "reviewed",
  "skipped",
  "failed",
  "split",
];
const CHILD_EXTRACTED = ["pending_review", "auto_approved", "reviewed"];

/** One display row per split child (progress + final summary views). */
function splitChildRow(c: any) {
  return {
    file: c.original_filename,
    ...(c.page_range ? { pages: c.page_range } : {}),
    type: c.predicted_type ?? "unknown",
    status: CHILD_TERMINAL.includes(c.status) ? c.status : "processing",
    ...(c.overall_confidence != null ? { confidence: c.overall_confidence } : {}),
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
  const [docId, setDocId] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const { run: uploadAndProcess } = useUploadAndProcess();
  const { mutateAsync: cancelDocument } = usePostCancelDocument();
  const setActiveRun = useIdpRunStore((s) => s.setActiveRun);
  const clearActiveRun = useIdpRunStore((s) => s.clearActiveRun);
  // Persisted run history — survives closing the Playground and page reloads, so a result never
  // vanishes and the user can revisit any past document.
  const sessions = useIdpSessionStore((s) => s.sessions);
  const activeSessionId = useIdpSessionStore((s) => s.activeId);
  const upsertSession = useIdpSessionStore((s) => s.upsertSession);
  const selectSession = useIdpSessionStore((s) => s.selectSession);
  // Guards against double-polling the same document (handleRun vs the restore effect).
  const attachedRef = useRef<string | null>(null);

  const stopPolling = () => {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  // NOTE: the extracted result is published to `useIdpResultStore` (rendered in the MAIN chat area)
  // when a run finishes or a past session is opened — see `pollForResult` and the restore effect below.
  // It is intentionally NOT cleared on unmount, so closing the Playground doesn't wipe the result.

  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollCountRef = useRef(0);
  const MAX_POLLS = 240; // 240 × 2500 ms = 10 minutes
  // Split-run tracking: last seen children-done count (progress resets the poll budget) and the
  // human summary line shown in the side panel while/after a split run.
  const lastSplitDoneRef = useRef(-1);
  const [splitProgress, setSplitProgress] = useState<string | null>(null);
  const [splitSummary, setSplitSummary] = useState<string | null>(null);

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

      // ── Split parent: the parent is terminal, but the run isn't — follow the children. ──
      if (doc.status === "split") {
        const children: any[] = doc.children ?? [];
        const summary = doc.children_summary ?? {
          total: children.length, done: 0, extracted: 0, skipped: 0, failed: 0,
        };
        const allDone = summary.total > 0 && summary.done >= summary.total;
        if (!allDone) {
          // Children still running. Progress resets the poll budget so MAX_POLLS only fires on a
          // genuinely stuck run, not on a slow OCR-heavy bundle.
          if (lastSplitDoneRef.current !== summary.done) {
            lastSplitDoneRef.current = summary.done;
            pollCountRef.current = 0;
          }
          setSplitProgress(`${summary.done}/${summary.total} done`);
          setIdpResult({
            status: `split — processing ${summary.total} documents (${summary.done} done)`,
            documents: children.map(splitChildRow),
          });
          pollRef.current = setTimeout(() => pollForResult(documentId), 2500);
          return;
        }
        // All children terminal → final summary with each extracted child's fields embedded.
        attachedRef.current = null;
        const extractedKids = children
          .filter((c) => CHILD_EXTRACTED.includes(c.status))
          .slice(0, 10); // sanity cap on detail fetches
        const extractions: Record<string, any> = {};
        await Promise.all(
          extractedKids.map(async (c) => {
            try {
              const r = await api.get(`${getURL("IDP_PROCESSED_DOCS")}/${c.id}`);
              extractions[c.original_filename ?? c.id] = buildIdpDisplay(r.data);
            } catch {
              /* the summary row still shows the child */
            }
          }),
        );
        const summaryText =
          `Split into ${summary.total} documents — ${summary.extracted} extracted · ` +
          `${summary.skipped} skipped · ${summary.failed} failed`;
        const display: any = {
          status: "split — completed",
          summary: summaryText,
          documents: children.map(splitChildRow),
        };
        const keys = Object.keys(extractions);
        if (keys.length === 1) {
          // exactly one extracted child → surface its fields at top level, like a single document
          Object.assign(display, extractions[keys[0]], {
            status: "split — completed",
            summary: summaryText,
            documents: children.map(splitChildRow),
          });
        } else if (keys.length > 1) {
          display.extracted_documents = extractions;
        }
        setResult(doc);
        setState("done");
        setSplitProgress(null);
        setSplitSummary(summaryText);
        setIdpResult(display);
        upsertSession({ id: documentId, status: "done", result: display });
        return;
      }

      const terminal = [
        "extracted",
        "pending_review",
        "auto_approved",
        "reviewed",
        "failed",
        "skipped",
      ];
      if (terminal.includes(doc.status)) {
        attachedRef.current = null;
        if (doc.status === "failed") {
          const msg = doc.error_message || "Document processing failed";
          setState("error");
          setErrorMsg(msg);
          upsertSession({ id: documentId, status: "error", error: msg });
          setErrorData({ title: "Processing failed", list: [msg] });
        } else if (doc.status === "skipped") {
          // Show the specific reason the extractor recorded (e.g. multiple field configs with no
          // Document Classifier to route them), falling back to a generic message.
          const msg =
            doc.error_message ||
            "Document was skipped — no matching field configuration.";
          setState("error");
          setErrorMsg(msg);
          upsertSession({ id: documentId, status: "error", error: msg });
          setErrorData({ title: "Document skipped", list: [msg] });
        } else {
          const display = buildIdpDisplay(doc);
          setResult(doc);
          setState("done");
          setIdpResult(display);
          upsertSession({ id: documentId, status: "done", result: display });
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
      stopPolling(); // kill any in-flight poll (e.g. switching from an active session)
      setState("uploading");
      setErrorMsg("");
      pollCountRef.current = 0;
      lastSplitDoneRef.current = -1;
      setSplitProgress(null);
      setSplitSummary(null);
      const processResult = await uploadAndProcess(currentAgentId, file);
      const did = String(processResult.document_id);
      const jid = processResult.job_id ? String(processResult.job_id) : null;
      setDocId(did);
      // Share the run so the Builder-page panel keeps tracking it (timeline + native canvas
      // highlighting) even after this Playground modal is closed — the backend run is detached.
      setActiveRun(did, file.name, jid);
      // Record + open a persisted session so this run (and its result) survives a modal close/reload.
      upsertSession({
        id: did,
        jobId: jid,
        fileName: file.name,
        agentId: currentAgentId ?? null,
        status: "processing",
      });
      attachedRef.current = did; // handleRun owns this poll; the restore effect won't double it
      setState("processing");
      selectSession(did);
      pollForResult(did);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Upload failed";
      setState("error");
      setErrorMsg(msg);
      setErrorData({ title: "Upload failed", list: [msg] });
    }
  };

  // Stop button: cancel the running backend process and return to the upload view. The backend run is
  // a detached task — the cancel endpoint actually stops it (not just the UI). Best-effort: the UI
  // resets even if the network call fails.
  const handleStop = async () => {
    const did = docId;
    stopPolling();
    attachedRef.current = null;
    clearActiveRun();
    if (did) {
      upsertSession({ id: did, status: "error", error: "Cancelled by user." });
      cancelDocument({ document_id: did }).catch(() => {
        /* best-effort — the run may have just finished */
      });
    }
    setState("idle");
    setFile(null);
    setSplitProgress(null);
  };

  const reset = () => {
    stopPolling();
    attachedRef.current = null;
    setState("idle");
    setFile(null);
    setResult(null);
    setDocId(null);
    setErrorMsg("");
    lastSplitDoneRef.current = -1;
    setSplitProgress(null);
    setSplitSummary(null);
    clearIdpResult();
    selectSession(null); // back to the fresh upload + history view
  };

  // Attach the view to a session: re-render a finished/failed one, or resume polling an in-flight run
  // (the detached backend run kept going while the modal was closed). Idempotent per document.
  const showSession = (s: IdpSession) => {
    setDocId(s.id);
    setSplitProgress(null);
    // A restored split session carries its summary line inside the stored result display.
    setSplitSummary((s.result as any)?.summary ?? null);
    if (s.status === "done") {
      stopPolling();
      attachedRef.current = null;
      setResult(null);
      setState("done");
      if (s.result) setIdpResult(s.result);
    } else if (s.status === "error") {
      stopPolling();
      attachedRef.current = null;
      setState("error");
      setErrorMsg(s.error ?? "Processing failed");
    } else {
      setState("processing");
      if (attachedRef.current !== s.id) {
        attachedRef.current = s.id;
        pollCountRef.current = 0;
        stopPolling();
        pollForResult(s.id);
      }
    }
  };

  // On mount and whenever the active session changes (user picks one from history), show it.
  useEffect(() => {
    if (!activeSessionId) {
      stopPolling();
      attachedRef.current = null;
      setDocId(null);
      setResult(null);
      setState("idle");
      setIdpResult(null);
      return;
    }
    const s = sessions.find((x) => x.id === activeSessionId);
    if (s) showSession(s);
    // Intentionally only re-run when the active session id changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSessionId]);

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

      {/* In-progress state — a simple spinner. The per-node progress lights up on the CANVAS
          (Builder-page run panel), not here in the Playground. */}
      {(state === "uploading" || state === "processing") && (
        <div className="flex flex-col gap-3 rounded-lg border border-border bg-muted/30 px-4 py-4">
          <div className="flex flex-col items-center gap-3 py-2">
            <Loading className="h-6 w-6 text-primary" />
            <p className="text-sm font-medium text-foreground">
              {state === "uploading"
                ? "Uploading document…"
                : splitProgress
                  ? `Processing split documents — ${splitProgress}`
                  : "Processing document…"}
            </p>
          </div>
          {file && (
            <p className="max-w-[240px] truncate text-xs text-muted-foreground">
              {file.name}
            </p>
          )}
          <Button
            variant="outline"
            className="w-full text-sm"
            data-testid="button-stop-idp"
            disabled={!docId}
            onClick={handleStop}
          >
            <IconComponent
              name="Square"
              className="mr-1.5 h-3.5 w-3.5"
              strokeWidth={2}
            />
            Stop
          </Button>
        </div>
      )}

      {/* Done — the extracted result is rendered in the MAIN chat area (idpResultStore). */}
      {state === "done" && (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border bg-muted/30 px-4 py-5">
          <div className="flex items-center gap-2 text-center text-sm font-medium text-foreground">
            <IconComponent
              name="CircleCheck"
              className="h-5 w-5 shrink-0 text-emerald-500"
              strokeWidth={ICON_STROKE_WIDTH}
            />
            {splitSummary ?? "Document processed"}
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
