import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface ReportRow {
  document_id: string;
  original_filename: string;
  agent_id: string;
  predicted_type: string | null;
  status: string;
  overall_confidence: number | null; // percentage 0–100
  uploaded_at: string;
  processing_started_at: string | null;
  pipeline_completed_at: string | null;
  processing_time_ms: number | null;
  reviewer: string | null;
  reviewed_at: string | null;
  review_final_status: string | null;
  header_count: number;
  line_item_count: number;
  has_log: boolean;
}

export interface ReportPage {
  items: ReportRow[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface GetReportParams {
  page?: number;
  size?: number;
  status_filter?: string;
  agent_id?: string;
  predicted_type?: string;
  created_start?: string;
  created_end?: string;
}

export const useGetReport: useQueryFunctionType<GetReportParams, ReportPage> = (
  params,
  options?,
) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<ReportPage> => {
    const qp: Record<string, string> = {};
    if (params?.page !== undefined) qp.page = String(params.page);
    if (params?.size !== undefined) qp.size = String(params.size);
    if (params?.status_filter) qp.status_filter = params.status_filter;
    if (params?.agent_id) qp.agent_id = params.agent_id;
    if (params?.predicted_type) qp.predicted_type = params.predicted_type;
    if (params?.created_start) qp.created_start = params.created_start;
    if (params?.created_end) qp.created_end = params.created_end;

    const res = await api.get(`${getURL("IDP_REPORTS")}/processed-docs`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<ReportPage, any> = query(
    [
      "useGetReport",
      params?.status_filter ?? "all",
      params?.agent_id ?? "all",
      params?.predicted_type ?? "all",
      params?.created_start ?? "",
      params?.created_end ?? "",
      params?.page ?? 1,
      params?.size ?? 25,
    ],
    getFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
