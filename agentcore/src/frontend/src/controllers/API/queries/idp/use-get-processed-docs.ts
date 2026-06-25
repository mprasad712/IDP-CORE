import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface ProcessedDoc {
  id: string;
  agent_id: string;
  agent_name?: string | null;
  parent_document_id: string | null;
  original_filename: string;
  file_path: string;
  file_type: string;
  mime_type: string | null;
  file_size_bytes: number;
  page_count: number | null;
  checksum: string | null;
  source: string;
  predicted_type: string | null;
  status: string;
  overall_confidence: number | null;
  uploaded_by: string | null;
  processing_started_at: string | null;
  processing_completed_at: string | null;
  failed_at: string | null;
  created_at: string;
  updated_at: string;
  review_draft?: boolean; // reviewer saved edits as a draft (not yet submitted)
}

export interface ProcessedDocPage {
  items: ProcessedDoc[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface GetProcessedDocsParams {
  page?: number;
  size?: number;
  status_filter?: string;
  agent_id?: string;
  predicted_type?: string;
  confidence_min?: number;
  confidence_max?: number;
}

export const useGetProcessedDocs: useQueryFunctionType<
  GetProcessedDocsParams,
  ProcessedDocPage
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<ProcessedDocPage> => {
    const qp: Record<string, string> = {};
    if (params?.page !== undefined) qp.page = String(params.page);
    if (params?.size !== undefined) qp.size = String(params.size);
    if (params?.status_filter) qp.status_filter = params.status_filter;
    if (params?.agent_id) qp.agent_id = params.agent_id;
    if (params?.predicted_type) qp.predicted_type = params.predicted_type;
    if (params?.confidence_min !== undefined) qp.confidence_min = String(params.confidence_min);
    if (params?.confidence_max !== undefined) qp.confidence_max = String(params.confidence_max);

    const res = await api.get(`${getURL("IDP_PROCESSED_DOCS")}/`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<ProcessedDocPage, any> = query(
    [
      "useGetProcessedDocs",
      params?.status_filter ?? "all",
      params?.agent_id ?? "all",
      params?.page ?? 1,
      params?.size ?? 50,
    ],
    getFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
