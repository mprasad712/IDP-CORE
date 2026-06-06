import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface FieldConfigNamesResponse {
  names: string[];
}

export const useGetFieldConfigNames: useQueryFunctionType<
  undefined,
  FieldConfigNamesResponse
> = (_, options?) => {
  const { query } = UseRequestProcessor();

  const getFieldConfigNamesFn = async (): Promise<FieldConfigNamesResponse> => {
    const res = await api.get(`${getURL("IDP_FIELD_CONFIGS")}/names`);
    const raw: string[] = res.data ?? [];
    return { names: raw };
  };

  const queryResult: UseQueryResult<FieldConfigNamesResponse, any> = query(
    ["useGetFieldConfigNames"],
    getFieldConfigNamesFn,
    { refetchOnMount: true, staleTime: 30_000, ...options },
  );

  return queryResult;
};
