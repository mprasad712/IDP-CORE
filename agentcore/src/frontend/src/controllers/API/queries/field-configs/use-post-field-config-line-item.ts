import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfigLineItem } from "./use-get-field-configs";

export interface LineItemCreatePayload {
  column_name: string;
  column_type: "text" | "number" | "date";
  is_required?: boolean;
  display_order: number;
  prompt?: string | null;
}

export const usePostFieldConfigLineItem = () => {
  const queryClient = useQueryClient();

  return useMutation<FieldConfigLineItem, Error, { configId: string; payload: LineItemCreatePayload }>({
    mutationFn: async ({ configId, payload }) => {
      const res = await api.post(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/line-items`, payload);
      return res.data;
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
