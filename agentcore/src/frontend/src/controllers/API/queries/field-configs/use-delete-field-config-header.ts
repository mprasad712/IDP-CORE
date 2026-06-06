import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export const useDeleteFieldConfigHeader = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, { configId: string; headerId: string }>({
    mutationFn: async ({ configId, headerId }) => {
      await api.delete(`${getURL("IDP_FIELD_CONFIGS")}/${configId}/headers/${headerId}`);
    },
    onSuccess: (_, { configId }) => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfig", configId] });
    },
  });
};
