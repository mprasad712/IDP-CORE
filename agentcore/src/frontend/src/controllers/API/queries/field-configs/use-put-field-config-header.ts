import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfigHeader } from "./use-get-field-configs";

export interface HeaderUpdatePayload {
  field_name?: string;
  field_type?: "text" | "number" | "date" | "boolean";
  is_required?: boolean;
  display_order?: number;
  description?: string | null;
  prompt?: string | null;
}

export const usePutFieldConfigHeader = () => {
  const queryClient = useQueryClient();

  return useMutation<
    FieldConfigHeader,
    Error,
    { configId: string; headerId: string; payload: HeaderUpdatePayload }
  >({
    mutationFn: async ({ configId, headerId, payload }) => {
      const res = await api.put(
        `${getURL("IDP_FIELD_CONFIGS")}/${configId}/headers/${headerId}`,
        payload,
      );
      return res.data;
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
