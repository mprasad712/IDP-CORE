import { useMemo } from "react";
import { BuildStatus } from "@/constants/enums";
import useAgentStore from "@/stores/agentStore";
import type { IdpProgressNode } from "./idp-node-timeline";

const STATUS_MAP: Record<string, IdpProgressNode["status"]> = {
  [BuildStatus.BUILDING]: "running",
  [BuildStatus.BUILT]: "done",
  [BuildStatus.ERROR]: "failed",
};

/**
 * The IDP run timeline, derived from the LIVE canvas build-status (which the native SSE stream drives
 * via `updateBuildStatus`). Replaces the old server `/progress` poll: each canvas node maps to a
 * timeline row whose status follows the same BUILDING → BUILT/ERROR signal that lights the node.
 */
export function useIdpTimelineNodes(): IdpProgressNode[] {
  const nodes = useAgentStore((s) => s.nodes);
  const buildStatus = useAgentStore((s) => s.agentBuildStatus);

  return useMemo(
    () =>
      (nodes ?? []).map((n: any) => ({
        id: n.id,
        name: n.data?.node?.display_name ?? n.data?.type ?? n.id,
        status: STATUS_MAP[buildStatus?.[n.id]?.status as string] ?? "pending",
      })),
    [nodes, buildStatus],
  );
}

export default useIdpTimelineNodes;
