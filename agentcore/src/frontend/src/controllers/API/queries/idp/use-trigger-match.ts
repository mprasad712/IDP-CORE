import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export interface TriggerMatchPayload {
  match_type: "2way" | "3way";
  sap_connector_id: string;
  amount_tolerance_pct?: number;
  quantity_tolerance_pct?: number;
  unit_price_tolerance_pct?: number;
  vendor_name_fuzzy_threshold?: number;
}

export const useTriggerMatch = (documentId: string) => {
  const queryClient = useQueryClient();

  return useMutation<any, Error, TriggerMatchPayload>({
    mutationFn: async (payload) => {
      const res = await api.post(
        `${getURL("IDP_MATCHING")}/${documentId}/trigger`,
        payload,
      );
      return res.data;
    },
    onSuccess: () => {
      setTimeout(() => {
        queryClient.invalidateQueries({
          queryKey: ["useGetMatchResults", documentId],
        });
      }, 2000);
    },
  });
};
