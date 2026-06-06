import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface HeaderReorderItem {
  id: string;
  display_order: number;
}

export const usePatchHeadersReorder = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, { configId: string; items: HeaderReorderItem[] }>({
    mutationFn: async ({ configId, items }) => {
      await api.patch(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/headers/reorder`, items);
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
