import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

// Soft-delete a processed document: DELETE /api/v1/idp/processed-docs/{id} -> 204.
// The row is retained in the DB (deleted_at set); it just disappears from the lists.
export const useDeleteProcessedDoc = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, string>({
    mutationFn: async (id) => {
      await api.delete(`${getURL("IDP_PROCESSED_DOCS")}/${id}`);
    },
    onSuccess: (_data, id) => {
      // Invalidate both the list (row disappears) and this doc's detail cache, matching the
      // row-mutation hooks (post/patch line-items) so a deleted doc's detail is never stale.
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
    },
  });
};
