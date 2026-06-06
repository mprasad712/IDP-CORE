import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface LineItemReorderItem {
  id: string;
  display_order: number;
}

export const usePatchLineItemsReorder = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, { configId: string; items: LineItemReorderItem[] }>({
    mutationFn: async ({ configId, items }) => {
      await api.patch(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/line-items/reorder`, items);
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
