import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface ReviewPayload {
  notes?: string | null;
}

export const usePostProcessedDocReview = (id: string) => {
  const queryClient = useQueryClient();

  return useMutation<any, Error, ReviewPayload>({
    mutationFn: async (payload) => {
      const res = await api.post(`${getURL("IDP_PROCESSED_DOCS")}/${id}/review`, payload ?? {});
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};
