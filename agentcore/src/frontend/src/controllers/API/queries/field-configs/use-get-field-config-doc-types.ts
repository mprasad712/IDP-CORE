import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface FieldConfigDocTypesResponse {
  docTypes: string[];
}

export const useGetFieldConfigDocTypes: useQueryFunctionType<
  undefined,
  FieldConfigDocTypesResponse
> = (_, options?) => {
  const { query } = UseRequestProcessor();

  const getFieldConfigDocTypesFn = async (): Promise<FieldConfigDocTypesResponse> => {
    const res = await api.get(`${getURL("IDP_FIELD_CONFIGS")}/doc-types`);
    const raw: string[] = res.data ?? [];
    return { docTypes: raw };
  };

  const queryResult: UseQueryResult<FieldConfigDocTypesResponse, any> = query(
    ["useGetFieldConfigDocTypes"],
    getFieldConfigDocTypesFn,
    { refetchOnMount: true, staleTime: 30_000, ...options },
  );

  return queryResult;
};
