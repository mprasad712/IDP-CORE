import { useRef, useState } from "react";
import IconComponent from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import { BuildStatus } from "@/constants/enums";
import {
  usePostCancelDocument,
  useUploadAndProcess,
} from "@/controllers/API/queries/idp/use-upload-and-process";
import useAgentsManagerStore from "@/stores/agentsManagerStore";
import useAgentStore from "@/stores/agentStore";
import useAlertStore from "@/stores/alertStore";
import { useIdpRunStore } from "@/stores/idpRunStore";
import { cn } from "@/utils/utils";
import IdpNodeTimeline from "./idp-node-timeline";
import { useIdpTimelineNodes } from "./use-idp-timeline-nodes";
import { useNativeBuildHighlight } from "./use-native-build-highlight";

const IDP_DOCUMENT_TYPES = new Set(["DocumentUpload", "ConnectorInput"]);
const IDP_ACCEPT =
  ".pdf,.png,.jpg,.jpeg,.tiff,.tif,.bmp,.xlsx,.xls,.docx,.doc";

/**
 * Floating panel on the Agent Builder canvas: upload a document and WATCH the real canvas nodes light
 * up (ring + spinner while running, green when done, red on error) as the graph engine executes them.
 *
 * The active run lives in `useIdpRunStore`, so this panel tracks a run started from EITHER here or the
 * Playground upload, and it keeps polling + highlighting even after the Playground modal is closed
 * (the backend run is a detached background task and never stops on close). Only shows for IDP agents.
 */
export default function IdpRunPanel() {
  const nodes = useAgentStore((s) => s.nodes);
  const updateBuildStatus = useAgentStore((s) => s.updateBuildStatus);
  const currentAgentId = useAgentsManagerStore((s) => s.currentAgentId);
  const setErrorData = useAlertStore((s) => s.setErrorData);
  const activeDocId = useIdpRunStore((s) => s.activeDocId);
  const activeFileName = useIdpRunStore((s) => s.activeFileName);
  const activeJobId = useIdpRunStore((s) => s.activeJobId);
  const runDone = useIdpRunStore((s) => s.runDone);
  const setActiveRun = useIdpRunStore((s) => s.setActiveRun);
  const clearActiveRun = useIdpRunStore((s) => s.clearActiveRun);

  const isIDPAgent = nodes.some(
    (n: any) => n.data?.type && IDP_DOCUMENT_TYPES.has(n.data.type),
  );

  const [open, setOpen] = useState(true);
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const { run: uploadAndProcess, isPending } = useUploadAndProcess();
  const { mutateAsync: cancelDocument } = usePostCancelDocument();

  // Light up the real canvas nodes from the native per-vertex SSE stream (the IDP nodes run through the
  // generic graph_langgraph engine) — survives Playground close, since this panel is mounted here.
  useNativeBuildHighlight(activeJobId);
  // The timeline is derived from that same live build-status.
  const timelineNodes = useIdpTimelineNodes();

  const resetHighlight = () =>
    updateBuildStatus(
      nodes.map((n: any) => n.id),
      BuildStatus.TO_BUILD,
    );

  const handleRun = async () => {
    if (!file || !currentAgentId) return;
    try {
      resetHighlight();
      const res = await uploadAndProcess(currentAgentId, file);
      setActiveRun(String(res.document_id), file.name, res.job_id ? String(res.job_id) : null);
    } catch (e: any) {
      const msg = e?.response?.data?.detail || e?.message || "Upload failed";
      setErrorData({ title: "Upload failed", list: [msg] });
    }
  };

  const reset = () => {
    setFile(null);
    clearActiveRun();
    resetHighlight();
  };

  // Stop the running document: cancel the detached backend task and clear the panel. Best-effort — the
  // panel resets even if the cancel call fails (the run may have just finished).
  const handleStop = () => {
    const did = activeDocId;
    clearActiveRun();
    resetHighlight();
    setFile(null);
    if (did) cancelDocument({ document_id: did }).catch(() => {});
  };

  if (!isIDPAgent) return null;

  const terminal = runDone;
  const engineLabel = "native engine";

  return (
    <div className="fixed bottom-4 left-4 z-30 w-72 overflow-hidden rounded-lg border border-border bg-background shadow-lg">
      <button
        className="flex w-full items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2 text-sm font-medium text-foreground"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="flex items-center gap-2">
          {activeDocId && !terminal ? (
            <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" />
          ) : (
            <IconComponent name="Play" className="h-4 w-4 text-primary" strokeWidth={2} />
          )}
          {activeDocId
            ? terminal
              ? "Document run"
              : "Agent running…"
            : "Run a document"}
        </span>
        <IconComponent
          name={open ? "ChevronDown" : "ChevronUp"}
          className="h-4 w-4 text-muted-foreground"
          strokeWidth={2}
        />
      </button>

      {open && (
        <div className="flex flex-col gap-2 p-3">
          {!activeDocId ? (
            <>
              <button
                className="flex flex-col items-center justify-center gap-1 rounded-md border-2 border-dashed border-border bg-muted/30 px-3 py-4 text-center hover:border-primary/50 hover:bg-muted/50"
                onClick={() => fileRef.current?.click()}
              >
                <IconComponent
                  name={file ? "File" : "Upload"}
                  className="h-5 w-5 text-muted-foreground"
                  strokeWidth={2}
                />
                <span className="max-w-[220px] truncate text-xs text-foreground">
                  {file ? file.name : "Click to choose a document"}
                </span>
              </button>
              <input
                ref={fileRef}
                type="file"
                accept={IDP_ACCEPT}
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) setFile(f);
                  e.target.value = "";
                }}
              />
              <Button
                className="w-full font-semibold"
                size="sm"
                disabled={!file || isPending}
                onClick={handleRun}
              >
                <IconComponent name="Play" className="mr-1.5 h-4 w-4" strokeWidth={2} />
                Run &amp; watch
              </Button>
            </>
          ) : (
            <>
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span className="truncate">{activeFileName ?? "document"}</span>
                {engineLabel && <span className="shrink-0">{engineLabel}</span>}
              </div>
              <IdpNodeTimeline
                nodes={timelineNodes}
                className="max-h-64 overflow-auto"
              />
              {terminal ? (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-xs"
                  onClick={reset}
                >
                  Run another
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full text-xs"
                  data-testid="button-stop-idp-canvas"
                  onClick={handleStop}
                >
                  <IconComponent
                    name="Square"
                    className="mr-1.5 h-3 w-3"
                    strokeWidth={2}
                  />
                  Stop
                </Button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
