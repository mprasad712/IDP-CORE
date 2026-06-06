import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { FieldConfigLineItem } from "./use-get-field-configs";

export interface LineItemUpdatePayload {
  column_name?: string;
  column_type?: "text" | "number" | "date";
  is_required?: boolean;
  display_order?: number;
}

export const usePutFieldConfigLineItem = () => {
  const queryClient = useQueryClient();

  return useMutation<
    FieldConfigLineItem,
    Error,
    { configId: string; lineItemId: string; payload: LineItemUpdatePayload }
  >({
    mutationFn: async ({ configId, lineItemId, payload }) => {
      const res = await api.put(
        `${getURL("IDP_FIELD_CONFIGS")}/${configId}/line-items/${lineItemId}`,
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
