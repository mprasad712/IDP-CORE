import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

// Delete an entire line-item ROW (all its cells) from a processed document:
// DELETE /api/v1/idp/processed-docs/{id}/line-items/{row_index} -> 204.
// row_index identifies the row on the document's latest job; other rows are not renumbered.
export const useDeleteLineItemRow = () => {
  const queryClient = useQueryClient();

  return useMutation<void, Error, { id: string; rowIndex: number }>({
    mutationFn: async ({ id, rowIndex }) => {
      await api.delete(`${getURL("IDP_PROCESSED_DOCS")}/${id}/line-items/${rowIndex}`);
    },
    onSuccess: (_data, { id }) => {
      // Refetch the detail (row removed) and the list (counts).
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};
