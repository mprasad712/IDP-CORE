import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export const usePostProcessedDocApprove = (id: string) => {
  const queryClient = useQueryClient();

  return useMutation<any, Error, void>({
    mutationFn: async () => {
      const res = await api.post(`${getURL("IDP_PROCESSED_DOCS")}/${id}/approve`, {});
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};
