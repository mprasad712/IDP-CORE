import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import type { ProcessedDocDetail } from "./use-get-processed-doc";

export interface FieldUpdateItem {
  id: string;
  value: string | null;
}

export interface HumanFieldsUpdatePayload {
  headers?: FieldUpdateItem[];
  line_items?: FieldUpdateItem[];
}

export const usePatchProcessedDocFields = (id: string) => {
  const queryClient = useQueryClient();

  return useMutation<ProcessedDocDetail, Error, HumanFieldsUpdatePayload>({
    mutationFn: async (payload) => {
      const res = await api.patch(`${getURL("IDP_PROCESSED_DOCS")}/${id}/fields`, payload);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDoc", id] });
      queryClient.invalidateQueries({ queryKey: ["useGetProcessedDocs"] });
    },
  });
};
