import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

// Add a new line-item ROW to a processed document:
// POST /api/v1/idp/processed-docs/{id}/line-items  body {columns: {col: value}} -> {row_index}.
// `columns` maps each line-item column to its initial value (blank "" for a new editable row);
// the backend inserts one cell per column at max(row_index)+1 on the latest job.
export const usePostLineItemRow = () => {
  const queryClient = useQueryClient();

  return useMutation<
    { row_index: number },
    Error,
    { id: string; columns: Record<string, string | null> }
  >({
    mutationFn: async ({ id, columns }) => {
      const res = await api.post(`${getURL("IDP_PROCESSED_DOCS")}/${id}/line-items`, { columns });
      return res.data;
    },
    onSuccess: (_data, { id }) => {
      // Refetch the detail (new row + its cell ids for the PATCH flow) and the list (counts).
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};
