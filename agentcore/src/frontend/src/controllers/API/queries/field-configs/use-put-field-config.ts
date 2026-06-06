import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfig } from "./use-get-field-configs";

export interface FieldConfigUpdatePayload {
  name?: string;
  description?: string | null;
  is_template?: boolean;
  is_active?: boolean;
  extra?: Record<string, unknown> | null;
}

export const usePutFieldConfig = () => {
  const queryClient = useQueryClient();

  return useMutation<FieldConfig, Error, { id: string; payload: FieldConfigUpdatePayload }>({
    mutationFn: async ({ id, payload }) => {
      const res = await api.put(`${getURL("IDP_FIELD_CONFIGS")}/${id}`, payload);
      return res.data;
    },
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", id] });
    },
  });
};
