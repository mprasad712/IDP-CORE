import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfigHeader } from "./use-get-field-configs";

export interface HeaderCreatePayload {
  field_name: string;
  field_type: "text" | "number" | "date" | "boolean";
  is_required?: boolean;
  display_order: number;
  description?: string | null;
}

export const usePostFieldConfigHeader = () => {
  const queryClient = useQueryClient();

  return useMutation<FieldConfigHeader, Error, { configId: string; payload: HeaderCreatePayload }>({
    mutationFn: async ({ configId, payload }) => {
      const res = await api.post(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/headers`, payload);
      return res.data;
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
