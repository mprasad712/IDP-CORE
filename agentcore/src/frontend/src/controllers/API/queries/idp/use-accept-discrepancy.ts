import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";

export const useAcceptDiscrepancy = (documentId: string) => {
  const queryClient = useQueryClient();

  return useMutation<
    any,
    Error,
    { discrepancyId: string; reviewer_note?: string }
  >({
    mutationFn: async ({ discrepancyId, reviewer_note }) => {
      const res = await api.patch(
        `${getURL("IDP_MATCHING")}/${documentId}/discrepancies/${discrepancyId}/accept`,
        { reviewer_note },
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["useGetMatchResults", documentId],
      });
    },
  });
};

export const useRejectDiscrepancy = (documentId: string) => {
  const queryClient = useQueryClient();

  return useMutation<
    any,
    Error,
    { discrepancyId: string; reviewer_note?: string }
  >({
    mutationFn: async ({ discrepancyId, reviewer_note }) => {
      const res = await api.patch(
        `${getURL("IDP_MATCHING")}/${documentId}/discrepancies/${discrepancyId}/reject`,
        { reviewer_note },
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["useGetMatchResults", documentId],
      });
    },
  });
};
