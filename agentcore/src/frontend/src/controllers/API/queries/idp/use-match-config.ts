import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { useQueryFunctionType } from "@/types/api";
import { api } from "../../api";
import { getURL } from "../../helpers/constants";
import { UseRequestProcessor } from "../../services/request-processor";

export interface MatchConfig {
  agent_id: string;
  match_enabled: boolean;
  match_type: "2way" | "3way";
  amount_tolerance_pct: number;
  quantity_tolerance_pct: number;
  unit_price_tolerance_pct: number;
  vendor_name_fuzzy_threshold: number;
  sap_connector_id: string | null;
}

export const useGetMatchConfig: useQueryFunctionType<
  { agentId: string },
  MatchConfig
> = (params, options?) => {
  const { query } = UseRequestProcessor();

  const getFn = async (): Promise<MatchConfig> => {
    const res = await api.get(
      `${getURL("IDP_MATCHING_CONFIG")}/${params!.agentId}`,
    );
    return res.data;
  };

  const queryResult: UseQueryResult<MatchConfig, any> = query(
    ["useGetMatchConfig", params?.agentId ?? ""],
    getFn,
    { enabled: !!params?.agentId, ...options },
  );

  return queryResult;
};

export const useSaveMatchConfig = (agentId: string) => {
  const queryClient = useQueryClient();

  return useMutation<MatchConfig, Error, Omit<MatchConfig, "agent_id">>({
    mutationFn: async (payload) => {
      const res = await api.put(
        `${getURL("IDP_MATCHING_CONFIG")}/${agentId}`,
        payload,
      );
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["useGetMatchConfig", agentId],
      });
    },
  });
};
