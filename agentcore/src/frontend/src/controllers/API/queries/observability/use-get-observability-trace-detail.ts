import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";
import type { TraceDetailResponse } from "./types";

export interface GetObservabilityTraceDetailParams {
  trace_id: string;
  org_id?: string;
  dept_id?: string;
  trace_scope?: string;
}

export const useGetObservabilityTraceDetail: useQueryFunctionType<
  GetObservabilityTraceDetailParams,
  TraceDetailResponse
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<TraceDetailResponse> => {
    if (!params?.trace_id) {
      throw new Error("trace_id is required");
    }

    const qp: Record<string, string> = {};
    if (params?.org_id) qp.org_id = params.org_id;
    if (params?.dept_id) qp.dept_id = params.dept_id;
    if (params?.trace_scope) qp.trace_scope = params.trace_scope;

    const res = await api.get(`${getURL("OBSERVABILITY")}/traces/${params.trace_id}`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<TraceDetailResponse, any> = query(
    [
      "useGetObservabilityTraceDetail",
      params?.trace_id ?? "none",
      params?.org_id ?? "all",
      params?.dept_id ?? "all",
      params?.trace_scope ?? "all",
    ],
    getFn,
    { refetchOnMount: true, enabled: !!params?.trace_id, ...options },
  );

  return queryResult;
};
