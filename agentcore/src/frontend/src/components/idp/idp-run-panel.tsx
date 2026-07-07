import IconComponent from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import { BuildStatus } from "@/constants/enums";
import { usePostCancelDocument } from "@/controllers/API/queries/idp/use-upload-and-process";
import useAgentStore from "@/stores/agentStore";
import { useIdpRunStore } from "@/stores/idpRunStore";
import { useNativeBuildHighlight } from "./use-native-build-highlight";

const IDP_DOCUMENT_TYPES = new Set(["DocumentUpload", "ConnectorInput"]);

/**
 * Floating "agent running" indicator on the Agent Builder canvas. Uploading + processing a document
 * lives in the Playground; this ONLY shows — as a pill at the bottom-center — while a run is actually
 * in progress, with a Stop button to cancel the detached backend run.
 *
 * The active run lives in `useIdpRunStore`, so this tracks a run started from the Playground even after
 * that modal is closed (the backend run is a detached background task and never stops on close), and it
 * also lights up the real canvas nodes as the graph engine executes them. Only for IDP agents.
 */
export default function IdpRunPanel() {
  const nodes = useAgentStore((s) => s.nodes);
  const updateBuildStatus = useAgentStore((s) => s.updateBuildStatus);
  const activeDocId = useIdpRunStore((s) => s.activeDocId);
  const activeFileName = useIdpRunStore((s) => s.activeFileName);
  const activeJobId = useIdpRunStore((s) => s.activeJobId);
  const runDone = useIdpRunStore((s) => s.runDone);
  const clearActiveRun = useIdpRunStore((s) => s.clearActiveRun);

  const isIDPAgent = nodes.some(
    (n: any) => n.data?.type && IDP_DOCUMENT_TYPES.has(n.data.type),
  );

  const { mutateAsync: cancelDocument } = usePostCancelDocument();

  // Light up the real canvas nodes from the native per-vertex SSE stream — survives Playground close.
  useNativeBuildHighlight(activeJobId);

  // Stop the running document: cancel the detached backend task and clear the indicator. Best-effort —
  // it clears even if the cancel call fails (the run may have just finished).
  const handleStop = () => {
    const did = activeDocId;
    clearActiveRun();
    updateBuildStatus(
      nodes.map((n: any) => n.id),
      BuildStatus.TO_BUILD,
    );
    if (did) cancelDocument({ document_id: did }).catch(() => {});
  };

  // Show ONLY while a run is in progress (active + not finished). No upload UI on the canvas.
  if (!isIDPAgent || !activeDocId || runDone) return null;

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
      <div className="pointer-events-auto flex items-center gap-3 rounded-full border border-border bg-background/95 px-4 py-2 shadow-lg backdrop-blur-sm">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
        </span>
        <span className="max-w-[260px] truncate text-sm font-medium text-foreground">
          Agent running{activeFileName ? ` — ${activeFileName}` : "…"}
        </span>
        <Button
          variant="destructive"
          size="sm"
          className="h-7 gap-1 rounded-full px-3 text-xs"
          data-testid="button-stop-idp-canvas"
          onClick={handleStop}
        >
          <IconComponent name="Square" className="h-3 w-3" strokeWidth={2} />
          Stop
        </Button>
      </div>
    </div>
  );
}
