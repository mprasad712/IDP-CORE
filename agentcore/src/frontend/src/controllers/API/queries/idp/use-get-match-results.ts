import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface MatchDiscrepancy {
  id: string;
  scope: "header" | "line_item";
  line_item_index: number | null;
  field_name: string;
  invoice_value: string | null;
  po_value: string | null;
  gr_value: string | null;
  variance_pct: number | null;
  status: "full_match" | "partial_match" | "mismatch";
  is_accepted: boolean;
  reviewer_note: string | null;
}

export interface MatchResult {
  has_match: boolean;
  id?: string;
  document_id: string;
  match_type?: "2way" | "3way";
  overall_status?: "full_match" | "partial_match" | "mismatch" | "error";
  match_score?: number;
  po_number?: string;
  gr_document_number?: string | null;
  tolerance_config?: Record<string, unknown> | null;
  reviewed_by?: string | null;
  reviewed_at?: string | null;
  created_at?: string;
  discrepancies?: MatchDiscrepancy[];
}

export const useGetMatchResults: useQueryFunctionType<
  { documentId: string },
  MatchResult
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<MatchResult> => {
    const res = await api.get(
      `${getURL("IDP_MATCHING")}/${params!.documentId}/results`,
    );
    return res.data;
  };

  const queryResult: UseQueryResult<MatchResult, any> = query(
    ["useGetMatchResults", params?.documentId ?? ""],
    getFn,
    { enabled: !!params?.documentId, ...options },
  );

  return queryResult;
};
