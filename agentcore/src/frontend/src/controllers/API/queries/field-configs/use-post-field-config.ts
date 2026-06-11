import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfig } from "./use-get-field-configs";

export interface HeaderCreatePayload {
  field_name: string;
  field_type: "text" | "number" | "date" | "boolean";
  is_required?: boolean;
  display_order: number;
  description?: string | null;
  prompt?: string | null;
}

export interface FieldConfigCreatePayload {
  name: string;
  description?: string | null;
  org_id?: string | null;
  is_template?: boolean;
  is_active?: boolean;
  visibility?: "private" | "org" | "dept";
  extra?: Record<string, unknown> | null;
  headers?: HeaderCreatePayload[];
}

export const usePostFieldConfig = () => {
  const queryClient = useQueryClient();

  return useMutation<FieldConfig, Error, FieldConfigCreatePayload>({
    mutationFn: async (payload) => {
      const res = await api.post(`${getURL("IDP_FIELD_CONFIGS")}/`, payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
    },
  });
};
