import type { UseQueryResult } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface FieldConfigHeader {
  id: string;
  config_id: string;
  field_name: string;
  field_type: "text" | "number" | "date" | "boolean";
  is_required: boolean;
  display_order: number;
  description: string | null;
  prompt: string | null;
  created_at: string;
  updated_at: string;
}

export interface FieldConfigLineItem {
  id: string;
  config_id: string;
  column_name: string;
  column_type: "text" | "number" | "date";
  is_required: boolean;
  display_order: number;
  prompt: string | null;
  created_at: string;
  updated_at: string;
}

export interface FieldConfig {
  id: string;
  name: string;
  description: string | null;
  org_id: string | null;
  is_template: boolean;
  is_active: boolean;
  visibility: "private" | "org" | "dept";
  deleted_at: string | null;
  extra: Record<string, unknown> | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
  headers: FieldConfigHeader[];
  line_items: FieldConfigLineItem[];
}

export interface FieldConfigPage {
  items: FieldConfig[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

export interface GetFieldConfigsParams {
  page?: number;
  size?: number;
  name?: string;
  is_template?: boolean;
  org_id?: string;
  is_active?: boolean;
}

export const useGetFieldConfigs: useQueryFunctionType<
  GetFieldConfigsParams,
  FieldConfigPage
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFieldConfigsFn = async (): Promise<FieldConfigPage> => {
    const queryParams: Record<string, string> = {};
    if (params?.page !== undefined) queryParams.page = String(params.page);
    if (params?.size !== undefined) queryParams.size = String(params.size);
    if (params?.name) queryParams.name = params.name;
    if (params?.is_template !== undefined) queryParams.is_template = String(params.is_template);
    if (params?.org_id) queryParams.org_id = params.org_id;
    if (params?.is_active !== undefined) queryParams.is_active = String(params.is_active);

    const res = await api.get(`${getURL("IDP_FIELD_CONFIGS")}/`, {
      params: Object.keys(queryParams).length > 0 ? queryParams : undefined,
    });
    return res.data;
  };

  const queryResult: UseQueryResult<FieldConfigPage, any> = query(
    [
      "useGetFieldConfigs",
      params?.is_template ?? "all",
      params?.org_id ?? "all",
      params?.name ?? "",
      params?.page ?? 1,
      params?.size ?? 50,
    ],
    getFieldConfigsFn,
    { refetchOnMount: true, ...options },
  );

  return queryResult;
};
