import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";
import type { FieldConfig } from "./use-get-field-configs";

export const useGetFieldConfig: useQueryFunctionType<{ id: string }, FieldConfig> = (
  params,
  options?,
) => {
  const { query } = UseRequestProcessor();

  const getFieldConfigFn = async (): Promise<FieldConfig> => {
    const res = await api.get(`${getURL("IDP_FIELD_CONFIGS")}/${params!.id}`);
    return res.data;
  };

  const queryResult: UseQueryResult<FieldConfig, any> = query(
    ["useGetFieldConfig", params?.id ?? ""],
    getFieldConfigFn,
    { enabled: !!params?.id, refetchOnMount: true, ...options },
  );

  return queryResult;
};
