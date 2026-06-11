import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";
import type { ProcessedDoc } from "./use-get-processed-docs";

export interface ExtractedHeader {
  id: string;
  field_name: string;
  extracted_value: string | null;
  confidence_score: number | null;
  reasoning_trace: string | null;
  source_location: Record<string, unknown> | null;
  reviewed_value: string | null;
  is_reviewed: boolean;
}

export interface ExtractedLineItem {
  id: string;
  row_index: number;
  column_name: string;
  extracted_value: string | null;
  confidence_score: number | null;
  reasoning_trace: string | null;
  source_location: Record<string, unknown> | null;
  reviewed_value: string | null;
  is_reviewed: boolean;
}

export interface ProcessedDocDetail extends ProcessedDoc {
  headers: ExtractedHeader[];
  line_items: ExtractedLineItem[];
}

export const useGetProcessedDoc: useQueryFunctionType<
  { id: string },
  ProcessedDocDetail
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<ProcessedDocDetail> => {
    const res = await api.get(`${getURL("IDP_PROCESSED_DOCS")}/${params!.id}`);
    return res.data;
  };

  const queryResult: UseQueryResult<ProcessedDocDetail, any> = query(
    ["useGetProcessedDoc", params?.id ?? ""],
    getFn,
    { enabled: !!params?.id, refetchOnMount: true, ...options },
  );

  return queryResult;
};
