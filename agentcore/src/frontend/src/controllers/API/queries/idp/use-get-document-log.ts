import { useQuery } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface FlowLogStep {
  ts?: string;
  step: string;
  status: string;            // ok | warn | error
  detail?: string;
  io?: {
    input?: Record<string, unknown>;
    output?: Record<string, unknown>;
  };
  [k: string]: unknown;
}

export interface DocumentLog {
  document_id: string;
  job_id: string | null;
  status: string;
  steps_completed: string[];
  error_message: string | null;
  processing_time_ms: number | null;
  log: FlowLogStep[];
}

/** GET /documents/{id}/log — the per-component processing trace (input received → OCR/text →
 *  classify → extract (what the LLM returned) → route → finalize), each step with an io payload. */
export const useGetDocumentLog = (
  documentId: string,
  options?: { enabled?: boolean },
) =>
  useQuery<DocumentLog>({
    queryKey: ["useGetDocumentLog", documentId],
    queryFn: async () => {
      const res = await api.get(`${getURL("IDP_DOCUMENTS")}/${documentId}/log`);
      return res.data;
    },
    enabled: !!documentId && (options?.enabled ?? true),
  });
