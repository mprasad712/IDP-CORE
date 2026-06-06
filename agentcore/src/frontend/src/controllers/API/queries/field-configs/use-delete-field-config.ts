import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export const useDeleteFieldConfig = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await api.delete(`${getURL("IDP_FIELD_CONFIGS")}/${id}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
    },
  });
};
