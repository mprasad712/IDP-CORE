import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";
import type { MetricsResponse } from "./types";

export interface GetObservabilityMetricsParams {
  days?: number;
  org_id?: string;
  dept_id?: string;
  trace_scope?: string;
  environment?: string;
  from_date?: string;
  to_date?: string;
}

export const useGetObservabilityMetrics: useQueryFunctionType<
  GetObservabilityMetricsParams,
  MetricsResponse
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<MetricsResponse> => {
    const qp: Record<string, string> = {};
    if (params?.days !== undefined) qp.days = String(params.days);
    if (params?.org_id) qp.org_id = params.org_id;
    if (params?.dept_id) qp.dept_id = params.dept_id;
    if (params?.trace_scope) qp.trace_scope = params.trace_scope;
    if (params?.environment) qp.environment = params.environment;
    if (params?.from_date) qp.from_date = params.from_date;
    if (params?.to_date) qp.to_date = params.to_date;

    const res = await api.get(`${getURL("OBSERVABILITY")}/metrics`, {
      params: Object.keys(qp).length > 0 ? qp : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<MetricsResponse, any> = query(
    [
      "useGetObservabilityMetrics",
      params?.days ?? 30,
      params?.org_id ?? "all",
      params?.dept_id ?? "all",
      params?.trace_scope ?? "all",
      params?.environment ?? "all",
      params?.from_date ?? "none",
      params?.to_date ?? "none",
    ],
    getFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
