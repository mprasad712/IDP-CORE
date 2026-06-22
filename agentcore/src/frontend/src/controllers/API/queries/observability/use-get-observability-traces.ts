import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";
import type { TracesListResponse } from "./types";

export interface GetObservabilityTracesParams {
  limit?: number;
  page?: number;
  session_id?: string;
  org_id?: string;
  dept_id?: string;
  from_date?: string;
  to_date?: string;
  environment?: string;
  trace_scope?: string;
}

export const useGetObservabilityTraces: useQueryFunctionType<
  GetObservabilityTracesParams,
  TracesListResponse
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<TracesListResponse> => {
    const qp: Record<string, string> = {};
    if (params?.limit !== undefined) qp.limit = String(params.limit);
    if (params?.page !== undefined) qp.page = String(params.page);
    if (params?.session_id) qp.session_id = params.session_id;
    if (params?.org_id) qp.org_id = params.org_id;
    if (params?.dept_id) qp.dept_id = params.dept_id;
    if (params?.from_date) qp.from_date = params.from_date;
    if (params?.to_date) qp.to_date = params.to_date;
    if (params?.environment) qp.environment = params.environment;
    if (params?.trace_scope) qp.trace_scope = params.trace_scope;

    const res = await api.get(`${getURL("OBSERVABILITY")}/traces`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<TracesListResponse, any> = query(
    [
      "useGetObservabilityTraces",
      params?.limit ?? 50,
      params?.page ?? 1,
      params?.session_id ?? "all",
      params?.org_id ?? "all",
      params?.dept_id ?? "all",
      params?.from_date ?? "none",
      params?.to_date ?? "none",
      params?.environment ?? "all",
      params?.trace_scope ?? "all",
    ],
    getFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
