import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export const useDeleteFieldConfigLineItem = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, { configId: string; lineItemId: string }>({
    mutationFn: async ({ configId, lineItemId }) => {
      await api.delete(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/line-items/${lineItemId}`);
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
