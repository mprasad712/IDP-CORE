import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface DocumentLog {
  id: string;
  original_filename: string;
  status: string;
  created_at: string;
  agent_id: string;
  agent_name: string | null;
}

export interface DocumentLogPage {
  items: DocumentLog[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface GetDocumentLogsParams {
  page?: number;
  size?: number;
  status_filter?: string;
}

export const useGetDocumentLogs: useQueryFunctionType<
  GetDocumentLogsParams,
  DocumentLogPage
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<DocumentLogPage> => {
    const qp: Record<string, string> = {};
    if (params?.page !== undefined) qp.page = String(params.page);
    if (params?.size !== undefined) qp.size = String(params.size);
    if (params?.status_filter) qp.status_filter = params.status_filter;

    const res = await api.get(`${getURL("IDP_LOGS")}/`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<DocumentLogPage, any> = query(
    [
      "useGetDocumentLogs",
      params?.status_filter ?? "all",
      params?.page ?? 1,
      params?.size ?? 50,
    ],
    getFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
