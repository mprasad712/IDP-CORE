import { useEffect } from "react";
import { BuildStatus } from "@/constants/enums";
import { customEventsUrl } from "@/customization/utils/custom-buildUtils";
import { customGetAccessToken } from "@/customization/utils/custom-get-access-token";
import useAgentStore from "@/stores/agentStore";
import { useIdpRunStore } from "@/stores/idpRunStore";

/**
 * NATIVE canvas highlighting for the `IDP_EXECUTION_ENGINE=native` engine.
 *
 * When IDP nodes run through the SAME generic `graph_langgraph` engine that simple agents use, the
 * backend streams per-vertex build events under the processing-job id via `GET /build/{jobId}/events`
 * (the exact same SSE contract as an interactive agent build). This hook consumes that stream and
 * lights the real canvas nodes: BUILDING as the wave reaches a node, BUILT when it finishes, ERROR on
 * failure — no polling. `useIdpTimelineNodes` derives the run timeline from the same build-status.
 */
export function useNativeBuildHighlight(jobId?: string | null) {
  const updateBuildStatus = useAgentStore((s) => s.updateBuildStatus);
  const setRunDone = useIdpRunStore((s) => s.setRunDone);

  useEffect(() => {
    if (!jobId) return;

    const controller = new AbortController();
    let cancelled = false;

    const applyEvent = (evt: any) => {
      const type = evt?.event;
      const data = evt?.data ?? {};
      if (type === "end_vertex") {
        const build = data.build_data ?? {};
        if (build.id) {
          updateBuildStatus(
            [build.id],
            build.valid === false ? BuildStatus.ERROR : BuildStatus.BUILT,
          );
        }
        // The wave moves on — light up the nodes about to run.
        const next = build.next_vertices_ids;
        if (Array.isArray(next) && next.length) {
          updateBuildStatus(next, BuildStatus.BUILDING);
        }
      } else if (type === "vertices_sorted") {
        // Mark every node the engine is about to run as queued.
        const ids: string[] = data.ids ?? data.to_run ?? [];
        if (Array.isArray(ids) && ids.length) {
          updateBuildStatus(ids, BuildStatus.BUILDING);
        }
      }
    };

    (async () => {
      try {
        const token = customGetAccessToken();
        const res = await fetch(customEventsUrl(jobId), {
          method: "GET",
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
          credentials: "include",
        });
        if (!res.ok || !res.body) return;

        // A live run's stream connected — clear any prior highlight so the user watches a clean
        // wave light up node-by-node (only for a real stream, so viewing an old session doesn't wipe
        // the canvas).
        const nodeIds = useAgentStore.getState().nodes.map((n: any) => n.id);
        if (nodeIds.length) updateBuildStatus(nodeIds, BuildStatus.TO_BUILD);

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!cancelled) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // Events are framed with a blank line ("\n\n"); keep the trailing partial frame.
          const frames = buffer.split("\n\n");
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const trimmed = frame.trim();
            if (!trimmed) continue;
            let evt: any;
            try {
              evt = JSON.parse(trimmed);
            } catch {
              continue;
            }
            if (evt?.event === "end") {
              cancelled = true;
              break;
            }
            applyEvent(evt);
          }
        }
        // Stream reached its end (or the "end" event) — the run finished.
        if (!controller.signal.aborted) setRunDone(true);
      } catch {
        // Aborted (unmount / new run) or the job has no native stream (graph/fixed engine) — ignore.
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [jobId, updateBuildStatus, setRunDone]);
}
